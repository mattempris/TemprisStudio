"""Step 3's arithmetic, against hand-computed fixtures.

Every number above the action level is code, not judgement, so it can be checked
exactly. The model's part is stubbed; what is under test is:

  - percentages that do not sum are normalised to exactly 100
  - a score outside 0-80 is rejected, the call repeated, and clamped only if the
    repeat is also bad — and flagged when it is
  - the cluster roll-up is the effort-weighted mean of its actions
  - the role roll-up is the time-weighted mean of its clusters, over assessed time
    only, with coverage reported
  - actions appear in the graph only for an expanded task cluster
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.workforce import graph as wf  # noqa: E402
from app.services.workforce import opportunity as opp  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")


def close(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol


# --------------------------------------------------------------------------
# A stub standing in for the model, so the arithmetic is the only variable.
# --------------------------------------------------------------------------
def stub(payload, *, calls=None):
    def _fn(prompt, **kwargs):
        if calls is not None:
            calls.append(prompt)
        return payload if not isinstance(payload, list) else payload[min(len(calls) - 1, len(payload) - 1)]

    return _fn


IN = opp.ClusterInput(
    cluster_id=7,
    name="Handling Customer Complaints",
    category="Customer Resolution",
    domain="Customer Operations",
    tasks=[("Complaint Handling", "Investigates and resolves customer complaints.")],
    n_roles=3,
    proportion_sum=48.0,
)


def main() -> int:
    # ---- normalisation: 97 becomes exactly 100, largest absorbs the residual ----
    calls: list[str] = []
    opp.llm.complete_json = stub(
        {
            "actions": [
                {"name": "Acknowledging Complaints", "definition": "d", "pct_of_task": 30,
                 "automation_pct": 70, "augmentation_pct": 60},
                {"name": "Investigating History", "definition": "d", "pct_of_task": 47,
                 "automation_pct": 40, "augmentation_pct": 65},
                {"name": "Deciding Redress", "definition": "d", "pct_of_task": 20,
                 "automation_pct": 10, "augmentation_pct": 30},
            ]
        },
        calls=calls,
    )  # type: ignore[assignment]
    a = opp.assess_cluster(IN)
    total = sum(x.pct_of_task for x in a.actions)
    check("percentages normalised to exactly 100", close(total, 100.0, 0.001), f"{total}")
    check("raw sum recorded", close(a.raw_pct_sum, 97.0), f"{a.raw_pct_sum}")
    check("one call when the answer is in range", len(calls) == 1, f"{len(calls)} calls")
    check("not flagged as clamped", a.clamped is False)

    # Hand-computed: pcts rescale to 30.93 / 48.45 / 20.62 (largest absorbs residual).
    # automation = (30.93*70 + 48.45*40 + 20.62*10) / 100 = 21.65 + 19.38 + 2.06 = 43.1
    check("cluster automation is the effort-weighted mean", close(a.automation_pct, 43.1),
          f"{a.automation_pct} (expected 43.1)")
    # augmentation = (30.93*60 + 48.45*65 + 20.62*30)/100 = 18.56 + 31.49 + 6.19 = 56.2
    check("cluster augmentation likewise", close(a.augmentation_pct, 56.2),
          f"{a.augmentation_pct} (expected 56.2)")
    check("augmentation exceeds automation here", a.augmentation_pct > a.automation_pct)

    # ---- out of range: rejected once, then accepted ----
    calls = []
    good = {"actions": [{"name": "A", "definition": "d", "pct_of_task": 100,
                         "automation_pct": 55, "augmentation_pct": 60}]}
    bad = {"actions": [{"name": "A", "definition": "d", "pct_of_task": 100,
                        "automation_pct": 95, "augmentation_pct": 60}]}
    opp.llm.complete_json = stub([bad, good], calls=calls)  # type: ignore[assignment]
    a = opp.assess_cluster(IN)
    check("out-of-range score triggers a retry", len(calls) == 2, f"{len(calls)} calls")
    check("retry told the model what was wrong", "outside the permitted" in calls[1])
    check("retry result used, not clamped", close(a.automation_pct, 55.0) and not a.clamped)

    # ---- out of range twice: clamped, and says so ----
    calls = []
    opp.llm.complete_json = stub([bad, bad], calls=calls)  # type: ignore[assignment]
    a = opp.assess_cluster(IN)
    check("a stubborn cluster is clamped rather than lost", a.clamped is True)
    check("clamped to the ceiling", close(a.automation_pct, float(opp.SCORE_CEILING)),
          f"{a.automation_pct}")

    # ---- role roll-up ----
    # 50% of the week on cluster 1 (automation 60), 30% on cluster 2 (20),
    # 20% on cluster 3 (unassessed).
    # Assessed time = 80. automation = (50*60 + 30*20)/80 = (3000+600)/80 = 45.0
    role = opp.role_opportunity(
        profile_key="p1",
        title="Complaints Handler",
        headcount=10,
        tasks=[(50.0, 1), (30.0, 2), (20.0, 3)],
        cluster_scores={1: (60.0, 70.0), 2: (20.0, 40.0)},
        hours_per_fte_week=37.5,
    )
    check("role automation weights over assessed time only", close(role.automation_pct, 45.0),
          f"{role.automation_pct} (expected 45.0)")
    check("coverage reports the unassessed fifth", close(role.coverage_pct, 80.0),
          f"{role.coverage_pct}")
    # augmentation = (50*70 + 30*40)/80 = (3500+1200)/80 = 58.75 -> 58.8
    check("role augmentation likewise", close(role.augmentation_pct, 58.8),
          f"{role.augmentation_pct}")
    check("FTE released = automation x headcount", close(role.fte_released or 0, 4.5),
          f"{role.fte_released}")
    check("hours = FTE x hours per week", close(role.hours_per_week or 0, 168.75, 0.1),
          f"{role.hours_per_week}")

    no_hc = opp.role_opportunity(
        profile_key="p2", title="X", headcount=None, tasks=[(100.0, 1)],
        cluster_scores={1: (60.0, 70.0)},
    )
    check("no headcount means no invented FTE", no_hc.fte_released is None)

    unassessed = opp.role_opportunity(
        profile_key="p3", title="Y", headcount=5, tasks=[(100.0, 9)], cluster_scores={1: (60.0, 70.0)}
    )
    check("a wholly unassessed role reports zero coverage, not zero opportunity",
          unassessed.coverage_pct == 0.0 and unassessed.fte_released is None)

    # ---- the audit's discrimination check ----
    def fake(cid, auto):
        return opp.ClusterAssessment(
            cluster_id=cid, cluster_name=f"c{cid}",
            actions=[opp.Action("a", "d", 100.0, auto, auto)],
            raw_pct_sum=100.0, clamped=False, attempts=1,
        )

    flat = opp.audit([fake(i, 45 + i % 5) for i in range(20)], requested=20)
    spread = opp.audit([fake(i, 5 + i * 4) for i in range(20)], requested=20)
    check("a uniformly mid-range run is reported as not discriminating",
          flat.summary()["discriminating"] is False)
    check("a spread run is reported as discriminating",
          spread.summary()["discriminating"] is True)
    check("failures are counted", opp.audit([fake(1, 40), None], requested=2).failed == 1)

    # ---- the graph: action nodes only when expanded ----
    facts = wf.Facts(
        version=1,
        built_at="now",
        entities={
            "job": wf.EntityFacts(labels={"profile": {1: "Analyst"}, "category": {1: "Ops"},
                                          "family": {1: "Operations"}},
                                  ancestry={1: (1, 1)}, metrics={1: 4.0}, members={1: 4}),
            "skill": wf.EntityFacts(labels={"profile": {1: "Excel"}, "category": {1: "Tools"},
                                            "family": {1: "Technical"}},
                                    ancestry={1: (1, 1)}, metrics={1: 3.0}, members={1: 3}),
            "task": wf.EntityFacts(labels={"profile": {7: "Handling Complaints"},
                                           "category": {2: "Resolution"},
                                           "family": {3: "Customer"}},
                                   ancestry={7: (2, 3)}, metrics={7: 10.0}, members={7: 5}),
        },
        has_headcount=True,
        job_task=[(1, 7, 40.0)],
        actions=[
            wf.ActionFact(7, "Acknowledging", "d", 60.0, 70.0, 60.0),
            wf.ActionFact(7, "Deciding Redress", "d", 40.0, 10.0, 30.0),
        ],
        task_opportunity={7: (46.0, 48.0)},
        job_opportunity={1: (46.0, 48.0, 100.0)},
    )
    plain = wf.cut(facts, levels={"job": "profile", "skill": "profile", "task": "profile"})
    check("no action nodes until a cluster is opened", plain["totals"]["actions"] == 0)
    task_node = next(n for n in plain["nodes"] if n["entity"] == "task")
    check("a task cluster with actions stays expandable at the finest level",
          task_node["expandable"] is True)
    check("nodes carry the assessment", close(task_node["automation"], 46.0))
    skill_node = next(n for n in plain["nodes"] if n["entity"] == "skill")
    check("an unassessed hierarchy reports None, not zero", skill_node["automation"] is None)

    opened = wf.cut(
        facts,
        levels={"job": "profile", "skill": "profile", "task": "profile"},
        expanded={"task:profile:7"},
    )
    check("opening the cluster adds its actions", opened["totals"]["actions"] == 2)
    acts = [n for n in opened["nodes"] if n["entity"] == "action"]
    # 60% of a 10-FTE cluster
    check("an action's size is its share of the cluster's metric",
          close(next(a["metric"] for a in acts if a["name"] == "Acknowledging"), 6.0))
    check("actions are edged to their cluster",
          any(e["source"] == "task:profile:7" and e["target"].startswith("action:")
              for e in opened["edges"]))

    detail = wf.node_detail(facts, "action:7:0")
    check("an action node has its own detail", detail["name"] == "Acknowledging")
    check("and carries its siblings for context", len(detail["actions"]) == 2)
    check("and names its parent cluster", detail["parent"]["name"] == "Handling Complaints")

    tdetail = wf.node_detail(facts, "task:family:3")
    check("a task domain rolls its children's actions up", len(tdetail["actions"]) == 2)
    check("and carries the opportunity", close(tdetail["opportunity"]["automation"], 46.0))

    # A facts blob written before step 3 must still load.
    old = {k: v for k, v in facts.to_json().items() if k not in ("actions", "task_opportunity",
                                                                "job_opportunity")}
    reloaded = wf.Facts.from_json(old)
    check("a pre-step-3 facts blob still loads", reloaded.actions == [] and not reloaded.task_opportunity)

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
