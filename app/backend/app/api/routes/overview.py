"""The final combined deliverable — one browsable tree carrying every artifact.

instructions.txt, final requirement: "Ability to browse full hierarchy of job
family, job category, job profile, and job profile detail including skills and
tasks", with headcount analytics and the 3rd-party taxonomy match.

Everything downstream of clustering keys off `profile_key`, so this is a join
rather than a recomputation: job profile + JE result + required skills + tasks +
external taxonomy match, rolled up through Family › Category › Profile with
headcount at every tier.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.project_state import ProjectState
from app.services.exports import architecture
from app.services.project_service import ProjectService

router = APIRouter(
    prefix="/api/projects/{client_slug}/{project_slug}/overview", tags=["overview"]
)


def _load(client_slug: str, project_slug: str) -> ProjectState:
    svc = ProjectService()
    try:
        return svc.load_state(client_slug, project_slug)
    except LookupError as e:
        raise HTTPException(404, f"project not found: {client_slug}/{project_slug}") from e


@router.get("")
def overview(client_slug: str, project_slug: str) -> dict:
    state = _load(client_slug, project_slug)
    data = architecture.build(state)
    if data is None:
        raise HTTPException(409, "cluster and name the job hierarchy first")
    return data
