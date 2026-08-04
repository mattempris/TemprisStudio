"""What a repeat would invalidate, and the shared helper that does it.

The preview endpoint exists so the confirmation dialog and the actual cascade come from
one source. If the dialog computed its own counts they would drift.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.project_state import ProjectState
from app.services import lineage
from app.services.project_service import ProjectService

router = APIRouter(prefix="/api/projects/{client_slug}/{project_slug}/lineage", tags=["lineage"])


@router.get("/steps")
def list_steps(client_slug: str, project_slug: str) -> dict:
    """The dependency graph, so the UI can render badges without hardcoding the edges."""
    return {
        "steps": [
            {
                "key": s.key,
                "title": s.title,
                "consumes": list(s.consumes),
                "verb": s.verb.value,
                "counter": s.counter,
            }
            for s in lineage.STEPS
        ]
    }


@router.get("/preview")
def preview(client_slug: str, project_slug: str, step: str) -> dict:
    """What re-running `step` would invalidate. Changes nothing."""
    svc = ProjectService()
    try:
        state = svc.load_state(client_slug, project_slug)
    except LookupError as e:
        raise HTTPException(404, f"project not found: {client_slug}/{project_slug}") from e
    if step not in lineage.BY_KEY:
        raise HTTPException(
            422, f"unknown step {step!r}; expected one of {sorted(lineage.BY_KEY)}"
        )
    return lineage.preview(state, step)


def cascade(svc: ProjectService, state: ProjectState, step: str) -> list[dict]:
    """Invalidate everything downstream of `step` on `state`, and clean up its blobs.

    Called from inside a stage's work function, on the freshly-loaded state it is about to
    save — not on a copy read earlier, or the invalidation would be written on top of
    whatever else happened in between.

    The graph fact table lives in its own blob rather than in state, so clearing it means
    deleting the blob and dropping the process-level cache. Left in place it would keep
    serving a graph built from records that no longer exist.
    """
    affected = lineage.apply(state, step)
    if any(a["step"] == "workforce:graph" for a in affected) or step in (
        "task:family",
        "skill:family",
        "profiles",
    ):
        client, project = state.meta.client_slug, state.meta.project_slug
        # Imported here rather than at module scope: routes.workforce imports this module
        # for `cascade`, so a top-level import would be circular.
        from app.api.routes import workforce as wf_routes

        wf_routes._FACTS.pop((client, project), None)
        try:
            svc.store.delete_blob(client, f"{project}/{wf_routes.GRAPH_BLOB}.json")
        except Exception:  # noqa: BLE001 — a missing blob is the desired end state anyway
            pass
        if not any(a["step"] == "workforce:graph" for a in affected):
            affected.append(
                {
                    "step": "workforce:graph",
                    "title": "Work architecture",
                    "verb": "clear",
                    "count": 1,
                    "counter": "graph",
                }
            )
    return affected
