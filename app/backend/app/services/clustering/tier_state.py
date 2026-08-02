"""Bridging the per-tier engine to persisted project state.

Three jobs:
  - assemble a tier's input items, from the entity's own base records (finest tier)
    or from the tier below's confirmed clusters (category, family);
  - persist a confirmed tier;
  - rebuild the flat `ClusteringState` that everything downstream reads.

That last one is the point of this module. Splitting clustering into three
confirmable steps changes how a hierarchy is *produced*, but job profile
generation, the overview, the exports and the skills/tasks headcount rollups all
consume one denormalised structure — item -> (profile, category, family). Keeping
that view as a derived artifact means the restructure does not ripple into any of
them.

**Entity-parameterised.** Jobs, skills and tasks are three hierarchies of the same
shape, and the only differences are where the tier records live in state, what the
finest tier's base items are, and what the tiers are called to a reader. Everything
else — stability, gating, routing, naming, the denormalised rollup — is identical, so
it is written once here and the entity is an argument. The skill and task taxonomies
previously used a separate single-shot path that cut all three tiers at once; they
now go through this, which is what gives them the same per-tier review.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from app.models.project_state import (
    ClusteringState,
    ItemAssignmentRecord,
    ProjectState,
    TierMemberRecord,
    TierState,
)
from app.services.clustering import tier as tier_engine
from app.services.normalization import NormalizedResult
from app.services.project_service import ProjectService

# Tier order, finest first — the order the user confirms them in.
ORDER = ("profile", "category", "family")
PARENT_OF = {"profile": "category", "category": "family"}
CHILD_OF = {"category": "profile", "family": "category"}

ENTITIES = ("job", "skill", "task")


@dataclass(frozen=True)
class EntitySpec:
    """What differs between the three hierarchies.

    `embeddings_entity` is the model key (jobQWEN/skillQWEN/taskQWEN);
    `array_name` is the blob basename its vectors are stored under, which has to
    match what the build step wrote; `nouns` is per-tier UI wording, finest first.
    """

    name: str
    embeddings_entity: str
    array_name: str
    tier_titles: tuple[str, str, str]   # finest -> coarsest
    nouns: tuple[str, str, str]         # what each tier groups, finest -> coarsest


ENTITY_SPECS: dict[str, EntitySpec] = {
    "job": EntitySpec(
        "job", "job", "cluster_embeddings",
        ("Job profiles", "Job categories", "Job families"),
        ("normalised jobs", "job profiles", "job categories"),
    ),
    "skill": EntitySpec(
        "skill", "skill", "skill_embeddings",
        ("Skill clusters", "Skill categories", "Skill families"),
        ("inferred skills", "skill clusters", "skill categories"),
    ),
    "task": EntitySpec(
        "task", "task", "task_embeddings",
        ("Task clusters", "Task categories", "Task domains"),
        ("inferred tasks", "task clusters", "task categories"),
    ),
}


def spec(entity: str) -> EntitySpec:
    try:
        return ENTITY_SPECS[entity]
    except KeyError:
        raise ValueError(f"unknown entity {entity!r}; expected one of {list(ENTITY_SPECS)}") from None


def tier_noun(entity: str, tier: str) -> str:
    return spec(entity).nouns[ORDER.index(tier)]


def tier_title(entity: str, tier: str) -> str:
    return spec(entity).tier_titles[ORDER.index(tier)]


def tiers_of(state: ProjectState, entity: str) -> dict[str, TierState]:
    """The tier records for this entity. Mutable — callers assign into it."""
    if entity == "job":
        return state.clustering_tiers
    if entity == "skill":
        return state.skills.clustering_tiers
    if entity == "task":
        return state.tasks.clustering_tiers
    raise ValueError(f"unknown entity {entity!r}")


def base_items(state: ProjectState, entity: str) -> list[tuple[str, str]]:
    """(id, text) per base record, in state order — the finest tier's population.

    The text is what gets embedded and what the router reads, so it carries the
    same content in both places by construction.
    """
    if entity == "job":
        return [
            (
                p.id,
                NormalizedResult(
                    purpose_statement=p.purpose_statement,
                    key_tasks=p.key_tasks,
                    management_line=p.management_line,
                    budget_responsibility=p.budget_responsibility,
                ).embedding_text(),
            )
            for p in state.normalized_profiles
        ]
    if entity == "skill":
        return [(x.id, f"{x.name}. {x.description}") for x in state.skills.inferred]
    if entity == "task":
        return [(x.id, f"{x.name}. {x.description}") for x in state.tasks.inferred]
    raise ValueError(f"unknown entity {entity!r}")


class TierNotReady(RuntimeError):
    """A tier was asked for before the tier below it was confirmed."""


def previous_tier(tier: str) -> str | None:
    return CHILD_OF.get(tier)


def is_confirmed(state: ProjectState, entity: str, tier: str) -> bool:
    t = tiers_of(state, entity).get(tier)
    return bool(t and t.names)


def build_items(
    svc: ProjectService, state: ProjectState, entity: str, tier: str
) -> tier_engine.TierItems:
    """The items this tier clusters.

    Finest tier: the entity's base records, using the embeddings the build step
    already computed. Coarser tiers: the confirmed clusters below, using their
    stored centroids — so no re-embedding happens as you move up.
    """
    client, project = state.meta.client_slug, state.meta.project_slug
    es = spec(entity)

    if tier == "profile":
        emb = svc.load_array(client, f"{project}/artifacts/{es.array_name}.npy")
        ids = svc.load_index(client, f"{project}/artifacts/{es.array_name}_index.json")
        if emb is None or ids is None:
            raise TierNotReady(
                f"the {tier_noun(entity, 'profile')} have not been embedded yet — run "
                f"this step's build first"
            )
        text_by_id = dict(base_items(state, entity))
        texts = [text_by_id.get(item_id, item_id) for item_id in ids]
        return tier_engine.TierItems(ids=list(ids), texts=texts, embeddings=emb)

    below = CHILD_OF[tier]
    prev = tiers_of(state, entity).get(below)
    if prev is None or not prev.names:
        raise TierNotReady(
            f"the {below} tier must be confirmed before the {tier} tier — "
            f"{tier} clusters {below}s"
        )
    centroids = svc.load_array(client, prev.centroids_blob_path) if prev.centroids_blob_path else None
    if centroids is None:
        raise TierNotReady(
            f"the {below} tier's centroids are missing; re-confirm the {below} tier"
        )

    ids, texts, rows = [], [], []
    for cid in sorted(prev.names):
        if cid >= centroids.shape[0]:
            continue
        members = prev.exemplars.get(cid, [])[:4]
        detail = "; ".join(m.split(".")[0][:90] for m in members)
        ids.append(f"{below}:{cid}")
        texts.append(prev.names[cid] + (f". Includes: {detail}" if detail else ""))
        rows.append(centroids[cid])
    return tier_engine.TierItems(
        ids=ids, texts=texts, embeddings=np.vstack(rows) if rows else np.empty((0, 0))
    )


def save_tier(
    svc: ProjectService,
    state: ProjectState,
    entity: str,
    tier: str,
    result: tier_engine.TierResult,
    *,
    embedding_model: str,
) -> TierState:
    """Persist a confirmed tier and drop anything above it.

    Confirming a tier invalidates the coarser tiers built on top of the previous
    version of it: their members refer to cluster ids that may no longer mean the
    same thing. Dropping them is the honest outcome — the alternative is a
    hierarchy whose upper levels silently describe an older lower level.
    """
    client, project = state.meta.client_slug, state.meta.project_slug
    centroid_path = svc.save_array(
        client, project, f"tier_{entity}_{tier}_centroids", result.centroids
    )

    record = TierState(
        tier=tier,
        k=result.k,
        gate=result.gate,
        embedding_model=embedding_model,
        names=result.names,
        members=[
            TierMemberRecord(
                item_id=m.item_id,
                backbone_cluster_id=m.backbone_cluster_id,
                final_cluster_id=m.final_cluster_id,
                stability_score=m.stability_score,
                routed_by_llm=m.routed_by_llm,
                route_confidence=m.route_confidence,
                secondary_cluster_id=m.secondary_cluster_id,
                secondary_confidence=m.secondary_confidence,
                self_consistency=m.self_consistency,
            )
            for m in result.members
        ],
        exemplars={cid: texts[:4] for cid, texts in result.exemplar_texts.items()},
        centroids_blob_path=centroid_path,
        n_routed=result.n_routed,
        n_moved=result.n_moved,
        computed_at=datetime.now(timezone.utc),
    )
    tiers = tiers_of(state, entity)
    tiers[tier] = record

    for above in ORDER[ORDER.index(tier) + 1 :]:
        if above in tiers:
            print(f"  [{entity}:{tier}] dropping the {above} tier — it was built on the previous {tier}s")
            del tiers[above]

    rebuild_denormalised(state, entity)
    return record


def _set_clustering(state: ProjectState, entity: str, value: ClusteringState | None) -> None:
    if entity == "job":
        state.clustering = value
    elif entity == "skill":
        state.skills.clustering = value
    elif entity == "task":
        state.tasks.clustering = value
    else:
        raise ValueError(f"unknown entity {entity!r}")


def rebuild_denormalised(state: ProjectState, entity: str) -> None:
    """Rebuild the entity's flat `ClusteringState` from its tier records.

    Left as None until all three tiers exist, because a partial hierarchy is not
    something downstream can use: a job profile document needs its category and
    family for the breadcrumb, and the exports and analytics assume all three. The
    wizard gates on the tiers themselves, so nothing needs a half-built view.
    """
    tiers = tiers_of(state, entity)
    if not all(is_confirmed(state, entity, t) for t in ORDER):
        _set_clustering(state, entity, None)
        return

    prof, cat, fam = tiers["profile"], tiers["category"], tiers["family"]
    cat_of_profile = {
        int(m.item_id.split(":")[1]): m.final_cluster_id for m in cat.members if ":" in m.item_id
    }
    backbone_cat_of_profile = {
        int(m.item_id.split(":")[1]): m.backbone_cluster_id for m in cat.members if ":" in m.item_id
    }
    fam_of_category = {
        int(m.item_id.split(":")[1]): m.final_cluster_id for m in fam.members if ":" in m.item_id
    }
    backbone_fam_of_category = {
        int(m.item_id.split(":")[1]): m.backbone_cluster_id for m in fam.members if ":" in m.item_id
    }

    assignments: list[ItemAssignmentRecord] = []
    for m in prof.members:
        final_p = m.final_cluster_id
        backbone_p = m.backbone_cluster_id
        final_c = cat_of_profile.get(final_p, -1)
        backbone_c = backbone_cat_of_profile.get(backbone_p, -1)
        assignments.append(
            ItemAssignmentRecord(
                item_id=m.item_id,
                backbone_profile_id=backbone_p,
                backbone_category_id=backbone_c,
                backbone_family_id=backbone_fam_of_category.get(backbone_c, -1),
                final_profile_id=final_p,
                final_category_id=final_c,
                final_family_id=fam_of_category.get(final_c, -1),
                stability_score=m.stability_score,
                routed_by_llm=m.routed_by_llm,
                route_confidence=m.route_confidence,
                secondary_profile_id=m.secondary_cluster_id,
                secondary_confidence=m.secondary_confidence,
                self_consistency=m.self_consistency,
            )
        )

    es = spec(entity)
    _set_clustering(state, entity, ClusteringState(
        embedding_model=prof.embedding_model or f"{entity}QWEN",
        linkage_blob_path=f"{state.meta.project_slug}/artifacts/{es.array_name}_linkage.npy",
        embedding_index_blob_path=f"{state.meta.project_slug}/artifacts/{es.array_name}_index.json",
        k_profiles=prof.k,
        k_categories=cat.k,
        k_families=fam.k,
        assignments=assignments,
        profile_names=prof.names,
        category_names=cat.names,
        family_names=fam.names,
        # One gate no longer describes the run; the profile tier's is the one that
        # governed the job-level assignments, and each tier keeps its own.
        gate=prof.gate,
        computed_at=fam.computed_at,
    ))


def hierarchy_summary(state: ProjectState, entity: str = "job") -> dict:
    """Per-tier status for the wizard: what is confirmed, and what it produced."""
    out: dict[str, dict] = {}
    for t in ORDER:
        rec = tiers_of(state, entity).get(t)
        out[t] = {
            "confirmed": bool(rec and rec.names),
            "k": rec.k if rec else None,
            "gate": rec.gate if rec else None,
            "n_routed": rec.n_routed if rec else 0,
            "n_moved": rec.n_moved if rec else 0,
            "computed_at": rec.computed_at.isoformat() if rec and rec.computed_at else None,
            "ready_to_run": _ready(state, entity, t),
        }
    return out


def _ready(state: ProjectState, entity: str, tier: str) -> bool:
    below = CHILD_OF.get(tier)
    if below is None:
        # The finest tier needs enough base records to cluster at all.
        return len(base_items(state, entity)) >= 3
    return is_confirmed(state, entity, below)
