"""Live naming across a batch boundary: real API, real budget, real distinctiveness.

35 clusters with NAME_BATCH=30 means two calls, so this exercises the part the unit
test can only stub: that the second call's "already in use" preamble is accepted and
that the budget actually holds a full batch of names with adaptive thinking on.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.clustering import naming  # noqa: E402

# Deliberately close together — near-duplicate themes are where distinctiveness
# either holds or collapses into "Banking Operations 1/2/3".
THEMES = [
    ["Mortgage Underwriter", "Senior Mortgage Underwriter", "Mortgage Case Assessor"],
    ["Credit Risk Analyst", "Senior Credit Risk Analyst", "Credit Portfolio Analyst"],
    ["AML Investigator", "Financial Crime Analyst", "Sanctions Screening Officer"],
    ["Branch Customer Adviser", "Personal Banking Adviser", "Customer Service Officer"],
    ["Java Developer", "Backend Engineer", "Senior Software Engineer"],
    ["Data Engineer", "ETL Developer", "Data Platform Engineer"],
    ["Data Scientist", "Machine Learning Engineer", "Quantitative Analyst"],
    ["Internal Auditor", "Audit Manager", "Assurance Lead"],
    ["Compliance Officer", "Compliance Advisory Manager", "Regulatory Compliance Lead"],
    ["Treasury Analyst", "Liquidity Risk Analyst", "Balance Sheet Manager"],
    ["Product Owner", "Digital Product Manager", "Proposition Manager"],
    ["Business Analyst", "Change Analyst", "Process Improvement Analyst"],
    ["Project Manager", "Programme Manager", "Delivery Lead"],
    ["HR Business Partner", "People Adviser", "Reward Analyst"],
    ["Financial Accountant", "Management Accountant", "Finance Business Partner"],
    ["Procurement Manager", "Category Manager", "Supplier Relationship Manager"],
    ["Marketing Manager", "Brand Manager", "Campaign Executive"],
    ["Relationship Manager SME", "Business Banking Manager", "Commercial Director"],
    ["Wealth Planner", "Investment Adviser", "Private Banker"],
    ["Insurance Claims Handler", "Claims Assessor", "Protection Claims Specialist"],
    ["Pricing Analyst", "Senior Pricing Analyst", "Technical Pricing Manager"],
    ["Actuarial Analyst", "Actuary", "Capital Modelling Analyst"],
    ["Network Engineer", "Infrastructure Engineer", "Cloud Platform Engineer"],
    ["Cyber Security Analyst", "SOC Analyst", "Security Engineer"],
    ["Service Desk Analyst", "IT Support Engineer", "Desktop Support"],
    ["Test Analyst", "QA Engineer", "Test Automation Engineer"],
    ["Collections Adviser", "Recoveries Officer", "Arrears Support Specialist"],
    ["Fraud Analyst", "Fraud Operations Manager", "Disputes Handler"],
    ["Payments Operations Analyst", "Settlements Officer", "Reconciliations Analyst"],
    ["Legal Counsel", "Company Secretary", "Regulatory Lawyer"],
    ["Facilities Manager", "Property Manager", "Workplace Coordinator"],
    ["Trader", "Equities Sales", "Markets Structurer"],
    ["Operational Risk Manager", "Risk Framework Lead", "Controls Assurance Manager"],
    ["Customer Complaints Officer", "Complaints Team Manager", "Root Cause Analyst"],
    ["Learning & Development Adviser", "Training Manager", "Capability Lead"],
]


def main() -> int:
    blocks = [naming.build_cluster_block(i, t) for i, t in enumerate(THEMES)]
    n = len(blocks)
    seen: list[tuple[int, int]] = []
    print(f"naming {n} clusters (batch size {naming.NAME_BATCH} -> "
          f"{-(-n // naming.NAME_BATCH)} calls)")

    t0 = time.time()
    names = naming.name_level(
        "job", "profile", blocks, n,
        progress=lambda d, tot: seen.append((d, tot)) or print(f"  progress {d}/{tot}"),
    )
    elapsed = time.time() - t0

    print(f"\n{elapsed:.1f}s")
    for i, t in enumerate(THEMES):
        print(f"  [{i:>2}] {names.get(i, '*** MISSING ***'):<42} <- {t[0]}")

    ok = True
    complete = set(names) == set(range(n))
    ok &= complete
    print(f"\n{'OK  ' if complete else 'FAIL'}  every cluster named ({len(names)}/{n})")

    lowered = [v.lower() for v in names.values()]
    unique = len(set(lowered)) == len(lowered)
    ok &= unique
    print(f"{'OK  ' if unique else 'FAIL'}  all names distinct ({len(set(lowered))} unique)")

    progressed = len(seen) == -(-n // naming.NAME_BATCH) and seen[-1] == (n, n)
    ok &= progressed
    print(f"{'OK  ' if progressed else 'FAIL'}  progress reported per batch, ending at {n}/{n}")

    print("\n" + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
