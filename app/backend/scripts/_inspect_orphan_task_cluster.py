"""What exactly is the parentless task cluster, and what references it.

Read-only. Run before the purge so the blast radius is known rather than assumed:
removing a task cluster touches the tier records, the denormalised clustering view, the
inferred tasks underneath it, every Workforce Studio artefact keyed on it, and — the
easy one to miss — the affected roles' task proportions, which are guaranteed to sum to
100 and will not once tasks are removed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.project_service import ProjectService  # noqa: E402

CLIENT, PROJECT = "banking-demo", "full-ja"


def main() -> int:
    state = ProjectService().load_state(CLIENT, PROJECT)
    c = state.tasks.clustering
    if c is None:
        print("no task clustering on this project")
        return 1

    orphan_clusters = sorted(
        {
            a.final_profile_id
            for a in c.assignments
            if a.final_category_id < 0 or a.final_family_id < 0
        }
    )
    print(f"task clusters with a missing parent: {orphan_clusters}")
    if not orphan_clusters:
        print("nothing to purge")
        return 0

    task_by_id = {t.id: t for t in state.tasks.inferred}
    title_of = {p.profile_key: p.title for p in state.job_profiles}

    victims: list[str] = []
    for cid in orphan_clusters:
        rows = [a for a in c.assignments if a.final_profile_id == cid]
        print(f"\n--- cluster {cid} ---")
        print(f"  name in profile_names: {c.profile_names.get(cid)!r}")
        print(f"  category id {rows[0].final_category_id}, family id {rows[0].final_family_id}")
        print(f"  in category_names: {c.category_names.get(rows[0].final_category_id)!r}")
        print(f"  in family_names:   {c.family_names.get(rows[0].final_family_id)!r}")
        print(f"  {len(rows)} underlying tasks:")
        for a in rows:
            t = task_by_id.get(a.item_id)
            victims.append(a.item_id)
            if t is None:
                print(f"    {a.item_id}  (no inferred task record!)")
                continue
            print(
                f"    {t.name!r} — {t.proportion}% of "
                f"{title_of.get(t.source_profile_key, t.source_profile_key)!r}"
            )
            print(f"      {t.description[:110]}")

    # ---- what else points at these clusters -------------------------------
    w = state.workforce
    print("\n--- Workforce Studio references ---")
    print(f"  actions:            {sum(1 for a in w.actions if a.task_cluster_id in orphan_clusters)}")
    print(f"  opportunity records:{sum(1 for o in w.opportunity if o.task_cluster_id in orphan_clusters)}")
    print(f"  skills guidance:    {sum(1 for s in w.skills_guidance if s.task_cluster_id in orphan_clusters)}")
    print(f"  agents:             {sum(1 for a in w.agents if a.task_cluster_id in orphan_clusters)}")
    print(
        f"  process steps:      "
        f"{sum(1 for p in w.processes for s in p.steps if s.task_cluster_id in orphan_clusters)}"
    )

    # ---- the tier records the denormalised view is rebuilt from ------------
    print("\n--- tier records ---")
    for tier, ts in state.tasks.clustering_tiers.items():
        hits = [m for m in ts.members if m.final_cluster_id in orphan_clusters]
        by_item = [m for m in ts.members if m.item_id in set(victims)]
        print(f"  {tier}: k={ts.k}, {len(ts.members)} members, "
              f"{len(hits)} land in an orphan cluster, {len(by_item)} are a victim task")
        if tier == "profile":
            named = set(ts.names)
            print(f"      names cover {len(named)} clusters; orphan named: "
                  f"{ {cid: ts.names.get(cid) for cid in orphan_clusters} }")

    # ---- the roles whose proportions will stop summing to 100 -------------
    print("\n--- affected roles (proportions must sum to 100) ---")
    affected: dict[str, list] = {}
    for tid in victims:
        t = task_by_id.get(tid)
        if t:
            affected.setdefault(t.source_profile_key, []).append(t)
    for key, ts in affected.items():
        total = sum(x.proportion for x in state.tasks.inferred if x.source_profile_key == key)
        losing = sum(x.proportion for x in ts)
        print(
            f"  {title_of.get(key, key)!r}: currently {total:.1f}%, "
            f"losing {losing:.1f}% -> {total - losing:.1f}% before renormalising"
        )

    print(f"\ntotals: {len(orphan_clusters)} cluster(s), {len(victims)} task(s), "
          f"{len(affected)} role(s) needing renormalisation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
