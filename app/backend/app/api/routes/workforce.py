"""Workforce Studio routes.

Steps 1 (the work architecture graph) and 3 (the AI opportunity assessment) are here.
Later steps — process upload, personal productivity, agent definitions, future role
design — extend this module and the same fact table.

The endpoint split follows cost, as elsewhere in the app:

  status              what is ready and what is missing, from state alone. Cheap, polled.
  build               compute the whole graph at leaf resolution and persist it. Once, as a job.
  graph               roll the persisted table up to one view. Instant, so it drives the zoom.
  node                everything behind one node, for its modal.
  opportunity/assess  one LLM call per task cluster. The only paid path here, as a job.
  opportunity/*       read the assessment back, rolled up per cluster or per role.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.project_state import (
    ProjectState,
    TaskActionRecord,
    TaskOpportunityRecord,
)
from app.services import llm
from app.services.orchestrator import JobAlreadyRunning, ProgressReporter, get_registry, run_job
from app.services.project_service import ProjectService
from app.services.workforce import graph as wf
from app.services.workforce import opportunity as opp

router = APIRouter(prefix="/api/projects/{client_slug}/{project_slug}/workforce", tags=["workforce"])

GRAPH_BLOB = "workforce/graph_facts"

# Per (client, project): the parsed fact table. Rebuilding is deterministic and the
# blob read is the only real cost, so a cold cache costs a read rather than
# correctness.
_FACTS: dict[tuple[str, str], wf.Facts] = {}


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


def _facts(svc: ProjectService, client_slug: str, project_slug: str) -> wf.Facts:
    key = (client_slug, project_slug)
    if key in _FACTS:
        return _FACTS[key]
    raw = svc.load_json(client_slug, f"{project_slug}/{GRAPH_BLOB}.json")
    if raw is None:
        raise HTTPException(409, "the work architecture has not been built yet")
    facts = wf.Facts.from_json(raw)
    _FACTS[key] = facts
    return facts


@router.get("/status")
def workforce_status(client_slug: str, project_slug: str) -> dict:
    """Gate state plus whether the graph exists. Never builds anything."""
    svc, state = _load(client_slug, project_slug)
    built = (client_slug, project_slug) in _FACTS or svc.json_exists(
        client_slug, f"{project_slug}/{GRAPH_BLOB}.json"
    )
    r = wf.readiness(state)
    return {
        **r,
        "graph_built": bool(built),
        "levels": list(wf.LEVELS),
        "entities": list(wf.ENTITIES),
        "level_titles": wf.LEVEL_TITLES,
    }


@router.post("/graph/build")
async def build_graph(client_slug: str, project_slug: str) -> dict:
    """Compute and persist the whole graph at leaf resolution.

    A job rather than a synchronous call: it walks every inferred skill and task on
    the project (5,000-10,000 of each at real scale) and writes a blob, which is
    seconds rather than milliseconds. No model calls — it is derived from state.
    """
    svc, state = _load(client_slug, project_slug)
    r = wf.readiness(state)
    if not r["ready"]:
        raise HTTPException(409, {"message": "not ready", "missing": r["missing"]})

    def work(reporter: ProgressReporter) -> dict:
        reporter.message("Reading the job, skill and task hierarchies")
        facts = wf.build(state)
        reporter.message("Saving the work architecture")
        svc.save_json(client_slug, project_slug, GRAPH_BLOB, facts.to_json())
        _FACTS[(client_slug, project_slug)] = facts
        summary = {
            "job_profiles": facts.leaf_counts()["job"],
            "skill_clusters": facts.leaf_counts()["skill"],
            "task_clusters": facts.leaf_counts()["task"],
            "job_skill_edges": len(facts.job_skill),
            "job_task_edges": len(facts.job_task),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "workforce", work)


@router.get("/graph")
def get_graph(
    client_slug: str,
    project_slug: str,
    jobs: str = "family",
    skills: str = "family",
    tasks: str = "family",
    expand: str = "",
) -> dict:
    """One view of the graph: the fact table rolled up to the requested resolution.

    `expand` is a comma-separated list of node ids whose children should be shown, so
    one branch can be opened without dropping the whole view to a finer level.
    """
    # Deliberately no state read: a cut needs only the fact table, and the state blob
    # is ~9MB on a real project. Reading it here put ~2.8s on every zoom and every
    # expand, which is the whole latency budget for a control meant to feel live.
    facts = _facts(ProjectService(), client_slug, project_slug)
    expanded = {x for x in (e.strip() for e in expand.split(",")) if x}
    try:
        return wf.cut(facts, levels={"job": jobs, "skill": skills, "task": tasks}, expanded=expanded)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/node/{node_id}")
def get_node(client_slug: str, project_slug: str, node_id: str) -> dict:
    """Everything behind one node, for its modal."""
    facts = _facts(ProjectService(), client_slug, project_slug)
    try:
        return wf.node_detail(facts, node_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


# ===========================================================================
# Step 3 — AI opportunity assessment
# ===========================================================================
def _profile_headcount(state: ProjectState) -> dict[str, int]:
    """Headcount per job profile_key.

    The graph builder's own rollup, reused rather than reimplemented: the FTE numbers
    in the opportunity report and the node sizes in the graph must be the same figure,
    and two copies of this walk through dedupe groups is how they stop being.
    """
    return wf.profile_headcount(state)


@router.get("/opportunity/status")
def opportunity_status(client_slug: str, project_slug: str) -> dict:
    """What is assessed, what is left, and what finishing it would cost.

    The estimate is shown before the button is pressed, following the clustering
    gate's precedent: this is the first Workforce Studio step that spends money, and
    a run over a real project is hundreds of calls.
    """
    _, state = _load(client_slug, project_slug)
    if state.tasks.clustering is None:
        raise HTTPException(409, "the task taxonomy has not been built yet")
    total = len(state.tasks.clustering.profile_names)
    assessed = {o.task_cluster_id for o in state.workforce.opportunity}
    remaining = total - len(assessed)
    return {
        "task_clusters": total,
        "assessed": len(assessed),
        "remaining": remaining,
        "actions": len(state.workforce.actions),
        "audit": state.workforce.audit,
        "hours_per_fte_week": state.workforce.hours_per_fte_week,
        "has_headcount": bool(_profile_headcount(state)),
        "estimate_remaining": opp.cost_estimate(remaining),
        "estimate_all": opp.cost_estimate(total),
    }


class AssessRequest(BaseModel):
    """`limit` assesses only the largest N clusters by time share — the shape of a
    live calibration check before committing to the whole taxonomy. `redo` re-assesses
    clusters that already have a result instead of skipping them."""

    cluster_ids: list[int] | None = None
    limit: int | None = None
    redo: bool = False


@router.post("/opportunity/assess")
async def assess_opportunity(
    client_slug: str, project_slug: str, req: AssessRequest, workers: int | None = None
) -> dict:
    svc, state = _load(client_slug, project_slug)
    if state.tasks.clustering is None:
        raise HTTPException(409, "the task taxonomy has not been built yet")
    _workers = llm.resolve_workers(workers)

    inputs = opp.cluster_inputs(state)
    if req.cluster_ids is not None:
        wanted = set(req.cluster_ids)
        inputs = [i for i in inputs if i.cluster_id in wanted]
    if not req.redo:
        done = {o.task_cluster_id for o in state.workforce.opportunity}
        inputs = [i for i in inputs if i.cluster_id not in done]
    if req.limit:
        inputs = inputs[: req.limit]
    if not inputs:
        raise HTTPException(400, "nothing to assess — every requested cluster already has a result")

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(
            len(inputs), f"Assessing AI opportunity across {len(inputs)} task clusters"
        )
        results = opp.assess_many(inputs, workers=_workers, progress=reporter.pmap_callback())
        audit = opp.audit(results, requested=len(inputs))

        reporter.message("Saving the assessment")
        now = datetime.now(timezone.utc)
        fresh = svc.load_state(client_slug, project_slug)
        # Replace per cluster rather than appending: a redo of one cluster must not
        # leave the old actions behind alongside the new ones, which would double its
        # weight in every roll-up.
        touched = {r.cluster_id for r in results if r is not None}
        fresh.workforce.actions = [
            a for a in fresh.workforce.actions if a.task_cluster_id not in touched
        ]
        fresh.workforce.opportunity = [
            o for o in fresh.workforce.opportunity if o.task_cluster_id not in touched
        ]
        for r in results:
            if r is None:
                continue
            fresh.workforce.actions.extend(
                TaskActionRecord(
                    id=f"act-{uuid.uuid4().hex[:8]}",
                    task_cluster_id=r.cluster_id,
                    name=a.name,
                    definition=a.definition,
                    pct_of_task=a.pct_of_task,
                    automation_pct=a.automation_pct,
                    augmentation_pct=a.augmentation_pct,
                )
                for a in r.actions
            )
            fresh.workforce.opportunity.append(
                TaskOpportunityRecord(
                    task_cluster_id=r.cluster_id,
                    cluster_name=r.cluster_name,
                    automation_pct=r.automation_pct,
                    augmentation_pct=r.augmentation_pct,
                    n_actions=len(r.actions),
                    raw_pct_sum=r.raw_pct_sum,
                    clamped=r.clamped,
                    computed_at=now,
                )
            )
        fresh.workforce.audit = {
            **audit.summary(),
            "total_assessed": len(fresh.workforce.opportunity),
        }
        svc.save_state(
            fresh,
            action="assess-ai-opportunity",
            lineage_payload={"requested": len(inputs), **audit.summary()},
        )

        # The graph is derived, so it is rebuilt rather than patched — otherwise the
        # architecture keeps showing no opportunity until someone happens to press
        # "rebuild", and the two views of the same project disagree.
        if svc.json_exists(client_slug, f"{project_slug}/{GRAPH_BLOB}.json"):
            reporter.message("Refreshing the work architecture")
            facts = wf.build(fresh)
            svc.save_json(client_slug, project_slug, GRAPH_BLOB, facts.to_json())
            _FACTS[(client_slug, project_slug)] = facts

        summary = {
            "requested": len(inputs),
            "actions": sum(len(r.actions) for r in results if r is not None),
            **audit.summary(),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "opportunity", work)


@router.get("/opportunity/clusters")
def opportunity_clusters(client_slug: str, project_slug: str) -> dict:
    """The assessment per task cluster, with its actions, ordered by absorbable time.

    `fte_weighted` is the honest priority for step 6: automation percentage alone
    ranks a rare, highly automatable task above a ubiquitous, moderately automatable
    one, and an agent should be built for the second.
    """
    _, state = _load(client_slug, project_slug)
    c = state.tasks.clustering
    if c is None:
        raise HTTPException(409, "the task taxonomy has not been built yet")

    headcount = _profile_headcount(state)
    task_by_id = {t.id: t for t in state.tasks.inferred}
    # Time on each cluster, as a share of one job holder's week and as FTE.
    proportion: dict[int, float] = {}
    fte: dict[int, float] = {}
    roles: dict[int, set[str]] = {}
    for a in c.assignments:
        t = task_by_id.get(a.item_id)
        if t is None:
            continue
        cid = a.final_profile_id
        proportion[cid] = proportion.get(cid, 0.0) + t.proportion
        heads = headcount.get(t.source_profile_key, 0)
        if heads:
            fte[cid] = fte.get(cid, 0.0) + t.proportion / 100.0 * heads
        roles.setdefault(cid, set()).add(t.source_profile_key)

    actions_by_cluster: dict[int, list[dict]] = {}
    for a in state.workforce.actions:
        actions_by_cluster.setdefault(a.task_cluster_id, []).append(
            {
                "name": a.name,
                "definition": a.definition,
                "pct_of_task": a.pct_of_task,
                "automation": a.automation_pct,
                "augmentation": a.augmentation_pct,
            }
        )
    for items in actions_by_cluster.values():
        items.sort(key=lambda x: -x["pct_of_task"])

    # A cluster's parents, for the domain/category filter in the UI.
    parents: dict[int, tuple[int, int]] = {}
    for a in c.assignments:
        parents[a.final_profile_id] = (a.final_category_id, a.final_family_id)

    rows = []
    for o in state.workforce.opportunity:
        cid = o.task_cluster_id
        cat, fam = parents.get(cid, (-1, -1))
        prop = round(proportion.get(cid, 0.0), 2)
        f = fte.get(cid)
        rows.append(
            {
                "cluster_id": cid,
                "name": c.profile_names.get(cid, o.cluster_name),
                "category": c.category_names.get(cat, "—"),
                "domain": c.family_names.get(fam, "—"),
                "automation": o.automation_pct,
                "augmentation": o.augmentation_pct,
                "n_actions": o.n_actions,
                "clamped": o.clamped,
                "roles": len(roles.get(cid, ())),
                "proportion_sum": prop,
                "fte": round(f, 2) if f is not None else None,
                # What the assessment says is absorbable, in whichever unit the
                # project actually has. Never both — one of them would be invented.
                "absorbable": round(
                    (f if f is not None else prop / 100.0) * o.automation_pct / 100.0, 2
                ),
                "actions": actions_by_cluster.get(cid, []),
            }
        )
    rows.sort(key=lambda r: -r["absorbable"])
    return {
        "clusters": rows,
        "has_headcount": bool(headcount),
        "unit": "FTE" if headcount else "role-weeks",
        "total_clusters": len(c.profile_names),
        "audit": state.workforce.audit,
    }


@router.get("/opportunity/roles")
def opportunity_roles(client_slug: str, project_slug: str) -> dict:
    """The role-level report — instructions step 3, demo section 6.

    Every role with its automation and augmentation potential, its released capacity
    where headcount is known, and the task breakdown behind each number.
    """
    _, state = _load(client_slug, project_slug)
    c = state.tasks.clustering
    if c is None:
        raise HTTPException(409, "the task taxonomy has not been built yet")

    scores = {
        o.task_cluster_id: (o.automation_pct, o.augmentation_pct)
        for o in state.workforce.opportunity
    }
    if not scores:
        raise HTTPException(409, "no AI opportunity assessment has been run yet")

    headcount = _profile_headcount(state)
    hours = state.workforce.hours_per_fte_week
    cluster_of = {a.item_id: a.final_profile_id for a in c.assignments}
    title_of = {p.profile_key: p.title for p in state.job_profiles}

    by_role: dict[str, list] = {}
    for t in state.tasks.inferred:
        cid = cluster_of.get(t.id)
        if cid is None:
            continue
        by_role.setdefault(t.source_profile_key, []).append((t, cid))

    rows = []
    for key, items in by_role.items():
        r = opp.role_opportunity(
            profile_key=key,
            title=title_of.get(key, key),
            headcount=headcount.get(key),
            tasks=[(t.proportion, cid) for t, cid in items],
            cluster_scores=scores,
            hours_per_fte_week=hours,
        )
        rows.append(
            {
                "profile_key": r.profile_key,
                "title": r.title,
                "headcount": r.headcount,
                "automation": r.automation_pct,
                "augmentation": r.augmentation_pct,
                "coverage": r.coverage_pct,
                "n_tasks": r.n_tasks,
                "fte_released": r.fte_released,
                "hours_per_week": r.hours_per_week,
                "tasks": sorted(
                    [
                        {
                            "name": t.name,
                            "description": t.description,
                            "proportion": t.proportion,
                            "cluster_id": cid,
                            "cluster": c.profile_names.get(cid, str(cid)),
                            "automation": scores[cid][0] if cid in scores else None,
                            "augmentation": scores[cid][1] if cid in scores else None,
                            # Ranking key for step 5: where a prompt helps this
                            # person most is augmentation weighted by how much of
                            # their week the task takes, not augmentation alone.
                            "augmentation_weighted": round(
                                t.proportion / 100.0 * scores[cid][1], 2
                            )
                            if cid in scores
                            else None,
                        }
                        for t, cid in items
                    ],
                    key=lambda x: -x["proportion"],
                ),
            }
        )
    rows.sort(key=lambda r: (-(r["fte_released"] or 0), -r["automation"]))

    assessed = [r for r in rows if r["coverage"] > 0]
    total_fte = sum(r["fte_released"] or 0 for r in rows)
    return {
        "roles": rows,
        "has_headcount": bool(headcount),
        "hours_per_fte_week": hours,
        "totals": {
            "roles": len(rows),
            "roles_assessed": len(assessed),
            "headcount": sum(r["headcount"] or 0 for r in rows) or None,
            "mean_automation": round(
                sum(r["automation"] for r in assessed) / len(assessed), 1
            )
            if assessed
            else 0.0,
            "mean_augmentation": round(
                sum(r["augmentation"] for r in assessed) / len(assessed), 1
            )
            if assessed
            else 0.0,
            "mean_coverage": round(sum(r["coverage"] for r in rows) / len(rows), 1) if rows else 0.0,
            "fte_released": round(total_fte, 1) if headcount else None,
            "hours_per_week": round(total_fte * hours, 0) if headcount else None,
        },
    }
