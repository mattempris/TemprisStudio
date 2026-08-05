"""Step 6's schema split and spec assembly, offline.

The split into two grammar-constrained halves is the load-bearing decision here: the
full schema exceeds the API's 24-parameter limit, so the halves are what actually get
sent. If a later edit adds a field to AGENT_SCHEMA and forgets to put it in a half, the
field silently never gets asked for — the spec assembles, the section is just empty.
These assertions are what make that a failure rather than a mystery.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.workforce import agents as ag  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")


def close(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol


def count_params(node) -> int:
    """Properties at every level.

    NOT the API's own rule — the compile limit does not map onto this number, or onto
    any other count I could derive (the ops half compiles at 32 by this measure while
    the full schema fails at 54). Kept only as a rough size signal, so a half that grows
    a lot gets noticed and re-probed rather than silently crossing the real threshold.
    Whether a schema compiles is settled by scripts/_probe_agent_grammar.py, not here.
    """
    n = 0
    if isinstance(node, dict):
        if "properties" in node and isinstance(node["properties"], dict):
            n += len(node["properties"])
        for v in node.values():
            n += count_params(v)
    elif isinstance(node, list):
        for v in node:
            n += count_params(v)
    return n


# Sizes as probed on 2026-08-05: business 26 and ops 32 both compile, full 58 does not.
# A half that grows past its recorded size needs the probe re-run before it is trusted.
#
# Business went 22 -> 26 when `oversight_tasks` was added. The probe was re-run and both
# halves still compile, along with `ops + oversight` and a flat three-array fallback — so
# there is headroom, not just a pass. This guard is the reason that happened before the
# field was relied on rather than after a bulk run failed.
PROBED_SIZES = {"business": 26, "ops": 32}


MODEL_OUTPUT = {
    "purpose": "Drafts first-response letters for customer complaints.",
    "problem_statement": "Handlers spend most of a complaint on drafting rather than deciding.",
    "goals": [{"description": "Cut drafting time", "metric": "median minutes per complaint", "target": "under 10"}],
    "non_goals": ["Deciding redress"],
    "assumptions": ["Case history is available in the system of record"],
    "constraints": ["Data protection applies to every complaint record"],
    "success_criteria": [{"description": "Drafts accepted unedited", "measurement_method": "sampled review"}],
    "capabilities": [
        {
            "name": "Draft acknowledgement",
            "description": "Produces the first response.",
            "priority": "must_have",
            "acceptance_criteria": ["Given a complaint, when logged, then a draft exists"],
            "edge_cases": ["Complaint with no identifiable customer record"],
            "inputs": ["complaint text"],
            "outputs": ["draft letter"],
            "data_sources": ["case management system"],
        }
    ],
    "retained_by_people": ["Deciding redress", "Signing off the final letter"],
    "workflow_steps": [
        {"type": "trigger", "description": "A complaint is logged."},
        {"type": "human_review", "description": "A handler approves the draft."},
    ],
    "trigger": "A complaint is logged.",
    "completion_definition": "A draft is attached to the case.",
    "user_personas": [
        {"name": "Complaints Handler", "description": "Owns the case.", "skill_level": "expert",
         "jobs_to_be_done": ["Resolve the complaint"]}
    ],
    "knowledge_sources": [
        {"name": "Case management system", "type": "structured", "access_method": "API", "ownership": "Operations"}
    ],
    "tools": [{"name": "Case API", "type": "api", "description": "Reads and writes cases."}],
    "risks": [{"description": "A draft misstates the outcome", "likelihood": "medium", "impact": "high",
               "mitigation": "Handler approval before sending"}],
    "kpis": [{"name": "Median drafting time", "target": "under 10 minutes"}],
    "system_instructions_summary": "Draft, never decide.",
    "pii_types": ["customer contact details"],
    "compliance_frameworks": ["UK GDPR"],
    "escalation_path": "The owning complaints handler.",
    "human_in_the_loop": True,
}

INPUT = ag.AgentInput(
    task_cluster_id=7,
    cluster_name="Handling Customer Complaints",
    category="Customer Resolution",
    domain="Customer Operations",
    automation_pct=43.1,
    augmentation_pct=56.2,
    actions=[
        ("Drafting Response Letters", "Writes the first response.", 70.0, 60.0),
        ("Deciding Redress", "Decides what is owed.", 10.0, 30.0),
    ],
    roles=[("Complaints Handler", 40.0), ("Team Leader", 8.0)],
    task_names=["Complaint Handling", "Redress Decisions"],
    absorbable=4.31,
    unit="FTE",
    client_name="Example Bank",
)


def main() -> int:
    # ---- the split ----
    props = set(ag.AGENT_SCHEMA["properties"])
    halves = set(ag.BUSINESS_KEYS) | set(ag.OPS_KEYS)
    check("the halves cover every schema property", halves == props,
          f"missing {sorted(props - halves)}, extra {sorted(halves - props)}")
    check("the halves do not overlap", not (set(ag.BUSINESS_KEYS) & set(ag.OPS_KEYS)))
    check("every property is required in its half",
          set(ag.BUSINESS_SCHEMA["required"]) == set(ag.BUSINESS_KEYS)
          and set(ag.OPS_SCHEMA["required"]) == set(ag.OPS_KEYS))
    for label, s in (("business", ag.BUSINESS_SCHEMA), ("ops", ag.OPS_SCHEMA)):
        n = count_params(s)
        check(
            f"the {label} half has not grown past its last probed size",
            n <= PROBED_SIZES[label],
            f"{n} vs {PROBED_SIZES[label]} probed — re-run scripts/_probe_agent_grammar.py",
        )
    check("the full schema is larger than either half, which is why it is split",
          count_params(ag.AGENT_SCHEMA) > max(PROBED_SIZES.values()),
          f"{count_params(ag.AGENT_SCHEMA)} vs halves {PROBED_SIZES}")
    check("additionalProperties is set on both halves — the API requires it",
          ag.BUSINESS_SCHEMA["additionalProperties"] is False
          and ag.OPS_SCHEMA["additionalProperties"] is False)

    # ---- the prompt ----
    p = INPUT.prompt()
    check("prompt separates absorbable from retained actions",
          "Actions the agent should absorb" in p and "must stay with a person" in p)
    check("the low-automation action is on the retained side",
          p.index("Deciding Redress") > p.index("Actions that must stay with a person"))
    check("prompt states the time released and its unit", "4.31 FTE" in p)
    check("prompt names the roles that do the work", "Complaints Handler: 40%" in p)
    check("prompt names the organisation", "Example Bank" in p)

    # ---- assembly ----
    spec = ag.build_spec(INPUT, MODEL_OUTPUT, client_slug="example-bank")
    check("all eight sections are present", all(s in spec.spec for s in ag.SECTIONS),
          str([s for s in ag.SECTIONS if s not in spec.spec]))
    check("ids are stamped sequentially", spec.spec["business_context"]["goals"][0]["id"] == "G1")
    check("capabilities are numbered",
          spec.spec["functional_requirements"]["capabilities"][0]["capability_id"] == "CAP1")
    check("workflow steps are numbered",
          spec.spec["functional_requirements"]["workflows"][0]["steps"][0]["step_id"] == "S1")
    check("the retained work reaches the safety policy",
          "Deciding redress" in spec.spec["non_functional_requirements"]["safety_alignment"]["policy_scope"])
    check("the absorbed actions are recorded in scope",
          spec.spec["business_context"]["scope"]["absorbed_actions"] == ["Drafting Response Letters"])
    check("the retained action is NOT listed as absorbed",
          "Deciding Redress" not in spec.spec["business_context"]["scope"]["absorbed_actions"])
    check("the opportunity is carried with its provenance",
          "not a measurement" in spec.spec["meta"]["opportunity"]["basis"])
    check("human-in-the-loop drives tool approval",
          spec.spec["technical_architecture"]["tools_and_integrations"]["tool_registry"][0][
              "safety_controls"]["human_approval_required"] is True)
    check("the model's compliance frameworks are used, not a hardcoded regime",
          spec.spec["non_functional_requirements"]["security_privacy"]["compliance"]["frameworks"] == ["UK GDPR"])

    # Every host must be under the reserved .example TLD — a spec that names a
    # plausible real internal system is worse than one that names none.
    import json as _json
    text = _json.dumps(spec.spec)
    hosts = [w for w in re.findall(r"https?://([a-z0-9.\-]+)", text)]
    check("every URL uses the reserved .example TLD", all(h.endswith(".example") for h in hosts),
          str(sorted({h for h in hosts if not h.endswith('.example')})))
    check("the client slug appears in the placeholder hosts",
          any("example-bank.example" in h for h in hosts), str(sorted(set(hosts))[:3]))

    # ---- an unsupervised agent flips the controls ----
    unsup = ag.build_spec(INPUT, {**MODEL_OUTPUT, "human_in_the_loop": False}, client_slug="x")
    check("an unsupervised agent does not require tool approval",
          unsup.spec["technical_architecture"]["tools_and_integrations"]["tool_registry"][0][
              "safety_controls"]["human_approval_required"] is False)
    check("and does not require action confirmations",
          unsup.spec["technical_architecture"]["tools_and_integrations"]["action_policies"][
              "side_effecting_actions"]["require_confirmations"] is False)

    # ---- an empty compliance list falls back rather than shipping nothing ----
    bare = ag.build_spec(INPUT, {**MODEL_OUTPUT, "compliance_frameworks": []}, client_slug="x")
    check("an empty compliance list falls back to data protection",
          bare.spec["non_functional_requirements"]["security_privacy"]["compliance"]["frameworks"] == ["UK GDPR"])

    check("slugify handles punctuation", ag.slugify("Handling  Customer/Complaints!") == "handling-customer-complaints")

    # ---- oversight tasks ----
    # Work Design multiplies these percentages by the time an agent absorbs, so a bad one
    # produces a plausible number rather than an error. Every case below is a way that
    # could happen quietly.
    print("\nOversight tasks — the cost an agent creates, not the work it takes")
    tasks, total, clamped = ag._oversight_tasks(
        [
            {"name": "Reviewing drafted letters", "definition": "d", "pct_of_absorbed_time": 18},
            {"name": "Handling escalations", "definition": "e", "pct_of_absorbed_time": 7},
        ]
    )
    check("both tasks survive", len(tasks) == 2)
    check("the total is their sum", close(total, 25.0), f"{total}")
    check("and is not flagged as clamped", clamped is False)

    tasks, total, clamped = ag._oversight_tasks(
        [{"name": "A", "pct_of_absorbed_time": 70}, {"name": "B", "pct_of_absorbed_time": 30}]
    )
    check("a total above the ceiling is clamped", clamped is True and close(total, ag.OVERSIGHT_CEILING))
    check(
        "and scaled proportionally, keeping the model's relative weighting",
        close(tasks[0]["pct_of_absorbed_time"], 42.0) and close(tasks[1]["pct_of_absorbed_time"], 18.0),
        str([t["pct_of_absorbed_time"] for t in tasks]),
    )

    check(
        "an unnamed task is dropped — it could not appear in a job description",
        ag._oversight_tasks([{"name": "  ", "pct_of_absorbed_time": 5}])[0] == [],
    )
    check(
        "a zero-cost task is dropped — supervising something is never free",
        ag._oversight_tasks([{"name": "A", "pct_of_absorbed_time": 0}])[0] == [],
    )
    check(
        "a missing list is empty rather than an error",
        ag._oversight_tasks(None) == ([], 0, False),
    )

    spec = ag.build_spec(INPUT, MODEL_OUTPUT, client_slug="x")
    scope = spec.spec["business_context"]["scope"]
    check("the spec carries them beside what the agent absorbs", "oversight_tasks" in scope)
    check(
        "the spec records the total and whether it was clamped",
        "oversight_pct_of_absorbed_time" in scope and "oversight_clamped" in scope,
    )
    check(
        "and they are on the returned spec, so the route need not read the blob",
        isinstance(spec.oversight_tasks, list) and spec.oversight_pct_total >= 0,
    )

    # A specification written before this field existed. It must degrade, not fail — every
    # agent on every existing project is in this state.
    legacy = ag.build_spec(INPUT, {k: v for k, v in MODEL_OUTPUT.items() if k != "oversight_tasks"},
                           client_slug="x")
    check("a specification with no oversight tasks still builds", legacy.oversight_pct_total == 0)
    check("and reports an empty list rather than inventing one", legacy.oversight_tasks == [])

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    import re  # noqa: E402  — used inside main only

    raise SystemExit(main())
