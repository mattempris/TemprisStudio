"""Azure Blob Storage project persistence.

IMPORTANT — storage model (confirmed by direct inspection of the live `temprisdev`
account, not assumed): each Tempris client is its own **container** named
`client-<slug>` (not a blob-name prefix within one shared container). Several of
these containers are already populated by an existing, live Tempris pipeline
(`inputs/`, `runs/`, `taxonomy/` at the container root). Tempris JAStudio must
never touch those paths — it owns exactly one new top-level subtree per client
container: `job-architecture/`.

Layout within `client-<slug>/job-architecture/`:

    <ProjectSlug>/
        project.json
        lineage/<ISO8601>_<action>.json
        state/current.json
        inputs/raw/<filename>
        artifacts/embeddings/<entity>_embeddings.npy (+ index.json)
        artifacts/clustering/<entity>_linkage_tree.npy (+ index.json)
        artifacts/llm_cache/<entity>/<stage>/<id>.json
        profiles/<profile_key>/...
        exports/<profile_key>/...

New-client onboarding: container creation **works** as of 2026-08-04 — verified by
creating `client-fs-demo` from this service principal. It did not when this module was
written, which is why `ensure_client_container()` still translates an AuthorizationFailure
into an actionable PermissionError: the grant lives in Azure RBAC rather than in this repo,
so it can be revoked without anything here changing, and an opaque SDK error at that point
would be a poor way to find out.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from app.core.config import Settings, get_settings

CLIENT_CONTAINER_PREFIX = "client-"
APP_SUBTREE = "job-architecture"


def client_container_name(client_slug: str) -> str:
    return f"{CLIENT_CONTAINER_PREFIX}{client_slug}"


_service_client: BlobServiceClient | None = None
_service_lock = threading.Lock()


def _shared_service_client(settings: Settings) -> BlobServiceClient:
    """One BlobServiceClient for the process, built once.

    Every route constructs a `ProjectService`, which constructed a `BlobProjectStore`,
    which built a fresh `ClientSecretCredential` and `BlobServiceClient` — and a new
    credential means a new token fetch from Entra on the first blob call. Measured
    against the opportunity endpoints, which read the 9MB state blob: 3.08s before,
    2.19s after, so the discarded credential was costing ~0.9s per request.

    It costs nothing on the graph-cut endpoints, which never touch blob at all once
    the fact table is cached — those measure 16-160ms either way. Worth saying because
    it is easy to attribute a slow round trip to the server: the 220ms these appeared
    to take over `localhost` was curl trying IPv6 first against an IPv4-only bind, and
    had nothing to do with this code.

    Sharing the client is the Azure SDK's own recommendation — both it and the
    credential are thread-safe, and the credential caches and refreshes the token —
    so the fan-out stages that hit this from a thread pool are fine.
    """
    global _service_client
    if _service_client is None:
        with _service_lock:
            if _service_client is None:
                _service_client = BlobServiceClient(
                    account_url=f"https://{settings.azure_blob_account}.blob.core.windows.net",
                    credential=ClientSecretCredential(
                        tenant_id=settings.azure_tenant_id,
                        client_id=settings.azure_client_id,
                        client_secret=settings.azure_client_secret,
                    ),
                )
    return _service_client


class BlobProjectStore:
    """Thin wrapper around the Azure Blob SDK for project state I/O.

    Every path this class writes to is scoped under `<client container>/job-architecture/`.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.service_client = _shared_service_client(self.settings)

    # ---- client/container discovery ----
    def list_clients(self) -> list[str]:
        """All existing client-<slug> containers, returned as bare slugs."""
        containers = self.service_client.list_containers(name_starts_with=CLIENT_CONTAINER_PREFIX)
        return sorted(c.name[len(CLIENT_CONTAINER_PREFIX) :] for c in containers)

    def client_container_exists(self, client_slug: str) -> bool:
        try:
            self.service_client.get_container_client(client_container_name(client_slug)).get_container_properties()
            return True
        except ResourceNotFoundError:
            return False

    def ensure_client_container(self, client_slug: str) -> None:
        """Create the client-<slug> container if it doesn't exist.

        Raises PermissionError with an actionable message if the service principal
        lacks account-level container-create rights (observed default state — see
        module docstring). Existing containers are left untouched (idempotent).
        """
        name = client_container_name(client_slug)
        if self.client_container_exists(name.removeprefix(CLIENT_CONTAINER_PREFIX)):
            return
        try:
            self.service_client.create_container(name)
        except ResourceExistsError:
            pass
        except HttpResponseError as e:
            if "AuthorizationFailure" in str(e) or getattr(e, "status_code", None) == 403:
                raise PermissionError(
                    f"Cannot create container '{name}': the app's service principal "
                    f"lacks container-create rights on {self.settings.azure_blob_account}. "
                    "An admin must either pre-create this container or grant the service "
                    "principal the 'Storage Blob Data Contributor' role at the STORAGE "
                    "ACCOUNT scope (not a per-container scope)."
                ) from e
            raise

    # ---- generic blob I/O (all paths are relative to job-architecture/ within a client container) ----
    def _container(self, client_slug: str):
        return self.service_client.get_container_client(client_container_name(client_slug))

    def _app_path(self, path: str) -> str:
        return f"{APP_SUBTREE}/{path}"

    def write_json(self, client_slug: str, path: str, data: dict, *, overwrite: bool = True) -> None:
        payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        blob = self._container(client_slug).get_blob_client(self._app_path(path))
        blob.upload_blob(payload, overwrite=overwrite, content_settings=ContentSettings(content_type="application/json"))

    def read_json(self, client_slug: str, path: str) -> dict | None:
        try:
            data = self._container(client_slug).get_blob_client(self._app_path(path)).download_blob().readall()
        except ResourceNotFoundError:
            return None
        return json.loads(data)

    def write_bytes(self, client_slug: str, path: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        blob = self._container(client_slug).get_blob_client(self._app_path(path))
        blob.upload_blob(data, overwrite=True, content_settings=ContentSettings(content_type=content_type))

    def read_bytes(self, client_slug: str, path: str) -> bytes | None:
        try:
            return self._container(client_slug).get_blob_client(self._app_path(path)).download_blob().readall()
        except ResourceNotFoundError:
            return None

    def blob_exists(self, client_slug: str, path: str) -> bool:
        """Existence without downloading — a HEAD, not a GET.

        Used by the stage summary, which is polled: reading a multi-megabyte
        embedding matrix just to answer "has this been built" would make every
        poll pay for the whole array.
        """
        return self._container(client_slug).get_blob_client(self._app_path(path)).exists()

    def delete_blob(self, client_slug: str, path: str) -> None:
        try:
            self._container(client_slug).get_blob_client(self._app_path(path)).delete_blob()
        except ResourceNotFoundError:
            pass

    def list_paths(self, client_slug: str, prefix: str = "") -> list[str]:
        container = self._container(client_slug)
        full_prefix = self._app_path(prefix)
        return [b.name[len(APP_SUBTREE) + 1 :] for b in container.list_blobs(name_starts_with=full_prefix)]

    # ---- project-level helpers (all under job-architecture/<project_slug>/) ----
    def list_projects(self, client_slug: str) -> list[str]:
        container = self._container(client_slug)
        prefix = self._app_path("")
        seen: set[str] = set()
        for blob in container.walk_blobs(name_starts_with=prefix, delimiter="/"):
            name = blob.name[len(prefix) :].rstrip("/")
            if name:
                seen.add(name)
        return sorted(seen)

    def write_lineage_entry(self, client_slug: str, project_slug: str, action: str, payload: dict) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
        path = f"{project_slug}/lineage/{ts}_{action}.json"
        self.write_json(client_slug, path, {"action": action, "timestamp": ts, **payload}, overwrite=False)
        return path

    def write_state(self, client_slug: str, project_slug: str, state: dict) -> None:
        self.write_json(client_slug, f"{project_slug}/state/current.json", state)

    def read_state(self, client_slug: str, project_slug: str) -> dict | None:
        return self.read_json(client_slug, f"{project_slug}/state/current.json")

    def state_etag(self, client_slug: str, project_slug: str) -> str | None:
        """The state blob's current ETag, without downloading it.

        One metadata call against a 42.5 MB blob, which is what makes caching the parsed
        state safe: another process writing is detected rather than assumed away.
        """
        path = self._app_path(f"{project_slug}/state/current.json")
        try:
            return self._container(client_slug).get_blob_client(path).get_blob_properties().etag
        except ResourceNotFoundError:
            return None

    def read_state_with_etag(
        self, client_slug: str, project_slug: str
    ) -> tuple[dict | None, str | None]:
        """State and the ETag it was read at, in one round trip."""
        path = self._app_path(f"{project_slug}/state/current.json")
        try:
            stream = self._container(client_slug).get_blob_client(path).download_blob()
        except ResourceNotFoundError:
            return None, None
        data = stream.readall()
        return json.loads(data.decode("utf-8")), stream.properties.etag

    def write_project_meta(self, client_slug: str, project_slug: str, meta: dict) -> None:
        self.write_json(client_slug, f"{project_slug}/project.json", meta)

    def read_project_meta(self, client_slug: str, project_slug: str) -> dict | None:
        return self.read_json(client_slug, f"{project_slug}/project.json")


def smoke_test(client_slug: str = "mercer-demo") -> None:
    """Read-only client discovery + a scoped write/read/delete under job-architecture/
    in an existing, clearly non-production-named client container ('-demo' suffix).

    Run standalone: `python -m app.core.blob_store`
    """
    store = BlobProjectStore()
    print(f"Account: {store.settings.azure_blob_account}")

    clients = store.list_clients()
    print(f"Found {len(clients)} existing client containers, e.g.: {clients[:8]}")
    assert client_slug in clients, f"expected test client '{client_slug}' to already exist"

    test_path = "_smoke_test/ping.json"
    payload = {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}
    store.write_json(client_slug, test_path, payload)
    print(f"Write OK under client-{client_slug}/job-architecture/{test_path}")

    read_back = store.read_json(client_slug, test_path)
    assert read_back is not None and read_back["ok"] is True, "read-back mismatch"
    print("Read OK:", read_back)

    store.delete_blob(client_slug, test_path)
    print("Delete OK.")

    print("\nSMOKE TEST PASSED — job-architecture/ subtree is writable in existing client containers.")
    print("NOTE: container creation for brand-new clients still needs an account-scope RBAC grant (see module docstring).")


if __name__ == "__main__":
    smoke_test()
