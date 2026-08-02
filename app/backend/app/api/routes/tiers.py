"""Per-tier clustering routes — instructions.txt steps 5/6, split by tier.

One set of endpoints parameterised by tier, so the profile, category and family
steps are the same code and the same UI three times over.

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
from app.services import embeddings, llm, normalization
from app.services.clustering import backbone as bb
from app.services.clustering import tier as tier_engine
from app.services.clustering import tier_state
from app.services.embeddings import get_embedding_service
from app.services.orchestrator import JobAlreadyRunning, ProgressReporter, get_registry, run_job
from app.services.project_service import ProjectService

router = APIRouter(
    prefix="/api/projects/{client_slug}/{project_slug}/cluster/tier/{tier}", tags=["clustering"]
)

# Above this many items the bootstrap is too slow to run on every slider move
# (measured: ~0.13s for 120 items, ~6.7s for 916 at 50 perturbations), so those
# tiers get an explicit analyse step instead. Categories and families sit well
# under it, which is why only the profile tier needs the extra press.
INLINE_STABILITY_LIMIT = 300

# Per (client, project, tier): the built tree and items, and the last analysis.
# Rebuilding either is deterministic and cheap relative to an LLM call, so a cold
# cache costs a rebuild rather than correctness.
_TIER_CACHE: dict[tuple[str, str, str], tuple[tier_engine.TierItems, np.ndarray]] = {}
_ANALYSIS_CACHE: dict[tuple[str, str, str], tier_engine.TierAnalysis] = {}


# How many job titles travel with each cluster for the hover tooltip. Enough to
# recognise what a cluster is; not the whole membership, which at the family tier
# would be hundreds of titles per cluster on every slider move.
TOOLTIP_TITLES = 12


def _title_map(state: ProjectState) -> dict[str, list[str]]:
    """Source job titles beneath every item id, at every tier.

    Hovering a cluster has to answer "which real jobs are actually in here?", and at
    the category and family tiers that means resolving all the way down rather than
    listing the child clusters' names — a family called "Finance" containing three
    categories tells you nothing you did not already know.

    Built in tier order so each tier can reuse the tier below's resolution: profile
    items resolve through their dedupe group to raw record titles, and each coarser
    tier concatenates whatever its children resolved to.
    """
    titles = {r.id: (r.job_title or r.id) for r in state.raw_records}
    groups = {g.group_id: g.member_ids for g in state.dedupe_groups}

    out: dict[str, list[str]] = {}
    for p in state.normalized_profiles:
        members = groups.get(p.id, [p.id])
        out[p.id] = [titles.get(m, m) for m in members]

    for t in tier_engine.TIERS:
        rec = state.clustering_tiers.get(t)
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


def _titles_by_cluster(
    items: tier_engine.TierItems, labels: np.ndarray, k: int, state: ProjectState
) -> list[dict]:
    """Per-cluster title samples, aligned with the size array."""
    resolved = _title_map(state)
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


def _check_tier(tier: str) -> None:
    if tier not in tier_engine.TIERS:
        raise HTTPException(422, f"unknown tier {tier!r}; expected one of {list(tier_engine.TIERS)}")


def _items_and_tree(
    svc: ProjectService, state: ProjectState, tier: str
) -> tuple[tier_engine.TierItems, np.ndarray]:
    key = (state.meta.client_slug, state.meta.project_slug, tier)
    if key in _TIER_CACHE:
        return _TIER_CACHE[key]
    try:
        items = tier_state.build_items(svc, state, tier)
    except tier_state.TierNotReady as e:
        raise HTTPException(409, str(e)) from e
    if len(items) < 3:
        raise HTTPException(
            409,
            f"only {len(items)} items at the {tier} tier — at least 3 are needed to cluster. "
            f"Choose more clusters at the tier below.",
        )
    tree = bb.build_linkage_tree(items.embeddings)
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


# The wizard step each tier maps onto, for progress routing.
_STAGE = {"profile": "cluster", "category": "categories", "family": "families"}


def _expected_item_count(svc: ProjectService, state: ProjectState, tier: str) -> int | None:
    """How many items this tier will cluster, answerable before anything is built.

    `build_items` needs artifacts that may not exist yet — the profile tier needs
    the normalised jobs embedded first. Returning None then would understate the
    count to zero, which makes `stability_inline` claim the bootstrap is instant
    on a tier where it takes seconds, and leaves the slider with no upper bound.
    Both are knowable from state without loading a single vector.
    """
    if not tier_state._ready(state, tier):
        return None
    try:
        return len(tier_state.build_items(svc, state, tier))
    except tier_state.TierNotReady:
        pass
    if tier == "profile":
        return len(state.normalized_profiles) or None
    below = tier_state.CHILD_OF[tier]
    rec = state.clustering_tiers.get(below)
    return rec.k if rec else None


@router.get("/status")
def tier_status(client_slug: str, project_slug: str, tier: str) -> dict:
    """What this tier can do right now, without building anything."""
    _check_tier(tier)
    svc, state = _load(client_slug, project_slug)
    key = (client_slug, project_slug, tier)
    rec = state.clustering_tiers.get(tier)

    n_items = _expected_item_count(svc, state, tier)

    return {
        "tier": tier,
        "clusters": tier_engine.TIERS,
        "ready_to_run": tier_state._ready(state, tier),
        "below": tier_state.previous_tier(tier),
        "item_count": n_items,
        "item_noun": {"profile": "normalised jobs", "category": "job profiles", "family": "job categories"}[tier],
        "built": key in _TIER_CACHE,
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
    tier: str,
    device: str | None = None,
    embedding_model: str | None = None,
) -> dict:
    """Get this tier ready to cluster: embed if it needs embedding, then build its
    Ward tree so previews are instant.

    The profile tier embeds the normalised job summaries; the coarser tiers reuse
    centroids from the tier below and never embed. This is one job on purpose —
    embedding used to be a separate endpoint the client called first, and since the
    registry allows one job per project the second call always lost with a 409
    while the embed ran on regardless, unattached and invisible.
    """
    _check_tier(tier)
    svc, state = _load(client_slug, project_slug)

    if tier == "profile" and not state.normalized_profiles:
        raise HTTPException(400, "no normalised jobs yet — run the normalise step first")

    try:
        spec = embeddings.resolve_model("job", embedding_model)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    def work(reporter: ProgressReporter) -> dict:
        if tier == "profile":
            _embed_profiles(svc, state, reporter, spec.name, device, embedding_model)
        reporter.message(f"Grouping into {tier} clusters")
        items, _ = _items_and_tree(svc, state, tier)
        n = len(items)
        summary = {
            "items": n,
            "suggested_k": max(2, min(n - 1, round(n / {"profile": 6, "category": 5, "family": 4}[tier]))),
            "max_k": n - 1,
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, _STAGE[tier], work)


def _embed_profiles(
    svc: ProjectService,
    state: ProjectState,
    reporter: ProgressReporter,
    model_name: str,
    device: str | None,
    embedding_model: str | None,
) -> None:
    """Embed the normalised job summaries, skipping the work if it is already done
    with the same model.

    Re-embedding 916 jobs is minutes of GPU time, so it is worth not repeating —
    but only when the vectors came from the model now selected. The fingerprint is
    the only thing that can tell, since both job models emit 1024 dimensions.
    """
    client, project = state.meta.client_slug, state.meta.project_slug
    index_path = f"{project}/artifacts/cluster_embeddings_index.json"
    want = get_embedding_service().fingerprint("job", embedding_model)

    existing = svc.load_index(client, index_path)
    if existing is not None and svc.load_index_fingerprint(client, index_path) == want:
        expected = [p.id for p in state.normalized_profiles]
        if existing == expected:
            reporter.message(f"Reusing existing {model_name} embeddings for {len(existing)} jobs")
            return

    texts, ids = [], []
    for p in state.normalized_profiles:
        ids.append(p.id)
        texts.append(
            normalization.NormalizedResult(
                purpose_statement=p.purpose_statement,
                key_tasks=p.key_tasks,
                management_line=p.management_line,
                budget_responsibility=p.budget_responsibility,
            ).embedding_text()
        )

    svc_emb = get_embedding_service()
    if not embeddings.is_loaded("job", embedding_model):
        reporter.message(f"Loading the {model_name} model (first use this session)")
        svc_emb.warm("job", device, embedding_model)

    reporter.stage_start(len(texts), f"Embedding {len(texts)} normalised job summaries with {model_name}")
    emb = svc_emb.embed_documents(
        "job", texts, device=device, model=embedding_model,
        progress=lambda done, total: reporter.progress(done, total, "embedded"),
    )
    svc.save_array(client, project, "cluster_embeddings", emb)
    svc.save_index(client, project, "cluster_embeddings", ids, model_fingerprint=want)
    # A fresh embedding invalidates any tier tree cached from the old vectors.
    for t in tier_engine.TIERS:
        _TIER_CACHE.pop((client, project, t), None)
        _ANALYSIS_CACHE.pop((client, project, t), None)


@router.get("/preview")
def tier_preview(client_slug: str, project_slug: str, tier: str, k: int) -> dict:
    """Cluster sizes at k, plus stability where it is cheap enough to compute now.

    Drives the slider, so it must stay fast. On small tiers that includes the
    stability distribution and the routed-count preview, which makes the gate
    control live in exactly the way the dedupe threshold is.
    """
    _check_tier(tier)
    svc, state = _load(client_slug, project_slug)
    items, tree = _items_and_tree(svc, state, tier)

    if not (2 <= k < len(items)):
        raise HTTPException(422, f"k must be between 2 and {len(items) - 1} for {len(items)} items")

    labels = bb.cut_tree(tree, k)
    sizes = np.bincount(labels, minlength=int(labels.max()) + 1).tolist()
    samples = _titles_by_cluster(items, labels, len(sizes), state)
    out: dict = {
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
        _ANALYSIS_CACHE[(client_slug, project_slug, tier)] = analysis
        out.update(_stability_payload(analysis, settings.stability_gate))
    return out


class AnalyseRequest(BaseModel):
    k: int = Field(ge=2)


@router.post("/analyse")
async def tier_analyse(client_slug: str, project_slug: str, tier: str, req: AnalyseRequest) -> dict:
    """The bootstrap stability pass, for tiers too large to do it inline.

    No LLM calls, so it is safe to re-run while exploring cluster counts — it just
    is not fast enough to sit behind a slider at job scale.
    """
    _check_tier(tier)
    svc, state = _load(client_slug, project_slug)
    items, tree = _items_and_tree(svc, state, tier)
    if not (2 <= req.k < len(items)):
        raise HTTPException(422, f"k must be between 2 and {len(items) - 1}")

    settings = get_settings()

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(
            settings.stability_n_perturb,
            f"Assessing stability of {len(items)} items at k={req.k} "
            f"({settings.stability_n_perturb} resamples)",
        )
        analysis = tier_engine.analyse(
            items, k=req.k, n_perturb=settings.stability_n_perturb,
            subsample_frac=settings.stability_subsample_frac, tree=tree,
        )
        _ANALYSIS_CACHE[(client_slug, project_slug, tier)] = analysis
        summary = {"k": req.k, "items": len(items), **_stability_payload(analysis, settings.stability_gate)}
        reporter.stage_complete({k: v for k, v in summary.items() if not isinstance(v, list)})
        return summary

    return _start_job(client_slug, project_slug, _STAGE[tier], work)


@router.get("/gate")
def tier_gate(client_slug: str, project_slug: str, tier: str, gate: float) -> dict:
    """How many items this gate sends to the model — instant, from the cached
    analysis. This is the cost preview that has to exist before the user pays."""
    _check_tier(tier)
    key = (client_slug, project_slug, tier)
    analysis = _ANALYSIS_CACHE.get(key)
    if analysis is None:
        raise HTTPException(
            409,
            f"no stability analysis cached for the {tier} tier — run its analyse "
            f"step (or move the cluster slider on a small tier) first",
        )
    return {"tier": tier, "k": analysis.k, **_stability_payload(analysis, gate)}


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
    tier: str,
    req: ConfirmRequest,
    workers: int | None = None,
) -> dict:
    """Name the clusters and route the unstable slice. The step that spends."""
    _check_tier(tier)
    svc, state = _load(client_slug, project_slug)
    items, tree = _items_and_tree(svc, state, tier)
    if not (2 <= req.k < len(items)):
        raise HTTPException(422, f"k must be between 2 and {len(items) - 1}")

    settings = get_settings()
    _workers = llm.resolve_workers(workers)
    model_name = embeddings.resolve_model("job").name

    def work(reporter: ProgressReporter) -> dict:
        key = (client_slug, project_slug, tier)
        analysis = _ANALYSIS_CACHE.get(key)
        if analysis is None or analysis.k != req.k:
            reporter.message(f"Assessing stability at k={req.k}")
            analysis = tier_engine.analyse(
                items, k=req.k, n_perturb=settings.stability_n_perturb,
                subsample_frac=settings.stability_subsample_frac, tree=tree,
            )
            _ANALYSIS_CACHE[key] = analysis

        n_route = analysis.routed_count(req.gate)
        # Naming and routing are reported as two phases. They used to share one bar
        # sized to the routed count, so naming — which at 150 clusters is minutes of
        # sequential calls — showed as a bar frozen at zero.
        reporter.stage_start(
            req.k, f"Naming {req.k} {tier} clusters ({n_route} uncertain items to re-check after)"
        )
        result = asyncio.run(
            tier_engine.finalise(
                items, analysis,
                entity="job", tier=tier, gate=req.gate,
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
                   if t in fresh.clustering_tiers]
        tier_state.save_tier(svc, fresh, tier, result, embedding_model=model_name)
        svc.save_state(
            fresh,
            action=f"confirm-{tier}-tier",
            lineage_payload={
                "tier": tier, "k": req.k, "gate": req.gate,
                "n_routed": result.n_routed, "n_moved": result.n_moved,
                "tiers_invalidated": dropped,
            },
        )
        # The tier above now clusters different things, so its cached tree is stale.
        for above in tier_state.ORDER[tier_state.ORDER.index(tier) + 1 :]:
            _TIER_CACHE.pop((client_slug, project_slug, above), None)
            _ANALYSIS_CACHE.pop((client_slug, project_slug, above), None)

        summary = {
            "tier": tier, "k": req.k, "clusters": len(result.names),
            "routed": result.n_routed, "moved_by_model": result.n_moved,
            "low_confidence": result.low_confidence, "multi_home": result.multi_home,
            "tiers_invalidated": len(dropped),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, _STAGE[tier], work)


@router.get("/clusters")
def tier_clusters(client_slug: str, project_slug: str, tier: str) -> dict:
    """The confirmed clusters with their members and audit trail, for review."""
    _check_tier(tier)
    _, state = _load(client_slug, project_slug)
    rec = state.clustering_tiers.get(tier)
    if rec is None or not rec.names:
        raise HTTPException(409, f"the {tier} tier has not been confirmed yet")

    label_for = _member_labeller(state, tier)
    resolved = _title_map(state)
    titles_for: dict[int, list[str]] = {}
    for m in rec.members:
        titles_for.setdefault(m.final_cluster_id, []).extend(resolved.get(m.item_id, []))

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
                if m.routed_by_llm and m.backbone_cluster_id != m.final_cluster_id
                else None,
            }
        )
    clusters = [
        {
            "id": cid,
            "name": rec.names.get(cid, "?"),
            "members": sorted(by_cluster.get(cid, []), key=lambda x: x["label"]),
            "size": len(by_cluster.get(cid, [])),
            # The underlying source job titles, for the hover tooltip. At the profile
            # tier these expand a member's dedupe group; above it they resolve through
            # every tier below.
            **_title_sample(titles_for.get(cid, [])),
        }
        for cid in sorted(rec.names)
    ]
    clusters.sort(key=lambda c: -c["size"])
    sizes = [c["size"] for c in clusters]
    return {
        "tier": tier, "k": rec.k, "gate": rec.gate,
        "n_routed": rec.n_routed, "n_moved": rec.n_moved,
        "clusters": clusters,
        **_size_stats(sizes),
        "singletons": sum(1 for s in sizes if s == 1),
        "largest": max(sizes) if sizes else 0,
    }


def _member_labeller(state: ProjectState, tier: str):
    """Human-readable name for a tier member — a source job title at the profile
    tier, the confirmed cluster name at the coarser tiers."""
    if tier == "profile":
        titles = {r.id: r.job_title for r in state.raw_records}
        groups = {g.group_id: g.member_ids for g in state.dedupe_groups}

        def label(item_id: str) -> str:
            members = groups.get(item_id, [item_id])
            names = [titles.get(m, m) for m in members]
            return names[0] + (f" (+{len(names) - 1})" if len(names) > 1 else "")

        return label

    below = tier_state.CHILD_OF[tier]
    names = state.clustering_tiers[below].names if below in state.clustering_tiers else {}

    def label(item_id: str) -> str:
        try:
            return names.get(int(item_id.split(":")[1]), item_id)
        except (IndexError, ValueError):
            return item_id

    return label


class RenameRequest(BaseModel):
    cluster_id: int
    name: str = Field(min_length=1)


@router.post("/rename")
def tier_rename(client_slug: str, project_slug: str, tier: str, req: RenameRequest) -> dict:
    """Edit a cluster name. Kept per tier so a correction does not require
    re-running anything — it is a label, not a placement."""
    _check_tier(tier)
    svc, state = _load(client_slug, project_slug)
    rec = state.clustering_tiers.get(tier)
    if rec is None or req.cluster_id not in rec.names:
        raise HTTPException(404, f"no cluster {req.cluster_id} in the {tier} tier")
    rec.names[req.cluster_id] = req.name.strip()
    tier_state.rebuild_denormalised(state)
    svc.save_state(
        state,
        action=f"rename-{tier}-cluster",
        lineage_payload={"tier": tier, "cluster_id": req.cluster_id, "name": req.name},
    )
    return {"tier": tier, "cluster_id": req.cluster_id, "name": rec.names[req.cluster_id]}
