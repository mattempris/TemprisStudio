"""Per-tier clustering: geometry, stability, and the tier-to-tier handoff.

Uses synthetic embeddings with a known three-level structure, so the assertions are
about whether the engine recovers a hierarchy it should be able to recover. No GPU
(vectors are generated), and only `analyse` / `items_from_clusters` are exercised —
`finalise` needs the LLM for naming and routing, so it is covered separately.
"""
from __future__ import annotations

import sys

import numpy as np

from app.services.clustering import tier

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

rng = np.random.default_rng(7)
DIM = 32

# 2 families x 3 categories x 4 profiles x 8 jobs = 192 jobs, nested by construction.
N_FAM, N_CAT, N_PROF, N_JOB = 2, 3, 4, 8


def unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


fam_centres = unit(rng.normal(size=(N_FAM, DIM)))
rows, truth = [], []
for f in range(N_FAM):
    for c in range(N_CAT):
        cat_centre = unit(fam_centres[f] + 0.55 * rng.normal(size=DIM))
        for p in range(N_PROF):
            prof_centre = unit(cat_centre + 0.28 * rng.normal(size=DIM))
            for j in range(N_JOB):
                rows.append(unit(prof_centre + 0.10 * rng.normal(size=DIM)))
                truth.append((f, c, p))

emb = np.vstack(rows).astype(np.float32)
n = len(rows)
items = tier.TierItems(
    ids=[f"rec-{i}" for i in range(n)],
    texts=[f"job {i} in family {t[0]} category {t[1]} profile {t[2]}" for i, t in enumerate(truth)],
    embeddings=emb,
)
print(f"{n} synthetic jobs, true structure {N_FAM} families / {N_FAM*N_CAT} categories / {N_FAM*N_CAT*N_PROF} profiles\n")

K_PROF = N_FAM * N_CAT * N_PROF   # 24
K_CAT = N_FAM * N_CAT             # 6
K_FAM = N_FAM                     # 2

print("=== tier 1: jobs -> profiles ===")
a1 = tier.analyse(items, k=K_PROF, n_perturb=20)
print(f"  k={a1.k} sizes={a1.sizes}")
print(f"  stability: mean {np.nanmean(a1.stability):.3f}, min {np.nanmin(a1.stability):.3f}")
assert len(a1.sizes) == K_PROF
assert sum(a1.sizes) == n

# Every job in one true profile should land in one cluster if the geometry is clean.
pure = 0
for p in range(K_PROF):
    idxs = [i for i, t in enumerate(truth) if t == (p // (N_CAT * N_PROF), (p // N_PROF) % N_CAT, p % N_PROF)]
    if idxs and len({int(a1.labels[i]) for i in idxs}) == 1:
        pure += 1
print(f"  true profiles recovered intact: {pure}/{K_PROF}")

print("\n=== gate preview is instant and monotonic ===")
prev = -1
for gate in (0.3, 0.5, 0.58, 0.7, 0.9):
    c = a1.routed_count(gate)
    print(f"  gate {gate:<5} -> {c:>3} of {n} would be routed ({100*c/n:.0f}%)")
    assert c >= prev, "a higher gate must route at least as many"
    prev = c
dist = a1.distribution()
print(f"  histogram buckets: {len(dist)}, total counted {sum(b['count'] for b in dist)}")
assert sum(b["count"] for b in dist) == int(np.sum(~np.isnan(a1.stability)))

print("\n=== the gate on data where it actually bites ===")
# The structure above is deliberately clean, so every item scores 1.0 and the gate
# does nothing — which would make the monotonicity check above pass trivially.
# Overlapping profiles produce the ambiguous tail the gate exists to catch.
noisy_rows = []
for p in range(8):
    centre = unit(rng.normal(size=DIM))
    for j in range(12):
        # 0.8 noise puts members of different profiles genuinely close together
        noisy_rows.append(unit(centre + 0.8 * rng.normal(size=DIM)))
noisy = tier.TierItems(
    ids=[f"n-{i}" for i in range(len(noisy_rows))],
    texts=[f"ambiguous role {i}" for i in range(len(noisy_rows))],
    embeddings=np.vstack(noisy_rows).astype(np.float32),
)
an = tier.analyse(noisy, k=8, n_perturb=20)
valid = an.stability[~np.isnan(an.stability)]
print(f"  {len(noisy)} overlapping items, stability mean {valid.mean():.3f} "
      f"min {valid.min():.3f} max {valid.max():.3f}")
counts = {g: an.routed_count(g) for g in (0.3, 0.5, 0.58, 0.7, 0.9)}
for g, c in counts.items():
    print(f"  gate {g:<5} -> {c:>3} of {len(noisy)} routed ({100*c/len(noisy):.0f}%)")
assert counts[0.9] > counts[0.3], "the gate must separate items on realistic data"
assert 0 < counts[0.58] < len(noisy), (
    f"at the default gate some but not all items should route, got {counts[0.58]}"
)
print(f"  gate genuinely discriminates: {counts[0.3]} -> {counts[0.9]} across the range")

print("\n=== tier 1 -> tier 2 handoff (no LLM: synthesise the confirmed result) ===")
r1 = tier.TierResult(
    k=K_PROF, gate=0.58,
    names={cid: f"Profile {cid}" for cid in range(K_PROF)},
    members=[
        tier.TierMemberOutcome(items.ids[i], int(a1.labels[i]), int(a1.labels[i]),
                               None if np.isnan(a1.stability[i]) else float(a1.stability[i]), False)
        for i in range(n)
    ],
    n_routed=0, n_moved=0, low_confidence=0, multi_home=0,
    centroids=tier._centroids(emb, a1.labels, K_PROF),
    exemplar_texts={cid: [items.texts[i] for i in range(n) if a1.labels[i] == cid][:4] for cid in range(K_PROF)},
)
items2 = tier.items_from_clusters(r1, "profile")
print(f"  tier 2 items: {len(items2)} (one per profile cluster)")
print(f"  example text: {items2.texts[0][:100]}")
assert len(items2) == K_PROF
assert items2.embeddings.shape == (K_PROF, DIM)
norms = np.linalg.norm(items2.embeddings, axis=1)
print(f"  centroid norms: min {norms.min():.4f} max {norms.max():.4f}")
assert np.allclose(norms, 1.0, atol=1e-5), "centroids must be unit vectors for cosine to mean anything"

print("\n=== tier 2: profiles -> categories ===")
a2 = tier.analyse(items2, k=K_CAT, n_perturb=20)
print(f"  k={a2.k} sizes={a2.sizes} stability mean {np.nanmean(a2.stability):.3f}")
assert sum(a2.sizes) == K_PROF

print("\n=== tier 3: categories -> families ===")
r2 = tier.TierResult(
    k=K_CAT, gate=0.58, names={cid: f"Category {cid}" for cid in range(K_CAT)},
    members=[tier.TierMemberOutcome(items2.ids[i], int(a2.labels[i]), int(a2.labels[i]), None, False)
             for i in range(len(items2))],
    n_routed=0, n_moved=0, low_confidence=0, multi_home=0,
    centroids=tier._centroids(items2.embeddings, a2.labels, K_CAT),
    exemplar_texts={cid: [items2.texts[i] for i in range(len(items2)) if a2.labels[i] == cid][:4] for cid in range(K_CAT)},
)
items3 = tier.items_from_clusters(r2, "category")
a3 = tier.analyse(items3, k=K_FAM, n_perturb=20)
print(f"  k={a3.k} sizes={a3.sizes}")
assert sum(a3.sizes) == K_CAT

print("\n=== nesting is structural: every job resolves to exactly one of each tier ===")
prof_of_job = {items.ids[i]: int(a1.labels[i]) for i in range(n)}
cat_of_prof = {int(items2.ids[i].split(":")[1]): int(a2.labels[i]) for i in range(len(items2))}
fam_of_cat = {int(items3.ids[i].split(":")[1]): int(a3.labels[i]) for i in range(len(items3))}
chains = {(p, cat_of_prof[p], fam_of_cat[cat_of_prof[p]]) for p in prof_of_job.values()}
print(f"  distinct profile->category->family chains: {len(chains)} (one per profile: {len(chains) == K_PROF})")
assert len(chains) == K_PROF, "each profile must have exactly one parent chain"
for job_id, p in prof_of_job.items():
    assert p in cat_of_prof and cat_of_prof[p] in fam_of_cat
print("  every job resolves through the full chain with no orphans: OK")

print("\n=== k must be valid ===")
for bad, why in [(1, "below 2"), (len(items3), "not fewer than the item count")]:
    try:
        tier.analyse(items3, k=bad, n_perturb=5)
    except ValueError as e:
        print(f"  k={bad} ({why}) -> {e}")
    else:
        raise AssertionError(f"k={bad} should have been rejected")

print("\nTIER ENGINE TESTS PASSED (no GPU, no LLM)")
