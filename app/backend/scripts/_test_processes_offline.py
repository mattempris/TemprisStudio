"""Step 2's matching and step 4/7's arithmetic, offline.

The interesting logic here is the three-way outcome of matching a process step onto the
task taxonomy: trusted geometry, model confirmation, or an honest no-match. The
no-match branch is the one worth protecting — a step with no plausible task cluster is
work the job descriptions never mentioned, which is a finding, and forcing it into the
nearest cluster would destroy exactly the signal step 2 exists to produce.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.workforce import future_roles as fr  # noqa: E402
from app.services.workforce import processes as proc  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")


def unit(v: list[float]) -> np.ndarray:
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


def step(name: str, seq: int, **kw) -> proc.InferredStep:
    return proc.InferredStep(
        name=name,
        description=kw.get("description", "does a thing"),
        actor=kw.get("actor", "Handler"),
        system=kw.get("system", "none"),
        automated=kw.get("automated", False),
        handoff=kw.get("handoff", False),
        sign_off=kw.get("sign_off", False),
        sequence=seq,
    )


def main() -> int:
    # Clusters on orthogonal axes so cosine is exactly controllable. Six dimensions, not
    # three: with the candidates spanning the whole space, any vector in it has a maximum
    # cosine of at least 1/sqrt(3) = 0.577, so the no-match branch is unreachable. The
    # spare axes are the room needed to place a step genuinely far from the taxonomy.
    candidates = [
        proc.ClusterCandidate(1, "Payroll Processing", unit([1, 0, 0, 0, 0, 0])),
        proc.ClusterCandidate(2, "Complaint Handling", unit([0, 1, 0, 0, 0, 0])),
        proc.ClusterCandidate(3, "Vendor Management", unit([0, 0, 1, 0, 0, 0])),
    ]

    # Cosine against an orthogonal unit axis is just that component over the norm, so
    # each of the three branches can be hit exactly. Asserted below rather than trusted,
    # because a fixture that lands in the wrong band tests nothing and still passes.
    CONFIDENT = unit([1, 0.05, 0, 0, 0, 0])          # ~0.999 to cluster 1 — above the gate
    UNCERTAIN = unit([0.2, 0.55, 0.2, 0.6, 0.4, 0])  # ~0.58 to cluster 2 — in the band
    DISTANT = unit([0.1, 0.1, 0.1, 1, 1, 1])         # ~0.06 to each — below the ceiling

    print("Matching: the band each fixture lands in")
    matrix = np.vstack([c.centroid for c in candidates])
    for label, v, want in (
        ("confident", CONFIDENT, "above the gate"),
        ("uncertain", UNCERTAIN, "in the confirmation band"),
    ):
        best = float(np.max(matrix @ v))
        in_band = (
            best >= proc.MATCH_GATE
            if want == "above the gate"
            else proc.NO_MATCH_CEILING <= best < proc.MATCH_GATE
        )
        check(f"the {label} fixture is {want}", in_band, f"cosine {best:.3f}")

    print("\nMatching: three outcomes")
    steps = [step("Run payroll", 1), step("Middling thing", 2)]
    vectors = np.vstack([CONFIDENT, UNCERTAIN])
    asked: list[str] = []

    def confirm(s, shortlist):
        asked.append(s.name)
        return {"cluster_id": 2, "confidence": 0.71, "reasoning": "same work", "no_match": False}

    matches = proc.match_steps(steps, vectors, candidates, confirm=confirm)
    by_seq = {m.sequence: m for m in matches}
    check("a confident geometric match is taken without asking the model",
          by_seq[1].cluster_id == 1 and not by_seq[1].routed_by_llm, str(by_seq[1]))
    check("an uncertain match is confirmed by the model",
          by_seq[2].routed_by_llm and by_seq[2].cluster_id == 2, str(by_seq[2]))
    check("the model was asked only about the uncertain steps",
          "Run payroll" not in asked, str(asked))
    check("the model's confidence is recorded", by_seq[2].confidence == 0.71)

    print("\nNo-match is preserved, not forced")
    distant_best = float(np.max(matrix @ DISTANT))
    check("the distant fixture is below the no-match ceiling",
          distant_best < proc.NO_MATCH_CEILING, f"cosine {distant_best:.3f}")
    m = proc.match_steps(
        [step("Something novel", 1)], np.vstack([DISTANT]), candidates, confirm=confirm
    )[0]
    check("a distant step is recorded as unmatched", m.cluster_id is None, str(m))
    check("and says why", "job descriptions" in m.reasoning, m.reasoning)
    check("and the model was not asked", "Something novel" not in asked, str(asked))

    print("\nThe model may still return no match from a shortlist")

    def refuse(s, shortlist):
        return {"cluster_id": -1, "confidence": 0.2, "reasoning": "none of these", "no_match": True}

    m2 = proc.match_steps(
        [step("Ambiguous", 1)], np.vstack([UNCERTAIN]), candidates, confirm=refuse
    )[0]
    check("a model refusal is honoured", m2.cluster_id is None and m2.routed_by_llm, str(m2))

    print("\nNo model available falls back to geometry, and says so")
    m3 = proc.match_steps([step("Ambiguous", 1)], np.vstack([UNCERTAIN]), candidates, confirm=None)[0]
    check("geometry is used rather than dropping the step", m3.cluster_id == 2, str(m3))
    check("and the fallback is recorded", "no confirmation" in m3.reasoning, m3.reasoning)

    print("\nAn empty taxonomy does not crash")
    m4 = proc.match_steps([step("Anything", 1)], np.vstack([unit([1, 0])]), [])
    check("every step comes back unmatched", m4[0].cluster_id is None)

    print("\nCentroids")
    members = np.array([[1, 0], [0.8, 0.6], [0, 1]], dtype=np.float32)
    cands = proc.cluster_centroids(
        members, ["a", "b", "c"], {"a": 1, "b": 1, "c": 2}, {1: "One", 2: "Two"}
    )
    one = next(c for c in cands if c.cluster_id == 1)
    check("a centroid is the unit-normalised mean of its members",
          abs(float(np.linalg.norm(one.centroid)) - 1.0) < 1e-5)
    check("an item missing from the index is skipped rather than fatal",
          len(proc.cluster_centroids(members, ["a"], {"a": 1, "zz": 2}, {1: "One"})) == 1)

    print("\nProcess counts are measured, not asked")
    p = proc.InferredProcess(
        "Offer to Hire", "summary", "medium",
        steps=[
            step("Raise requisition", 1, actor="Hiring Manager", sign_off=True),
            step("Approve headcount", 2, actor="Finance", sign_off=True, handoff=True),
            step("Post advert", 3, actor="Recruiter", automated=True),
            step("Screen applicants", 4, actor="Recruiter"),
            step("Unspecified step", 5, actor="unspecified"),
        ],
    )
    check("manual steps exclude the already-automated one", p.manual_steps == 4, str(p.manual_steps))
    check("actors are de-duplicated", p.actors == ["Hiring Manager", "Finance", "Recruiter"], str(p.actors))
    check("'unspecified' is not counted as an actor", "unspecified" not in p.actors)

    print("\nTo-be counts cannot exceed the as-is")
    proc.llm.complete_json = lambda *a, **k: {  # type: ignore[assignment]
        "as_is_narrative": "n", "to_be_narrative": "n",
        "what_changes": ["Post advert absorbed"],
        "to_be_steps": 99, "to_be_manual_touchpoints": 99,
        "to_be_actors": 99, "to_be_sign_offs": 99,
        "effort_reduction_pct": 250, "elapsed_reduction_pct": -5,
        "risks": ["r"], "prerequisites": ["p"],
    }
    a = proc.assess_process(p, [], {})
    check("to-be steps are capped at the as-is count", a.to_be_steps == 5, str(a.to_be_steps))
    check("to-be manual touchpoints capped too", a.to_be_manual_touchpoints == 4)
    check("to-be sign-offs capped at the as-is count", a.to_be_sign_offs == 2)
    check("an over-range reduction is clamped to the ceiling", a.effort_reduction_pct == 70.0)
    check("a negative reduction is clamped to zero", a.elapsed_reduction_pct == 0.0)

    print("\nFuture roles")
    inp = fr.FutureRoleInput(
        profile_key="p1", title="Payroll Analyst", automation_pct=38.0, augmentation_pct=52.0,
        tasks=[
            ("Payroll Processing", 50.0, 60.0, 70.0),
            ("Handling Queries", 30.0, 30.0, 55.0),
            ("Coaching Juniors", 20.0, 5.0, 15.0),
        ],
    )
    # 50%*60 + 30%*30 + 20%*5 = 30 + 9 + 1 = 40.0
    check("time released is the automatable share of each task, summed",
          abs(inp.time_released_pct - 40.0) < 0.01, str(inp.time_released_pct))
    check("absorbed lists only the genuinely automatable tasks",
          inp.absorbed == ["Payroll Processing"], str(inp.absorbed))
    check("coaching is not treated as absorbed", "Coaching Juniors" not in inp.absorbed)
    pr = inp.prompt()
    check("the prompt orders tasks by share of the week",
          pr.index("Payroll Processing") < pr.index("Coaching Juniors"))

    fr.llm.complete_json = lambda *a, **k: {  # type: ignore[assignment]
        "evolution_today": "t", "evolution_after_automation": "a", "evolution_future": "f",
        "future_purpose": "p", "future_responsibilities": ["own the controls"],
        "deepened_tasks": ["Coaching Juniors"], "skills_to_build": ["review agent output"],
        "deliberate_practice": [],
    }
    role = fr.design_role(inp)
    check("a missing deliberate-practice list becomes a stated gap, not silence",
          len(role.deliberate_practice) == 1 and "decide what" in role.deliberate_practice[0],
          str(role.deliberate_practice))
    check("absorbed tasks are computed, not taken from the model",
          role.absorbed_tasks == ["Payroll Processing"])

    fr.llm.complete_json = lambda *a, **k: {  # type: ignore[assignment]
        "evolution_today": "t", "evolution_after_automation": "a", "evolution_future": "f",
        "future_purpose": "p", "future_responsibilities": [],
        "deepened_tasks": [], "skills_to_build": [], "deliberate_practice": ["x"],
    }
    try:
        fr.design_role(inp)
        check("a design with no responsibilities is rejected", False)
    except fr.FutureRoleError:
        check("a design with no responsibilities is rejected", True)

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
