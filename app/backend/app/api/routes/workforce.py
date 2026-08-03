"""Workforce Studio routes.

Step 1 (the work architecture graph) is here. Later steps — AI opportunity, process
upload, personal productivity, agent definitions, future role design — extend this
module and the same fact table.

The endpoint split follows cost, as elsewhere in the app:

  status   what is ready and what is missing, from state alone. Cheap, polled.
  build    compute the whole graph at leaf resolution and persist it. Once, as a job.
  graph    roll the persisted table up to one view. Instant, so it drives the zoom.
  node     everything behind one node, for its modal.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.models.project_state import ProjectState
from app.services.orchestrator import JobAlreadyRunning, ProgressReporter, get_registry, run_job
from app.services.project_service import ProjectService
from app.services.workforce import graph as wf

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

    registry = get_registry()
    try:
        job = registry.create(client_slug, project_slug, "workforce")
    except JobAlreadyRunning as e:
        raise HTTPException(
            409, {"message": str(e), "existing_job_id": e.job.job_id, "existing_stage": e.job.stage}
        ) from e
    asyncio.create_task(run_job(job, work))
    return {"job_id": job.job_id, "stage": "workforce", "websocket_url": f"/ws/pipeline/{job.job_id}"}


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
