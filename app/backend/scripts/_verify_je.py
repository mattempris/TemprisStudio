"""Verify the JE ensemble produces sensible, well-formed scores on real profiles."""
from app.services.evaluation.job_evaluation import (
    PERSONAS,
    evaluate_one,
    load_default_framework,
    weighted_score,
)

fw = load_default_framework()

# Two profiles at deliberately different seniority, so the evaluation has to
# separate them. Content shaped like real generate_content() output.
DIRECTOR = {
    "about_role": [
        "Leads all water production and distribution operations for a regional water company, "
        "accountable for supply reliability, water quality compliance and health & safety."
    ],
    "responsibilities": [
        "Own operational performance of clean water production and distribution",
        "Lead a directorate of several hundred operational staff through senior managers",
        "Accountable for a multi-million pound annual operating budget",
        "Ensure regulatory compliance with DWI and OFWAT obligations",
        "Set the operational strategy for the region",
    ],
    "requirements": ["Degree in engineering or science", "15+ years water industry experience"],
    "essential_skills": ["Senior operational leadership", "Regulatory engagement", "Budget management"],
    "required_of_you": [
        {"label": "Hours", "value": "Senior leadership hours with on-call incident escalation"},
        {"label": "Travel", "value": "Regular travel across regional operational sites"},
    ],
    "reporting_line": "Reports to the Chief Operating Officer; leads 4 senior managers and ~300 staff",
    "budget_responsibility": "Owns a £40m annual operating budget",
}

ANALYST = {
    "about_role": [
        "Analyses environmental and operational data to identify storm overflow trends and "
        "support interventions that reduce pollution."
    ],
    "responsibilities": [
        "Build dashboards and analytics products for stakeholders",
        "Identify trends and anomalies in storm overflow data",
        "Support root cause analysis and programme evaluation",
        "Document data definitions and methods",
    ],
    "requirements": ["Degree in a numerate discipline", "2+ years data analysis experience"],
    "essential_skills": ["SQL and Python", "Data visualisation", "Stakeholder communication"],
    "required_of_you": [{"label": "Working Environment", "value": "Office/hybrid"}],
    "reporting_line": "Reports to the Data & Insight Manager; no direct reports",
    "budget_responsibility": None,
}

results = {}
for key, title, content in [
    ("director-of-water-operations", "Director of Water Operations", DIRECTOR),
    ("clean-rivers-data-analyst", "Clean Rivers and Seas Data Analyst", ANALYST),
]:
    print(f"\n=== {title} ===")
    res = evaluate_one(key, title, content, fw)
    results[key] = res
    print(f"  persona scores : {res.persona_scores}")
    print(f"  aggregate      : {res.aggregate_score}  ->  LEVEL: {res.level_name}")
    print(f"  spread (G-H)   : {res.spread}")
    print("  domain rollups :")
    for dom, val in res.domain_subtotals(fw).items():
        print(f"    {dom:22s} {val:6.2f}")
    sample_dom = fw.domains[0].name
    print(f"  sample rationale ({sample_dom}, Balanced):")
    print(f"    {res.personas['Balanced'][sample_dom]['Rationale'][:200]}")

    # invariants
    for d in fw.domains:
        for s in d.subdomains:
            g = res.personas["Generous"][d.name][s.name]
            b = res.personas["Balanced"][d.name][s.name]
            h = res.personas["Harsh"][d.name][s.name]
            assert g >= b >= h, f"clamp violated {d.name}/{s.name}: G={g} B={b} H={h}"
            assert 1 <= h <= 5 and 1 <= g <= 5, f"out of range {d.name}/{s.name}"
        for p in PERSONAS:
            assert res.personas[p][d.name]["Rationale"].strip(), f"{p}/{d.name}: empty rationale"
    assert res.level_name != "Unbanded"
    assert res.persona_scores["Generous"] >= res.aggregate_score >= res.persona_scores["Harsh"]
    # a hollow response would score 0.0 across the board
    assert res.aggregate_score > 5.0, f"suspiciously low aggregate {res.aggregate_score} — hollow response?"

print("\n=== invariants ===")
print("  clamp (G >= B >= H) held for every subfactor")
print("  all scores within 1-5, all rationales non-empty")

d, a = results["director-of-water-operations"], results["clean-rivers-data-analyst"]
print(f"\n=== seniority separation ===")
print(f"  Director {d.aggregate_score} ({d.level_name})  vs  Analyst {a.aggregate_score} ({a.level_name})")
assert d.aggregate_score > a.aggregate_score, "a Director should evaluate above an Analyst"
print("  Director evaluates above Analyst")

print("\n=== weighting sanity ===")
mid = weighted_score({dm.name: {**{s.name: 3 for s in dm.subdomains}, "Rationale": "x"} for dm in fw.domains}, fw)
top = weighted_score({dm.name: {**{s.name: 5 for s in dm.subdomains}, "Rationale": "x"} for dm in fw.domains}, fw)
print(f"  all-3s -> {mid} (expect 50.0)   all-5s -> {top} (expect 100.0)")
assert abs(mid - 50.0) < 0.01 and abs(top - 100.0) < 0.01

print("\nJE ENSEMBLE VERIFIED")
