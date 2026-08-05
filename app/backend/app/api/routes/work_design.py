"""Work Design Studio routes.

Registered before the workforce router in `main.py`. No path collides today, but registering
first makes it impossible for a future `/workforce/{something}` route to shadow this prefix.

Every read here is served from the graph blob rather than project state — see the note in
`services/workforce/work_design.py` on why. State is only loaded on the writes.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.models.project_state import ProjectState
from app.services.project_service import ProjectService
from app.services.workforce import graph as wf
from app.services.workforce import work_design as wd

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
    return wd.readiness(state, graph_built=_graph_built(svc, client_slug, project_slug))


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
