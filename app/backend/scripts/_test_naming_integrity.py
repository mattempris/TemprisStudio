"""Naming must return a name for every cluster, and no two the same.

Both failures are silent and both corrupt the hierarchy: an unnamed cluster is
dropped by the tier above (its members end up with parent -1), and two clusters
sharing a name are indistinguishable in every view and read as one group listed
twice. Both were observed on a real 750-cluster task taxonomy.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.clustering import naming  # noqa: E402

calls: list[str] = []


def check(label, ok):
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    passed = True
    n = 8
    blocks = [naming.build_cluster_block(i, [f"task {i} alpha", f"task {i} beta"]) for i in range(n)]

    print("=== a cluster the model skips is still named ===")
    def skips_one(prompt, *, system, json_schema, effort, max_tokens):
        calls.append(prompt)
        ids = [int(l.split("]")[0][1:]) for l in prompt.splitlines() if l.startswith("[")]
        # Never names cluster 5, on either the first call or the retry.
        return {"clusters": [{"id": i, "name": f"Group {i}"} for i in ids if i != 5]}

    naming.llm.complete_json = skips_one  # type: ignore[assignment]
    calls.clear()
    names = naming.name_level("task", "profile", blocks, n)
    passed &= check(f"every cluster named ({len(names)}/{n})", set(names) == set(range(n)))
    passed &= check(f"the skipped one got a fallback from its own members: {names[5]!r}",
                    "task 5" in names[5].lower())
    passed &= check(f"it retried before falling back ({len(calls)} calls)", len(calls) == 2)
    retry_ids = [int(l.split("]")[0][1:]) for l in calls[1].splitlines() if l.startswith("[")]
    passed &= check(f"the retry asked only for the missing id ({retry_ids})", retry_ids == [5])

    print("=== duplicate names are made distinct ===")
    def all_same(prompt, *, system, json_schema, effort, max_tokens):
        ids = [int(l.split("]")[0][1:]) for l in prompt.splitlines() if l.startswith("[")]
        return {"clusters": [{"id": i, "name": "Sales Pipeline Tracking"} for i in ids]}

    naming.llm.complete_json = all_same  # type: ignore[assignment]
    names = naming.name_level("task", "profile", blocks, n)
    lowered = [v.strip().lower() for v in names.values()]
    passed &= check(f"all {n} named", len(names) == n)
    passed &= check(f"all distinct ({len(set(lowered))} unique)", len(set(lowered)) == n)
    passed &= check(f"first keeps the clean name: {names[0]!r}", names[0] == "Sales Pipeline Tracking")
    passed &= check(f"the rest are suffixed visibly: {names[1]!r}", names[1].endswith("(2)"))

    print("=== a clean response is left exactly alone ===")
    def clean(prompt, *, system, json_schema, effort, max_tokens):
        ids = [int(l.split("]")[0][1:]) for l in prompt.splitlines() if l.startswith("[")]
        return {"clusters": [{"id": i, "name": f"Doing Thing {i}"} for i in ids]}

    naming.llm.complete_json = clean  # type: ignore[assignment]
    names = naming.name_level("task", "profile", blocks, n)
    passed &= check("no renaming, no suffixes",
                    all(names[i] == f"Doing Thing {i}" for i in range(n)))

    print("=== task naming rules reach the prompt ===")
    sysmsg = naming._build_system_prompt("task", "profile", has_parent_context=False)
    for needle, why in [("active voice", "verb voice"), ("NO adjectives", "adjective ban"),
                        ("Be terse", "terseness"), ("single underlying task", "single-task adoption")]:
        passed &= check(f"{why} present", needle in sysmsg)

    print("\n" + ("PASSED" if passed else "FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
