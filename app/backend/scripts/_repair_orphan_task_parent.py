"""Give task cluster 438 the parent the category tier never assigned it.

Nothing is deleted. The cause is a bookkeeping gap, not bad data: the category tier was
cut over 749 clusters because 438 was unnamed at the moment that tier was confirmed, so
it was never offered a parent and has sat with category/family -1 ever since.

The fix is at the source, not on the derived view: add the missing member to the category
tier and let `rebuild_denormalised` regenerate the flat `ClusteringState` from the tiers,
exactly as a normal confirmation would. Patching `clustering.assignments` directly would
be undone by the next rebuild.

The member is flagged `moved_by_user`, which is what that field is for — a placement a
person chose stays distinguishable from one the backbone or the router produced.

Run with --apply to write. Without it, reports the choice and changes nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.project_state import TierMemberRecord  # noqa: E402
from app.services.clustering import tier_state  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402

CLIENT, PROJECT = "banking-demo", "full-ja"
ORPHAN = 438

# Nearest centroid, checked against the candidate's siblings rather than taken on trust.
#
# I first assumed the nearest category — "Control Testing Validation" in "Quality and
# Control Testing" — was software testing, and went looking for a risk-flavoured parent
# instead. Reading what that category actually contains settled it: Control Effectiveness
# Testing, Model Validation Review, Independent Risk Control Testing, Testing Methodology
# Guidance. That is exactly the company for liquidity stress testing, market-risk VaR
# back-testing, derivatives model stress testing and resilience scenario exercises.
#
# The risk-domain alternative, "Control Design Integration", holds governance and strategy
# *design* work — stress testing is execution and validation, not design — and fitted
# worse geometrically too, 0.45 against 0.62.
#
# 0.62 is still below the median cluster-to-own-category cosine of 0.82, which is the
# honest signal that this cluster has no ideal home among the existing 125 categories. It
# gets the best available one, agreed by both measures, flagged as a human decision so it
# can be revisited if the taxonomy is ever recut.


def unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n else v


def main() -> int:
    apply = "--apply" in sys.argv
    svc = ProjectService()
    state = svc.load_state(CLIENT, PROJECT)
    c = state.tasks.clustering
    if c is None:
        print("no task clustering")
        return 1

    cat_tier = state.tasks.clustering_tiers.get("category")
    if cat_tier is None:
        print("no category tier")
        return 1
    if any(m.item_id == f"profile:{ORPHAN}" for m in cat_tier.members):
        print(f"cluster {ORPHAN} already has a category tier member — nothing to repair")
        return 0

    spec = tier_state.spec("task")
    matrix = svc.load_array(CLIENT, f"{PROJECT}/artifacts/{spec.array_name}.npy")
    ids = svc.load_index(CLIENT, f"{PROJECT}/artifacts/{spec.array_name}_index.json")
    if matrix is None or ids is None:
        print("task embeddings not cached")
        return 1
    row = {i: n for n, i in enumerate(ids)}

    members: dict[int, list[int]] = {}
    for a in c.assignments:
        r = row.get(a.item_id)
        if r is not None:
            members.setdefault(a.final_profile_id, []).append(r)
    centroid = {cid: unit(matrix[rs].mean(axis=0)) for cid, rs in members.items()}
    target = centroid[ORPHAN]

    cat_clusters: dict[int, set[int]] = {}
    cat_family: dict[int, int] = {}
    for a in c.assignments:
        if a.final_category_id >= 0:
            cat_clusters.setdefault(a.final_category_id, set()).add(a.final_profile_id)
            cat_family[a.final_category_id] = a.final_family_id

    scored = []
    for cat, cids in cat_clusters.items():
        vecs = [centroid[x] for x in cids if x in centroid]
        if not vecs:
            continue
        sim = float(target @ unit(np.mean(vecs, axis=0)))
        fam = cat_family[cat]
        scored.append((sim, cat, fam, c.family_names.get(fam, "?")))

    print("nearest categories:")
    for sim_, cat_, fam_, domain_ in sorted(scored, reverse=True)[:5]:
        print(
            f"  cos {sim_:.4f}  {c.category_names.get(cat_, '?')[:36]:36s} domain: {domain_}"
        )

    if not scored:
        print("no categories to choose from")
        return 1
    sim, cat, fam, domain = max(scored)
    siblings = sorted(c.profile_names.get(x, str(x)) for x in cat_clusters[cat])
    print(
        f"\nchosen parent: category {cat} ({c.category_names.get(cat)!r}) "
        f"in domain {fam} ({domain!r}) at cosine {sim:.4f}"
    )
    print("  the company it would keep:")
    for name in siblings:
        print(f"    {name}")

    if not apply:
        print("\nDRY RUN — pass --apply to write this to project state")
        return 0

    # Back up the state blob before touching it. Cheap, and this is live shared storage.
    raw = svc.store.read_state(CLIENT, PROJECT)
    backup = f"{PROJECT}/workforce/backups/state-before-orphan-repair.json"
    svc.store.write_json(CLIENT, backup, raw)
    print(f"\nstate backed up to {backup}")

    fresh = svc.load_state(CLIENT, PROJECT)
    tier = fresh.tasks.clustering_tiers["category"]
    before_members = len(tier.members)
    tier.members.append(
        TierMemberRecord(
            item_id=f"profile:{ORPHAN}",
            backbone_cluster_id=cat,
            final_cluster_id=cat,
            moved_by_user=True,
        )
    )
    tier.n_moved += 1
    if ORPHAN not in tier.exemplars and ORPHAN in members:
        pass  # exemplars are per-category, not per-cluster; nothing to add here

    tier_state.rebuild_denormalised(fresh, "task")

    after = fresh.tasks.clustering
    orphans = [
        a for a in after.assignments
        if a.final_profile_id == ORPHAN
    ]
    print(f"category tier members: {before_members} -> {len(tier.members)}")
    for a in orphans:
        print(
            f"  {a.item_id}: category {a.final_category_id} "
            f"({after.category_names.get(a.final_category_id)!r}), "
            f"family {a.final_family_id} ({after.family_names.get(a.final_family_id)!r})"
        )
    still_broken = [
        a.final_profile_id for a in after.assignments
        if a.final_category_id < 0 or a.final_family_id < 0
    ]
    print(f"clusters still without a parent: {sorted(set(still_broken))}")
    total_tasks = len(after.assignments)
    print(f"task assignments: {len(c.assignments)} -> {total_tasks} (must be unchanged)")

    svc.save_state(
        fresh,
        action="repair-orphan-task-parent",
        lineage_payload={
            "cluster_id": ORPHAN,
            "assigned_category": cat,
            "assigned_family": fam,
            "cosine": round(sim, 4),
            "reason": "category tier was cut over 749 clusters; this one was unnamed at the time",
        },
    )
    print("state saved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
