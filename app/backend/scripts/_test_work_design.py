"""The work pool's arithmetic, offline.

Every assertion here is a way the pool could be wrong while still drawing a plausible
treemap. A wrong number does not raise; it just misrepresents how an organisation spends its
week, which is the only thing the studio is for.

The load-bearing one is the per-holder identity. A drop from the pool into a designed job uses
`hours_per_holder_week`, so if the sum of those across every cluster is not one holder's week,
then dragging everything in either overfills or underfills a one-person job — and nothing
would say so.

Built on a hand-made `Facts` rather than a project, so the numbers are checkable by hand.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.project_state import (  # noqa: E402
    DesignedJobRecord,
    DesignedTaskLine,
    ProjectMeta,
    ProjectState,
)
from app.services.workforce import graph as wf  # noqa: E402
from app.services.workforce import work_design as wd  # noqa: E402

ok = True
HPW = 37.5


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")


def close(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol


def build_facts(*, has_headcount: bool = True, business: bool = False) -> wf.Facts:
    """Two job families, two task domains, deliberately lopsided headcount.

    Job 10 has 100 people and spends 80% of its week on task 100; job 11 has 2 people and
    spends 90% on task 101. Unweighted, task 101 would look like the bigger commitment. It is
    not: 100 x 0.8 beats 2 x 0.9 by a factor of forty-four.
    """
    jf = wf.EntityFacts(
        labels={
            "family": {1: "Operations", 2: "Specialist"},
            "category": {5: "Servicing", 6: "Advisory"},
            "profile": {10: "Case Handler", 11: "Technical Adviser"},
        },
        ancestry={10: (5, 1), 11: (6, 2)},
        metrics={10: 100.0 if has_headcount else 1.0, 11: 2.0 if has_headcount else 1.0},
        members={10: 4, 11: 1},
    )
    tf = wf.EntityFacts(
        labels={
            "family": {7: "Customer Operations", 8: "Technical"},
            "category": {70: "Case Work", 80: "Engineering"},
            "profile": {100: "Handling Cases", 101: "Deep Technical Work", 102: "Admin"},
        },
        ancestry={100: (70, 7), 101: (80, 8), 102: (70, 7)},
        metrics={},
        members={},
    )
    f = wf.Facts(
        version=1,
        built_at="x",
        entities={"job": jf, "task": tf},
        has_headcount=has_headcount,
        job_task=[(10, 100, 80.0), (10, 102, 20.0), (11, 101, 90.0), (11, 102, 10.0)],
        task_opportunity={100: (40.0, 60.0), 102: (55.0, 30.0)},  # 101 deliberately unassessed
        job_profile_keys={10: ("case-handler-aaa", "Case Handler"), 11: ("tech-adviser-bbb", "Technical Adviser")},
        has_business_framework=business,
    )
    if business:
        # Job 10's hundred people split across two departments; job 11's two sit in one.
        f.business_units = [
            wf.BusinessUnitFact(10, "Retail", "Servicing", "Branch", 60),
            wf.BusinessUnitFact(10, "Commercial", "Servicing", "Corporate", 40),
            wf.BusinessUnitFact(11, "Commercial", "Technical", "Platform", 2),
        ]
    return f


def _state_with(jobs: list[DesignedJobRecord]) -> ProjectState:
    """A minimal project carrying only what the allocation functions read."""
    now = datetime.now(timezone.utc)
    st = ProjectState(
        meta=ProjectMeta(
            client_slug="c", project_slug="p", display_name="C", created_at=now, updated_at=now
        )
    )
    st.work_design.jobs = list(jobs)
    return st


def main() -> int:
    f = build_facts()
    p = wd.pool(f, wd.Facets(), hours_per_fte_week=HPW)
    by = {c["cluster_id"]: c for c in p["clusters"]}

    print("Hours are headcount-weighted, not an unweighted mean of proportions")
    check(
        "task 100 = 0.8 x 100 people x 37.5 = 3,000 h/wk",
        close(by[100]["hours_per_week"], 3000.0),
        str(by[100]["hours_per_week"]),
    )
    check(
        "task 101 = 0.9 x 2 people x 37.5 = 67.5 h/wk",
        close(by[101]["hours_per_week"], 67.5),
        str(by[101]["hours_per_week"]),
    )
    check(
        "so the 100-person task outranks the 2-person one, which an unweighted mean would invert",
        p["clusters"][0]["cluster_id"] == 100,
        str([c["cluster_id"] for c in p["clusters"]]),
    )
    check(
        "the sample's capacity is its people x a week",
        close(p["sample"]["capacity_hours_per_week"], 102 * HPW),
        str(p["sample"]["capacity_hours_per_week"]),
    )
    check(
        "and every hour of it is accounted for, since proportions sum to 100 per job",
        close(p["sample"]["shown_hours_per_week"], 102 * HPW),
        str(p["sample"]["shown_hours_per_week"]),
    )

    print("\nThe per-holder identity — what a drop into a designed job uses")
    total_per_holder = sum(c["hours_per_holder_week"] for c in p["clusters"])
    check(
        "per-holder hours sum to one holder's week",
        close(total_per_holder, HPW, 0.02),
        f"{total_per_holder:.3f} vs {HPW}",
    )
    check(
        "so dragging every cluster into a one-person job fills it exactly",
        close(total_per_holder / HPW, 1.0, 0.001),
    )
    check(
        "task 100's per-holder rate is its hours over the sample's people",
        close(by[100]["hours_per_holder_week"], 3000.0 / 102),
        str(by[100]["hours_per_holder_week"]),
    )

    print("\nAn unassessed cluster is unknown, not zero")
    check("task 101 is flagged unassessed", by[101]["assessed"] is False)
    check(
        "and reports null rather than 0 for both axes",
        by[101]["automation"] is None and by[101]["augmentation"] is None,
        f"{by[101]['automation']} / {by[101]['augmentation']}",
    )
    check("its hours are still counted", close(by[101]["hours_per_week"], 67.5))
    check(
        "and the sample says how much time is unassessed",
        close(p["sample"]["unassessed_hours_per_week"], 67.5),
    )
    check("with a warning naming it", any("not been assessed" in w for w in p["warnings"]))

    print("\nRoles behind a cluster, so a line can be carved along a real boundary")
    check("task 102 is done by both jobs", by[102]["n_roles"] == 2, str(by[102]["n_roles"]))
    check(
        "the bigger contributor is listed first",
        by[102]["roles"][0]["title"] == "Case Handler",
        str([r["title"] for r in by[102]["roles"]]),
    )
    check(
        "and role hours sum to the cluster's hours",
        close(sum(r["hours_per_week"] for r in by[102]["roles"]), by[102]["hours_per_week"]),
    )
    check(
        "each role carries its profile_key, the join key outside the graph",
        by[102]["roles"][0]["profile_key"] == "case-handler-aaa",
    )

    print("\nJob facets narrow the sample")
    p2 = wd.pool(f, wd.Facets(job_family_ids=[2]), hours_per_fte_week=HPW)
    check("one family leaves one profile", p2["sample"]["job_profiles"] == 1)
    check(
        "and only its work, still summing to its own week",
        close(p2["sample"]["shown_hours_per_week"], 2 * HPW),
        str(p2["sample"]["shown_hours_per_week"]),
    )
    check("task 100 is gone entirely", 100 not in {c["cluster_id"] for c in p2["clusters"]})

    print("\nTask facets remove part of every job's week, and say so")
    p3 = wd.pool(f, wd.Facets(task_family_ids=[7]), hours_per_fte_week=HPW)
    check(
        "the sample's full week is still reported",
        close(p3["sample"]["sample_hours_per_week"], 102 * HPW),
        str(p3["sample"]["sample_hours_per_week"]),
    )
    check(
        "but shown hours are less than it",
        p3["sample"]["shown_hours_per_week"] < p3["sample"]["sample_hours_per_week"],
    )
    check(
        "and the shortfall is stated rather than left to look like the whole week",
        any("of the sample's week" in w for w in p3["warnings"]),
        str(p3["warnings"]),
    )

    print("\nNo headcount column: identical arithmetic, different label")
    fn = build_facts(has_headcount=False)
    pn = wd.pool(fn, wd.Facets(), hours_per_fte_week=HPW)
    check("the unit changes", pn["unit"] == "role-weeks", pn["unit"])
    check("the basis says why", "notional holder" in pn["basis"])
    check(
        "each profile counts as one, so the sample is 2 not 102",
        close(pn["sample"]["headcount"], 2.0),
        str(pn["sample"]["headcount"]),
    )
    check(
        "the per-holder identity still holds",
        close(sum(c["hours_per_holder_week"] for c in pn["clusters"]), HPW, 0.02),
    )

    print("\nA business-framework facet returns a partial profile, not all-or-nothing")
    fb = build_facts(business=True)
    pb = wd.pool(fb, wd.Facets(business_level_1=["Retail"]), hours_per_fte_week=HPW)
    check(
        "only job 10 matches, and only 60 of its 100 people",
        close(pb["sample"]["headcount"], 60.0),
        str(pb["sample"]["headcount"]),
    )
    check("the partial contribution is counted", pb["sample"]["partial_profiles"] == 1)
    check("and reported as a warning", any("only part of their headcount" in w for w in pb["warnings"]))
    check(
        "hours scale with the included share: 0.8 x 60 x 37.5 = 1,800",
        close({c["cluster_id"]: c for c in pb["clusters"]}[100]["hours_per_week"], 1800.0),
        str({c["cluster_id"]: c for c in pb["clusters"]}[100]["hours_per_week"]),
    )
    pb2 = wd.pool(fb, wd.Facets(business_level_1=["Commercial"]), hours_per_fte_week=HPW)
    check(
        "the other department sees the remaining 40 plus job 11's 2",
        close(pb2["sample"]["headcount"], 42.0),
        str(pb2["sample"]["headcount"]),
    )
    check(
        "so the two departments partition the workforce rather than double-counting it",
        close(pb["sample"]["headcount"] + pb2["sample"]["headcount"], 102.0),
    )

    print("\nEmpty and degenerate cases return rather than raise")
    check(
        "a facet matching nothing gives an empty pool",
        wd.pool(f, wd.Facets(job_family_ids=[999]), hours_per_fte_week=HPW)["clusters"] == [],
    )
    empty = wf.Facts(version=1, built_at="x", entities={})
    check(
        "a graph with no entities does not raise",
        wd.pool(empty, wd.Facets(), hours_per_fte_week=HPW)["sample"]["job_profiles"] == 0,
    )

    # ------------------------------------------------------------------ levers
    print("\nAutomation: absorbed actions leave, and are a union over agents")
    fl = build_facts()
    # Cluster 100 has 3,000 h/wk. Two actions: one at 60% automation taking half the task
    # (absorbed, since 60 >= the 40% threshold), one at 20% (retained). So removed is
    # 3000 x 0.5 x 0.6 = 900.
    fl.actions = [
        wf.ActionFact(100, "Drafting", "d", 50.0, 60.0, 80.0),
        wf.ActionFact(100, "Deciding", "d", 50.0, 20.0, 30.0),
    ]
    fl.agents = [
        wf.AgentFact(
            "ag-1", "Drafter", 100, 40.0, True, 0.20, "specification",
            [("Reviewing drafts", "d", 100.0)],
        )
    ]
    base = wd.pool(fl, wd.Facets(), hours_per_fte_week=HPW)
    r = wd.apply_levers(fl, base, agent_ids=["ag-1"], skill_ids=[], uplift=1.0)
    c100 = {c["cluster_id"]: c for c in r["clusters"]}[100]
    check(
        "removed = hours x pct_of_task x automation",
        close(c100["removed_by_automation_hours_per_week"], 900.0),
        str(c100["removed_by_automation_hours_per_week"]),
    )
    check(
        "the sub-threshold action's automatable time is surfaced, not silently lost",
        close(c100["retained_automatable_hours_per_week"], 300.0),
        str(c100["retained_automatable_hours_per_week"]),
    )
    check(
        "oversight is the agent's own fraction of what it absorbed",
        close(r["totals"]["oversight_hours_per_week"], 180.0),
        str(r["totals"]["oversight_hours_per_week"]),
    )
    check("and lands as a named task line", r["added"][0]["name"] == "Reviewing drafts")
    check(
        "labelled as coming from the specification",
        "specification" in r["added"][0]["basis"],
        r["added"][0]["basis"],
    )

    fl.agents.append(
        wf.AgentFact("ag-2", "Second", 100, 40.0, True, 0.20, "specification", [("Checking", "d", 100.0)])
    )
    r2 = wd.apply_levers(fl, base, agent_ids=["ag-1", "ag-2"], skill_ids=[], uplift=1.0)
    c2 = {c["cluster_id"]: c for c in r2["clusters"]}[100]
    check(
        "two agents on one cluster remove the SAME total as one — a union, not a sum",
        close(c2["removed_by_automation_hours_per_week"], 900.0),
        str(c2["removed_by_automation_hours_per_week"]),
    )
    check(
        "and share the oversight rather than doubling it",
        close(r2["totals"]["oversight_hours_per_week"], 180.0),
        str(r2["totals"]["oversight_hours_per_week"]),
    )
    check("producing one line each", len(r2["added"]) == 2, str(len(r2["added"])))

    print("\nA specification with no oversight tasks degrades to a labelled assumption")
    fl.agents = [wf.AgentFact("ag-1", "Drafter", 100, 40.0, True, 0.15, "fallback", [])]
    r3 = wd.apply_levers(fl, base, agent_ids=["ag-1"], skill_ids=[], uplift=1.0)
    check("still produces a line", len(r3["added"]) == 1)
    check(
        "named generically rather than left blank",
        r3["added"][0]["name"].startswith("Overseeing"),
        r3["added"][0]["name"],
    )
    check(
        "and says it is an assumption rather than the model's judgement",
        "assumption" in r3["added"][0]["basis"],
        r3["added"][0]["basis"],
    )

    print("\nThe residual-augmentation correction")
    # The identity that makes it safe: with nothing absorbed it must reduce exactly to the
    # cluster's own effort-weighted mean, so the correction is invisible when there is no
    # collision and self-correcting when there is.
    fl.agents = []
    r4 = wd.apply_levers(fl, base, agent_ids=[], skill_ids=[], uplift=1.0)
    c4 = {c["cluster_id"]: c for c in r4["clusters"]}[100]
    stored = 0.5 * 80.0 + 0.5 * 30.0
    check(
        "with nothing absorbed it equals the effort-weighted mean exactly",
        close(c4["residual_augmentation_pct"], stored),
        f"{c4['residual_augmentation_pct']} vs {stored}",
    )
    fl.agents = [wf.AgentFact("ag-1", "Drafter", 100, 40.0, True, 0.0, "specification", [])]
    r5 = wd.apply_levers(fl, base, agent_ids=["ag-1"], skill_ids=[], uplift=1.0)
    c5 = {c["cluster_id"]: c for c in r5["clusters"]}[100]
    check(
        "after absorbing the MORE augmentable action, the residual falls",
        c5["residual_augmentation_pct"] < stored,
        f"{c5['residual_augmentation_pct']} vs {stored}",
    )

    print("\nAugmentation is scoped to the role its skill was written for")
    fl.agents = []
    fl.augmentations = [
        wf.AugmentationFact("sk-1", "faster-drafting", "case-handler-aaa", "Case Handler", 100, 40.0)
    ]
    r6 = wd.apply_levers(fl, base, agent_ids=[], skill_ids=["sk-1"], uplift=1.0)
    c6 = {c["cluster_id"]: c for c in r6["clusters"]}[100]
    check(
        "freed = that role's hours x the residual augmentation",
        close(c6["freed_by_augmentation_hours_per_week"], 3000.0 * stored / 100.0),
        str(c6["freed_by_augmentation_hours_per_week"]),
    )
    check(
        "coverage reports how many of the cluster's roles it reached",
        close(c6["augmentation_coverage_pct"], 100.0),
        str(c6["augmentation_coverage_pct"]),
    )
    r7 = wd.apply_levers(fl, base, agent_ids=[], skill_ids=["sk-1"], uplift=0.5)
    check(
        "uplift scales it — the one assumption, applied visibly",
        close(
            {c["cluster_id"]: c for c in r7["clusters"]}[100]["freed_by_augmentation_hours_per_week"],
            c6["freed_by_augmentation_hours_per_week"] / 2,
        ),
    )

    print("\nThe two levers never sum past the work available")
    fl.agents = [wf.AgentFact("ag-1", "Drafter", 100, 40.0, True, 0.0, "specification", [])]
    r8 = wd.apply_levers(fl, base, agent_ids=["ag-1"], skill_ids=["sk-1"], uplift=1.0)
    over = [
        c
        for c in r8["clusters"]
        if c["removed_by_automation_hours_per_week"] + c["freed_by_augmentation_hours_per_week"]
        > c["as_is_hours_per_week"] + 0.01
    ]
    check("every cluster's removed + freed stays within its as-is hours", not over, str(over[:1]))
    check(
        "and the two are reported separately, never as one 'time saved'",
        "removed_by_automation_hours_per_week" in r8["clusters"][0]
        and "freed_by_augmentation_hours_per_week" in r8["clusters"][0]
        and "time_saved" not in r8["totals"],
    )

    print("\nWith the ceiling removed, a fully automatable cluster reaches zero")
    ff = build_facts()
    ff.actions = [wf.ActionFact(100, "Transferring", "d", 100.0, 100.0, 50.0)]
    ff.agents = [wf.AgentFact("ag-1", "Mover", 100, 100.0, False, 0.0, "specification", [])]
    rz = wd.apply_levers(
        ff, wd.pool(ff, wd.Facets(), hours_per_fte_week=HPW),
        agent_ids=["ag-1"], skill_ids=[], uplift=1.0,
    )
    cz = {c["cluster_id"]: c for c in rz["clusters"]}[100]
    check(
        "its whole area is absorbed and the tile goes to zero",
        close(cz["to_be_hours_per_week"], 0.0),
        str(cz["to_be_hours_per_week"]),
    )
    check(
        "and augmenting nothing does not divide by zero",
        close(cz["residual_augmentation_pct"], 0.0),
        str(cz["residual_augmentation_pct"]),
    )

    print("\nA lever outside the filter is skipped, loudly")
    fs2 = build_facts()
    fs2.agents = [wf.AgentFact("ag-x", "Elsewhere", 999, 40.0, True, 0.2, "specification", [])]
    rs = wd.apply_levers(
        fs2, wd.pool(fs2, wd.Facets(), hours_per_fte_week=HPW),
        agent_ids=["ag-x"], skill_ids=[], uplift=1.0,
    )
    check("it appears in skipped_agents", len(rs["skipped_agents"]) == 1)
    check("with a reason", "not in this sample" in rs["skipped_agents"][0]["reason"])
    check(
        "and a warning, rather than being silently dropped",
        any("outside this filter" in w for w in rs["warnings"]),
    )
    check(
        "and contributes no hours",
        close(rs["totals"]["removed_by_automation_hours_per_week"], 0.0),
    )

    # ------------------------------------------------- capacity and the pool draining
    print("\nCapacity: one job definition, headcount raises capacity not size")
    now = datetime.now(timezone.utc)
    job = DesignedJobRecord(
        id="wd-1", title="Designed", headcount=1.0, created_at=now, updated_at=now,
        tasks=[
            DesignedTaskLine(id="l1", name="A", task_cluster_id=100, hours_per_week=30.0),
            DesignedTaskLine(id="l2", name="B", task_cluster_id=102, hours_per_week=5.0),
        ],
    )
    cap = wd.capacity(job, hours_per_fte_week=HPW)
    check("capacity is headcount x a week", close(cap["capacity_hours_per_week"], HPW))
    check("fill is assigned over capacity", close(cap["fill_pct"], 100 * 35.0 / HPW), str(cap["fill_pct"]))
    check("not over capacity", cap["over_capacity"] is False)
    check("and the spare is stated", close(cap["spare_hours_per_week"], 2.5))

    job.headcount = 0.5
    cap = wd.capacity(job, hours_per_fte_week=HPW)
    check("halving headcount halves capacity", close(cap["capacity_hours_per_week"], HPW / 2))
    check("over capacity is reported, not rejected", cap["over_capacity"] is True)
    check(
        "required headcount is the useful output",
        close(cap["required_headcount"], 35.0 / HPW),
        str(cap["required_headcount"]),
    )
    check("fill_pct is unbounded — 187% is a sentence, not an error", cap["fill_pct"] > 100)
    check("and the message says it in words", "needs" in cap["message"], cap["message"])

    print("\nThe pool drains as work is allocated, and the invariant holds")
    st = _state_with([job])
    job.headcount = 1.0
    fp = build_facts()
    fp.actions = [wf.ActionFact(100, "Drafting", "d", 50.0, 60.0, 80.0),
                  wf.ActionFact(100, "Deciding", "d", 50.0, 20.0, 30.0)]
    applied = wd.apply_levers(
        fp, wd.pool(fp, wd.Facets(), hours_per_fte_week=HPW), agent_ids=[], skill_ids=[], uplift=1.0
    )
    alloc = wd.allocated_hours(st)
    drained = wd.drain(applied, alloc)
    t = drained["totals"]
    check("allocated hours are what the jobs hold", close(t["allocated_hours_per_week"], 35.0), str(t["allocated_hours_per_week"]))
    check(
        "remaining = as-is minus everything accounted for",
        close(t["remaining_hours_per_week"], t["as_is_hours_per_week"] - 35.0),
        str(t["remaining_hours_per_week"]),
    )
    check(
        "the conservation identity closes",
        close(t["conservation_check"], 0.0, 0.1),
        f"{t['conservation_check']}",
    )
    by = {c["cluster_id"]: c for c in drained["clusters"]}
    check(
        "the drawn hours of a cluster fall by what was taken from it",
        close(by[100]["hours_per_week"], 3000.0 - 30.0),
        str(by[100]["hours_per_week"]),
    )

    print("\nEditing a job must not drain the pool twice")
    excl = wd.allocated_hours(st, exclude_job_id="wd-1")
    check("its own allocation is excluded", excl == {}, str(excl))
    d2 = wd.drain(applied, excl)
    check(
        "so the pool shows the hours the edit can draw on, including its own",
        close(d2["totals"]["allocated_hours_per_week"], 0.0),
    )
    check(
        "which is the undrained total",
        close(d2["totals"]["remaining_hours_per_week"], d2["totals"]["as_is_hours_per_week"]),
    )

    print("\nDeleting a job returns its hours — no second code path")
    st.work_design.jobs = []
    back = wd.drain(applied, wd.allocated_hours(st))
    check(
        "removing the job restores the pool exactly",
        close(back["totals"]["remaining_hours_per_week"], t["as_is_hours_per_week"]),
        str(back["totals"]["remaining_hours_per_week"]),
    )

    print("\nOversight lines are created work, so they do not drain the pool")
    ovs = DesignedJobRecord(
        id="wd-2", title="With oversight", headcount=1.0, created_at=now, updated_at=now,
        tasks=[
            DesignedTaskLine(id="o1", name="Reviewing", task_cluster_id=100,
                             origin="agent_oversight", hours_per_week=4.0),
            DesignedTaskLine(id="a1", name="Real work", task_cluster_id=100, hours_per_week=6.0),
        ],
    )
    a2 = wd.allocated_hours(_state_with([ovs]))
    check(
        "only the as-is line counts against the pool",
        close(a2.get(100, 0.0), 6.0),
        str(a2.get(100)),
    )
    check(
        "a hand-typed line with no cluster cannot consume pool hours either",
        wd.allocated_hours(
            _state_with([
                DesignedJobRecord(id="wd-3", title="Manual", created_at=now, updated_at=now,
                                  tasks=[DesignedTaskLine(id="m", name="Typed", origin="manual",
                                                          hours_per_week=9.0)])
            ])
        ) == {},
    )

    print("\nThe target profile is the deliverable view")
    tgt = wd.target_profile(_state_with([ovs]), hours_per_fte_week=HPW)
    check("it totals every line", close(tgt["totals"]["hours_per_week"], 10.0))
    check(
        "and keeps oversight separate, so supervision is visible",
        close(tgt["totals"]["oversight_hours_per_week"], 4.0),
        str(tgt["totals"]["oversight_hours_per_week"]),
    )
    check("with one row per (origin, cluster)", len(tgt["lines"]) == 2, str(len(tgt["lines"])))

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
