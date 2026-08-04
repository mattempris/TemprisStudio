"""Reset a seeded demo project to its starting point.

Demonstrating repeat-and-invalidate is destructive by design: re-running a tier clears the
tiers above it and cascades through everything downstream. That is the feature. It also
means a demo project is single-use unless something can put it back, which is what this is.

**State alone is not enough, and that is the whole difficulty.** Re-clustering writes new
centroid and linkage arrays over the old ones. Restore the seeded state on top of those and
it describes 565 clusters while the centroid array has however many the demo chose — a
project that looks fine until something reads a row that is not there. So reset also repairs
the blobs:

  - blobs the demo created that the seed never had are deleted
  - blobs whose size no longer matches the manifest are re-copied from the reference project
  - the seeded state is written back over the current one

The reference project is read-only in that repair, so the coupling is one-directional. It
also means the reference must still exist; that is a deliberate trade against keeping a
second 121 MB pristine copy inside the demo container.

**The guard is the manifest, not a list of names.** Only a project seeded by
`scripts/seed_demo_project.py` has `demo/manifest.json`, so reset cannot touch a real
project however it is addressed. A slug allowlist would need maintaining and would fail
open on a typo; this fails closed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.core.blob_store import BlobProjectStore, client_container_name
from app.services import project_service
from app.services.project_service import ProjectService

router = APIRouter(prefix="/api/projects/{client_slug}/{project_slug}/demo", tags=["demo"])

SEED_STATE = "demo/seed-state.json"
MANIFEST = "demo/manifest.json"
# None of these is part of blob repair.
#
#   demo/     — the reset machinery itself.
#   state/    — the seed snapshot is its only authority. Letting the repair pass re-copy it
#               from the reference would move 42 MB for a blob overwritten a moment later,
#               and would leave the demo holding state naming the reference client if that
#               overwrite ever failed.
#   lineage/  — append-only audit history, not project content. Excluded in both directions:
#               reset must not delete a record of what happened, and reset's own entry must
#               not then be reported as drift — which it was, leaving every freshly-reset
#               project claiming to be dirty.
EXCLUDE = ("demo/", "state/", "lineage/")


def _prefix(project_slug: str) -> str:
    return f"job-architecture/{project_slug}/"


def _current(store: BlobProjectStore, client_slug: str, project_slug: str) -> dict[str, int]:
    cc = store.service_client.get_container_client(client_container_name(client_slug))
    base = _prefix(project_slug)
    return {
        b.name[len(base) :]: b.size
        for b in cc.list_blobs(name_starts_with=base)
        if not b.name[len(base) :].startswith(EXCLUDE)
    }


def _load_demo(client_slug: str, project_slug: str) -> tuple[ProjectService, dict, dict]:
    svc = ProjectService()
    manifest = svc.load_json(client_slug, f"{project_slug}/{MANIFEST}")
    seed = svc.load_json(client_slug, f"{project_slug}/{SEED_STATE}")
    if not manifest or not seed:
        raise HTTPException(
            409,
            f"{client_slug}/{project_slug} is not a seeded demo project — it has no "
            f"{MANIFEST}. Reset only works on projects created by "
            f"scripts/seed_demo_project.py.",
        )
    return svc, manifest, seed


@router.get("/status")
def demo_status(client_slug: str, project_slug: str) -> dict:
    """Whether this is a demo project, and how far it has drifted from its seed."""
    svc = ProjectService()
    manifest = svc.load_json(client_slug, f"{project_slug}/{MANIFEST}")
    if not manifest:
        return {"is_demo": False, "drifted": False}
    blobs = manifest.get("blobs", {})
    now = _current(svc.store, client_slug, project_slug)
    added = sorted(set(now) - set(blobs))
    removed = sorted(set(blobs) - set(now))
    changed = sorted(p for p in set(now) & set(blobs) if now[p] != blobs[p])
    return {
        "is_demo": True,
        "seeded_at": manifest.get("seeded_at"),
        "seeded_from": manifest.get("seeded_from"),
        "blobs_at_seed": len(blobs),
        "blobs_now": len(now),
        # Drift is what a reset would undo. Reported before anyone presses it.
        "drifted": bool(added or removed or changed),
        "added": added[:40],
        "removed": removed[:40],
        "changed": changed[:40],
        "counts": {"added": len(added), "removed": len(removed), "changed": len(changed)},
    }


@router.post("/reset")
def demo_reset(client_slug: str, project_slug: str, dry_run: bool = False) -> dict:
    """Put a demo project back to its seeded state.

    `dry_run=true` reports what would change without touching anything, which is worth
    having on an operation whose whole job is to discard work.
    """
    svc, manifest, seed = _load_demo(client_slug, project_slug)
    store = svc.store
    blobs: dict[str, int] = manifest.get("blobs", {})
    src_client = manifest.get("seeded_from", {}).get("client")
    src_project = manifest.get("seeded_from", {}).get("project")

    now = _current(store, client_slug, project_slug)
    to_delete = sorted(set(now) - set(blobs))
    to_restore = sorted(
        [p for p in set(blobs) & set(now) if now[p] != blobs[p]] + list(set(blobs) - set(now))
    )

    plan = {
        "client": client_slug,
        "project": project_slug,
        "delete": to_delete,
        "restore": to_restore,
        "counts": {"delete": len(to_delete), "restore": len(to_restore)},
        "state_restored": True,
        "seeded_from": manifest.get("seeded_from"),
    }
    if dry_run:
        return {**plan, "dry_run": True}

    if to_restore and not (src_client and src_project):
        raise HTTPException(
            409,
            "the manifest does not record where this demo was seeded from, so overwritten "
            f"blobs cannot be repaired: {to_restore[:5]}",
        )

    for rel in to_delete:
        store.delete_blob(client_slug, f"{project_slug}/{rel}")

    if to_restore:
        src = store.service_client.get_container_client(client_container_name(src_client))
        dst = store.service_client.get_container_client(client_container_name(client_slug))
        missing: list[str] = []
        for rel in to_restore:
            blob = src.get_blob_client(f"{_prefix(src_project)}{rel}")
            try:
                data = blob.download_blob().readall()
            except Exception:  # noqa: BLE001 — reported rather than aborting the reset
                missing.append(rel)
                continue
            dst.get_blob_client(f"{_prefix(project_slug)}{rel}").upload_blob(
                data, overwrite=True
            )
        plan["unrepairable"] = missing
        if missing:
            # Said out loud rather than swallowed: the project is back to its seeded state
            # apart from these, and pretending otherwise is how a demo fails mid-meeting.
            plan["warning"] = (
                f"{len(missing)} blobs could not be re-copied from "
                f"{src_client}/{src_project} and are left as they are"
            )

    store.write_state(client_slug, project_slug, seed)
    store.write_project_meta(client_slug, project_slug, seed["meta"])

    # Every process-level cache keyed on this project now describes the pre-reset world.
    # Imported here rather than at module scope to keep the route modules from importing
    # each other in a cycle.
    from app.api.routes import tiers as tier_routes
    from app.api.routes import workforce as wf_routes

    project_service.invalidate_state_cache(client_slug, project_slug)
    wf_routes._FACTS.pop((client_slug, project_slug), None)
    for cache in (tier_routes._TIER_CACHE, tier_routes._ANALYSIS_CACHE):
        for key in [k for k in cache if k[0] == client_slug and k[1] == project_slug]:
            cache.pop(key, None)

    # The lineage entry is written directly rather than by round-tripping through
    # save_state: state has already been written from the seed, and going through
    # save_state would re-serialise and re-upload all 42 MB of it just to get an audit line.
    store.write_lineage_entry(
        client_slug,
        project_slug,
        "reset-demo-project",
        {
            "deleted": len(to_delete),
            "restored": len(to_restore),
            "seeded_at": manifest.get("seeded_at"),
            "seeded_from": manifest.get("seeded_from"),
        },
    )
    plan["reset_at"] = datetime.now(timezone.utc).isoformat()
    return plan
