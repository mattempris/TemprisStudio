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
