"""Work Design Studio routes.

Registered before the workforce router in `main.py`. No path collides today, but registering
first makes it impossible for a future `/workforce/{something}` route to shadow this prefix.

Every read here is served from the graph blob rather than project state — see the note in
`services/workforce/work_design.py` on why. State is only loaded on the writes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.models.project_state import (
    DesignedJobRecord,
    DesignedTaskLine,
    ProjectState,
    WorkDesignFacets,
)
from app.services.project_service import ProjectService
from app.services.workforce import graph as wf
from app.services.exports import workbook
from app.services.workforce import work_design as wd

# Reused rather than restated, so a change to the media type lands in one place.
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

router = APIRouter(
    prefix="/api/projects/{client_slug}/{project_slug}/work-design",
    tags=["work-design"],
)

# The graph blob's name, shared with the workforce routes. Imported lazily in `_facts` rather
# than at module scope so the two route modules do not import each other.
GRAPH_BLOB = "workforce/graph_facts"


def _load(client_slug: str, project_slug: str) -> tuple[ProjectService, ProjectState]:
    svc = ProjectService()
    return svc, svc.load_state(client_slug, project_slug)


def _graph_built(svc: ProjectService, client_slug: str, project_slug: str) -> bool:
    from app.api.routes.workforce import _FACTS

    return (client_slug, project_slug) in _FACTS or svc.json_exists(
        client_slug, f"{project_slug}/{GRAPH_BLOB}.json"
    )


def _facts(svc: ProjectService, client_slug: str, project_slug: str) -> wf.Facts:
    """The graph, from the in-process cache or the blob. Never rebuilt here.

    Rebuilding is the workforce studio's job and is an explicit user action there; doing it
    implicitly on a read would make a filter click occasionally cost seconds.
    """
    from app.api.routes.workforce import _facts as workforce_facts

    return workforce_facts(svc, client_slug, project_slug)


@router.get("/status")
def work_design_status(client_slug: str, project_slug: str) -> dict:
    """Whether the studio can be entered, and what is missing if not."""
    svc, state = _load(client_slug, project_slug)
    built = _graph_built(svc, client_slug, project_slug)
    version = 0
    if built:
        try:
            version = _facts(svc, client_slug, project_slug).version
        except Exception:
            version = 0
    return wd.readiness(state, graph_built=built, graph_version=version)


@router.get("/facets")
def work_design_facets(client_slug: str, project_slug: str) -> dict:
    """Filter options for the work pool, with match counts."""
    svc, _ = _load(client_slug, project_slug)
    return wd.facet_options(_facts(svc, client_slug, project_slug))


def _ints(value: str | None) -> list[int]:
    """Comma-separated ids from a query string, ignoring anything unparseable.

    Lenient rather than 422: a filter is a view, and rejecting the whole request because one
    id in a list was malformed would break the screen over something with no consequence.
    """
    out: list[int] = []
    for part in (value or "").split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                continue
    return out


def _strs(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


@router.get("/levers")
def work_design_levers(client_slug: str, project_slug: str) -> dict:
    """Every agent and augmentation that could be applied, with what it targets."""
    svc, state = _load(client_slug, project_slug)
    facts = _facts(svc, client_slug, project_slug)
    tf = facts.entities.get("task")
    return {
        "uplift": state.work_design.augmentation_uplift,
        "threshold": wd.ABSORPTION_THRESHOLD,
        "agents": [
            {
                "id": a.agent_id,
                "name": a.name,
                "cluster_id": a.task_cluster,
                "cluster": wf.label_of(tf, "profile", a.task_cluster) if tf else "",
                "automation": a.automation_pct,
                "human_in_the_loop": a.human_in_the_loop,
                "oversight_fraction": a.oversight_fraction,
                "oversight_source": a.oversight_source,
                "oversight_tasks": [
                    {"name": n, "definition": d, "pct_of_absorbed_time": p}
                    for n, d, p in a.oversight_tasks
                ],
            }
            for a in facts.agents
        ],
        "augmentations": [
            {
                "id": s.skill_id,
                "name": s.name,
                "role_title": s.role_title,
                "profile_key": s.profile_key,
                "cluster_id": s.task_cluster,
                "cluster": wf.label_of(tf, "profile", s.task_cluster) if tf else "",
                "rank_score": s.rank_score,
            }
            for s in sorted(facts.augmentations, key=lambda x: -x.rank_score)
        ],
    }


class LeversRequest(BaseModel):
    """A lever selection, applied to a filtered pool.

    Facets travel in the body rather than the query string because this is one operation over
    both — computing automation and augmentation in two round trips would let the client
    combine them in the wrong order, and the order is load-bearing.
    """

    job_family_ids: list[int] = Field(default_factory=list)
    job_category_ids: list[int] = Field(default_factory=list)
    task_family_ids: list[int] = Field(default_factory=list)
    task_category_ids: list[int] = Field(default_factory=list)
    business_level_1: list[str] = Field(default_factory=list)
    business_level_2: list[str] = Field(default_factory=list)
    business_level_3: list[str] = Field(default_factory=list)
    agent_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    # Overrides the project setting for a what-if, without saving it.
    uplift: float | None = Field(default=None, ge=0, le=1)
    # The job open in the design panel, whose own allocation must not drain the pool twice.
    editing_job_id: str | None = None


@router.post("/apply")
def work_design_apply(client_slug: str, project_slug: str, req: LeversRequest) -> dict:
    """The pool with levers applied — automation first, then augmentation on what survives."""
    svc, state = _load(client_slug, project_slug)
    facts = _facts(svc, client_slug, project_slug)
    facets = wd.Facets(
        job_family_ids=req.job_family_ids,
        job_category_ids=req.job_category_ids,
        task_family_ids=req.task_family_ids,
        task_category_ids=req.task_category_ids,
        business_level_1=req.business_level_1,
        business_level_2=req.business_level_2,
        business_level_3=req.business_level_3,
    )
    pool = wd.pool(facts, facets, hours_per_fte_week=state.workforce.hours_per_fte_week)
    applied = wd.apply_levers(
        facts,
        pool,
        agent_ids=req.agent_ids,
        skill_ids=req.skill_ids,
        uplift=req.uplift if req.uplift is not None else state.work_design.augmentation_uplift,
    )
    # Drain what designed jobs already hold. `editing_job_id` excludes the job currently open
    # in the panel — its lines are in the panel, so counting them as allocated as well would
    # show the same hours twice and take them out of the pool the edit needs to draw from.
    return wd.drain(applied, wd.allocated_hours(state, exclude_job_id=req.editing_job_id))


def _job(state: ProjectState, job_id: str) -> DesignedJobRecord:
    for j in state.work_design.jobs:
        if j.id == job_id:
            return j
    raise HTTPException(404, f"no designed job {job_id}")


def _job_json(job: DesignedJobRecord, *, hpw: float) -> dict:
    return {
        **job.model_dump(mode="json"),
        "capacity": wd.capacity(job, hours_per_fte_week=hpw),
    }


class TaskLineIn(BaseModel):
    id: str | None = None
    task_cluster_id: int | None = None
    cluster_name: str = ""
    name: str
    description: str = ""
    origin: str = "as_is"
    hours_per_week: float = Field(default=0.0, ge=0)
    agent_id: str | None = None
    source_profile_key: str | None = None
    contributing_tasks: list[str] = Field(default_factory=list)
    lever_ids: list[str] = Field(default_factory=list)
    automation_pct: float | None = None
    augmentation_pct: float | None = None


class JobIn(BaseModel):
    """A designed job as the client holds it.

    `tasks` is the full list rather than a set of per-line operations. The client owns the
    authoritative arrangement of a drag-and-drop board; per-line operations would need ordering
    semantics and invite the two sides diverging. One drop is one small PATCH.
    """

    title: str = "New job"
    headcount: float = Field(default=1.0, gt=0)
    notes: str = ""
    facets: dict | None = None
    selected_agent_ids: list[str] = Field(default_factory=list)
    selected_skill_ids: list[str] = Field(default_factory=list)
    tasks: list[TaskLineIn] = Field(default_factory=list)


@router.get("/jobs")
def list_designed_jobs(client_slug: str, project_slug: str) -> dict:
    _, state = _load(client_slug, project_slug)
    hpw = state.workforce.hours_per_fte_week
    return {
        "jobs": [_job_json(j, hpw=hpw) for j in state.work_design.jobs],
        "hours_per_fte_week": hpw,
        "augmentation_uplift": state.work_design.augmentation_uplift,
    }


@router.get("/jobs/{job_id}")
def get_designed_job(client_slug: str, project_slug: str, job_id: str) -> dict:
    """One job, for loading back into the panel to edit."""
    _, state = _load(client_slug, project_slug)
    return _job_json(_job(state, job_id), hpw=state.workforce.hours_per_fte_week)


def _apply_in(job: DesignedJobRecord, body: JobIn) -> None:
    job.title = body.title
    job.headcount = body.headcount
    job.notes = body.notes
    job.selected_agent_ids = body.selected_agent_ids
    job.selected_skill_ids = body.selected_skill_ids
    if body.facets is not None:
        job.facets = WorkDesignFacets.model_validate(body.facets)
    job.tasks = [
        DesignedTaskLine(
            **{**t.model_dump(), "id": t.id or f"wdl-{uuid.uuid4().hex[:8]}"}
        )
        for t in body.tasks
    ]
    job.updated_at = datetime.now(timezone.utc)
    # Any change to the arrangement, the headcount or the levers invalidates a generated
    # document: it described a different job.
    if job.profile_doc:
        job.profile_doc.stale = True


@router.post("/jobs")
def create_designed_job(client_slug: str, project_slug: str, body: JobIn) -> dict:
    svc, state = _load(client_slug, project_slug)
    now = datetime.now(timezone.utc)
    job = DesignedJobRecord(id=f"wd-{uuid.uuid4().hex[:8]}", title=body.title, created_at=now, updated_at=now)
    _apply_in(job, body)
    state.work_design.jobs.append(job)
    svc.save_designed_jobs(
        state,
        action="create-designed-job",
        lineage_payload={"job_id": job.id, "title": job.title, "lines": len(job.tasks)},
    )
    return _job_json(job, hpw=state.workforce.hours_per_fte_week)


@router.put("/jobs/{job_id}")
def update_designed_job(client_slug: str, project_slug: str, job_id: str, body: JobIn) -> dict:
    """Replace a job in place. Used by Save after loading one back to edit."""
    svc, state = _load(client_slug, project_slug)
    job = _job(state, job_id)
    _apply_in(job, body)
    # Re-saving a job the user has just reviewed clears the stale badge: whatever moved
    # upstream, this arrangement is the one they mean now.
    job.stale = False
    job.stale_reason = ""
    svc.save_designed_jobs(
        state,
        action="update-designed-job",
        lineage_payload={"job_id": job.id, "title": job.title, "lines": len(job.tasks)},
    )
    return _job_json(job, hpw=state.workforce.hours_per_fte_week)


@router.delete("/jobs/{job_id}")
def delete_designed_job(client_slug: str, project_slug: str, job_id: str) -> dict:
    """Delete a job. Its hours return to the unreviewed pool.

    Nothing here puts them back explicitly — the pool is computed as
    `to_be - allocated`, and `allocated` is summed over the jobs that exist. Removing a job
    removes its contribution, so the work reappears in the pool on the next read. That is the
    conservation invariant doing the work rather than a second code path that could disagree
    with it.
    """
    svc, state = _load(client_slug, project_slug)
    job = _job(state, job_id)
    returned = round(
        sum(t.hours_per_week for t in job.tasks if t.origin != "agent_oversight"), 2
    )
    state.work_design.jobs = [j for j in state.work_design.jobs if j.id != job_id]
    svc.save_designed_jobs(
        state,
        action="delete-designed-job",
        lineage_payload={"job_id": job_id, "title": job.title, "hours_returned": returned},
    )
    return {
        "deleted": job_id,
        "title": job.title,
        "hours_returned_to_pool": returned,
        "jobs": len(state.work_design.jobs),
    }


@router.get("/export.xlsx")
def export_designed_jobs(client_slug: str, project_slug: str) -> Response:
    """Every designed job as a two-sheet workbook.

    Separate from `/exports/workbook.xlsx`, which is the whole fourteen-sheet project. What a
    client wants out of this studio is the designs, not the architecture behind them — though
    both datasets are registered in the main workbook too, so nothing is only available here.
    """
    _, state = _load(client_slug, project_slug)
    if not state.work_design.jobs:
        raise HTTPException(409, "no jobs have been designed yet")
    data = workbook.to_xlsx(
        [workbook.designed_jobs_dataset(state), workbook.designed_job_tasks_dataset(state)]
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=data,
        media_type=XLSX_MIME,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{client_slug}-{project_slug}-designed-jobs-{stamp}.xlsx"'
            )
        },
    )


@router.get("/target")
def work_design_target(client_slug: str, project_slug: str) -> dict:
    """The accumulated to-be work across every designed job."""
    _, state = _load(client_slug, project_slug)
    return wd.target_profile(state, hours_per_fte_week=state.workforce.hours_per_fte_week)


@router.get("/import/{profile_key}")
def import_preview(client_slug: str, project_slug: str, profile_key: str, headcount: float = 1.0) -> dict:
    """One role's task profile as designed-job lines, scaled to the designed headcount.

    Grouped by task cluster rather than one line per inferred task — a role with three tasks in
    the same cluster wants one line, the same reasoning `_skill_inputs` uses. Scaled by the
    *designed* job's headcount, not the source role's, so an import fills the job to exactly
    100% utilisation: proportions sum to 100 per role, so the hours sum to headcount x a week.
    """
    svc, state = _load(client_slug, project_slug)
    facts = _facts(svc, client_slug, project_slug)
    hpw = state.workforce.hours_per_fte_week
    c = state.tasks.clustering
    if c is None:
        raise HTTPException(409, "the task taxonomy has not been built yet")
    cluster_of = {a.item_id: a.final_profile_id for a in c.assignments}

    grouped: dict[int, dict] = {}
    for t in state.tasks.inferred:
        if t.source_profile_key != profile_key:
            continue
        cid = cluster_of.get(t.id)
        if cid is None:
            continue
        g = grouped.setdefault(
            cid,
            {"task_cluster_id": cid, "cluster_name": c.profile_names.get(cid, str(cid)),
             "proportion": 0.0, "contributing_tasks": []},
        )
        g["proportion"] += float(t.proportion)
        g["contributing_tasks"].append(t.name)

    opp = facts.task_opportunity
    lines = [
        {
            "task_cluster_id": g["task_cluster_id"],
            "cluster_name": g["cluster_name"],
            "name": g["cluster_name"],
            "origin": "as_is",
            "hours_per_week": round(g["proportion"] / 100.0 * headcount * hpw, 2),
            "source_profile_key": profile_key,
            "contributing_tasks": g["contributing_tasks"],
            "automation_pct": opp.get(g["task_cluster_id"], (None, None))[0],
            "augmentation_pct": opp.get(g["task_cluster_id"], (None, None))[1],
        }
        for g in sorted(grouped.values(), key=lambda x: -x["proportion"])
    ]
    return {
        "profile_key": profile_key,
        "headcount": headcount,
        "lines": lines,
        "total_hours_per_week": round(sum(x["hours_per_week"] for x in lines), 2),
    }


@router.get("/pool")
def work_design_pool(
    client_slug: str,
    project_slug: str,
    job_family: str | None = None,
    job_category: str | None = None,
    task_family: str | None = None,
    task_category: str | None = None,
    bf1: str | None = None,
    bf2: str | None = None,
    bf3: str | None = None,
) -> dict:
    """The as-is work of the filtered sample, per task cluster, in hours per week.

    This is the studio's starting position — the work stack to be re-allocated. Levers and
    allocations are applied on top of it by later endpoints; nothing here is persisted.
    """
    svc, state = _load(client_slug, project_slug)
    facets = wd.Facets(
        job_family_ids=_ints(job_family),
        job_category_ids=_ints(job_category),
        task_family_ids=_ints(task_family),
        task_category_ids=_ints(task_category),
        business_level_1=_strs(bf1),
        business_level_2=_strs(bf2),
        business_level_3=_strs(bf3),
    )
    return wd.pool(
        _facts(svc, client_slug, project_slug),
        facets,
        hours_per_fte_week=state.workforce.hours_per_fte_week,
    )
