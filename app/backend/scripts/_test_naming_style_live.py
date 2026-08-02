"""Live: do the three job tiers produce the right KIND of name?

profile  -> job titles ('Designer', 'Head of Risk'), no abstract nouns, no compounds
category -> fields of work ('Design', 'Advisory'), never a person noun or seniority
family   -> broad domains ('Technology', 'Finance')
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.clustering import naming  # noqa: E402

# Clusters chosen to bait the failure modes: abstract-noun themes (design, testing),
# a seniority-spanning cluster, and a genuinely leadership-shaped one.
PROFILE_CLUSTERS = [
    ["UX Designer", "Product Designer", "Service Design Lead", "Interaction Designer"],
    ["Test Analyst", "QA Engineer", "Test Automation Engineer", "Senior Test Manager"],
    ["Head of Credit Risk", "Director of Risk", "Chief Risk Officer", "Head of Risk Oversight"],
    ["Pricing Analyst", "Senior Pricing Analyst", "Principal Pricing Analyst", "Pricing Manager"],
    ["Mortgage Underwriter", "Mortgage Case Assessor", "Senior Mortgage Underwriter"],
    ["Branch Customer Adviser", "Personal Banking Adviser", "Customer Service Officer"],
    ["Java Developer", "Backend Engineer", "Full Stack Developer", "Senior Software Engineer"],
    ["Compliance Officer", "Compliance Advisory Manager", "Regulatory Compliance Lead"],
]

CATEGORY_CLUSTERS = [
    ["Designer. Includes: UX, product and service design roles",
     "Front End Engineer. Includes: UI implementation"],
    ["Tester. Includes: manual and automated test roles",
     "Software Engineer. Includes: backend and full stack development"],
    ["Head of Risk. Includes: risk oversight leadership",
     "Head of Compliance. Includes: compliance leadership",
     "Chief Operating Officer. Includes: executive operations leadership"],
    ["Compliance Officer. Includes: regulatory advice to the business",
     "Legal Counsel. Includes: legal advice and opinions"],
    ["Mortgage Underwriter. Includes: mortgage credit decisions",
     "Credit Risk Analyst. Includes: portfolio credit assessment"],
]

FAMILY_CLUSTERS = [
    ["Software Engineering. Includes: Software Engineer, Tester",
     "Design. Includes: Designer", "Infrastructure. Includes: Cloud Engineer"],
    ["Underwriting. Includes: Mortgage Underwriter", "Credit Risk. Includes: Credit Risk Analyst",
     "Compliance. Includes: Compliance Officer"],
    ["Accounting. Includes: Financial Accountant", "Treasury. Includes: Treasury Analyst"],
]

PERSON_SUFFIX = re.compile(r"(er|or|ist|ant|ive|Officer|Manager|Director|Lead|Head)$", re.I)
SENIORITY = re.compile(r"\b(head of|chief|senior|junior|director|principal|lead|manager)\b", re.I)
COMPOUND = re.compile(r"\s(and|&|/)\s|/")
ABSTRACT = re.compile(r"(ing|ship|ment|ance|ence|ology|Leadership)$", re.I)


def show(title, names):
    print(f"\n=== {title} ===")
    for i in sorted(names):
        print(f"  [{i}] {names[i]}")


def check(label, ok):
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    ok = True

    prof = naming.name_level(
        "job", "profile",
        [naming.build_cluster_block(i, c) for i, c in enumerate(PROFILE_CLUSTERS)],
        len(PROFILE_CLUSTERS),
    )
    show("profile tier — expect job titles", prof)
    vals = [prof.get(i, "") for i in range(len(PROFILE_CLUSTERS))]
    compounds = [v for v in vals if COMPOUND.search(v)]
    abstracts = [v for v in vals if ABSTRACT.search(v.split()[-1]) and not PERSON_SUFFIX.search(v)]
    ok &= check(f"all named ({len(prof)}/{len(PROFILE_CLUSTERS)})", len(prof) == len(PROFILE_CLUSTERS))
    ok &= check(f"no compounded roles {compounds}", not compounds)
    ok &= check(f"reads as a title, not an abstract function {abstracts}", not abstracts)
    # The seniority-spanning pricing cluster must NOT be named 'Senior ...'
    ok &= check(f"seniority-spanning cluster kept broad: {vals[3]!r}",
                not re.match(r"^(senior|principal)\b", vals[3], re.I))
    # The leadership cluster SHOULD carry a level descriptor, not 'Leadership'
    ok &= check(f"leadership cluster uses a level descriptor: {vals[2]!r}",
                "leadership" not in vals[2].lower())

    cat = naming.name_level(
        "job", "category",
        [naming.build_cluster_block(i, c) for i, c in enumerate(CATEGORY_CLUSTERS)],
        len(CATEGORY_CLUSTERS),
    )
    show("category tier — expect fields of work", cat)
    cvals = [cat.get(i, "") for i in range(len(CATEGORY_CLUSTERS))]
    seniorities = [v for v in cvals if SENIORITY.search(v)]
    ok &= check(f"all named ({len(cat)}/{len(CATEGORY_CLUSTERS)})", len(cat) == len(CATEGORY_CLUSTERS))
    ok &= check(f"no seniority words {seniorities}", not seniorities)
    ok &= check("no category name duplicates a profile name",
                not (set(v.lower() for v in cvals) & set(v.lower() for v in vals)))

    fam = naming.name_level(
        "job", "family",
        [naming.build_cluster_block(i, c) for i, c in enumerate(FAMILY_CLUSTERS)],
        len(FAMILY_CLUSTERS),
    )
    show("family tier — expect broad domains", fam)
    fvals = [fam.get(i, "") for i in range(len(FAMILY_CLUSTERS))]
    ok &= check(f"all named ({len(fam)}/{len(FAMILY_CLUSTERS)})", len(fam) == len(FAMILY_CLUSTERS))
    ok &= check(f"three words or fewer {[len(v.split()) for v in fvals]}",
                all(len(v.split()) <= 3 for v in fvals))
    ok &= check("no family name duplicates a category name",
                not (set(v.lower() for v in fvals) & set(v.lower() for v in cvals)))

    print("\n" + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
