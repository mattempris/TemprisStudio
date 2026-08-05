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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
