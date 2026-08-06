"""Phase 3 routes — instructions.txt step 10 (tasks).

Same shape as the skills routes, with the tasks model and the time-proportion analytics
the spec asks for: "Browsable Task taxonommy with intelligence re total time
proportion against task (add headcount analytics where we have headcount".
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.project_state import InferredTaskRecord, ProjectState
from app.services import llm, provenance
from app.services.clustering import tier_state
from app.services.orchestrator import JobAlreadyRunning, ProgressReporter, get_registry, run_job
from app.api.routes import lineage as lineage_routes
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
    # Anchor roles that stand for exactly one uploaded record, and whether the anchor-role tier
    # was confirmed one-to-one. Together these decide which infer buttons the step offers —
    # see `_source_option`.
    source_eligible: int = 0
    anchor_roles: int = 0
    anchor_roles_skipped: bool = False


def _source_option(state) -> dict:
    """Whether the "infer from the uploaded descriptions" path is available, and its scope.

    Two facts, both needed before the UI can decide what to offer:

    `source_eligible` counts anchor roles that stand for exactly one uploaded record. Any at all
    means the second button is worth showing; the count tells the user how much of the run it
    would change, since roles that merge several records fall back to the document either way.

    `anchor_roles_skipped` says the anchor-role tier was confirmed one-to-one. Then every role is
    its own uploaded record and the generated documents are summaries of single descriptions the
    user already considers good — so the document path is not a choice worth offering, and the UI
    hides it rather than presenting two buttons where one is strictly worse.
    """
    eligible, total = provenance.coverage(state)
    return {
        "source_eligible": eligible,
        "anchor_roles": total,
        "anchor_roles_skipped": "cluster" in state.skipped_steps,
    }


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
        **_source_option(state),
    )


class InferRequest(BaseModel):
    profile_keys: list[str] | None = None
    # Read each role's own uploaded job description instead of the document written about it,
    # wherever the role stands for exactly one uploaded record.
    #
    # Opt-in rather than automatic. It was automatic first, and that was wrong: on a project that
    # clustered for real, 307 of 565 anchor roles qualify, so it would silently change what most
    # roles were inferred from with no way to choose otherwise. Which input to trust is the user's
    # judgement — a thin HRIS row is better summarised, a comprehensive profile is not — so the UI
    # offers it as a second button and this is the flag behind it.
    from_source: bool = False


@router.post("/infer")
async def infer_tasks(
    client_slug: str, project_slug: str, req: InferRequest, workers: int | None = None
) -> dict:
    svc, state = _load(client_slug, project_slug)
    _workers = llm.resolve_workers(workers)
    available = [p for p in state.job_profiles if not p.stale]
    if not available:
        raise HTTPException(400, "no anchor role documents yet — generate them first")
    selected = (
        [p for p in available if p.profile_key in set(req.profile_keys)] if req.profile_keys else available
    )
    if not selected:
        raise HTTPException(400, "none of the requested profile_keys matched a current anchor role")

    # Only when asked. Where a role merges several records there is no single source description
    # to pass, so those roles fall back to the document even on this path — the button says how
    # many go each way rather than the run doing it silently.
    sources = provenance.single_source_text(state) if req.from_source else {}
    payload = [
        (p.profile_key, p.title, p.content, sources.get(p.profile_key)) for p in selected
    ]
    n_from_source = sum(1 for row in payload if row[3])

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(len(payload), f"Inferring tasks for {len(payload)} anchor roles")
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
        fresh.tasks.audit = {
            **audit.summary(),
            "from_source_description": n_from_source,
            "profiles_run": len(payload),
        }
        # The taxonomy and everything downstream of it are declared descendants of this
        # step, so the cascade owns clearing them — and counts them before it does.
        invalidated = lineage_routes.cascade(svc, fresh, "tasks:infer")
        svc.save_state(
            fresh,
            action="infer-tasks",
            lineage_payload={"profiles": len(payload), "tasks": len(flat), "audit": audit.summary()},
        )

        summary = {
            "invalidated": invalidated,
            "tasks": len(flat),
            "profiles": len(payload),
            "mean_per_profile": round(len(flat) / max(1, len(payload)), 1),
            # How many roles were read from their own uploaded description rather than from the
            # document written about them. Reported per run, because a step that silently reads
            # different inputs for different roles is one whose output nobody can account for.
            "from_source_description": n_from_source,
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


# ===========================================================================
# Clustering into a taxonomy is handled by the shared per-tier routes —
# /cluster/{entity}/tier/{tier}/... in routes/tiers.py — with entity "task".
#
# There used to be a single-shot path here: one build, one preview cutting all
# three tiers at once, one confirm that named every level in the same call. It has
# been removed rather than kept alongside, because two code paths writing the same
# `clustering` state is how the two drift apart, and the per-tier flow supersedes it
# outright: a cluster count and a stability gate chosen per tier, the routing cost
# shown before it is paid, and each level's names confirmed on their own.
# ===========================================================================


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
    # One sentence per cluster, written at naming time. Sparse: empty for a tier
    # confirmed before descriptions existed.
    descs = tier_state.descriptions_of(state, "task")
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
            {"id": a.final_family_id, "name": c.family_names.get(a.final_family_id, "?"),
             "description": descs["family"].get(a.final_family_id, ""), "categories": {}},
        )
        cat = dom["categories"].setdefault(
            a.final_category_id,
            {"id": a.final_category_id, "name": c.category_names.get(a.final_category_id, "?"),
             "description": descs["category"].get(a.final_category_id, ""), "clusters": {}},
        )
        cl = cat["clusters"].setdefault(
            a.final_profile_id,
            {"id": a.final_profile_id, "name": c.profile_names.get(a.final_profile_id, "?"),
             "description": descs["profile"].get(a.final_profile_id, ""), "tasks": []},
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
