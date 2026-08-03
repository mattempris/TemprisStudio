"""Where cluster 438 belongs, by the same geometry that built the tier above it.

Read-only. The category tier was cut over 749 clusters because 438 was unnamed at the
time, so it simply never had a parent to be assigned. This works out which parent it
would have got: build every task category's centroid from the clusters it contains,
build 438's from its own tasks, and rank by cosine.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.clustering import tier_state  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402

CLIENT, PROJECT = "banking-demo", "full-ja"
ORPHAN = 438


def unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n else v


def main() -> int:
    svc = ProjectService()
    state = svc.load_state(CLIENT, PROJECT)
    c = state.tasks.clustering
    spec = tier_state.spec("task")
    matrix = svc.load_array(CLIENT, f"{PROJECT}/artifacts/{spec.array_name}.npy")
    ids = svc.load_index(CLIENT, f"{PROJECT}/artifacts/{spec.array_name}_index.json")
    if matrix is None or ids is None or c is None:
        print("task embeddings or clustering missing")
        return 1
    row = {i: n for n, i in enumerate(ids)}

    # Centroid per task cluster, from its member tasks.
    members: dict[int, list[int]] = {}
    for a in c.assignments:
        r = row.get(a.item_id)
        if r is not None:
            members.setdefault(a.final_profile_id, []).append(r)
    centroid = {cid: unit(matrix[rs].mean(axis=0)) for cid, rs in members.items()}

    if ORPHAN not in centroid:
        print(f"cluster {ORPHAN} has no member vectors")
        return 1

    # Centroid per category, from the clusters it contains.
    cat_clusters: dict[int, list[int]] = {}
    for a in c.assignments:
        if a.final_category_id >= 0:
            cat_clusters.setdefault(a.final_category_id, []).append(a.final_profile_id)
    cat_centroid = {}
    for cat, cids in cat_clusters.items():
        vecs = [centroid[x] for x in set(cids) if x in centroid]
        if vecs:
            cat_centroid[cat] = unit(np.mean(vecs, axis=0))

    target = centroid[ORPHAN]
    ranked = sorted(
        ((float(target @ v), cat) for cat, v in cat_centroid.items()), reverse=True
    )
    print(f"cluster {ORPHAN}: {c.profile_names.get(ORPHAN)!r}")
    print(f"compared against {len(cat_centroid)} task categories\n")
    print("nearest categories:")
    for sim, cat in ranked[:8]:
        fam = next(
            (a.final_family_id for a in c.assignments if a.final_category_id == cat), -1
        )
        n_clusters = len(set(cat_clusters[cat]))
        print(
            f"  cos {sim:.4f}  [cat {cat:>3}] {c.category_names.get(cat, '?')[:44]:44s} "
            f"({n_clusters} clusters)  domain: {c.family_names.get(fam, '?')}"
        )

    best_sim, best_cat = ranked[0]
    best_fam = next(a.final_family_id for a in c.assignments if a.final_category_id == best_cat)
    print(
        f"\nbest: category {best_cat} ({c.category_names.get(best_cat)!r}) "
        f"in domain {best_fam} ({c.family_names.get(best_fam)!r}) at cosine {best_sim:.4f}"
    )
    # How that compares to how tightly other clusters sit in their own category, so the
    # match can be judged rather than just accepted.
    sims = []
    for cid, v in centroid.items():
        cat = next(
            (a.final_category_id for a in c.assignments if a.final_profile_id == cid), -1
        )
        if cat in cat_centroid:
            sims.append(float(v @ cat_centroid[cat]))
    if sims:
        arr = np.array(sims)
        print(
            f"typical cluster-to-its-own-category cosine: median {np.median(arr):.4f}, "
            f"p10 {np.percentile(arr, 10):.4f}, p90 {np.percentile(arr, 90):.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
