"""The dependency graph, and what a repeat actually invalidates.

These assertions exist because the thing they replace — `pipeline._invalidate_from` — was
written, never called, and would have been wrong if it had been: it knew four stages out
of twenty-six. A graph declared as data is only safe if something checks that the edges
are complete and that walking them touches the right state.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.project_state import (  # noqa: E402
    AgentDefinitionRecord,
    DedupeGroup,
    FutureRoleRecord,
    InferredSkillRecord,
    InferredTaskRecord,
    JEEvaluationResult,
    JobProfileDoc,
    JobRecordRaw,
    JobRecordStripped,
    NormalizedProfile,
    ProcessRecord,
    ProjectMeta,
    ProjectState,
    TaskActionRecord,
    TaskOpportunityRecord,
    TaskSkillRecord,
    TaxonomyMatchRecord,
    TierState,
)
from app.services import lineage  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")


def now():
    return datetime.now(timezone.utc)


def full_state() -> ProjectState:
    """A project with every step populated, so every invalidation has something to hit."""
    s = ProjectState(
        meta=ProjectMeta(
            client_slug="c", project_slug="p", display_name="P", created_at=now(), updated_at=now()
        )
    )
    s.raw_records = [JobRecordRaw(id="r1", source_file_id="f", job_title="T", raw_text="x")]
    s.stripped_records = [
        JobRecordStripped(id="r1", stripped_text="x", model="m", generated_at=now())
    ]
    s.dedupe_groups = [DedupeGroup(group_id="g1", member_ids=["r1"], representative_id="r1", avg_similarity=1.0)]
    s.dedupe_threshold = 0.95
    s.normalized_profiles = [
        NormalizedProfile(id="g1", source_record_ids=["r1"], purpose_statement="p", key_tasks=["t"], generated_at=now())
    ]
    for entity_tiers in (s.clustering_tiers, s.skills.clustering_tiers, s.tasks.clustering_tiers):
        for tier in ("profile", "category", "family"):
            entity_tiers[tier] = TierState(tier=tier, k=2, gate=0.58, names={0: "A", 1: "B"})
    s.job_profiles = [
        JobProfileDoc(
            profile_key="pk1", profile_cluster_id=0, clustering_version=1, title="T",
            content={}, html="<p/>", generated_at=now(),
        )
    ]
    s.je_results = [
        JEEvaluationResult(
            profile_key="pk1", clustering_version=1, framework_version_hash="h",
            personas={}, aggregate_score=1.0, level_name="L", computed_at=now(),
        )
    ]
    s.skills.inferred = [
        InferredSkillRecord(id="s1", name="S", description="d", kind="technical", source_profile_key="pk1")
    ]
    s.tasks.inferred = [
        InferredTaskRecord(id="t1", name="T", description="d", proportion=100.0, source_profile_key="pk1")
    ]
    s.matching.matches = [TaxonomyMatchRecord(profile_key="pk1", profile_title="T", matched=True)]
    w = s.workforce
    w.opportunity = [TaskOpportunityRecord(task_cluster_id=0, cluster_name="C", automation_pct=40.0, augmentation_pct=50.0, n_actions=1)]
    w.actions = [TaskActionRecord(id="a1", task_cluster_id=0, name="A", definition="d", pct_of_task=100.0, automation_pct=40.0, augmentation_pct=50.0)]
    w.skills_guidance = [
        TaskSkillRecord(id="sk1", profile_key="pk1", role_title="T", task_cluster_id=0,
                        cluster_name="C", name="n", description="d", hook="h", blob_path="b")
    ]
    w.agents = [
        AgentDefinitionRecord(id="ag1", task_cluster_id=0, cluster_name="C", name="N",
                              slug="n", purpose="p", blob_path="b")
    ]
    w.processes = [ProcessRecord(id="pr1", filename="f", blob_path="b", process_name="P", summary="s")]
    w.future_roles = [
        FutureRoleRecord(profile_key="pk1", title="T", evolution_today="a",
                         evolution_after_automation="b", evolution_future="c", future_purpose="d")
    ]
    return s


def main() -> int:
    print("The graph")
    keys = {s.key for s in lineage.STEPS}
    dangling = {c for s in lineage.STEPS for c in s.consumes if c not in keys}
    check("every `consumes` names a declared step", not dangling, str(sorted(dangling)))
    check("no step consumes itself", all(s.key not in s.consumes for s in lineage.STEPS))
    check("keys are unique", len(keys) == len(lineage.STEPS))

    # A cycle would make `descendants` loop forever; the BFS guards against it, but a
    # cycle in a *dependency* graph is a modelling error worth failing on.
    def reaches(a: str, b: str) -> bool:
        return b in lineage.descendants(a)

    cycles = [s.key for s in lineage.STEPS if reaches(s.key, s.key)]
    check("the graph is acyclic", not cycles, str(cycles))

    only_root = [s.key for s in lineage.STEPS if not s.consumes]
    check("exactly one root, and it is ingest", only_root == ["ingest"], str(only_root))

    print("\nReach")
    d = lineage.descendants("ingest")
    check("ingest reaches every other step", len(d) == len(lineage.STEPS) - 1, f"{len(d)} of {len(lineage.STEPS) - 1}")
    check("dedupe reaches the job hierarchy and the profiles",
          {"normalize", "job:profile", "job:family", "profiles", "evaluation"} <= set(lineage.descendants("dedupe")))
    check("dedupe reaches Workforce Studio, which the old dead function never did",
          {"opportunity", "automation", "future-roles"} <= set(lineage.descendants("dedupe")))
    check("re-inferring tasks reaches the opportunity assessment and both generators",
          {"task:profile", "task:family", "opportunity", "augmentation", "automation"}
          <= set(lineage.descendants("tasks:infer")))
    check("the last step has no descendants", lineage.descendants("future-roles") == [])
    check("a leaf's descendants exclude itself", "augmentation" not in lineage.descendants("augmentation"))
    check("declaration order is preserved",
          lineage.descendants("normalize").index("job:profile")
          < lineage.descendants("normalize").index("profiles"))
    try:
        lineage.descendants("nope")
        check("an unknown step raises", False)
    except KeyError:
        check("an unknown step raises", True)

    print("\nPreview counts what exists")
    s = full_state()
    p = lineage.preview(s, "dedupe")
    steps_named = {i["step"] for i in p["affected"]}
    check("preview lists the job hierarchy", "job:profile" in steps_named)
    check("preview lists the agents", "automation" in steps_named)
    check("preview separates clears from stale marks",
          len(p["clears"]) > 0 and len(p["marks_stale"]) > 0)
    check("profiles are marked stale, not cleared",
          any(i["step"] == "profiles" and i["verb"] == "mark_stale" for i in p["affected"]))
    check("clustering is cleared, not marked stale",
          any(i["step"] == "job:profile" and i["verb"] == "clear" for i in p["affected"]))
    check("confirmation is required when something gets cleared", p["needs_confirmation"] is True)
    check("preview changes nothing", len(s.job_profiles) == 1 and not s.job_profiles[0].stale)

    print("\nApply does what preview promised")
    s = full_state()
    promised = {(i["step"], i["verb"]) for i in lineage.preview(s, "dedupe")["affected"]}
    done = {(i["step"], i["verb"]) for i in lineage.apply(s, "dedupe")}
    check("apply matches preview exactly", promised == done,
          f"only in preview {promised - done}, only in apply {done - promised}")

    check("the job hierarchy is gone", s.clustering_tiers == {})
    check("the denormalised job view is gone too", s.clustering is None)
    check("normalised profiles cleared", s.normalized_profiles == [])
    check("skills and tasks inference cleared", s.skills.inferred == [] and s.tasks.inferred == [])
    check("both taxonomies' tiers cleared",
          s.skills.clustering_tiers == {} and s.tasks.clustering_tiers == {})
    check("matching cleared", s.matching.matches == [])
    check("opportunity and its actions cleared",
          s.workforce.opportunity == [] and s.workforce.actions == [])
    check("profile documents kept but stale", len(s.job_profiles) == 1 and s.job_profiles[0].stale)
    check("evaluations kept but stale", len(s.je_results) == 1 and s.je_results[0].stale)
    check("the step itself is untouched — dedupe is being re-run, not destroyed",
          len(s.dedupe_groups) == 1)
    check("its own input is untouched", len(s.stripped_records) == 1)

    print("\nA narrow repeat stays narrow")
    s = full_state()
    affected = {i["step"] for i in lineage.apply(s, "task:category")}
    check("re-cutting task categories reaches task domains", "task:family" in affected)
    check("and reaches the opportunity assessment", "opportunity" in affected)
    check("but leaves the skills taxonomy alone",
          "skill:profile" not in affected and s.skills.clustering_tiers != {})
    check("and leaves job profiles alone", not s.job_profiles[0].stale)
    check("and leaves inferred tasks alone — they feed the tier, not the reverse",
          len(s.tasks.inferred) == 1)

    print("\nAlready-invalid work is not re-reported")
    s = full_state()
    lineage.apply(s, "dedupe")
    second = lineage.apply(s, "dedupe")
    check("a second identical repeat reports nothing left to invalidate", second == [],
          str([i["step"] for i in second]))

    print("\nEvery step is reachable from the root, so nothing is orphaned")
    reachable = set(lineage.descendants("ingest")) | {"ingest"}
    check("no orphan steps", reachable == keys, str(sorted(keys - reachable)))

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
