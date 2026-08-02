"""Bridging the per-tier engine to persisted project state.

Three jobs:
  - assemble a tier's input items, from normalised profiles (profile tier) or from
    the tier below's confirmed clusters (category, family);
  - persist a confirmed tier;
  - rebuild the flat `ClusteringState` that everything downstream reads.

That last one is the point of this module. Splitting clustering into three
confirmable steps changes how the hierarchy is *produced*, but job profile
generation, the overview, the exports and the skills/tasks headcount rollups all
consume one denormalised structure — item -> (profile, category, family). Keeping
that view as a derived artifact means the restructure does not ripple into any of
them.
"""
from __future__ import annotations

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


class TierNotReady(RuntimeError):
    """A tier was asked for before the tier below it was confirmed."""


def previous_tier(tier: str) -> str | None:
    return CHILD_OF.get(tier)


def is_confirmed(state: ProjectState, tier: str) -> bool:
    t = state.clustering_tiers.get(tier)
    return bool(t and t.names)


def build_items(
    svc: ProjectService, state: ProjectState, tier: str
) -> tier_engine.TierItems:
    """The items this tier clusters.

    Profile tier: the normalised job profiles, using the embeddings the cluster
    build already computed. Coarser tiers: the confirmed clusters below, using
    their stored centroids — so no re-embedding happens as you move up.
    """
    client, project = state.meta.client_slug, state.meta.project_slug

    if tier == "profile":
        emb = svc.load_array(client, f"{project}/artifacts/cluster_embeddings.npy")
        ids = svc.load_index(client, f"{project}/artifacts/cluster_embeddings_index.json")
        if emb is None or ids is None:
            raise TierNotReady(
                "normalised profiles have not been embedded yet — run the profile "
                "tier's build step first"
            )
        by_id = {p.id: p for p in state.normalized_profiles}
        texts = []
        for item_id in ids:
            p = by_id.get(item_id)
            texts.append(
                NormalizedResult(
                    purpose_statement=p.purpose_statement,
                    key_tasks=p.key_tasks,
                    management_line=p.management_line,
                    budget_responsibility=p.budget_responsibility,
                ).embedding_text()
                if p
                else item_id
            )
        return tier_engine.TierItems(ids=list(ids), texts=texts, embeddings=emb)

    below = CHILD_OF[tier]
    prev = state.clustering_tiers.get(below)
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
    centroid_path = svc.save_array(client, project, f"tier_{tier}_centroids", result.centroids)

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
    state.clustering_tiers[tier] = record

    for above in ORDER[ORDER.index(tier) + 1 :]:
        if above in state.clustering_tiers:
            print(f"  [tier:{tier}] dropping the {above} tier — it was built on the previous {tier}s")
            del state.clustering_tiers[above]

    rebuild_denormalised(state)
    return record


def rebuild_denormalised(state: ProjectState) -> None:
    """Rebuild `state.clustering` from the tier records.

    Left as None until all three tiers exist, because a partial hierarchy is not
    something downstream can use: a job profile document needs its category and
    family for the breadcrumb, and the exports and analytics assume all three. The
    wizard gates on the tiers themselves, so nothing needs a half-built view.
    """
    tiers = state.clustering_tiers
    if not all(is_confirmed(state, t) for t in ORDER):
        state.clustering = None
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

    state.clustering = ClusteringState(
        embedding_model=prof.embedding_model or "jobQWEN",
        linkage_blob_path=f"{state.meta.project_slug}/artifacts/cluster_linkage.npy",
        embedding_index_blob_path=f"{state.meta.project_slug}/artifacts/cluster_embeddings_index.json",
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
    )


def hierarchy_summary(state: ProjectState) -> dict:
    """Per-tier status for the wizard: what is confirmed, and what it produced."""
    out: dict[str, dict] = {}
    for t in ORDER:
        rec = state.clustering_tiers.get(t)
        out[t] = {
            "confirmed": bool(rec and rec.names),
            "k": rec.k if rec else None,
            "gate": rec.gate if rec else None,
            "n_routed": rec.n_routed if rec else 0,
            "n_moved": rec.n_moved if rec else 0,
            "computed_at": rec.computed_at.isoformat() if rec and rec.computed_at else None,
            "ready_to_run": _ready(state, t),
        }
    return out


def _ready(state: ProjectState, tier: str) -> bool:
    below = CHILD_OF.get(tier)
    if below is None:
        return bool(state.normalized_profiles)
    return is_confirmed(state, below)
