"""The demo reset's guard, its diff, and the one thing that can silently rot.

Reset is the only endpoint in this app whose purpose is to destroy work, so two properties
are worth pinning down in a test rather than trusting to review:

  1. **It cannot fire on a project that is not a seeded demo.** The guard is the presence of
     `demo/manifest.json`, not a list of slugs, so this checks a project without one is
     refused rather than reset.
  2. **The seeder's exclusion list matches the route's.** These live in two files and must
     agree. If the seeder records `state/current.json` in the manifest while the route
     excludes it from its listing, every reset thinks that blob has vanished and re-copies
     42 MB from the reference project — and would clobber the freshly-restored state with the
     reference's own if the ordering ever changed. Nothing else would notice; a demo would
     just start coming back wrong. So it is asserted by reading the seeder's source.

No blob, no network.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402

from app.api.routes import demo  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")


class FakeService:
    """Stands in for storage. `json` maps a blob path to its content."""

    def __init__(self, json_blobs: dict):
        self.json = json_blobs
        self.store = self

    def load_json(self, client_slug, path):
        return self.json.get(path)


def main() -> int:
    print("The guard: a project with no manifest cannot be reset")
    demo.ProjectService = lambda *a, **k: FakeService({})  # type: ignore[assignment]
    try:
        demo._load_demo("acme", "live-project")
        check("a project without a manifest is refused", False, "it was accepted")
    except HTTPException as e:
        check("a project without a manifest is refused", e.status_code == 409, f"HTTP {e.status_code}")
        check(
            "and the message says how a demo project is made",
            "seed_demo_project.py" in e.detail,
        )
    check(
        "status reports is_demo false rather than raising",
        demo.demo_status("acme", "live-project") == {"is_demo": False, "drifted": False},
    )

    # A manifest with no seed state is a half-seeded project, which must not be resettable
    # either — restoring blobs without state would leave arrays and state disagreeing.
    demo.ProjectService = lambda *a, **k: FakeService(  # type: ignore[assignment]
        {"p/demo/manifest.json": {"blobs": {}}}
    )
    try:
        demo._load_demo("c", "p")
        check("a manifest without a seed snapshot is refused", False, "it was accepted")
    except HTTPException as e:
        check("a manifest without a seed snapshot is refused", e.status_code == 409)

    print("\nThe exclusion list, which lives in two files and must agree")
    check(
        "the route excludes demo/, state/ and lineage/",
        set(demo.EXCLUDE) == {"demo/", "state/", "lineage/"},
        str(demo.EXCLUDE),
    )
    src = (Path(__file__).resolve().parents[1] / "scripts" / "seed_demo_project.py").read_text(
        encoding="utf-8"
    )
    m = re.search(r'startswith\(\((\s*"[^)]*?)\)\)', src)
    seeder = set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()
    check(
        "the seeder's manifest uses the same exclusions",
        seeder == set(demo.EXCLUDE),
        f"seeder={sorted(seeder)} route={sorted(demo.EXCLUDE)}",
    )

    print("\nWhat the exclusions buy")
    check(
        "state/ is excluded, so reset never re-copies 42 MB it is about to overwrite",
        "state/" in demo.EXCLUDE,
    )
    check(
        "lineage/ is excluded, so a reset's own audit entry is not counted as drift",
        "lineage/" in demo.EXCLUDE,
    )

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
