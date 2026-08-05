"""Per-tier clustering routes — instructions.txt steps 5/6, and the same flow for
the skill and task taxonomies.

One set of endpoints parameterised by entity AND tier, so nine steps across three
hierarchies are the same code and the same UI. The skill and task taxonomies used to
have their own single-shot endpoints that cut all three tiers at once and named them
in one go; routing them through here is what gives them per-tier cluster counts, a
per-tier stability gate with its cost preview, and per-tier naming the user confirms.

The endpoint split follows what things cost:

  build    embed / assemble the tier's items and build its Ward tree. Once.
  preview  cut at k and report cluster sizes. Instant, so it drives the slider.
           Stability comes back too when it is cheap enough to compute inline —
           see INLINE_STABILITY_LIMIT.
  analyse  the bootstrap stability pass, when it is too slow for the slider.
  gate     how many items a given gate would send to the model. Instant, from the
           cached analysis. This is the number the user needs before spending.
  confirm  name the clusters and route the unstable slice. The only step that
           costs money.
"""
from __future__ import annotations

import asyncio

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.models.project_state import ProjectState
from app.services import embeddings, llm
from app.services.clustering import backbone as bb
from app.services.clustering import tier as tier_engine
from app.services.clustering import tier_state
from app.services.embeddings import get_embedding_service
from app.services.orchestrator import JobAlreadyRunning, ProgressReporter, get_registry, run_job
from app.api.routes import lineage as lineage_routes
from app.services.project_service import ProjectService

router = APIRouter(
    prefix="/api/projects/{client_slug}/{project_slug}/cluster/{entity}/tier/{tier}",
    tags=["clustering"],
)

# One route that is not per-tier: the whole-hierarchy status the wizard opens with.
summary_router = APIRouter(
    prefix="/api/projects/{client_slug}/{project_slug}/cluster", tags=["clustering"]
)

# Above this many items the bootstrap is too slow to run on every slider move
# (measured: ~0.13s for 120 items, ~6.7s for 916 at 50 perturbations), so those
# tiers get an explicit analyse step instead. Categories and families sit well
# under it, which is why only the profile tier needs the extra press.
INLINE_STABILITY_LIMIT = 300

# Per (client, project, tier): the built tree and items, and the last analysis.
# Rebuilding either is deterministic and cheap relative to an LLM call, so a cold
# cache costs a rebuild rather than correctness.
_TIER_CACHE: dict[tuple[str, str, str, str], tuple[tier_engine.TierItems, np.ndarray]] = {}
_ANALYSIS_CACHE: dict[tuple[str, str, str, str], tier_engine.TierAnalysis] = {}


# How many job titles travel with each cluster for the hover tooltip. Enough to
# recognise what a cluster is; not the whole membership, which at the family tier
# would be hundreds of titles per cluster on every slider move.
TOOLTIP_TITLES = 12


# What the tooltip's list is called at the finest tier, where it lists source records.
_LABEL_NOUN = {
    "job": "source job titles",
    "skill": "skills",
    "task": "tasks",
}


def _label_noun(entity: str, tier: str) -> str:
    """What the tooltip is listing, which is not the same at every tier.

    At the finest tier it lists source records. Above it, it lists the child clusters —
    and `tier_noun` already names exactly those, since what a tier groups is the tier
    below it. Without this the family tooltip would announce "source job titles" over a
    list of category names.
    """
    if tier == tier_state.ORDER[0]:
        return _LABEL_NOUN[entity]
    return tier_state.tier_noun(entity, tier)


def _title_map(state: ProjectState, entity: str) -> dict[str, list[str]]:
    """The real underlying names beneath every item id, at every tier.

    Used at the finest tier, where the source titles are the thing the user recognises
    and there are no child clusters to name. Coarser tiers use `_child_view` instead —
    see the note there on why resolving all the way down was the wrong answer above the
    finest tier.

    Built in tier order so each tier reuses the tier below's resolution. For jobs the
    base resolution goes one step further and expands a dedupe group back to every
    source record's title, since that is the level the user recognises; for skills and
    tasks the base record is already the thing itself.
    """
    out: dict[str, list[str]] = {}
    if entity == "job":
        titles = {r.id: (r.job_title or r.id) for r in state.raw_records}
        groups = {g.group_id: g.member_ids for g in state.dedupe_groups}
        for p in state.normalized_profiles:
            out[p.id] = [titles.get(m, m) for m in groups.get(p.id, [p.id])]
    else:
        records = state.skills.inferred if entity == "skill" else state.tasks.inferred
        for r in records:
            out[r.id] = [r.name]

    tiers = tier_state.tiers_of(state, entity)
    for t in tier_engine.TIERS:
        rec = tiers.get(t)
        if rec is None:
            break  # nothing above an unconfirmed tier can resolve either
        for m in rec.members:
            out.setdefault(f"{t}:{m.final_cluster_id}", []).extend(out.get(m.item_id, []))
    return out


def _title_sample(titles: list[str]) -> dict:
    """A readable sample of a cluster's job titles, plus how much was left out.

    Duplicates are collapsed with a multiplier rather than listed: a dedupe group of
    18 identically-titled branch roles is one line reading "... ×18", where printing
    it 18 times would fill the whole tooltip and say less.
    """
    counts: dict[str, int] = {}
    for t in titles:
        counts[t] = counts.get(t, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = ordered[:TOOLTIP_TITLES]
    return {
        "titles": [f"{t} ×{n}" if n > 1 else t for t, n in shown],
        "title_count": len(titles),          # every source record, for the heading
        "titles_omitted": len(ordered) - len(shown),
    }


def _child_view(
    state: ProjectState, entity: str, tier: str
) -> tuple[dict[str, str], dict[str, int], str] | None:
    """What each item being clustered is called, and how much sits beneath it.

    Above the finest tier the items being clustered *are* clusters — grouping categories
    into families means the items are `category:57` — so the honest answer to "what is in
    this family?" is the names of its categories, not several hundred source job titles
    resolved from the bottom.

    This reverses an earlier decision here, which resolved every tier down to source
    titles on the grounds that "a family called Finance containing three categories tells
    you nothing". That held when a family had three categories; with 90 categories under
    14 families it does not. Three hundred raw job titles is not a description of a
    family, and the titles that fit in a tooltip are an arbitrary twelve of them. The
    category names, each carrying the count of profiles beneath it, are.

    Returns (label per item id, count beneath per item id, noun for that count), or None
    at the finest tier, where there is no child tier to read and the source titles are
    the right answer.
    """
    i = tier_state.ORDER.index(tier)
    if i == 0:
        return None
    child = tier_state.ORDER[i - 1]
    crec = tier_state.tiers_of(state, entity).get(child)
    if crec is None or not crec.names:
        return None  # the tier below is unconfirmed, so nothing above it can be previewed

    # The child tier's own members are the level below that, so counting them per child
    # cluster gives exactly "profiles beneath this category" for a family tile.
    beneath: dict[int, int] = {}
    for m in crec.members:
        beneath[m.final_cluster_id] = beneath.get(m.final_cluster_id, 0) + 1

    labels = {f"{child}:{cid}": name for cid, name in crec.names.items()}
    counts = {f"{child}:{cid}": beneath.get(cid, 0) for cid in crec.names}
    return labels, counts, tier_state.tier_noun(entity, child)


def _child_sample(children: list[tuple[str, int]], noun: str) -> dict:
    """A readable sample of a coarse cluster's child clusters.

    Ordered biggest-first rather than alphabetically: when the list is capped, the
    categories that define the family are the ones that should survive the cap.
    """
    ordered = sorted(children, key=lambda kv: (-kv[1], kv[0]))
    shown = ordered[:TOOLTIP_TITLES]
    return {
        "titles": [f"{name} · {n} {noun}" if n else name for name, n in shown],
        "title_count": len(ordered),
        "titles_omitted": len(ordered) - len(shown),
    }


def _titles_by_cluster(
    items: tier_engine.TierItems, labels: np.ndarray, k: int, state: ProjectState, entity: str,
    tier: str,
) -> list[dict]:
    """Per-cluster tooltip samples, aligned with the size array.

    Child cluster names above the finest tier, source titles at it.
    """
    child = _child_view(state, entity, tier)
    if child is not None:
        names, counts, noun = child
        kids: list[list[tuple[str, int]]] = [[] for _ in range(k)]
        for i, item_id in enumerate(items.ids):
            kids[int(labels[i])].append((names.get(item_id, item_id), counts.get(item_id, 0)))
        return [_child_sample(b, noun) for b in kids]

    resolved = _title_map(state, entity)
    buckets: list[list[str]] = [[] for _ in range(k)]
    for i, item_id in enumerate(items.ids):
        buckets[int(labels[i])].extend(resolved.get(item_id, [item_id]))
    return [_title_sample(b) for b in buckets]


def _size_stats(sizes: list[int]) -> dict:
    """Spread of cluster sizes.

    Mean and largest alone hide the shape: 40 clusters averaging 4 members reads the
    same whether every cluster has 4 or one has 90 and the rest have 1. The quartiles
    are what make an unbalanced cut visible before it is confirmed.
    """
    if not sizes:
        return {}
    arr = np.asarray(sizes, dtype=float)
    return {
        "smallest": int(arr.min()),
        "size_p25": round(float(np.percentile(arr, 25)), 1),
        "size_median": round(float(np.percentile(arr, 50)), 1),
        "size_p75": round(float(np.percentile(arr, 75)), 1),
        "size_mean": round(float(arr.mean()), 1),
    }


def _load(client_slug: str, project_slug: str) -> tuple[ProjectService, ProjectState]:
    svc = ProjectService()
    try:
        return svc, svc.load_state(client_slug, project_slug)
    except LookupError as e:
        raise HTTPException(404, f"project not found: {client_slug}/{project_slug}") from e


def _check(entity: str, tier: str) -> tier_state.EntitySpec:
    if tier not in tier_engine.TIERS:
        raise HTTPException(422, f"unknown tier {tier!r}; expected one of {list(tier_engine.TIERS)}")
    try:
        return tier_state.spec(entity)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


def _linkage_name(entity: str, tier: str) -> str:
    return f"tier_{entity}_{tier}_linkage"


def _has_persisted_tree(
    svc: ProjectService, state: ProjectState, entity: str, tier: str
) -> bool:
    """Whether a saved tree exists that still matches what this tier would cluster.

    Checks the stored id list against the ids the tier would build *now*, without
    downloading the tree itself — an existence check that ignored the ids would report
    "built" for a tree that is about to be rejected as stale, which is a worse lie than
    reporting not-built.
    """
    name = _linkage_name(entity, tier)
    client, project = state.meta.client_slug, state.meta.project_slug
    stored = svc.load_index(client, f"{project}/artifacts/{name}_index.json")
    if not stored:
        return False
    try:
        return stored == tier_state.expected_item_ids(svc, state, entity, tier)
    except tier_state.TierNotReady:
        return False


def _load_cached_tree(
    svc: ProjectService, client: str, project: str, entity: str, tier: str, ids: list[str]
) -> np.ndarray | None:
    """A previously saved Ward tree, if it was built from exactly these items.

    The guard is the point. A linkage tree encodes row positions, so one built from a
    different set — or the same set in a different order — would cut clusters over the
    wrong rows and produce a plausible, wrong hierarchy. The id list is stored beside the
    tree and compared in full; a mismatch means recompute, not adapt.
    """
    name = _linkage_name(entity, tier)
    stored_ids = svc.load_index(client, f"{project}/artifacts/{name}_index.json")
    if stored_ids is None or stored_ids != ids:
        return None
    tree = svc.load_array(client, f"{project}/artifacts/{name}.npy")
    # A tree over n items has n-1 merge rows. Cheap to check and it catches a truncated
    # or half-written blob, which would otherwise fail much later inside fcluster.
    if tree is None or tree.ndim != 2 or tree.shape[0] != len(ids) - 1:
        return None
    return tree


def _items_and_tree(
    svc: ProjectService, state: ProjectState, entity: str, tier: str
) -> tuple[tier_engine.TierItems, np.ndarray]:
    client, project = state.meta.client_slug, state.meta.project_slug
    key = (client, project, entity, tier)
    if key in _TIER_CACHE:
        return _TIER_CACHE[key]
    try:
        items = tier_state.build_items(svc, state, entity, tier)
    except tier_state.TierNotReady as e:
        raise HTTPException(409, str(e)) from e
    if len(items) < 3:
        raise HTTPException(
            409,
            f"only {len(items)} items at the {tier} tier — at least 3 are needed to cluster. "
            f"Choose more clusters at the tier below.",
        )

    tree = _load_cached_tree(svc, client, project, entity, tier, items.ids)
    if tree is None:
        tree = bb.build_linkage_tree(items.embeddings)
        name = _linkage_name(entity, tier)
        svc.save_array(client, project, name, tree)
        svc.save_index(client, project, name, items.ids)
    _TIER_CACHE[key] = (items, tree)
    return items, tree


def _start_job(client_slug: str, project_slug: str, stage: str, work) -> dict:
    registry = get_registry()
    try:
        job = registry.create(client_slug, project_slug, stage)
    except JobAlreadyRunning as e:
        raise HTTPException(
            409, {"message": str(e), "existing_job_id": e.job.job_id, "existing_stage": e.job.stage}
        ) from e
    asyncio.create_task(run_job(job, work))
    return {"job_id": job.job_id, "stage": stage, "websocket_url": f"/ws/pipeline/{job.job_id}"}


# The wizard step each (entity, tier) maps onto, for progress routing. Skills and
# tasks keep one wizard step each, with the three tier panels inside it, so all three
# of their tiers report under the same stage.
_JOB_STAGE = {"profile": "cluster", "category": "categories", "family": "families"}


# The lineage key for an (entity, tier) pair. Mirrors the stage map above; kept next to
# it so the two cannot drift apart unnoticed.
def _lineage_step(entity: str, tier: str) -> str:
    return f"{entity}:{tier}"


def _stage(entity: str, tier: str) -> str:
    return _JOB_STAGE[tier] if entity == "job" else f"{entity}s"


def _expected_item_count(state: ProjectState, entity: str, tier: str) -> int | None:
    """How many items this tier will cluster, answerable before anything is built.

    Derived from state alone, without loading a single vector. It used to call
    `build_items` first and fall back to this, which meant every status response
    fetched an embedding array (and centroids) from blob storage. That is fine for
    one tier and not fine for nine: the wizard asks for all of them on every
    refresh, and the whole step list rendered as locked for several seconds while
    they came back.

    Both answers here are exact rather than approximations: the finest tier clusters
    its base records, and a coarser tier clusters exactly the k clusters the tier
    below confirmed.
    """
    if not tier_state._ready(state, entity, tier):
        return None
    if tier == "profile":
        return len(tier_state.base_items(state, entity)) or None
    below = tier_state.CHILD_OF[tier]
    rec = tier_state.tiers_of(state, entity).get(below)
    return rec.k if rec else None


@router.get("/status")
def tier_status(client_slug: str, project_slug: str, entity: str, tier: str) -> dict:
    """What this tier can do right now, without building anything."""
    _check(entity, tier)
    _, state = _load(client_slug, project_slug)
    return _status_payload(client_slug, project_slug, state, entity, tier)


@summary_router.get("/tiers/status")
def all_tier_status(client_slug: str, project_slug: str) -> dict:
    """Every tier of every hierarchy, from ONE state read.

    The wizard needs all nine at once — a tier only becomes runnable when the one
    below it is confirmed, so the whole step list depends on the whole set. Asking
    per tier meant nine requests each re-reading a project state blob that is ~9MB
    on a real client, on every refresh after every job. The step list rendered as
    locked for the seconds that took.
    """
    _, state = _load(client_slug, project_slug)
    return {
        entity: {
            tier: _status_payload(client_slug, project_slug, state, entity, tier)
            for tier in tier_engine.TIERS
        }
        for entity in tier_state.ENTITIES
    }


def _status_payload(
    client_slug: str, project_slug: str, state: ProjectState, entity: str, tier: str
) -> dict:
    es = tier_state.spec(entity)
    key = (client_slug, project_slug, entity, tier)
    # Cheap to construct — the blob client is process-wide — and only used for the small
    # index blob that says whether the persisted tree still matches.
    svc = ProjectService()
    rec = tier_state.tiers_of(state, entity).get(tier)

    n_items = _expected_item_count(state, entity, tier)

    return {
        "entity": entity,
        "tier": tier,
        "title": tier_state.tier_title(entity, tier),
        "clusters": tier_engine.TIERS,
        "ready_to_run": tier_state._ready(state, entity, tier),
        "below": tier_state.previous_tier(tier),
        "below_title": (
            tier_state.tier_title(entity, tier_state.previous_tier(tier))
            if tier_state.previous_tier(tier)
            else None
        ),
        "item_count": n_items,
        "item_noun": tier_state.tier_noun(entity, tier),
        "label_noun": _label_noun(entity, tier),
        "embeds": tier == "profile",
        "embedding_entity": es.embeddings_entity,
        # In memory, or recoverable from the persisted tree without recomputing Ward.
        # This used to be process memory alone, so after any restart a confirmed tier
        # demanded a full rebuild — 21-23MB of vectors plus a Ward recompute over 5,193
        # items — before its controls would even appear. That is why re-clustering read as
        # unavailable rather than slow.
        "built": key in _TIER_CACHE or _has_persisted_tree(svc, state, entity, tier),
        "analysed_k": _ANALYSIS_CACHE[key].k if key in _ANALYSIS_CACHE else None,
        "stability_inline": (n_items or 0) <= INLINE_STABILITY_LIMIT,
        "confirmed": bool(rec and rec.names),
        "k": rec.k if rec else None,
        "gate": rec.gate if rec else None,
        "n_routed": rec.n_routed if rec else 0,
        "n_moved": rec.n_moved if rec else 0,
        "max_k": (n_items - 1) if n_items else None,
    }


@router.post("/build")
async def tier_build(
    client_slug: str,
    project_slug: str,
    entity: str,
    tier: str,
    device: str | None = None,
    embedding_model: str | None = None,
) -> dict:
    """Get this tier ready to cluster: embed if it needs embedding, then build its
    Ward tree so previews are instant.

    The finest tier embeds the entity's base records; the coarser tiers reuse
    centroids from the tier below and never embed. This is one job on purpose —
    embedding used to be a separate endpoint the client called first, and since the
    registry allows one job per project the second call always lost with a 409
    while the embed ran on regardless, unattached and invisible.
    """
    es = _check(entity, tier)
    svc, state = _load(client_slug, project_slug)

    if tier == "profile" and len(tier_state.base_items(state, entity)) < 3:
        raise HTTPException(
            400,
            f"need at least 3 {tier_state.tier_noun(entity, 'profile')} to cluster — "
            f"run the step that produces them first",
        )

    try:
        model_spec = embeddings.resolve_model(es.embeddings_entity, embedding_model)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    def work(reporter: ProgressReporter) -> dict:
        if tier == "profile":
            _embed_base(svc, state, entity, reporter, model_spec.name, device, embedding_model)
        reporter.message(f"Grouping into {tier_state.tier_title(entity, tier).lower()}")
        items, _ = _items_and_tree(svc, state, entity, tier)
        n = len(items)
        summary = {
            "items": n,
            "suggested_k": max(2, min(n - 1, round(n / {"profile": 6, "category": 5, "family": 4}[tier]))),
            "max_k": n - 1,
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, _stage(entity, tier), work)


def _embed_base(
    svc: ProjectService,
    state: ProjectState,
    entity: str,
    reporter: ProgressReporter,
    model_name: str,
    device: str | None,
    embedding_model: str | None,
) -> None:
    """Embed the entity's base records, skipping the work if it is already done with
    the same model.

    Re-embedding 916 jobs is minutes of GPU time, so it is worth not repeating — but
    only when the vectors came from the model now selected. The fingerprint is the
    only thing that can tell, since both job models emit 1024 dimensions.
    """
    client, project = state.meta.client_slug, state.meta.project_slug
    es = tier_state.spec(entity)
    index_path = f"{project}/artifacts/{es.array_name}_index.json"
    want = get_embedding_service().fingerprint(es.embeddings_entity, embedding_model)
    noun = tier_state.tier_noun(entity, "profile")

    pairs = tier_state.base_items(state, entity)
    ids = [i for i, _ in pairs]
    texts = [t for _, t in pairs]

    existing = svc.load_index(client, index_path)
    if (
        existing is not None
        and svc.load_index_fingerprint(client, index_path) == want
        and existing == ids
    ):
        reporter.message(f"Reusing existing {model_name} embeddings for {len(existing)} {noun}")
        return

    svc_emb = get_embedding_service()
    if not embeddings.is_loaded(es.embeddings_entity, embedding_model):
        reporter.message(f"Loading the {model_name} model (first use this session)")
        svc_emb.warm(es.embeddings_entity, device, embedding_model)

    reporter.stage_start(len(texts), f"Embedding {len(texts)} {noun} with {model_name}")
    emb = svc_emb.embed_documents(
        es.embeddings_entity, texts, device=device, model=embedding_model,
        progress=lambda done, total: reporter.progress(done, total, "embedded"),
    )
    svc.save_array(client, project, es.array_name, emb)
    svc.save_index(client, project, es.array_name, ids, model_fingerprint=want)
    # A fresh embedding invalidates any tier tree cached from the old vectors.
    for t in tier_engine.TIERS:
        _TIER_CACHE.pop((client, project, entity, t), None)
        _ANALYSIS_CACHE.pop((client, project, entity, t), None)


@router.get("/preview")
def tier_preview(client_slug: str, project_slug: str, entity: str, tier: str, k: int) -> dict:
    """Cluster sizes at k, plus stability where it is cheap enough to compute now.

    Drives the slider, so it must stay fast. On small tiers that includes the
    stability distribution and the routed-count preview, which makes the gate
    control live in exactly the way the dedupe threshold is.
    """
    es = _check(entity, tier)
    svc, state = _load(client_slug, project_slug)
    items, tree = _items_and_tree(svc, state, entity, tier)

    if not (2 <= k < len(items)):
        raise HTTPException(422, f"k must be between 2 and {len(items) - 1} for {len(items)} items")

    labels = bb.cut_tree(tree, k)
    sizes = np.bincount(labels, minlength=int(labels.max()) + 1).tolist()
    samples = _titles_by_cluster(items, labels, len(sizes), state, entity, tier)
    out: dict = {
        "entity": entity,
        "tier": tier,
        "k": k,
        "item_count": len(items),
        "sizes": sizes,
        "titles": [s["titles"] for s in samples],
        "title_counts": [s["title_count"] for s in samples],
        "titles_omitted": [s["titles_omitted"] for s in samples],
        "singletons": sum(1 for s in sizes if s == 1),
        "largest": max(sizes) if sizes else 0,
        **_size_stats(sizes),
        "stability_included": False,
    }

    if len(items) <= INLINE_STABILITY_LIMIT:
        settings = get_settings()
        analysis = tier_engine.analyse(
            items, k=k, n_perturb=settings.stability_n_perturb,
            subsample_frac=settings.stability_subsample_frac, tree=tree,
        )
        _ANALYSIS_CACHE[(client_slug, project_slug, entity, tier)] = analysis
        out.update(_stability_payload(analysis, settings.stability_gate))
    return out


class AnalyseRequest(BaseModel):
    k: int = Field(ge=2)


@router.post("/analyse")
async def tier_analyse(
    client_slug: str, project_slug: str, entity: str, tier: str, req: AnalyseRequest
) -> dict:
    """The bootstrap stability pass, for tiers too large to do it inline.

    No LLM calls, so it is safe to re-run while exploring cluster counts — it just
    is not fast enough to sit behind a slider at job scale.
    """
    es = _check(entity, tier)
    svc, state = _load(client_slug, project_slug)
    settings = get_settings()

    def work(reporter: ProgressReporter) -> dict:
        # Inside the job, not before it. Assembling the items and building the Ward
        # tree on a cold cache reads arrays from blob and is minutes of work at task
        # scale — and done in the route body it delayed the HTTP response, so the
        # client had no job id, no WebSocket and therefore no heartbeat for exactly
        # as long as the slowest part took.
        reporter.message(f"Preparing the {tier_state.tier_title(entity, tier).lower()}")
        items, tree = _items_and_tree(svc, state, entity, tier)
        if not (2 <= req.k < len(items)):
            raise ValueError(f"k must be between 2 and {len(items) - 1}")
        reporter.stage_start(
            settings.stability_n_perturb,
            f"Assessing stability of {len(items)} items at k={req.k} "
            f"({settings.stability_n_perturb} resamples)",
        )
        analysis = tier_engine.analyse(
            items, k=req.k, n_perturb=settings.stability_n_perturb,
            subsample_frac=settings.stability_subsample_frac, tree=tree,
        )
        _ANALYSIS_CACHE[(client_slug, project_slug, entity, tier)] = analysis
        summary = {"k": req.k, "items": len(items), **_stability_payload(analysis, settings.stability_gate)}
        reporter.stage_complete({k: v for k, v in summary.items() if not isinstance(v, list)})
        return summary

    return _start_job(client_slug, project_slug, _stage(entity, tier), work)


@router.get("/gate")
def tier_gate(client_slug: str, project_slug: str, entity: str, tier: str, gate: float) -> dict:
    """How many items this gate sends to the model — instant, from the cached
    analysis. This is the cost preview that has to exist before the user pays."""
    es = _check(entity, tier)
    key = (client_slug, project_slug, entity, tier)
    analysis = _ANALYSIS_CACHE.get(key)
    if analysis is None:
        raise HTTPException(
            409,
            f"no stability analysis cached for the {tier} tier — run its analyse "
            f"step (or move the cluster slider on a small tier) first",
        )
    return {"entity": entity, "tier": tier, "k": analysis.k, **_stability_payload(analysis, gate)}


def _stability_payload(analysis: tier_engine.TierAnalysis, gate: float) -> dict:
    valid = analysis.stability[~np.isnan(analysis.stability)]
    n_routed = analysis.routed_count(gate)
    total = int(analysis.stability.shape[0])
    return {
        "stability_included": True,
        "gate": gate,
        "n_routed": n_routed,
        "pct_routed": round(100 * n_routed / total, 1) if total else 0.0,
        "mean_stability": round(float(valid.mean()), 3) if valid.size else None,
        "min_stability": round(float(valid.min()), 3) if valid.size else None,
        "distribution": analysis.distribution(),
    }


class ConfirmRequest(BaseModel):
    k: int = Field(ge=2)
    gate: float = Field(ge=0.0, le=1.0)


@router.post("/confirm")
async def tier_confirm(
    client_slug: str,
    project_slug: str,
    entity: str,
    tier: str,
    req: ConfirmRequest,
    workers: int | None = None,
) -> dict:
    """Name the clusters and route the unstable slice. The step that spends."""
    es = _check(entity, tier)
    svc, state = _load(client_slug, project_slug)
    settings = get_settings()
    _workers = llm.resolve_workers(workers)
    model_name = embeddings.resolve_model(es.embeddings_entity).name

    def work(reporter: ProgressReporter) -> dict:
        # See tier_analyse: this has to happen inside the job so the client gets a
        # job id immediately and the heartbeat starts ticking straight away.
        reporter.message(f"Preparing the {tier_state.tier_title(entity, tier).lower()}")
        items, tree = _items_and_tree(svc, state, entity, tier)
        if not (2 <= req.k < len(items)):
            raise ValueError(f"k must be between 2 and {len(items) - 1}")
        key = (client_slug, project_slug, entity, tier)
        analysis = _ANALYSIS_CACHE.get(key)
        if analysis is None or analysis.k != req.k:
            reporter.message(f"Assessing stability at k={req.k}")
            analysis = tier_engine.analyse(
                items, k=req.k, n_perturb=settings.stability_n_perturb,
                subsample_frac=settings.stability_subsample_frac, tree=tree,
            )
            _ANALYSIS_CACHE[key] = analysis

        n_route = analysis.routed_count(req.gate)
        llm.reset_cache_stats()
        # Naming and routing are reported as two phases. They used to share one bar
        # sized to the routed count, so naming — which at 150 clusters is minutes of
        # sequential calls — showed as a bar frozen at zero.
        reporter.stage_start(
            req.k,
            f"Naming {req.k} {tier_state.tier_title(entity, tier).lower()} "
            f"({n_route} uncertain items to re-check after)",
        )
        result = asyncio.run(
            tier_engine.finalise(
                items, analysis,
                entity=entity, tier=tier, gate=req.gate,
                sc_confidence_threshold=settings.self_consistency_conf_threshold,
                sc_votes=settings.self_consistency_votes,
                route_concurrency=_workers,
                progress=reporter.pmap_callback(),
                naming_progress=lambda done, total: reporter.progress(done, total, "named"),
                on_phase=lambda label, total: reporter.stage_start(max(1, total), label),
            )
        )

        fresh = svc.load_state(client_slug, project_slug)
        dropped = [t for t in tier_state.ORDER[tier_state.ORDER.index(tier) + 1 :]
                   if t in tier_state.tiers_of(fresh, entity)]
        # Before writing the new tier: everything downstream describes the old one.
        # `save_tier` drops the coarser tiers inside this hierarchy; this covers the rest
        # of the app, which previously kept profiles, taxonomies, matches, the opportunity
        # assessment, agents and the graph all keyed to clusters that no longer existed.
        invalidated = lineage_routes.cascade(svc, fresh, _lineage_step(entity, tier))
        tier_state.save_tier(svc, fresh, entity, tier, result, embedding_model=model_name)
        svc.save_state(
            fresh,
            action=f"confirm-{entity}-{tier}-tier",
            lineage_payload={
                "entity": entity, "tier": tier, "k": req.k, "gate": req.gate,
                "n_routed": result.n_routed, "n_moved": result.n_moved,
                "tiers_invalidated": dropped,
                "invalidated": [i["step"] for i in invalidated],
            },
        )
        # The tier above now clusters different things, so its cached tree is stale.
        for above in tier_state.ORDER[tier_state.ORDER.index(tier) + 1 :]:
            _TIER_CACHE.pop((client_slug, project_slug, entity, above), None)
            _ANALYSIS_CACHE.pop((client_slug, project_slug, entity, above), None)

        cache = llm.cache_stats()
        summary = {
            "entity": entity, "tier": tier, "k": req.k, "clusters": len(result.names),
            "llm_calls": cache.calls,
            "cache_hit_pct": round(100 * cache.saved_fraction),
            "routed": result.n_routed, "moved_by_model": result.n_moved,
            "low_confidence": result.low_confidence, "multi_home": result.multi_home,
            "tiers_invalidated": len(dropped),
            "invalidated": invalidated,
        }
        if len(result.names) < req.k:
            summary["emptied_by_routing"] = req.k - len(result.names)
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, _stage(entity, tier), work)


@router.get("/clusters")
def tier_clusters(client_slug: str, project_slug: str, entity: str, tier: str) -> dict:
    """The confirmed clusters with their members and audit trail, for review."""
    es = _check(entity, tier)
    _, state = _load(client_slug, project_slug)
    rec = tier_state.tiers_of(state, entity).get(tier)
    if rec is None or not rec.names:
        raise HTTPException(409, f"the {tier} tier has not been confirmed yet")

    label_for = _member_labeller(state, entity, tier)
    # Same rule as the preview tiles: child cluster names above the finest tier, source
    # titles at it. Built here from the confirmed membership rather than a label array.
    child = _child_view(state, entity, tier)
    sample_for: dict[int, dict] = {}
    if child is not None:
        names, counts, noun = child
        kids: dict[int, list[tuple[str, int]]] = {}
        for m in rec.members:
            kids.setdefault(m.final_cluster_id, []).append(
                (names.get(m.item_id, m.item_id), counts.get(m.item_id, 0))
            )
        sample_for = {cid: _child_sample(v, noun) for cid, v in kids.items()}
    else:
        resolved = _title_map(state, entity)
        titles_for: dict[int, list[str]] = {}
        for m in rec.members:
            titles_for.setdefault(m.final_cluster_id, []).extend(resolved.get(m.item_id, []))
        sample_for = {cid: _title_sample(v) for cid, v in titles_for.items()}
    empty = _child_sample([], "") if child is not None else _title_sample([])

    by_cluster: dict[int, list[dict]] = {}
    for m in rec.members:
        by_cluster.setdefault(m.final_cluster_id, []).append(
            {
                "item_id": m.item_id,
                "label": label_for(m.item_id),
                "stability_score": m.stability_score,
                "routed_by_llm": m.routed_by_llm,
                "route_confidence": m.route_confidence,
                "moved": m.routed_by_llm and m.backbone_cluster_id != m.final_cluster_id,
                "moved_from": rec.names.get(m.backbone_cluster_id)
                if m.backbone_cluster_id != m.final_cluster_id
                else None,
                "moved_by_user": m.moved_by_user,
            }
        )
    clusters = [
        {
            "id": cid,
            "name": rec.names.get(cid, "?"),
            "members": sorted(by_cluster.get(cid, []), key=lambda x: x["label"]),
            "size": len(by_cluster.get(cid, [])),
            # For the hover tooltip: at the profile tier the member's dedupe group
            # expanded to source titles, above it the child clusters by name.
            **sample_for.get(cid, empty),
        }
        for cid in sorted(rec.names)
    ]
    clusters.sort(key=lambda c: -c["size"])
    sizes = [c["size"] for c in clusters]
    return {
        "entity": entity, "tier": tier, "k": rec.k, "gate": rec.gate,
        # Every cluster id and name at this tier, so the UI can offer somewhere to
        # move a member to without refetching or reconstructing the list.
        "cluster_options": [{"id": cid, "name": rec.names[cid]} for cid in sorted(rec.names)],
        "n_routed": rec.n_routed, "n_moved": rec.n_moved,
        "clusters": clusters,
        **_size_stats(sizes),
        "singletons": sum(1 for s in sizes if s == 1),
        "largest": max(sizes) if sizes else 0,
    }


def _member_details(state: ProjectState, entity: str, item_ids: list[str]) -> list[dict]:
    """One row per clustered item, carrying the text that actually separated them.

    This exists because collapsing identical names hid the answer to the most common
    question the clustering UI provokes. Four tasks inferred from four different jobs are
    all called "Administrative Reporting"; the modal showed a single row reading
    "Administrative Reporting ×4" and the tiles either side of it looked like duplicates.
    They are not — their embeddings sit 0.22-0.42 cosine apart, because what is embedded
    is `name. description` and the description is twenty words against the name's two.
    One is about network projects, one about installation faults, one about project
    governance, one about analysis progress. Showing the description makes that legible
    instead of leaving it as an apparent bug in the clustering.

    So identical names are deliberately *not* merged here — the whole point is that four
    rows with the same name and four different descriptions is the honest picture. The
    hover tooltip still collapses them, because it has no room for prose and now says to
    click through.

    For jobs the row is the normalised profile rather than the source record: several
    source titles can sit behind one profile after dedupe, and the purpose statement
    belongs to the profile, so repeating it per title would be noise.
    """
    if entity == "job":
        titles = {r.id: (r.job_title or r.id) for r in state.raw_records}
        groups = {g.group_id: g.member_ids for g in state.dedupe_groups}
        purpose = {p.id: p.purpose_statement for p in state.normalized_profiles}
        tasks = {p.id: p.key_tasks for p in state.normalized_profiles}
        rows: list[dict] = []
        for item_id in item_ids:
            members = groups.get(item_id, [item_id])
            names = [titles.get(m, m) for m in members]
            rows.append(
                {
                    "label": names[0],
                    # The other titles that deduped into this profile, named rather than
                    # counted — "(+3)" tells you less than knowing which three.
                    "also": names[1:],
                    "count": len(members),
                    "description": purpose.get(item_id, ""),
                    "points": tasks.get(item_id, []),
                }
            )
        return sorted(rows, key=lambda r: (-r["count"], r["label"]))

    records = state.skills.inferred if entity == "skill" else state.tasks.inferred
    by_id = {r.id: r for r in records}
    rows = []
    for item_id in item_ids:
        rec = by_id.get(item_id)
        rows.append(
            {
                "label": rec.name if rec else item_id,
                "also": [],
                "count": 1,
                "description": rec.description if rec else "",
                "points": [],
            }
        )
    # Grouped by name so the identically-named ones sit together and read as a set of
    # variations rather than being scattered through the list.
    return sorted(rows, key=lambda r: (r["label"].casefold(), r["description"]))


def _member_labeller(state: ProjectState, entity: str, tier: str):
    """Human-readable name for a tier member — the base record's own name at the
    finest tier, the confirmed cluster name at the coarser tiers."""
    if tier == "profile":
        if entity == "job":
            titles = {r.id: r.job_title for r in state.raw_records}
            groups = {g.group_id: g.member_ids for g in state.dedupe_groups}

            def label(item_id: str) -> str:
                members = groups.get(item_id, [item_id])
                names = [titles.get(m, m) for m in members]
                return names[0] + (f" (+{len(names) - 1})" if len(names) > 1 else "")

            return label

        records = state.skills.inferred if entity == "skill" else state.tasks.inferred
        by_id = {r.id: r.name for r in records}
        return lambda item_id: by_id.get(item_id, item_id)

    below = tier_state.CHILD_OF[tier]
    tiers = tier_state.tiers_of(state, entity)
    names = tiers[below].names if below in tiers else {}

    def label(item_id: str) -> str:
        try:
            return names.get(int(item_id.split(":")[1]), item_id)
        except (IndexError, ValueError):
            return item_id

    return label


@router.get("/cluster-members")
def tier_cluster_members(
    client_slug: str, project_slug: str, entity: str, tier: str, k: int, cluster: int
) -> dict:
    """Everything inside one cluster of an unconfirmed cut, in full.

    The preview caps each cluster's sample at TOOLTIP_TITLES so a slider drag stays
    cheap. That is right for a hover, and wrong for the moment the user stops to
    actually read a group — hence a separate call, made only when one is opened.
    """
    _check(entity, tier)
    svc, state = _load(client_slug, project_slug)
    items, tree = _items_and_tree(svc, state, entity, tier)
    if not (2 <= k < len(items)):
        raise HTTPException(422, f"k must be between 2 and {len(items) - 1}")

    labels = bb.cut_tree(tree, k)
    if not (0 <= cluster <= int(labels.max())):
        raise HTTPException(404, f"no cluster {cluster} at k={k}")

    member_ids = [items.ids[i] for i in range(len(items)) if int(labels[i]) == cluster]

    # Same rule as the tile it opens from. Listing 300 source titles under a heading that
    # says "7 job categories" would contradict the tooltip that led here, and the count
    # column is more useful holding "profiles in this category" than a repeat multiplier.
    child = _child_view(state, entity, tier)
    if child is not None:
        child_names, child_counts, beneath_noun = child
        members = [
            {"label": child_names.get(i, i), "count": child_counts.get(i, 0), "also": [],
             "description": "", "points": []}
            for i in member_ids
        ]
        members.sort(key=lambda r: (-r["count"], r["label"]))
        # The rows are child clusters; the total counts what sits beneath them, which is a
        # different unit. Naming both stops the subtitle reading "3 job categories · 8 job
        # categories" when the 8 are profiles.
        total_noun = beneath_noun
    else:
        # The finest tier carries each item's own description — the thing that decided
        # which cluster it landed in, and the only way to tell identically-named items
        # apart. See _member_details.
        members = _member_details(state, entity, member_ids)
        total_noun = _label_noun(entity, tier)
    return {
        "entity": entity,
        "tier": tier,
        "k": k,
        "cluster": cluster,
        "size": len(member_ids),
        "label_noun": _label_noun(entity, tier),
        "members": members,
        "total": sum(r["count"] for r in members),
        "total_noun": total_noun,
    }


class ReassignRequest(BaseModel):
    item_id: str
    cluster_id: int


@router.post("/reassign")
def tier_reassign(
    client_slug: str, project_slug: str, entity: str, tier: str, req: ReassignRequest
) -> dict:
    """Move one member to a different cluster in this tier.

    The geometry and the model both get a say in placement, and both are sometimes
    wrong in ways only a person reading the group can see — so the last word has to
    be manual. This is a label change, not a re-clustering: nothing is re-embedded,
    no tier above is invalidated, and the audit trail keeps what the backbone and
    the router each said so the override stays visible rather than silently
    rewriting history.
    """
    _check(entity, tier)
    svc, state = _load(client_slug, project_slug)
    rec = tier_state.tiers_of(state, entity).get(tier)
    if rec is None or not rec.names:
        raise HTTPException(409, f"the {tier} tier has not been confirmed yet")
    if req.cluster_id not in rec.names:
        raise HTTPException(404, f"no cluster {req.cluster_id} in the {tier} tier")

    member = next((m for m in rec.members if m.item_id == req.item_id), None)
    if member is None:
        raise HTTPException(404, f"no member {req.item_id!r} in the {tier} tier")

    was = member.final_cluster_id
    if was == req.cluster_id:
        return {"moved": False, "cluster_id": was, "emptied": None}
    member.final_cluster_id = req.cluster_id
    member.moved_by_user = True

    # Moving the last member out of a cluster leaves a name describing nothing, the
    # same end state routing can produce — so it is dropped the same way.
    emptied = None
    if not any(m.final_cluster_id == was for m in rec.members):
        emptied = rec.names.pop(was, None)
        rec.k = len(rec.names)

    tier_state.rebuild_denormalised(state, entity)
    svc.save_state(
        state,
        action=f"reassign-{entity}-{tier}-member",
        lineage_payload={
            "entity": entity, "tier": tier, "item_id": req.item_id,
            "from_cluster": was, "to_cluster": req.cluster_id,
            "emptied_cluster": emptied,
        },
    )
    return {
        "moved": True,
        "from_cluster": was,
        "cluster_id": req.cluster_id,
        "emptied": emptied,
    }


class RenameRequest(BaseModel):
    cluster_id: int
    name: str = Field(min_length=1)


@router.post("/rename")
def tier_rename(
    client_slug: str, project_slug: str, entity: str, tier: str, req: RenameRequest
) -> dict:
    """Edit a cluster name. Kept per tier so a correction does not require
    re-running anything — it is a label, not a placement."""
    es = _check(entity, tier)
    svc, state = _load(client_slug, project_slug)
    rec = tier_state.tiers_of(state, entity).get(tier)
    if rec is None or req.cluster_id not in rec.names:
        raise HTTPException(404, f"no cluster {req.cluster_id} in the {tier} tier")
    rec.names[req.cluster_id] = req.name.strip()
    tier_state.rebuild_denormalised(state, entity)
    svc.save_state(
        state,
        action=f"rename-{entity}-{tier}-cluster",
        lineage_payload={
            "entity": entity, "tier": tier, "cluster_id": req.cluster_id, "name": req.name
        },
    )
    return {
        "entity": entity, "tier": tier,
        "cluster_id": req.cluster_id, "name": rec.names[req.cluster_id],
    }


# `_invalidate_downstream` lived here and dropped skill proficiency when the skill
# taxonomy was re-cut. It is gone: proficiency is now a declared descendant of
# skill:family in services/lineage.py, so it is invalidated by the same walk that handles
# everything else, and the special case cannot fall out of step with the general rule.
