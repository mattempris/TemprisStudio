"""Phase 4 pieces needing no LLM and no GPU: catalogue load, level parse, shortlist."""
import numpy as np

from app.services.matching import taxonomy

print("=== industries are the 19 atoms, not the 81 comma-joined combinations ===")
industries = taxonomy.list_industries()
print(f"  {len(industries)}: {industries}")
assert len(industries) == 19, industries
assert not any("," in i for i in industries), "industry list must be atomic"

print("\n=== full catalogue collapses to specialization level ===")
specs = taxonomy.load_specializations()
print(f"  {len(specs)} specializations")
s = specs[0]
print(f"  first: {s.code} | {s.family_title} > {s.sub_family_title} > {s.title}")
print(f"    typical titles: {s.typical_titles[:4]}")
print(f"    levels: {s.available_levels}")
assert len(specs) == 2910, len(specs)
assert all(sp.code for sp in specs)
assert len({sp.code for sp in specs}) == len(specs), "codes must be unique after dedupe"

print("\n=== each typical title becomes its own index row, carrying the path ===")
multi = max(specs, key=lambda sp: len(sp.typical_titles))
variants = multi.variant_texts()
print(f"  widest spec {multi.code} has {len(multi.typical_titles)} titles -> {len(variants)} variants (cap {taxonomy.MAX_TITLE_VARIANTS})")
for v in variants[:3]:
    print("   ", v)
assert len(variants) <= taxonomy.MAX_TITLE_VARIANTS
assert all(v.endswith(f"{multi.family_title} | {multi.sub_family_title} | {multi.title}") for v in variants)
total_variants = sum(len(sp.variant_texts()) for sp in specs)
print(f"  {total_variants} variants across {len(specs)} specs")
assert total_variants > len(specs)

print("\n=== titles accumulate across career-level rows (manager AND specialist) ===")
vendor = next(sp for sp in specs if sp.code == "QLT.02.007")
print(f"  {vendor.code}: {len(vendor.typical_titles)} titles, {len(vendor.available_levels)} levels")
print(f"    {vendor.typical_titles[:3]} ... {vendor.typical_titles[-2:]}")
joined = " ".join(vendor.typical_titles)
assert "Manager" in joined and "Specialist" in joined, "must span both career streams' titles"
streams = {c[0] for c, _ in vendor.available_levels}
print(f"    level streams present: {sorted(streams)}")
assert streams == {"M", "P"}, streams

print("\n=== every spec has at least one parsed career level ===")
no_levels = [sp.code for sp in specs if not sp.available_levels]
print(f"  specs with no level: {len(no_levels)} {no_levels[:5]}")
assert not no_levels, "level-code parsing failed for some rows"

print("\n=== level codes parsed out of display titles resolve to definitions ===")
levels = taxonomy.load_career_levels()
print(f"  {len(levels)} level definitions: {sorted(levels)}")
used = {c for sp in specs for c, _ in sp.available_levels}
unknown = sorted(used - set(levels))
print(f"  level codes used by the catalogue but absent from levelJson: {unknown}")
d = levels["P3"]
print(f"  P3 -> stream={d.stream} name={d.name} desc={d.description[:80]}...")

print("\n=== industry filter matches ANY atom and always keeps Cross Industry ===")
target = "Retail"
filtered = taxonomy.load_specializations([target])
kept = {i for sp in filtered for i in sp.industries}
print(f"  filter={target!r} -> {len(filtered)} specs")
assert len(filtered) < len(specs), "filter should narrow the catalogue"
assert all(target in sp.industries or "Cross Industry" in sp.industries for sp in filtered)
# the regression this guards: a spec tagged 'Healthcare,Retail' must survive a
# 'Retail' filter, which exact-string matching on the raw column would drop
multi_ind = [sp for sp in filtered if target in sp.industries and len(sp.industries) > 1]
print(f"  multi-industry specs kept: {len(multi_ind)}, e.g. {multi_ind[0].code} {multi_ind[0].industries}")
assert multi_ind, "multi-industry specs were dropped — filter is matching the joined string"

print("\n=== shortlist max-pools variants up to spec level, ranked descending ===")
rng = np.random.default_rng(0)
# 4 specs with 3, 1, 2, 4 variants respectively
counts = [3, 1, 2, 4]
offsets = np.asarray([0, 3, 4, 6], dtype=np.int64)
var_vecs = rng.normal(size=(sum(counts), 16)).astype(np.float32)
var_vecs /= np.linalg.norm(var_vecs, axis=1, keepdims=True)
# profile 0 copies variant 4 (spec 2); profile 1 copies variant 8 (spec 3)
prof_vecs = var_vecs[[4, 8]] * 0.95 + rng.normal(size=(2, 16)).astype(np.float32) * 0.05
prof_vecs /= np.linalg.norm(prof_vecs, axis=1, keepdims=True)

idx, scores = taxonomy.cosine_shortlist(prof_vecs, var_vecs, offsets, top_n=3)
print(f"  shape {idx.shape}, row0 specs={idx[0].tolist()} scores={np.round(scores[0], 3).tolist()}")
assert idx.shape == (2, 3), idx.shape
for r in range(2):
    assert list(scores[r]) == sorted(scores[r], reverse=True), "shortlist must be ranked"
assert idx[0][0] == 2 and idx[1][0] == 3, (idx[0][0], idx[1][0])
print("  owning spec recovered for both profiles via its single best variant")

# max-pool, not mean: spec 3's other 3 variants are unrelated and must not dilute it
full = prof_vecs @ var_vecs.T
assert abs(scores[1][0] - full[1, 6:10].max()) < 1e-5, "spec score must be its best variant"
print(f"  spec 3 scored {scores[1][0]:.3f} = max of its variants "
      f"(mean would be {full[1, 6:10].mean():.3f})")

print("\n=== top_n larger than the spec count is clamped, not an error ===")
idx_s, _ = taxonomy.cosine_shortlist(prof_vecs, var_vecs, offsets, top_n=10)
print(f"  requested 10 from a 4-spec taxonomy -> {idx_s.shape}")
assert idx_s.shape == (2, 4)

print("\nPHASE 4 OFFLINE TESTS PASSED")
