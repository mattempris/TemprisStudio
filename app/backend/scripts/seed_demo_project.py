"""Seed `client-fs-demo/full-ja` as a copy of the reference project.

Why a copy rather than demoing on `banking-demo/full-ja`: demonstrating the repeat-and-
invalidate behaviour is destructive by design. Re-running the task tier on the reference
project clears 21 task domains and 750 assessed clusters — the paid assessment run — and
marks four agent specs and the future-role design stale. That leaves either never pressing
the button in a demo, or rebuilding for hours afterwards. A disposable copy plus a reset
makes it a thing you can show twice in an hour.

It also keeps the reference project stable as the thing every measurement in this repo was
taken against, so a demo cannot silently move the baseline.

**FS-Demo is strictly derived.** Re-run this whenever the reference changes; never hand-edit
the copy. That makes drift a one-command fix rather than something discovered mid-demo.

State carries 25 blob paths (`*_blob_path`, `centroids_blob_path`, `linkage_blob_path`) and
they turn out **not** to need rewriting: they are project-relative (`full-ja/artifacts/...`)
and the client is passed separately at every call site, so keeping the same project slug in
a different container leaves them all correct. The rewrite runs only if the slugs differ,
and is asserted either way — a path still pointing at the source would leave the demo
reading the reference project's arrays, which would work, look fine, and mean the two were
never actually independent.

That was not obvious: the first version of this script rewrote unconditionally and then
failed its own assertion, because rewriting `full-ja/` to `full-ja/` is a no-op that the
leftover check cannot tell apart from a missed path.

Usage:
    python scripts/seed_demo_project.py            # plan only
    python scripts/seed_demo_project.py --apply
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.blob_store import BlobProjectStore, client_container_name  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402

SRC_CLIENT, SRC_PROJECT = "banking-demo", "full-ja"
DST_CLIENT, DST_PROJECT = "fs-demo", "full-ja"
PREFIX = "job-architecture"

# Copied verbatim. Everything the app reads at runtime lives under one of these.
COPY_PREFIXES = ("state/", "artifacts/", "profiles/", "workforce/", "inputs/", "project.json")
# Not copied: lineage is the reference project's audit trail and would be a lie under a
# different project's name, and the pre-repair state backup is 42 MB of history nothing
# reads.
SKIP = ("lineage/", "workforce/backups/")


def rewrite_paths(obj, src: str, dst: str, counter: dict):
    """Replace `<src>/` with `<dst>/` in every string that looks like a blob path.

    Scoped to strings containing `<src>/` rather than a blanket replace of the slug: the
    slug could legitimately appear inside a job description or a cluster name, and
    rewriting those would corrupt content to fix plumbing.
    """
    if isinstance(obj, dict):
        return {k: rewrite_paths(v, src, dst, counter) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rewrite_paths(v, src, dst, counter) for v in obj]
    if isinstance(obj, str) and f"{src}/" in obj:
        counter["n"] += 1
        return obj.replace(f"{src}/", f"{dst}/")
    return obj


def main() -> int:
    apply = "--apply" in sys.argv
    store = BlobProjectStore()
    svc = ProjectService(store)

    src_container = store.service_client.get_container_client(client_container_name(SRC_CLIENT))
    blobs = [
        b for b in src_container.list_blobs(name_starts_with=f"{PREFIX}/{SRC_PROJECT}/")
    ]
    rel = {b.name[len(f"{PREFIX}/{SRC_PROJECT}/") :]: b.size for b in blobs}
    keep = {
        r: s
        for r, s in rel.items()
        if r.startswith(COPY_PREFIXES) and not r.startswith(SKIP)
    }
    skipped = {r: s for r, s in rel.items() if r not in keep}

    print(f"source: {SRC_CLIENT}/{SRC_PROJECT} — {len(rel)} blobs, {sum(rel.values())/1e6:.1f} MB")
    print(f"  copying {len(keep)} blobs, {sum(keep.values())/1e6:.1f} MB")
    print(f"  skipping {len(skipped)} blobs, {sum(skipped.values())/1e6:.1f} MB "
          f"(lineage and the state backup)")
    print(f"target: {DST_CLIENT}/{DST_PROJECT}")

    raw = store.read_state(SRC_CLIENT, SRC_PROJECT)
    if raw is None:
        print("source has no state")
        return 1
    counter = {"n": 0}
    if SRC_PROJECT == DST_PROJECT:
        # Nothing to rewrite: stored paths are project-relative and the project slug is
        # unchanged, so every one of them is already correct under the new container.
        rewritten = raw
        print(f"  project slug unchanged ('{DST_PROJECT}'), so no path rewriting is needed")
    else:
        rewritten = rewrite_paths(raw, SRC_PROJECT, DST_PROJECT, counter)
        print(f"  {counter['n']} blob paths rewritten from '{SRC_PROJECT}/' to '{DST_PROJECT}/'")
        # A leftover reference would have the demo silently reading the reference
        # project's arrays. Only meaningful when the slug actually changed.
        leftover = re.findall(rf'"[^"]*{SRC_PROJECT}/[^"]*"', json.dumps(rewritten))
        if leftover:
            print(f"  REFUSING: {len(leftover)} paths still point at the source: {leftover[:3]}")
            return 1
        print("  no residual references to the source project")

    rewritten["meta"]["client_slug"] = DST_CLIENT
    rewritten["meta"]["project_slug"] = DST_PROJECT
    rewritten["meta"]["display_name"] = "FS Demo"

    # The client is never stored in a path, so a stale one would be a real defect.
    embedded_client = re.findall(rf'"[^"]*{SRC_CLIENT}[^"]*"', json.dumps(rewritten))
    embedded_client = [x for x in embedded_client if "client_slug" not in x]
    if embedded_client:
        print(f"  REFUSING: {len(embedded_client)} values name the source client: "
              f"{embedded_client[:3]}")
        return 1
    print("  no stored value names the source client")

    if not apply:
        print("\nDRY RUN — pass --apply to create the demo project")
        return 0

    store.ensure_client_container(DST_CLIENT)
    dst_container = store.service_client.get_container_client(client_container_name(DST_CLIENT))

    copied = 0
    for r in sorted(keep):
        if r == "state/current.json":
            continue  # written from the rewritten copy below
        src_blob = src_container.get_blob_client(f"{PREFIX}/{SRC_PROJECT}/{r}")
        data = src_blob.download_blob().readall()
        dst_container.get_blob_client(f"{PREFIX}/{DST_PROJECT}/{r}").upload_blob(
            data, overwrite=True
        )
        copied += 1
        if copied % 100 == 0:
            print(f"  {copied}/{len(keep)-1} blobs")

    store.write_state(DST_CLIENT, DST_PROJECT, rewritten)
    store.write_project_meta(DST_CLIENT, DST_PROJECT, rewritten["meta"])
    print(f"  {copied} blobs copied, state and meta written")

    # The seed doubles as the reset point: /demo/reset restores from here rather than
    # re-copying 165 MB from the reference project every time.
    store.write_json(DST_CLIENT, f"{DST_PROJECT}/demo/seed-state.json", rewritten)
    print("  seed snapshot written to demo/seed-state.json for reset")

    check = svc.load_state(DST_CLIENT, DST_PROJECT)
    print(f"\nverified: {check.meta.client_slug}/{check.meta.project_slug} "
          f"({check.meta.display_name})")
    print(f"  {len(check.job_profiles)} profiles, "
          f"{len(check.clustering_tiers.get('profile').names) if check.clustering_tiers.get('profile') else 0} "
          f"job profile clusters, {len(check.workforce.opportunity)} assessed task clusters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
