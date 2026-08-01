"""Is the flat JE schema admitted where the nested one was not?"""
import json
import time

from app.services import llm
from app.services.evaluation.job_evaluation import (
    InvalidEvaluation,
    build_schema,
    build_system_prompt,
    build_user_prompt,
    _expand_flat_response,
    load_default_framework,
    validate_raw_evaluations,
)

fw = load_default_framework()
CONTENT = {
    "about_role": ["Leads water production and distribution for a large regional water company."],
    "responsibilities": [
        "Own operational performance of clean water production and distribution",
        "Lead several hundred operational staff through senior managers",
        "Accountable for a multi-million pound annual operating budget",
        "Ensure DWI and OFWAT regulatory compliance",
    ],
    "requirements": ["Degree in engineering or science", "15+ years water industry experience"],
    "reporting_line": "Reports to the Chief Operating Officer; leads 4 senior managers and ~300 staff",
    "budget_responsibility": "Owns a £40m annual operating budget",
}

schema = build_schema(fw)
n_sub = sum(len(d.subdomains) for d in fw.domains)
print(f"flat schema: {len(json.dumps(schema))} chars")
print(f"framework: {len(fw.domains)} domains, {n_sub} subfactors\n")

t0 = time.time()
try:
    text = llm.complete(
        build_user_prompt("Director of Water Operations", CONTENT),
        system=build_system_prompt(fw),
        json_schema=schema,
        effort="medium",
        max_tokens=32000,
        thinking="adaptive",
        retries=1,
    )
    print(f"ADMITTED in {time.time() - t0:.1f}s, {len(text)} chars returned")
    raw = json.loads(text)
    print(f"  score rows: {len(raw.get('scores', []))} (expected {n_sub})")
    print(f"  rationale rows: {len(raw.get('rationales', []))} (expected {len(fw.domains) * 3})")

    expanded = _expand_flat_response(raw, fw)
    d0 = fw.domains[0].name
    print(f"\n  {d0} (Balanced): "
          f"{ {k: v for k, v in expanded['Balanced'][d0].items() if k != 'Rationale'} }")
    print(f"  rationale: {expanded['Balanced'][d0]['Rationale'][:150]}")

    try:
        validate_raw_evaluations(expanded, fw)
        print("\n  VALIDATION PASSED — usable evaluation")
    except InvalidEvaluation as e:
        print(f"\n  VALIDATION REJECTED: {str(e)[:300]}")
except Exception as e:
    print(f"REJECTED in {time.time() - t0:.1f}s: {type(e).__name__}: {str(e)[:220]}")
