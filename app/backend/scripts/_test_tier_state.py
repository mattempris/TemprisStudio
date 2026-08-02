"""Three tiers confirmed in sequence, with naming and routing stubbed out.

The point is the state layer, not the LLM: that each tier's record persists, that
confirming a lower tier invalidates the ones above it, and — most importantly —
that the denormalised `clustering` view rebuilt from the tiers is exactly what
everything downstream already expects. If that view is wrong, job profiles, the
overview, the exports and the headcount rollups all silently break.

No GPU, no LLM, no blob storage: embeddings are synthetic, naming and routing are
patched, and the store is an in-memory stand-in.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

import numpy as np

from app.models.project_state import ProjectMeta, ProjectState, NormalizedProfile, JobRecordRaw, DedupeGroup
from app.services.clustering import naming, routing
from app.services.clustering import tier as tier_engine
from app.services.clustering import tier_state

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---- stubs -----------------------------------------------------------------
naming.name_level = lambda entity, level, blocks, k, **kw: {  # type: ignore[assignment]
    i: f"{level.title()} {i}" for i in range(k)
}


async def _no_routing(items, clusters_text, **kw):
    return {}


routing.route_all = _no_routing  # type: ignore[assignment]


class FakeStore:
    """Enough of ProjectService for the tier layer: arrays and indexes in memory."""

    def __init__(self) -> None:
        self.arrays: dict[str, np.ndarray] = {}
        self.indexes: dict[str, list[str]] = {}

    def save_array(self, client, project, name, arr):
        path = f"{project}/artifacts/{name}.npy"
        self.arrays[path] = arr
        return path

    def load_array(self, client, path):
        return self.arrays.get(path)

    def load_index(self, client, path):
        return self.indexes.get(path)


# ---- fixture ---------------------------------------------------------------
rng = np.random.default_rng(3)
DIM, N = 24, 60
K_PROF, K_CAT, K_FAM = 12, 5, 2

emb = rng.normal(size=(N, DIM)).astype(np.float32)
emb /= np.linalg.norm(emb, axis=1, keepdims=True)

state = ProjectState(
    meta=ProjectMeta(
        client_slug="c", project_slug="p", display_name="p",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
)
# Two raw records per normalised profile, so the profile-tier labeller has dedupe
# groups to resolve — the same shape the real pipeline produces.
for i in range(N):
    for j in range(2):
        state.raw_records.append(
            JobRecordRaw(id=f"rec-{i}-{j}", source_file_id="f", job_title=f"Job {i}.{j}", raw_text="x")
        )
    state.dedupe_groups.append(
        DedupeGroup(group_id=f"grp-{i}", member_ids=[f"rec-{i}-0", f"rec-{i}-1"],
                    representative_id=f"rec-{i}-0", avg_similarity=0.99, user_confirmed=True)
    )
    state.normalized_profiles.append(
        NormalizedProfile(id=f"grp-{i}", source_record_ids=[f"rec-{i}-0", f"rec-{i}-1"],
                          purpose_statement=f"purpose {i}", key_tasks=[f"task {i}"],
                          generated_at=datetime.now(timezone.utc))
    )

svc = FakeStore()
svc.arrays["p/artifacts/cluster_embeddings.npy"] = emb
svc.indexes["p/artifacts/cluster_embeddings_index.json"] = [f"grp-{i}" for i in range(N)]


def confirm(tier: str, k: int) -> None:
    items = tier_state.build_items(svc, state, tier)  # type: ignore[arg-type]
    analysis = tier_engine.analyse(items, k=k, n_perturb=10)
    result = asyncio.run(
        tier_engine.finalise(items, analysis, entity="job", tier=tier, gate=0.58)
    )
    tier_state.save_tier(svc, state, tier, result, embedding_model="jobQWEN")  # type: ignore[arg-type]
    print(f"  {tier:9} k={k:<3} items={len(items):<3} clusters={len(result.names)}")


print("=== a coarse tier cannot be built before the one below it ===")
for tier in ("category", "family"):
    try:
        tier_state.build_items(svc, state, tier)  # type: ignore[arg-type]
    except tier_state.TierNotReady as e:
        print(f"  {tier}: {str(e)[:78]}")
    else:
        raise AssertionError(f"{tier} should not be buildable yet")

print("\n=== confirm the three tiers in order ===")
confirm("profile", K_PROF)
assert state.clustering is None, "the flat view must stay empty until all three tiers exist"
print("  flat view still None after 1 of 3 tiers: OK")
confirm("category", K_CAT)
assert state.clustering is None
print("  flat view still None after 2 of 3 tiers: OK")
confirm("family", K_FAM)
assert state.clustering is not None, "the flat view must appear once the hierarchy is complete"

c = state.clustering
print(f"\n=== the denormalised view downstream reads ===")
print(f"  k: {c.k_profiles} profiles / {c.k_categories} categories / {c.k_families} families")
print(f"  assignments: {len(c.assignments)} (one per normalised job)")
print(f"  names: {len(c.profile_names)}/{len(c.category_names)}/{len(c.family_names)}")
assert (c.k_profiles, c.k_categories, c.k_families) == (K_PROF, K_CAT, K_FAM)
assert len(c.assignments) == N
assert len(c.profile_names) == K_PROF and len(c.category_names) == K_CAT and len(c.family_names) == K_FAM

print("\n=== every assignment resolves to a real cluster at all three tiers ===")
bad = [
    a.item_id for a in c.assignments
    if a.final_profile_id not in c.profile_names
    or a.final_category_id not in c.category_names
    or a.final_family_id not in c.family_names
]
print(f"  unresolved assignments: {len(bad)}")
assert not bad, f"orphaned assignments: {bad[:5]}"

print("\n=== nesting: each profile has exactly one category, each category one family ===")
cat_of_prof: dict[int, set[int]] = {}
fam_of_cat: dict[int, set[int]] = {}
for a in c.assignments:
    cat_of_prof.setdefault(a.final_profile_id, set()).add(a.final_category_id)
    fam_of_cat.setdefault(a.final_category_id, set()).add(a.final_family_id)
multi_cat = {p: v for p, v in cat_of_prof.items() if len(v) > 1}
multi_fam = {ct: v for ct, v in fam_of_cat.items() if len(v) > 1}
print(f"  profiles with >1 category: {len(multi_cat)} | categories with >1 family: {len(multi_fam)}")
assert not multi_cat and not multi_fam, "nesting is violated"

print("\n=== confirming a lower tier invalidates the ones above ===")
before = sorted(state.clustering_tiers)
confirm("profile", K_PROF - 2)
after = sorted(state.clustering_tiers)
print(f"  tiers before: {before} -> after re-confirming profile: {after}")
assert after == ["profile"], "category and family should have been dropped"
assert state.clustering is None, "the flat view must be withdrawn when the hierarchy is incomplete"
print("  flat view withdrawn so downstream cannot read a half-rebuilt hierarchy: OK")

print("\n=== rebuilding the upper tiers restores it ===")
confirm("category", K_CAT)
confirm("family", K_FAM)
assert state.clustering is not None and len(state.clustering.assignments) == N
print(f"  flat view restored with {len(state.clustering.assignments)} assignments: OK")

print("\n=== hierarchy_summary reports per-tier status ===")
for t, info in tier_state.hierarchy_summary(state).items():
    print(f"  {t:9} confirmed={info['confirmed']} k={info['k']} ready_to_run={info['ready_to_run']}")

print("\nTIER STATE TESTS PASSED (no GPU, no LLM, no blob)")
