"""Live: category and family names must not carry seniority — except the board.

Baits the exact failure the user saw: a cluster mixing administrators with entry-level
support was being named "Administration & Entry Level Support" rather than
"Administration".
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.clustering import naming  # noqa: E402

CATEGORY_CLUSTERS = [
    # the bait: administrators alongside entry-level support roles
    ["Administrator. Includes: office and team administration",
     "Business Support Apprentice. Includes: entry level administrative support",
     "Team Assistant. Includes: diary and document administration"],
    # the sanctioned exception: genuine board-level executives
    ["Chief Executive Officer. Includes: group leadership",
     "Chief Financial Officer. Includes: group finance leadership",
     "Chief Risk Officer. Includes: group risk leadership"],
    # senior management BELOW the board — must NOT be 'Executive ...'
    ["Head of Credit Risk. Includes: credit risk oversight",
     "Credit Risk Manager. Includes: portfolio credit risk management",
     "Credit Risk Analyst. Includes: credit assessment"],
    # a graduate-heavy cluster: must name the work, not the stage
    ["Graduate Analyst. Includes: rotational analytical work",
     "Data Analyst. Includes: reporting and analysis",
     "Business Analyst. Includes: requirements and process analysis"],
]

FAMILY_CLUSTERS = [
    ["Administration. Includes: Administrator", "Facilities. Includes: Facilities Manager"],
    ["Executive Leadership. Includes: Chief Executive Officer",
     "Corporate Governance. Includes: Company Secretary"],
    ["Credit Risk. Includes: Credit Risk Manager", "Compliance. Includes: Compliance Officer"],
]

BANNED = re.compile(
    r"\b(senior|junior|entry[- ]?level|head of|lead|trainee|apprentice|assistant|"
    r"support staff|graduate|associate|principal)\b",
    re.I,
)
EXEC_OK = re.compile(r"\bexecutive\b", re.I)


def show(t, names):
    print(f"\n=== {t} ===")
    for i in sorted(names):
        print(f"  [{i}] {names[i]}")


def check(label, ok):
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    ok = True

    cat = naming.name_level(
        "job", "category",
        [naming.build_cluster_block(i, c) for i, c in enumerate(CATEGORY_CLUSTERS)],
        len(CATEGORY_CLUSTERS),
    )
    show("category", cat)
    cv = [cat.get(i, "") for i in range(len(CATEGORY_CLUSTERS))]
    banned = [v for v in cv if BANNED.search(v)]
    ok &= check(f"all named ({len(cat)}/{len(CATEGORY_CLUSTERS)})", len(cat) == len(CATEGORY_CLUSTERS))
    ok &= check(f"no seniority or career stage anywhere {banned}", not banned)
    ok &= check(f"admin cluster names the work only: {cv[0]!r}",
                "admin" in cv[0].lower() and not BANNED.search(cv[0]))
    ok &= check(f"board cluster IS allowed 'Executive': {cv[1]!r}", bool(EXEC_OK.search(cv[1])))
    ok &= check(f"below-board risk cluster is NOT 'Executive': {cv[2]!r}", not EXEC_OK.search(cv[2]))
    ok &= check(f"graduate cluster names the work, not the stage: {cv[3]!r}",
                not BANNED.search(cv[3]))

    fam = naming.name_level(
        "job", "family",
        [naming.build_cluster_block(i, c) for i, c in enumerate(FAMILY_CLUSTERS)],
        len(FAMILY_CLUSTERS),
    )
    show("family", fam)
    fv = [fam.get(i, "") for i in range(len(FAMILY_CLUSTERS))]
    fbanned = [v for v in fv if BANNED.search(v)]
    ok &= check(f"all named ({len(fam)}/{len(FAMILY_CLUSTERS)})", len(fam) == len(FAMILY_CLUSTERS))
    ok &= check(f"no seniority or career stage anywhere {fbanned}", not fbanned)
    ok &= check(f"board family may say 'Executive': {fv[1]!r}", bool(EXEC_OK.search(fv[1])))

    print("\n" + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
