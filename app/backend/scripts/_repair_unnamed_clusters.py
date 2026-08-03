"""Name any cluster that has members but no name, on every tier of every hierarchy.

An unnamed cluster is not cosmetic: the tier above iterates the *names*, so its
members are dropped out of the hierarchy entirely and end up with parent id -1. This
repairs state written before naming was made complete-by-construction.

Names are derived from the members themselves — no LLM, no spend, deterministic.
Pass --apply to write; without it, reports only.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.clustering import tier_state  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402

CLIENT, PROJECT = "banking-demo", "full-ja"
APPLY = "--apply" in sys.argv


def main() -> int:
    svc = ProjectService()
    state = svc.load_state(CLIENT, PROJECT)
    label_for_entity = {
        "job": lambda: {},  # job members are record ids; handled below
        "skill": lambda: {r.id: r.name for r in state.skills.inferred},
        "task": lambda: {r.id: r.name for r in state.tasks.inferred},
    }
    changed: list[str] = []

    for entity in tier_state.ENTITIES:
        tiers = tier_state.tiers_of(state, entity)
        base_names = label_for_entity[entity]()
        for tier in tier_state.ORDER:
            rec = tiers.get(tier)
            if rec is None or not rec.names:
                continue
            members_by_cluster: dict[int, list[str]] = {}
            for m in rec.members:
                members_by_cluster.setdefault(m.final_cluster_id, []).append(m.item_id)

            unnamed = sorted(set(members_by_cluster) - set(rec.names))
            dupes: dict[str, list[int]] = {}
            for cid, n in rec.names.items():
                dupes.setdefault(n.strip().lower(), []).append(cid)
            collisions = {n: ids for n, ids in dupes.items() if len(ids) > 1}

            if not unnamed and not collisions:
                continue
            print(f"\n{entity}/{tier}: {len(unnamed)} unnamed, {len(collisions)} duplicated name(s)")

            for cid in unnamed:
                ids = members_by_cluster[cid]
                if tier == "profile" and entity != "job":
                    parts = [base_names.get(i, i) for i in ids]
                elif tier == "profile":
                    titles = {r.id: r.job_title for r in state.raw_records}
                    groups = {g.group_id: g.member_ids for g in state.dedupe_groups}
                    parts = [titles.get(groups.get(i, [i])[0], i) for i in ids]
                else:
                    below = tiers[tier_state.CHILD_OF[tier]].names
                    parts = [below.get(int(i.split(":")[1]), i) for i in ids if ":" in i]
                # The most common member name is the most defensible label for the group.
                counts: dict[str, int] = {}
                for x in parts:
                    counts[x] = counts.get(x, 0) + 1
                name = max(counts, key=lambda k: (counts[k], -len(k))) if counts else f"Cluster {cid}"
                rec.names[cid] = name
                changed.append(f"{entity}/{tier} cluster {cid} -> {name!r} ({len(ids)} members)")
                print(f"  named cluster {cid} ({len(ids)} members) -> {name!r}")

            for name, ids in collisions.items():
                for n, cid in enumerate(sorted(ids)[1:], start=2):
                    rec.names[cid] = f"{rec.names[cid]} ({n})"
                    changed.append(f"{entity}/{tier} cluster {cid} de-duplicated -> {rec.names[cid]!r}")
                    print(f"  cluster {cid} shared {name!r} -> {rec.names[cid]!r}")

            rec.k = len(rec.names)
        tier_state.rebuild_denormalised(state, entity)

    if not changed:
        print("nothing to repair")
        return 0

    print(f"\n{len(changed)} change(s)")
    if not APPLY:
        print("dry run — pass --apply to write")
        return 0

    svc.save_state(
        state,
        action="repair-unnamed-clusters",
        lineage_payload={"changes": changed},
    )
    print("written to blob")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
