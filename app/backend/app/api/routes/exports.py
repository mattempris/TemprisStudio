"""Tabular export routes — CSV per dataset, or the whole project as one XLSX."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.models.project_state import ProjectState
from app.services.exports import workbook
from app.services.project_service import ProjectService

router = APIRouter(
    prefix="/api/projects/{client_slug}/{project_slug}/exports", tags=["exports"]
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _load(client_slug: str, project_slug: str) -> ProjectState:
    try:
        return ProjectService().load_state(client_slug, project_slug)
    except LookupError as e:
        raise HTTPException(404, f"project not found: {client_slug}/{project_slug}") from e


def _attachment(name: str) -> str:
    # Quote-strip so a project name with a quote can't break the header.
    return f'attachment; filename="{re.sub(chr(34), "", name)}"'


@router.get("/manifest")
def manifest(client_slug: str, project_slug: str) -> dict:
    """What is exportable right now, with row counts — lets the UI list only
    datasets that actually have data instead of offering empty downloads."""
    state = _load(client_slug, project_slug)
    return {
        "datasets": [
            {"key": key, "name": ds.name, "rows": len(ds.rows), "columns": len(ds.columns)}
            for key, build in workbook.BUILDERS.items()
            for ds in [build(state)]
            if ds.rows
        ]
    }


@router.get("/workbook.xlsx")
def export_workbook(client_slug: str, project_slug: str) -> Response:
    state = _load(client_slug, project_slug)
    datasets = workbook.build_all(state)
    if not datasets:
        raise HTTPException(409, "nothing to export yet — run the pipeline first")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=workbook.to_xlsx(datasets),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": _attachment(f"{client_slug}-{project_slug}-{stamp}.xlsx")},
    )


@router.get("/{dataset}.csv")
def export_csv(client_slug: str, project_slug: str, dataset: str) -> Response:
    build = workbook.BUILDERS.get(dataset)
    if build is None:
        raise HTTPException(
            404, f"unknown dataset '{dataset}'; available: {sorted(workbook.BUILDERS)}"
        )
    ds = build(_load(client_slug, project_slug))
    if not ds.rows:
        raise HTTPException(409, f"'{dataset}' has no rows yet — that stage has not run")
    return Response(
        content=ds.to_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _attachment(f"{project_slug}-{dataset}.csv")},
    )
