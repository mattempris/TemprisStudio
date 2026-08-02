"""Phase 3 routes — instructions.txt step 10 (tasks).

Same shape as the skills routes, with taskQWEN and the time-proportion analytics
the spec asks for: "Browsable Task taxonommy with intelligence re total time
proportion against task (add headcount analytics where we have headcount".
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.project_state import (
    ClusteringState,
    InferredTaskRecord,
    ItemAssignmentRecord,
    ProjectState,
)
from app.services.clustering import backbone as bb
from app.services.clustering import engine as cluster_engine
from app.services import embeddings
from app.services.embeddings import get_embedding_service
from app.services import llm
from app.services.orchestrator import JobAlreadyRunning, ProgressReporter, get_registry, run_job
from app.services.project_service import ProjectService
from app.services.tasks import inference

router = APIRouter(prefix="/api/projects/{client_slug}/{project_slug}/tasks", tags=["tasks"])


def _load(client_slug: str, project_slug: str) -> tuple[ProjectService, ProjectState]:
    svc = ProjectService()
    try:
        return svc, svc.load_state(client_slug, project_slug)
    except LookupError as e:
        raise HTTPException(404, f"project not found: {client_slug}/{project_slug}") from e


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


class TasksSummary(BaseModel):
    inferred_tasks: int = 0
    profiles_covered: int = 0
    clustered: bool = False
    k_domains: int | None = None
    k_categories: int | None = None
    k_tasks: int | None = None
    named: bool = False
    audit: dict = Field(default_factory=dict)


@router.get("/summary")
def tasks_summary(client_slug: str, project_slug: str) -> TasksSummary:
    _, state = _load(client_slug, project_slug)
    t = state.tasks
    c = t.clustering
    return TasksSummary(
        inferred_tasks=len(t.inferred),
        profiles_covered=len({x.source_profile_key for x in t.inferred}),
        clustered=c is not None,
        k_domains=c.k_families if c else None,
        k_categories=c.k_categories if c else None,
        k_tasks=c.k_profiles if c else None,
        named=bool(c and c.profile_names),
        audit=t.audit,
    )


class InferRequest(BaseModel):
    profile_keys: list[str] | None = None


@router.post("/infer")
async def infer_tasks(
    client_slug: str, project_slug: str, req: InferRequest, workers: int | None = None
) -> dict:
    svc, state = _load(client_slug, project_slug)
    _workers = llm.resolve_workers(workers)
    available = [p for p in state.job_profiles if not p.stale]
    if not available:
        raise HTTPException(400, "no job profiles yet — generate job profiles first")
    selected = (
        [p for p in available if p.profile_key in set(req.profile_keys)] if req.profile_keys else available
    )
    if not selected:
        raise HTTPException(400, "none of the requested profile_keys matched a current job profile")

    payload = [(p.profile_key, p.title, p.content) for p in selected]

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(len(payload), f"Inferring tasks for {len(payload)} job profiles")
        results = inference.infer_many(payload, workers=_workers, progress=reporter.pmap_callback())
        flat = [t for tasks, _ in results for t in tasks]
        fixes = [fix for _, fix in results]
        audit = inference.audit_tasks(flat, fixes)

        fresh = svc.load_state(client_slug, project_slug)
        fresh.tasks.inferred = [
            InferredTaskRecord(
                id=f"task-{uuid.uuid4().hex[:8]}",
                name=t.name,
                description=t.description,
                proportion=t.proportion,
                source_profile_key=t.source_profile_key,
            )
            for t in flat
        ]
        fresh.tasks.audit = audit.summary()
        fresh.tasks.clustering = None  # re-inferring invalidates the old taxonomy
        svc.save_state(
            fresh,
            action="infer-tasks",
            lineage_payload={"profiles": len(payload), "tasks": len(flat), "audit": audit.summary()},
        )

        summary = {
            "tasks": len(flat),
            "profiles": len(payload),
            "mean_per_profile": round(len(flat) / max(1, len(payload)), 1),
            **audit.summary(),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "tasks", work)


@router.get("")
def list_tasks(client_slug: str, project_slug: str) -> dict:
    _, state = _load(client_slug, project_slug)
    by_profile: dict[str, list[dict]] = {}
    for t in state.tasks.inferred:
        by_profile.setdefault(t.source_profile_key, []).append(t.model_dump())
    return {
        "by_profile": {
            k: {"tasks": v, "proportion_total": round(sum(x["proportion"] for x in v), 2)}
            for k, v in by_profile.items()
        },
        "audit": state.tasks.audit,
    }


_TASK_TREE_CACHE: dict[tuple[str, str], tuple] = {}


@router.post("/cluster/build")
async def build_task_tree(
    client_slug: str, project_slug: str, device: str | None = None
) -> dict:
    svc, state = _load(client_slug, project_slug)
    tasks = state.tasks.inferred
    if len(tasks) < 3:
        raise HTTPException(400, "need at least 3 inferred tasks to cluster")

    texts = [f"{t.name}. {t.description}" for t in tasks]
    ids = [t.id for t in tasks]

    def work(reporter: ProgressReporter) -> dict:
        if not embeddings.is_loaded("task"):
            reporter.message("Loading the taskQWEN model (first use this session)")
            get_embedding_service().warm("task", device)
        reporter.stage_start(len(texts), f"Embedding {len(texts)} tasks with taskQWEN")
        emb = get_embedding_service().embed_documents(
            "task", texts, device=device,
            progress=lambda done, total: reporter.progress(done, total, "embedded")
        )
        reporter.message("Building the Ward tree")
        tree = bb.build_linkage_tree(emb)

        svc.save_array(client_slug, project_slug, "task_embeddings", emb)
        svc.save_array(client_slug, project_slug, "task_linkage", tree)
        svc.save_index(
            client_slug, project_slug, "task_embeddings", ids,
            model_fingerprint=get_embedding_service().fingerprint("task"),
        )
        _TASK_TREE_CACHE[(client_slug, project_slug)] = (tree, emb, ids)

        n = len(ids)
        summary = {
            "tasks": n,
            "suggested_k_domains": max(2, min(8, n // 15 or 2)),
            "suggested_k_categories": max(3, min(20, n // 6 or 3)),
            "suggested_k_tasks": max(4, min(50, n // 3 or 4)),
            "max_k": n - 1,
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "tasks", work)


def _get_task_tree(svc: ProjectService, state: ProjectState):
    client, project = state.meta.client_slug, state.meta.project_slug
    key = (client, project)
    if key in _TASK_TREE_CACHE:
        return _TASK_TREE_CACHE[key]
    index_path = f"{project}/artifacts/task_embeddings_index.json"
    tree = svc.load_array(client, f"{project}/artifacts/task_linkage.npy")
    emb = svc.load_array(client, f"{project}/artifacts/task_embeddings.npy")
    ids = svc.load_index(client, index_path)
    if tree is None or emb is None or ids is None:
        raise HTTPException(409, "task tree not built yet — run tasks/cluster/build first")
    try:
        embeddings.assert_cache_current("task", svc.load_index_fingerprint(client, index_path))
    except embeddings.StaleEmbeddingCache as e:
        raise HTTPException(409, str(e)) from e
    _TASK_TREE_CACHE[key] = (tree, emb, ids)
    return tree, emb, ids


@router.get("/cluster/preview-cut")
def preview_task_cut(
    client_slug: str, project_slug: str, k_domains: int, k_categories: int, k_tasks: int
) -> dict:
    svc, state = _load(client_slug, project_slug)
    tree, emb, ids = _get_task_tree(svc, state)
    try:
        cuts = cluster_engine.cut_three_tiers(
            tree, k_family=k_domains, k_category=k_categories, k_profile=k_tasks
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    def sizes(labels) -> list[int]:
        return np.bincount(labels, minlength=int(labels.max()) + 1).tolist()

    return {
        "k_domains": k_domains,
        "k_categories": k_categories,
        "k_tasks": k_tasks,
        "domain_sizes": sizes(cuts["family"]),
        "category_sizes": sizes(cuts["category"]),
        "task_sizes": sizes(cuts["profile"]),
        "singleton_tasks": int(sum(1 for s in sizes(cuts["profile"]) if s == 1)),
    }


class ConfirmTaskClusterRequest(BaseModel):
    k_domains: int = Field(ge=2)
    k_categories: int = Field(ge=2)
    k_tasks: int = Field(ge=2)
    gate: float = Field(default=0.58, ge=0.0, le=1.0)
    n_perturb: int = Field(default=50, ge=5, le=200)


@router.post("/cluster/confirm")
async def confirm_task_cluster(
    client_slug: str, project_slug: str, req: ConfirmTaskClusterRequest
) -> dict:
    svc, state = _load(client_slug, project_slug)
    tree, emb, ids = _get_task_tree(svc, state)
    if req.k_tasks >= len(ids):
        raise HTTPException(422, f"k_tasks must be < number of tasks ({len(ids)})")

    by_id = {t.id: t for t in state.tasks.inferred}
    item_texts = [f"{by_id[i].name}. {by_id[i].description}" if i in by_id else i for i in ids]

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(4, "Task taxonomy: stability, routing, naming")
        result = asyncio.run(
            cluster_engine.run_clustering_pipeline(
                "task",
                item_texts,
                emb,
                k_family=req.k_domains,
                k_category=req.k_categories,
                k_profile=req.k_tasks,
                gate=req.gate,
                n_perturb=req.n_perturb,
                route_concurrency=8,
                progress=reporter.pmap_callback(),
            )
        )

        fresh = svc.load_state(client_slug, project_slug)
        fresh.tasks.clustering = ClusteringState(
            embedding_model="taskQWEN",
            linkage_blob_path=f"{project_slug}/artifacts/task_linkage.npy",
            embedding_index_blob_path=f"{project_slug}/artifacts/task_embeddings_index.json",
            k_profiles=req.k_tasks,
            k_categories=req.k_categories,
            k_families=req.k_domains,
            gate=req.gate,
            computed_at=datetime.now(timezone.utc),
            profile_names=result.profile_names,
            category_names=result.category_names,
            family_names=result.family_names,
            assignments=[
                ItemAssignmentRecord(
                    item_id=ids[a.item_index],
                    backbone_profile_id=a.backbone_profile_id,
                    backbone_category_id=a.backbone_category_id,
                    backbone_family_id=a.backbone_family_id,
                    final_profile_id=a.final_profile_id,
                    final_category_id=a.final_category_id,
                    final_family_id=a.final_family_id,
                    stability_score=a.stability_score,
                    routed_by_llm=a.routed_by_llm,
                    route_confidence=a.route_confidence,
                    secondary_profile_id=a.secondary_profile_id,
                    secondary_confidence=a.secondary_confidence,
                    self_consistency=a.self_consistency,
                )
                for a in result.assignments
            ],
        )
        svc.save_state(
            fresh,
            action="confirm-task-cluster",
            lineage_payload={
                "k_domains": req.k_domains,
                "k_categories": req.k_categories,
                "k_tasks": req.k_tasks,
                "n_unstable": result.n_unstable,
            },
        )
        summary = {
            "tasks": len(result.assignments),
            "domains": len(result.family_names),
            "categories": len(result.category_names),
            "task_clusters": len(result.profile_names),
            "n_unstable_routed": result.n_unstable,
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "tasks", work)


@router.get("/taxonomy")
def tasks_taxonomy(client_slug: str, project_slug: str) -> dict:
    """Browsable task taxonomy with time-proportion analytics.

    Two different aggregations, and the distinction matters:
      - `proportion_sum` adds raw per-job percentages, which answers "how much of
        a single job holder's time".
      - `fte_equivalent` weights each contribution by the job's headcount, which
        answers "how much of the workforce's total time" — the number a workforce
        planner actually wants. It's only meaningful where headcount was mapped.
    """
    _, state = _load(client_slug, project_slug)
    c = state.tasks.clustering
    if c is None:
        raise HTTPException(409, "tasks not clustered yet")

    task_by_id = {t.id: t for t in state.tasks.inferred}

    # headcount per job profile, rolled up from raw records via dedupe groups
    hc_by_record = {r.id: r.headcount for r in state.raw_records}
    group_members = {g.group_id: g.member_ids for g in state.dedupe_groups}
    profile_headcount: dict[str, int] = {}
    if state.clustering:
        cluster_to_key = {d.profile_cluster_id: d.profile_key for d in state.job_profiles}
        for a in state.clustering.assignments:
            key = cluster_to_key.get(a.final_profile_id)
            if not key:
                continue
            total = sum(
                h for h in (hc_by_record.get(m) for m in group_members.get(a.item_id, [a.item_id])) if h
            )
            if total:
                profile_headcount[key] = profile_headcount.get(key, 0) + total

    tree: dict[int, dict] = {}
    for a in c.assignments:
        t = task_by_id.get(a.item_id)
        if not t:
            continue
        dom = tree.setdefault(
            a.final_family_id,
            {"id": a.final_family_id, "name": c.family_names.get(a.final_family_id, "?"), "categories": {}},
        )
        cat = dom["categories"].setdefault(
            a.final_category_id,
            {"id": a.final_category_id, "name": c.category_names.get(a.final_category_id, "?"), "clusters": {}},
        )
        cl = cat["clusters"].setdefault(
            a.final_profile_id,
            {"id": a.final_profile_id, "name": c.profile_names.get(a.final_profile_id, "?"), "tasks": []},
        )
        cl["tasks"].append(
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "proportion": t.proportion,
                "source_profile_key": t.source_profile_key,
                "headcount": profile_headcount.get(t.source_profile_key),
                "fte_equivalent": round(t.proportion / 100.0 * profile_headcount[t.source_profile_key], 3)
                if t.source_profile_key in profile_headcount
                else None,
                "stability_score": a.stability_score,
                "routed_by_llm": a.routed_by_llm,
            }
        )

    def aggregate(nodes: list[dict]) -> dict:
        prop = round(sum(n["proportion"] for n in nodes), 2)
        ftes = [n["fte_equivalent"] for n in nodes if n["fte_equivalent"] is not None]
        return {
            "task_count": len(nodes),
            "proportion_sum": prop,
            "fte_equivalent": round(sum(ftes), 3) if ftes else None,
            "jobs_contributing": len({n["source_profile_key"] for n in nodes}),
        }

    domains = []
    for dom in tree.values():
        cats = []
        dom_tasks: list[dict] = []
        for cat in dom["categories"].values():
            clusters = []
            cat_tasks: list[dict] = []
            for cl in cat["clusters"].values():
                clusters.append({**cl, **aggregate(cl["tasks"])})
                cat_tasks.extend(cl["tasks"])
            clusters.sort(key=lambda x: -x["proportion_sum"])
            cats.append(
                {**{k: v for k, v in cat.items() if k != "clusters"}, "clusters": clusters, **aggregate(cat_tasks)}
            )
            dom_tasks.extend(cat_tasks)
        cats.sort(key=lambda x: -x["proportion_sum"])
        domains.append(
            {**{k: v for k, v in dom.items() if k != "categories"}, "categories": cats, **aggregate(dom_tasks)}
        )
    domains.sort(key=lambda x: -x["proportion_sum"])

    return {
        "domains": domains,
        "has_headcount": bool(profile_headcount),
        "total_proportion": round(sum(d["proportion_sum"] for d in domains), 2),
        "total_fte": round(sum(d["fte_equivalent"] or 0 for d in domains), 3) if profile_headcount else None,
    }
