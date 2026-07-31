from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.blob_store import BlobProjectStore
from app.models.project_state import ProjectMeta

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise HTTPException(400, "name must contain at least one alphanumeric character")
    return slug


@router.get("/clients")
def list_clients() -> list[str]:
    store = BlobProjectStore()
    return store.list_clients()


@router.get("/clients/{client_slug}")
def list_projects(client_slug: str) -> list[str]:
    store = BlobProjectStore()
    if not store.client_container_exists(client_slug):
        raise HTTPException(404, f"client '{client_slug}' not found")
    return store.list_projects(client_slug)


class CreateClientRequest(BaseModel):
    name: str


@router.post("/clients")
def create_client(req: CreateClientRequest) -> dict:
    store = BlobProjectStore()
    slug = _slugify(req.name)
    try:
        store.ensure_client_container(slug)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    return {"client_slug": slug}


class CreateProjectRequest(BaseModel):
    client_slug: str
    project_name: str
    client_company_description: str | None = None
    accent_color: str = "#1d4ed8"


@router.post("")
def create_project(req: CreateProjectRequest) -> ProjectMeta:
    store = BlobProjectStore()
    if not store.client_container_exists(req.client_slug):
        raise HTTPException(404, f"client '{req.client_slug}' not found")

    project_slug = _slugify(req.project_name)
    if store.read_project_meta(req.client_slug, project_slug) is not None:
        raise HTTPException(409, f"project '{project_slug}' already exists for client '{req.client_slug}'")

    now = datetime.now(timezone.utc)
    meta = ProjectMeta(
        client_slug=req.client_slug,
        project_slug=project_slug,
        display_name=req.project_name,
        client_company_description=req.client_company_description,
        accent_color=req.accent_color,
        created_at=now,
        updated_at=now,
    )
    try:
        store.write_project_meta(req.client_slug, project_slug, meta.model_dump(mode="json"))
        store.write_lineage_entry(req.client_slug, project_slug, "create-project", {"meta": meta.model_dump(mode="json")})
    except Exception as e:  # azure SDK errors surface as generic HttpResponseError
        raise HTTPException(403, f"failed to write project: {e}") from e
    return meta


@router.get("/{client_slug}/{project_slug}")
def get_project_meta(client_slug: str, project_slug: str) -> ProjectMeta:
    store = BlobProjectStore()
    meta = store.read_project_meta(client_slug, project_slug)
    if meta is None:
        raise HTTPException(404, "project not found")
    return ProjectMeta.model_validate(meta)
