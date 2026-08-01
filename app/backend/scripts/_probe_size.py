"""Is the JE call's 529 about request size, or is the API just down for us?

Sends the same shape at descending max_tokens, plus a trivial control call, and
reports which are admitted. Truncation counts as ADMITTED — we only care here
whether the request is accepted at all.
"""
import time

from app.services import llm
from app.services.evaluation.job_evaluation import (
    build_schema,
    build_system_prompt,
    build_user_prompt,
    load_default_framework,
)

fw = load_default_framework()
CONTENT = {
    "about_role": ["Leads water production and distribution for a regional water company."],
    "responsibilities": ["Own operational performance", "Lead several hundred staff", "Own a £40m budget"],
    "reporting_line": "Reports to COO",
}
prompt = build_user_prompt("Director of Water Operations", CONTENT)
system = build_system_prompt(fw)
schema = build_schema(fw)

print("=== control: trivial call (is the API reachable at all?) ===")
t0 = time.time()
try:
    out = llm.complete("Reply with the single word: ok", max_tokens=64, retries=1, effort="low")
    print(f"  ADMITTED in {time.time() - t0:.1f}s -> {out.strip()[:40]!r}")
except Exception as e:
    print(f"  REJECTED in {time.time() - t0:.1f}s: {type(e).__name__}: {str(e)[:120]}")

for mt in (32000, 16000, 8000, 4000):
    print(f"\n=== JE-shaped call at max_tokens={mt} ===")
    t0 = time.time()
    try:
        text = llm.complete(
            prompt, system=system, json_schema=schema, effort="medium",
            max_tokens=mt, thinking="adaptive", retries=1,
        )
        print(f"  ADMITTED in {time.time() - t0:.1f}s, {len(text)} chars returned")
    except llm.LLMTransientError as e:
        msg = str(e)
        kind = "truncated (ADMITTED)" if "max_tokens" in msg and "truncated" in msg else "REJECTED"
        print(f"  {kind} in {time.time() - t0:.1f}s: {msg[:150]}")
    except Exception as e:
        print(f"  REJECTED in {time.time() - t0:.1f}s: {type(e).__name__}: {str(e)[:150]}")

print("\n=== same big max_tokens WITHOUT the schema (isolates schema vs tokens) ===")
t0 = time.time()
try:
    text = llm.complete(
        "List 3 UK water industry regulators, one per line.",
        max_tokens=32000, thinking="adaptive", effort="medium", retries=1,
    )
    print(f"  ADMITTED in {time.time() - t0:.1f}s, {len(text)} chars")
except Exception as e:
    print(f"  REJECTED in {time.time() - t0:.1f}s: {type(e).__name__}: {str(e)[:150]}")
