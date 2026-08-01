"""Test the Phase 2 pieces that need no LLM: skillQWEN embeddings, the skill
audit, proficiency template validation, and the deterministic rollup."""
import numpy as np

from app.services.clustering import backbone as bb
from app.services.clustering import engine as cluster_engine
from app.services.embeddings import get_embedding_service
from app.services.skills.inference import InferredSkill, audit_skills
from app.services.skills.proficiency import (
    ProficiencyLevel,
    ProficiencyTemplate,
    load_default_template,
    rollup_clusters_to_profiles,
    validate_template,
)

print("=== skillQWEN loads and separates semantically ===")
svc = get_embedding_service()
assert svc.is_ready("skill"), "skillQWEN not extracted"
skills_text = [
    "Stakeholder Influence. Ability to shape decisions among senior internal and external parties.",
    "Stakeholder Engagement. Capacity to build durable relationships with senior parties and shape outcomes.",
    "Hydraulic Modelling. Deep technical command of network hydraulic simulation and calibration.",
    "Financial Forecasting. Command of budget modelling, variance analysis and multi-year projections.",
]
emb = svc.embed_documents("skill", skills_text)
print(f"  embedded {emb.shape}")
assert emb.shape == (4, 1024)
sims = emb @ emb.T
print("  similarity matrix:\n", np.round(sims, 3))
# the two stakeholder skills are near-synonyms and must be closest
assert sims[0, 1] > sims[0, 2] and sims[0, 1] > sims[0, 3], "stakeholder pair should be closest"
print(f"  stakeholder pair {sims[0,1]:.3f} > stakeholder-vs-hydraulic {sims[0,2]:.3f}")

print("\n=== all three QWEN models load independently ===")
for entity in ("job", "skill", "task"):
    assert svc.is_ready(entity), f"{entity}QWEN not ready"
    v = svc.embed_documents(entity, ["Data Engineering"])
    print(f"  {entity}QWEN -> {v.shape}")

print("\n=== skill audit catches spec violations ===")
good = [
    InferredSkill("Stakeholder Influence", " ".join(["word"] * 20), "non-technical", "p1"),
    InferredSkill("Hydraulic Modelling", " ".join(["word"] * 22), "technical", "p1"),
]
bad = [
    # 4 words - violates the 1-3 word rule
    InferredSkill("Very Long Skill Name", " ".join(["word"] * 20), "technical", "p1"),
    # description too short
    InferredSkill("Short Desc", "only five words here now", "technical", "p1"),
    # task-phrased, which is the constraint most at risk of drift
    InferredSkill("Managing Budgets", " ".join(["word"] * 20), "non-technical", "p1"),
    InferredSkill("Reporting Performance", " ".join(["word"] * 20), "non-technical", "p1"),
]
clean = audit_skills(good)
print(f"  clean set: {clean.summary()}")
assert clean.name_too_long == [] and clean.description_out_of_range == [] and clean.task_phrased == []

dirty = audit_skills(good + bad)
print(f"  dirty set: {dirty.summary()}")
assert "Very Long Skill Name" in dirty.name_too_long
assert any("Short Desc" in d for d in dirty.description_out_of_range)
assert set(dirty.task_phrased) == {"Managing Budgets", "Reporting Performance"}
assert abs(dirty.task_phrased_pct - 100 * 2 / 6) < 0.1
print("  correctly flagged long names, bad descriptions, and task-phrased names")

print("\n=== proficiency template ===")
tpl = load_default_template()
print(f"  default levels: {tpl.level_names()}")
assert tpl.level_names() == ["Entry", "Intermediate", "Advanced", "Expert"]
assert not validate_template(tpl), validate_template(tpl)
print("  default template valid")
print(f"  rubric text starts: {tpl.rubric_text()[:90]}...")

broken_cases = [
    ("non-consecutive ordinals", ProficiencyTemplate(levels=[
        ProficiencyLevel("A", 1, "x"), ProficiencyLevel("B", 3, "y")])),
    ("duplicate names", ProficiencyTemplate(levels=[
        ProficiencyLevel("A", 1, "x"), ProficiencyLevel("A", 2, "y")])),
    ("empty criteria", ProficiencyTemplate(levels=[
        ProficiencyLevel("A", 1, "x"), ProficiencyLevel("B", 2, "  ")])),
    ("too few levels", ProficiencyTemplate(levels=[ProficiencyLevel("A", 1, "x")])),
]
for label, t in broken_cases:
    problems = validate_template(t)
    assert problems, f"{label} should have been rejected"
    print(f"  rejected {label}: {problems[0]}")

print("\n=== deterministic rollup (no LLM) ===")
# profile p1 has two skills landing in cluster 0 and one in cluster 1;
# p2 has one in cluster 0. Expect 3 requirements, not 4.
assignments = [
    ("p1", "Stakeholder Influence", "desc a", 0),
    ("p1", "Negotiation", "desc b", 0),
    ("p1", "Hydraulic Modelling", "desc c", 1),
    ("p2", "Stakeholder Influence", "desc d", 0),
]
names = {0: "Influence & Negotiation", 1: "Network Modelling"}
reqs = rollup_clusters_to_profiles(assignments, names)
for r in reqs:
    print(f"  {r.profile_key} requires '{r.cluster_name}' "
          f"(evidence: {[n for n, _ in r.contributing_skills]})")
assert len(reqs) == 3, f"expected 3 requirements, got {len(reqs)}"
p1c0 = next(r for r in reqs if r.profile_key == "p1" and r.cluster_id == 0)
assert len(p1c0.contributing_skills) == 2, "two skills in the same cluster should merge into one requirement"
assert {r.cluster_name for r in reqs} == {"Influence & Negotiation", "Network Modelling"}
# deterministic ordering
assert [(r.profile_key, r.cluster_id) for r in reqs] == [("p1", 0), ("p1", 1), ("p2", 0)]
print("  merged duplicate cluster hits, kept evidence, deterministic order")

print("\n=== skill clustering reuses the Phase 1 engine unchanged ===")
many = skills_text * 4  # 16 skills
emb_many = svc.embed_documents("skill", many)
tree = bb.build_linkage_tree(emb_many)
cuts = cluster_engine.cut_three_tiers(tree, k_family=2, k_category=3, k_profile=4)
print(f"  families={len(set(cuts['family'].tolist()))} "
      f"categories={len(set(cuts['category'].tolist()))} "
      f"clusters={len(set(cuts['profile'].tolist()))}")
assert len(set(cuts["profile"].tolist())) == 4
try:
    cluster_engine.cut_three_tiers(tree, k_family=5, k_category=3, k_profile=4)
    raise AssertionError("non-nesting k should be rejected")
except ValueError as e:
    print(f"  non-nesting k rejected: {e}")

print("\nPHASE 2 OFFLINE TESTS PASSED")
