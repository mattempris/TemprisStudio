"""Test the Phase 3 pieces needing no LLM: proportion normalisation and audit."""
from app.services.tasks.inference import InferredTask, audit_tasks, normalize_proportions


def mk(name: str, prop: float) -> InferredTask:
    return InferredTask(name=name, description="d", proportion=prop, source_profile_key="p1")


print("=== proportions that already sum to 100 are left alone ===")
tasks, fix = normalize_proportions([mk("Asset Plan Development", 60), mk("Stakeholder Reporting", 40)], "p1")
print(f"  {[t.proportion for t in tasks]} sum={sum(t.proportion for t in tasks)} adjusted={fix.adjusted}")
assert sum(t.proportion for t in tasks) == 100.0
assert not fix.adjusted

print("\n=== under-sum is rescaled to exactly 100 ===")
tasks, fix = normalize_proportions([mk("A B", 30), mk("C D", 30), mk("E F", 30)], "p1")
total = sum(t.proportion for t in tasks)
print(f"  raw sum 90 -> {[t.proportion for t in tasks]} sum={total} drift={fix.drift}")
assert total == 100.0, total
assert fix.adjusted and abs(fix.drift - 10.0) < 0.01

print("\n=== over-sum is rescaled to exactly 100 ===")
tasks, fix = normalize_proportions([mk("A B", 50), mk("C D", 40), mk("E F", 30)], "p1")
total = sum(t.proportion for t in tasks)
print(f"  raw sum 120 -> {[t.proportion for t in tasks]} sum={total} drift={fix.drift}")
assert total == 100.0, total
assert abs(fix.drift - 20.0) < 0.01

print("\n=== residual rounding lands on the largest task, so the total is exact ===")
# three-way split cannot be represented exactly at 2dp; 33.33*3 = 99.99
tasks, fix = normalize_proportions([mk("A B", 1), mk("C D", 1), mk("E F", 1)], "p1")
total = sum(t.proportion for t in tasks)
print(f"  {[t.proportion for t in tasks]} sum={total}")
assert total == 100.0, f"expected exactly 100, got {total}"
largest = max(tasks, key=lambda t: t.proportion)
print(f"  largest task absorbed the residual: {largest.proportion}")

print("\n=== degenerate inputs don't crash ===")
tasks, fix = normalize_proportions([], "p1")
print(f"  empty -> {tasks}, adjusted={fix.adjusted}")
assert tasks == [] and not fix.adjusted
tasks, fix = normalize_proportions([mk("A B", 0), mk("C D", 0)], "p1")
print(f"  all-zero -> {[t.proportion for t in tasks]}, adjusted={fix.adjusted}")
assert not fix.adjusted, "all-zero can't be rescaled; must be reported rather than divided by zero"

print("\n=== audit flags names outside the 2-4 word rule ===")
good = [mk("Asset Plan Development", 50), mk("Stakeholder Reporting", 50)]
bad = [mk("Reporting", 20), mk("Very Long Task Name Here", 20)]
a_clean = audit_tasks(good, [fix])
print(f"  clean: {a_clean.summary()}")
assert a_clean.name_out_of_range == []
a_dirty = audit_tasks(good + bad, [fix])
print(f"  dirty: {a_dirty.summary()}")
assert set(a_dirty.name_out_of_range) == {"Reporting", "Very Long Task Name Here"}

print("\n=== audit aggregates drift across jobs ===")
_, f1 = normalize_proportions([mk("A B", 30), mk("C D", 30), mk("E F", 30)], "p1")  # drift 10
_, f2 = normalize_proportions([mk("A B", 60), mk("C D", 40)], "p2")                 # drift 0
_, f3 = normalize_proportions([mk("A B", 80), mk("C D", 45)], "p3")                 # drift 25
audit = audit_tasks(good, [f1, f2, f3])
print(f"  {audit.summary()}")
assert audit.summary()["jobs_needing_proportion_fix"] == 2
assert abs(audit.max_drift - 25.0) < 0.01

print("\nPHASE 3 OFFLINE TESTS PASSED")
