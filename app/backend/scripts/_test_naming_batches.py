"""Naming a large level: batched, progress-reporting, distinctiveness preserved."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.clustering import naming  # noqa: E402

calls: list[dict] = []


def fake_complete_json(prompt, *, system, json_schema, effort, max_tokens):
    calls.append({"prompt": prompt, "max_tokens": max_tokens, "system": system})
    # Echo back a name for every id the prompt mentions.
    ids = [int(line.split("]")[0][1:]) for line in prompt.splitlines() if line.startswith("[")]
    return {"clusters": [{"id": i, "name": f"Group {i}"} for i in ids]}


def check(label, ok):
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    naming.llm.complete_json = fake_complete_json  # type: ignore[assignment]
    passed = True

    n = 74
    blocks = [naming.build_cluster_block(i, [f"role {i}a", f"role {i}b"]) for i in range(n)]
    seen: list[tuple[int, int]] = []
    names = naming.name_level("job", "profile", blocks, n, progress=lambda d, t: seen.append((d, t)))

    print(f"=== {n} clusters, batch size {naming.NAME_BATCH} ===")
    passed &= check(f"3 calls made ({len(calls)})", len(calls) == 3)
    passed &= check(f"all {n} named ({len(names)})", len(names) == n and set(names) == set(range(n)))
    passed &= check(f"progress reported per batch {seen}", seen == [(30, n), (60, n), (74, n)])
    passed &= check(
        f"budget scales with batch size {[c['max_tokens'] for c in calls]}",
        [c["max_tokens"] for c in calls] == [15000, 15000, naming._token_budget(14)],
    )
    passed &= check("first call carries no already-used list", "already in use" not in calls[0]["prompt"])
    passed &= check("second call forwards batch 1's names", "Group 0" in calls[1]["prompt"])
    passed &= check("third call forwards batch 1+2's names", "Group 45" in calls[2]["prompt"])
    batch2_ids = [
        int(ln.split("]")[0][1:]) for ln in calls[1]["prompt"].splitlines() if ln.startswith("[")
    ]
    passed &= check(
        f"batch 2 asks only for ids 30-59 ({len(batch2_ids)} ids)",
        batch2_ids == list(range(30, 60)),
    )

    # A level that fits in one call must behave exactly as before.
    calls.clear()
    small = [naming.build_cluster_block(i, [f"x{i}"]) for i in range(8)]
    names = naming.name_level("job", "family", small, 8)
    print("=== 8 clusters (single batch) ===")
    passed &= check("one call", len(calls) == 1)
    passed &= check("no already-used preamble", "already in use" not in calls[0]["prompt"])
    passed &= check("all named", len(names) == 8)
    passed &= check(
        f"budget {calls[0]['max_tokens']} exceeds the old fixed 4000",
        calls[0]["max_tokens"] > 4000,
    )

    print("\n" + ("PASSED" if passed else "FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
