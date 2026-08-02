"""Resolve cluster -> underlying source job titles, against the live project state."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes import tiers as R  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402

CLIENT, PROJECT = "banking-demo", "full-ja"


def main() -> int:
    svc = ProjectService()
    state = svc.load_state(CLIENT, PROJECT)
    print(f"{CLIENT}/{PROJECT}: {len(state.raw_records)} raw, "
          f"{len(state.dedupe_groups)} groups, {len(state.normalized_profiles)} normalised, "
          f"tiers confirmed: {sorted(state.clustering_tiers)}")

    m = R._title_map(state, "job")
    prof_keys = [k for k in m if not k.startswith(("profile:", "category:", "family:"))]
    print(f"\nresolved ids: {len(m)} total, {len(prof_keys)} normalised-job ids")

    total_titles = sum(len(m[k]) for k in prof_keys)
    print(f"titles reachable from normalised jobs: {total_titles} (raw records: {len(state.raw_records)})")

    multi = [k for k in prof_keys if len(m[k]) > 1]
    print(f"ids expanding to >1 title (dedupe groups): {len(multi)}")
    for k in multi[:3]:
        print(f"  {k} -> {m[k]}")
    for k in prof_keys[:3]:
        if k not in multi:
            print(f"  {k} -> {m[k]}")

    ok = total_titles == len(state.raw_records)
    print(f"\n{'OK  ' if ok else 'FAIL'}  every raw record is reachable exactly once")

    # Skills resolve to their own names rather than through dedupe groups.
    if state.skills.inferred:
        ms = R._title_map(state, "skill")
        base = [k for k in ms if not k.startswith(("profile:", "category:", "family:"))]
        good = len(base) == len(state.skills.inferred) and all(len(ms[k]) == 1 for k in base)
        print(f"\n  {'OK  ' if good else 'FAIL'}  skill entity: {len(base)} skills resolve "
              f"to one name each, e.g. {ms[base[0]]}")
        ok &= good

    for tier in ("profile", "category", "family"):
        rec = state.clustering_tiers.get(tier)
        if rec is None:
            print(f"  --    {tier} tier not confirmed, nothing to resolve")
            continue
        per = {cid: len(m.get(f"{tier}:{cid}", [])) for cid in sorted(rec.names)}
        tot = sum(per.values())
        good = tot == len(state.raw_records)
        ok &= good
        print(f"  {'OK  ' if good else 'FAIL'}  {tier}: {len(per)} clusters, {tot} titles total")
        for cid in list(per)[:2]:
            print(f"          [{cid}] {rec.names[cid]!r} ({per[cid]}) e.g. "
                  f"{sorted(m[f'{tier}:{cid}'])[:4]}")

    print("\n" + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
