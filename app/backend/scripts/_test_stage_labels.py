"""Every stage label a route uses must survive a real job end to end.

This exists because renaming the labels passed an import check and a route-listing
check and still broke five stages: the label is validated deep inside message
construction, so it only fails once the job is running — and it then broke the
error report too, so the UI showed nothing at all.

Runs a trivial job per label through the real orchestrator, emitting each message
type. No GPU, no LLM, no network.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from app.models.messages import STAGE_NAMES
from app.services.orchestrator import ProgressReporter, get_registry, run_job

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROUTES = Path("app/api/routes")


def labels_used_by_routes() -> set[str]:
    """Scrape the labels the routes actually pass, so a new route that invents one
    is caught here rather than by a user pressing the button."""
    found: set[str] = set()
    for path in ROUTES.glob("*.py"):
        for m in re.finditer(r'_start_job\([^,]+,\s*[^,]+,\s*"([a-z_]+)"', path.read_text(encoding="utf-8")):
            found.add(m.group(1))
    return found


async def exercise(stage: str) -> list[str]:
    """Run a job that emits every message type, and return the types seen."""
    job = get_registry().create("test-client", f"test-{stage}", stage)

    def work(reporter: ProgressReporter) -> dict:
        reporter.message("a status line with no total")
        reporter.stage_start(3, "starting")
        reporter.progress(1, 3, "working")
        reporter.stage_complete({"ok": 1})
        return {"ok": 1}

    await run_job(job, work)
    if job.status != "completed":
        raise AssertionError(f"stage {stage!r}: job {job.status} — {job.error}")
    return [m["type"] for m in job.history]


async def exercise_failure(stage: str) -> dict:
    """A job that raises must still produce a readable error message."""
    job = get_registry().create("test-client", f"fail-{stage}", stage)

    def work(reporter: ProgressReporter) -> dict:
        raise RuntimeError("deliberate failure")

    await run_job(job, work)
    assert job.status == "failed", job.status
    errors = [m for m in job.history if m.get("type") == "error"]
    assert errors, f"stage {stage!r}: failed job produced no error message"
    return errors[-1]


async def main() -> int:
    used = labels_used_by_routes()
    print(f"labels used by routes: {sorted(used)}")
    print(f"labels allowed by schema: {sorted(STAGE_NAMES)}\n")

    unknown = used - STAGE_NAMES
    if unknown:
        print(f"FAIL: routes use labels the schema rejects: {sorted(unknown)}")
        return 1

    for stage in sorted(STAGE_NAMES):
        types = await exercise(stage)
        err = await exercise_failure(stage)
        used_marker = "" if stage in used else "  (not currently used by a route)"
        print(f"  {stage:<10} ok — {len(types)} messages {types}{used_marker}")
        assert "error" == err["type"] and "deliberate failure" in err["message"], err

    print("\nfailure path reports a usable message for every stage: OK")

    # The guard must reject an unlisted label at creation, not mid-run.
    try:
        get_registry().create("test-client", "bad", "not_a_stage")
    except ValueError as e:
        print(f"unknown label rejected at creation: {str(e)[:80]}...")
    else:
        print("FAIL: an unknown stage label was accepted")
        return 1

    print("\nSTAGE LABEL TESTS PASSED (no GPU, no LLM)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
