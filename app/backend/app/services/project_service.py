"""Project state persistence — the durable spine of the app.

instructions.txt: "Save all progress and results to Blob storage with append style
lineage (without interrupting existing subdirectory structure) so the state for
any project can be imported. Ensure that progress is incrementally saved in the
same store."

Three storage concerns, deliberately separated (see plan's Data & Persistence
Design section):

  state/current.json   overwritten after every user-confirmed action. This is the
                       fast path for "load a project" — one blob read.
  lineage/<ts>_<a>.json append-only, one immutable file per confirmed decision.
                       The audit trail and the rebuild-from-scratch recovery path.
  artifacts/...        large binaries (embeddings, linkage trees) and the LLM
                       response cache. Written the moment a paid LLM response
                       arrives, independently of whether the enclosing pipeline
                       run finishes, so a crash never means re-paying for
                       completed work.
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone

import numpy as np

from app.core.blob_store import BlobProjectStore
from app.models.project_state import ProjectMeta, ProjectState


class ProjectNotFound(LookupError):
    pass


class ProjectService:
    def __init__(self, store: BlobProjectStore | None = None):
        self.store = store or BlobProjectStore()

    # ---- state ----
    def load_state(self, client_slug: str, project_slug: str) -> ProjectState:
        raw = self.store.read_state(client_slug, project_slug)
        if raw is None:
            # No state yet (project just created) — synthesize an empty state
            # around the meta so callers always get a usable object.
            meta_raw = self.store.read_project_meta(client_slug, project_slug)
            if meta_raw is None:
                raise ProjectNotFound(f"{client_slug}/{project_slug}")
            return ProjectState(meta=ProjectMeta.model_validate(meta_raw))
        return ProjectState.model_validate(raw)

    def save_state(
        self,
        state: ProjectState,
        *,
        action: str,
        lineage_payload: dict | None = None,
    ) -> None:
        """Persist state, and record the decision in lineage.

        `action` names the user-confirmed decision that produced this state (e.g.
        'confirm-dedupe'). Intermediate/automatic compute should not call this —
        it should cache to artifacts/ and only reach here once the user confirms.
        """
        state.meta.updated_at = datetime.now(timezone.utc)
        client, project = state.meta.client_slug, state.meta.project_slug

        payload = state.model_dump(mode="json")
        self.store.write_state(client, project, payload)
        self.store.write_project_meta(client, project, state.meta.model_dump(mode="json"))
        self.store.write_lineage_entry(client, project, action, lineage_payload or {})

    # ---- raw input files ----
    def save_input_file(
        self, client_slug: str, project_slug: str, filename: str, data: bytes
    ) -> tuple[str, str]:
        """Store an uploaded file immutably. Returns (blob_path, content_hash)."""
        content_hash = hashlib.sha256(data).hexdigest()
        path = f"{project_slug}/inputs/raw/{filename}"
        self.store.write_bytes(client_slug, path, data)
        return path, content_hash

    def read_input_file(self, client_slug: str, path: str) -> bytes | None:
        return self.store.read_bytes(client_slug, path)

    # ---- numpy artifacts (embeddings, linkage trees) ----
    def save_array(self, client_slug: str, project_slug: str, name: str, arr: np.ndarray) -> str:
        buf = io.BytesIO()
        np.save(buf, arr, allow_pickle=False)
        path = f"{project_slug}/artifacts/{name}.npy"
        self.store.write_bytes(client_slug, path, buf.getvalue())
        return path

    def load_array(self, client_slug: str, path: str) -> np.ndarray | None:
        data = self.store.read_bytes(client_slug, path)
        if data is None:
            return None
        return np.load(io.BytesIO(data), allow_pickle=False)

    def save_index(self, client_slug: str, project_slug: str, name: str, index: list[str]) -> str:
        path = f"{project_slug}/artifacts/{name}_index.json"
        self.store.write_json(client_slug, path, {"ids": index})
        return path

    def load_index(self, client_slug: str, path: str) -> list[str] | None:
        raw = self.store.read_json(client_slug, path)
        return raw["ids"] if raw else None

    # ---- LLM response cache ----
    # Keyed by a hash of the exact request so a rerun with identical inputs is a
    # cache hit. This is what makes an interrupted pipeline resumable without
    # re-spending: the deterministic parts (embeddings, Ward tree) are cheap to
    # recompute, the LLM parts are not.
    def llm_cache_key(self, *parts: str) -> str:
        h = hashlib.sha256()
        for p in parts:
            h.update(p.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()[:32]

    def read_llm_cache(
        self, client_slug: str, project_slug: str, entity: str, stage: str, key: str
    ) -> dict | None:
        path = f"{project_slug}/artifacts/llm_cache/{entity}/{stage}/{key}.json"
        return self.store.read_json(client_slug, path)

    def write_llm_cache(
        self, client_slug: str, project_slug: str, entity: str, stage: str, key: str, payload: dict
    ) -> None:
        path = f"{project_slug}/artifacts/llm_cache/{entity}/{stage}/{key}.json"
        self.store.write_json(client_slug, path, payload)

    # ---- generated profile documents & exports ----
    def save_profile_html(self, client_slug: str, project_slug: str, profile_key: str, html: str) -> str:
        path = f"{project_slug}/profiles/{profile_key}/profile.html"
        self.store.write_bytes(client_slug, path, html.encode("utf-8"), content_type="text/html")
        return path

    def save_profile_content(self, client_slug: str, project_slug: str, profile_key: str, content: dict) -> str:
        path = f"{project_slug}/profiles/{profile_key}/profile.json"
        self.store.write_json(client_slug, path, content)
        return path

    def save_export(
        self, client_slug: str, project_slug: str, profile_key: str, filename: str, data: bytes, content_type: str
    ) -> str:
        path = f"{project_slug}/exports/{profile_key}/{filename}"
        self.store.write_bytes(client_slug, path, data, content_type=content_type)
        return path

    def read_export(self, client_slug: str, path: str) -> bytes | None:
        return self.store.read_bytes(client_slug, path)

    # ---- lineage / recovery ----
    def list_lineage(self, client_slug: str, project_slug: str) -> list[str]:
        prefix = f"{project_slug}/lineage/"
        return sorted(self.store.list_paths(client_slug, prefix))

    def read_lineage_entry(self, client_slug: str, path: str) -> dict | None:
        return self.store.read_json(client_slug, path)


def framework_hash(framework: dict) -> str:
    """Stable hash of a JE framework config, so a JE result can be flagged stale
    when the framework it was computed under has since changed."""
    canonical = json.dumps(framework, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
