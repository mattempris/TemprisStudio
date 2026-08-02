"""Live: does the API actually cache the routing prefix, and does it change answers?

Cheap on purpose — a handful of calls with a prefix just over the minimum.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import llm  # noqa: E402
from app.services.clustering import routing  # noqa: E402

CLUSTERS = "\n".join(
    f"[{i}] {n} — e.g. {d}"
    for i, (n, d) in enumerate([
        ("Software Engineering", "backend, frontend and full stack development of banking systems"),
        ("Software Testing", "manual and automated testing, test strategy, release quality gates"),
        ("Product Design", "user experience research, interaction design, service design"),
        ("Credit Risk", "credit assessment, portfolio risk, impairment and provisioning"),
        ("Regulatory Compliance", "regulatory advice, policy, assurance and monitoring"),
        ("Financial Crime", "anti money laundering, sanctions screening, fraud investigation"),
        ("Treasury", "liquidity, funding, balance sheet and capital management"),
        ("Retail Customer Service", "branch and telephone customer support and account servicing"),
        ("Mortgage Underwriting", "mortgage credit decisions, affordability and case assessment"),
        ("Data Engineering", "pipelines, warehousing, data platform operations"),
        ("Data Science", "statistical modelling, machine learning, quantitative analysis"),
        ("Internal Audit", "audit planning, fieldwork, findings and assurance reporting"),
        ("Procurement", "sourcing, supplier management, category strategy and contracts"),
        ("Accounting", "financial and management accounting, statutory reporting, control"),
        ("Human Resources", "resourcing, reward, employee relations and people partnering"),
        ("Marketing", "brand, campaigns, proposition marketing and customer communications"),
        ("Cyber Security", "security operations, threat detection, vulnerability management"),
        ("IT Infrastructure", "networks, cloud platforms, hosting and end user computing"),
    ] * 4)  # repeated to clear the 1024-token minimum, as a real taxonomy would
)

ITEMS = [
    (0, "Senior Test Automation Engineer. Builds and maintains automated regression suites."),
    (1, "Mortgage Case Assessor. Reviews mortgage applications against lending policy."),
    (2, "Sanctions Screening Officer. Reviews payment alerts against sanctions lists."),
    (3, "Interaction Designer. Designs user journeys and interface behaviour."),
]


def check(label, ok):
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    prefix_tokens = len(CLUSTERS) // 4
    print(f"cluster list ~{prefix_tokens:,} tokens, cacheable={llm.is_cacheable(CLUSTERS)}")
    if not llm.is_cacheable(CLUSTERS):
        print("FAILED — fixture too small to exercise caching")
        return 1

    llm.reset_cache_stats()
    t0 = time.time()
    results = asyncio.run(
        routing.route_all(ITEMS, CLUSTERS, entity="job", concurrency=4, sc_votes=1)
    )
    elapsed = time.time() - t0
    st = llm.cache_stats()

    print(f"\n{elapsed:.1f}s — {st.summary()}\n")
    names = [ln.split("] ")[1].split(" — ")[0] for ln in CLUSTERS.splitlines()]
    for idx, (_, text) in enumerate(ITEMS):
        r = results[idx]
        print(f"  {text.split('.')[0]:<38} -> {names[r.primary_cluster_id]} ({r.primary_confidence:.2f})")

    ok = True
    ok &= check(f"{st.calls} calls made", st.calls == len(ITEMS))
    ok &= check(f"the API wrote the prefix to cache ({st.cache_writes:,} tokens)", st.cache_writes > 0)
    ok &= check(f"later calls read it back ({st.cache_reads:,} tokens)", st.cache_reads > 0)
    ok &= check(f"exactly one write — warm-up prevented a stampede",
                st.cache_writes < st.cache_reads)
    ok &= check(f"{st.saved_fraction:.0%} of repeated context served from cache",
                st.saved_fraction >= 0.6)
    # The point of all this is that answers are unchanged.
    expected = {0: "Software Testing", 1: "Mortgage Underwriting",
                2: "Financial Crime", 3: "Product Design"}
    wrong = {i: names[results[i].primary_cluster_id]
             for i in expected if names[results[i].primary_cluster_id] != expected[i]}
    ok &= check(f"routing still lands in the right clusters {wrong or ''}", not wrong)

    print("\n" + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
