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
import threading
from collections import OrderedDict
from datetime import datetime, timezone

import numpy as np

from app.core.blob_store import BlobProjectStore
from app.models.project_state import DesignedJobRecord, ProjectMeta, ProjectState


class ProjectNotFound(LookupError):
    pass


# Designed jobs live beside state rather than inside it.
#
# Every other writer of state/current.json is a step boundary — confirm the mapping, confirm
# the dedupe, generate the agents. Those already take seconds, so re-uploading the whole tree
# is invisible inside them. Saving a designed job is not that: it is a button in a workbench
# that someone presses after every rearrangement, and it was re-uploading 42.5 MB to persist
# about 8 KB. On a domestic uplink that is a single PUT that runs long enough for the Azure SDK
# to abort it, which is how this was found — every save returned 500.
#
# So `work_design.jobs` is the one part of state with its own blob: written alone, and
# hydrated back onto the state object inside `load_state` so that every reader — the routes,
# the workbook exporter, lineage — keeps reading `state.work_design.jobs` and cannot tell the
# difference. The settings that sit alongside it in `WorkDesignState` (the uplift, the
# oversight fractions) stay in state, because they change at most once a project.
DESIGNED_JOBS_BLOB = "work-design/jobs"


# Parsed state per (client, project), with the ETag it was read at. Shared across every
# ProjectService instance because they are constructed per request and the point is to not
# re-download per request.
#
# Bounded to a handful of projects: a session works on one, and an unbounded cache of
# 42 MB objects is a memory leak with extra steps.
_STATE_CACHE: "OrderedDict[tuple[str, str], tuple[str, ProjectState]]" = OrderedDict()
_STATE_CACHE_MAX = 4
_STATE_LOCK = threading.Lock()


def invalidate_state_cache(client_slug: str | None = None, project_slug: str | None = None) -> None:
    """Drop cached state. Called after a write, and available to tests."""
    with _STATE_LOCK:
        if client_slug is None or project_slug is None:
            _STATE_CACHE.clear()
        else:
            _STATE_CACHE.pop((client_slug, project_slug), None)


class ProjectService:
    def __init__(self, store: BlobProjectStore | None = None):
        self.store = store or BlobProjectStore()

    # ---- state ----
    def load_state(self, client_slug: str, project_slug: str) -> ProjectState:
        """The project's state, from cache when the blob has not changed.

        Returns a deep copy, always. Callers mutate what they get back — every stage does
        `fresh = load_state(...)`, edits it and saves — and handing out the cached object
        would let one request's half-finished edits leak into the next one's read.
        """
        key = (client_slug, project_slug)
        with _STATE_LOCK:
            cached = _STATE_CACHE.get(key)
        if cached is not None:
            etag, state = cached
            if self.store.state_etag(client_slug, project_slug) == etag:
                with _STATE_LOCK:
                    _STATE_CACHE.move_to_end(key)
                return self._with_designed_jobs(
                    client_slug, project_slug, state.model_copy(deep=True)
                )

        raw, etag = self.store.read_state_with_etag(client_slug, project_slug)
        if raw is None:
            # No state yet (project just created) — synthesize an empty state
            # around the meta so callers always get a usable object. Not cached: there is
            # no ETag to revalidate against.
            meta_raw = self.store.read_project_meta(client_slug, project_slug)
            if meta_raw is None:
                raise ProjectNotFound(f"{client_slug}/{project_slug}")
            return ProjectState(meta=ProjectMeta.model_validate(meta_raw))

        state = ProjectState.model_validate(raw)
        if etag:
            with _STATE_LOCK:
                _STATE_CACHE[key] = (etag, state)
                _STATE_CACHE.move_to_end(key)
                while len(_STATE_CACHE) > _STATE_CACHE_MAX:
                    _STATE_CACHE.popitem(last=False)
        return self._with_designed_jobs(client_slug, project_slug, state.model_copy(deep=True))

    def _with_designed_jobs(
        self, client_slug: str, project_slug: str, state: ProjectState
    ) -> ProjectState:
        """Hydrate designed jobs from their own blob onto a freshly-copied state.

        Read on every load rather than cached with the state, because the whole point of the
        split is that a job write does not touch state — so the state ETag does not move when
        jobs change, and a cached copy would go stale invisibly. The blob is a few hundred KB
        against the 42.5 MB it rides alongside, so this is noise on a cache miss and an
        acceptable small read on a cache hit.

        A project with no jobs blob is the normal case, not an error: nothing is designed yet,
        or the project predates this studio. Either way, leave whatever the state carried —
        which is how a state blob written before the split still surfaces its jobs.
        """
        raw = self.store.read_json(client_slug, f"{project_slug}/{DESIGNED_JOBS_BLOB}.json")
        if raw is None:
            return state
        state.work_design.jobs = [
            DesignedJobRecord.model_validate(j) for j in raw.get("jobs", [])
        ]
        return state

    def save_designed_jobs(
        self,
        state: ProjectState,
        *,
        action: str,
        lineage_payload: dict | None = None,
    ) -> None:
        """Persist only the designed jobs, and record the decision in lineage.

        The counterpart to `save_state` for the one part of state that a user edits
        interactively. Deliberately does *not* invalidate the state cache: state did not
        change, and dropping it would make the next read pay for a 42.5 MB download to
        observe an edit that is not in there.
        """
        client, project = state.meta.client_slug, state.meta.project_slug
        self.store.write_json(
            client,
            f"{project}/{DESIGNED_JOBS_BLOB}.json",
            {"jobs": [j.model_dump(mode="json") for j in state.work_design.jobs]},
        )
        state.meta.updated_at = datetime.now(timezone.utc)
        self.store.write_project_meta(client, project, state.meta.model_dump(mode="json"))
        self.store.write_lineage_entry(client, project, action, lineage_payload or {})

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
        # Designed jobs have their own blob, so they must not also be written here — two homes
        # for one list is two versions of it the moment either write fails. `load_state`
        # hydrates them straight back, so a caller never sees the gap.
        payload.get("work_design", {}).pop("jobs", None)
        self.store.write_state(client, project, payload)
        # The blob's ETag has changed, so the next read would re-download anyway. Dropping
        # it here means the very next reader does not pay for a stale-cache round trip.
        invalidate_state_cache(client, project)
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

    def array_exists(self, client_slug: str, project_slug: str, name: str) -> bool:
        """Whether a cached array has been built, without downloading it."""
        return self.store.blob_exists(client_slug, f"{project_slug}/artifacts/{name}.npy")

    def load_array(self, client_slug: str, path: str) -> np.ndarray | None:
        data = self.store.read_bytes(client_slug, path)
        if data is None:
            return None
        return np.load(io.BytesIO(data), allow_pickle=False)

    # ---- arbitrary derived JSON artefacts ----
    # For things too large to belong in the state blob, which is read on nearly
    # every request. `name` is a path relative to the project, so a caller can
    # namespace its own subtree (e.g. "workforce/graph_facts").
    def save_json(self, client_slug: str, project_slug: str, name: str, data: dict) -> str:
        path = f"{project_slug}/{name}.json"
        self.store.write_json(client_slug, path, data)
        return path

    def load_json(self, client_slug: str, path: str) -> dict | None:
        return self.store.read_json(client_slug, path)

    def json_exists(self, client_slug: str, path: str) -> bool:
        return self.store.blob_exists(client_slug, path)

    def save_index(
        self,
        client_slug: str,
        project_slug: str,
        name: str,
        index: list[str],
        *,
        model_fingerprint: str | None = None,
    ) -> str:
        """Save the row order for a cached embedding matrix.

        `model_fingerprint` records which build of the embedding model produced
        those vectors, so a later load can refuse to mix them with output from a
        replaced model. See EmbeddingService.fingerprint.
        """
        path = f"{project_slug}/artifacts/{name}_index.json"
        payload: dict = {"ids": index}
        if model_fingerprint:
            payload["model_fingerprint"] = model_fingerprint
        self.store.write_json(client_slug, path, payload)
        return path

    def load_index(self, client_slug: str, path: str) -> list[str] | None:
        raw = self.store.read_json(client_slug, path)
        return raw["ids"] if raw else None

    def load_index_fingerprint(self, client_slug: str, path: str) -> str | None:
        """The embedding-model fingerprint stored with an index, or None for an
        index written before fingerprinting (treated as unknown, not as valid)."""
        raw = self.store.read_json(client_slug, path)
        return raw.get("model_fingerprint") if raw else None

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
