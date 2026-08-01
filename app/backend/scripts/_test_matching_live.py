"""Live Phase 4 test: build the jobQWEN taxonomy index on GPU, then match real
profiles whose correct family is known in advance.

Checks the thing that actually matters — whether replacing JobBERT-v3 + Voyage
with jobQWEN + an LLM reranker still lands roles in the right part of the
taxonomy — including a role deliberately chosen to have no good home.
"""
import sys
import time

from app.services.matching import index as tax_index
from app.services.matching import matcher, taxonomy

# (title, content, expected family substring or None if no good match exists)
CASES = [
    (
        "Head of Financial Planning & Analysis",
        {
            "about_role": "Lead the FP&A function, owning the annual budget, rolling forecasts and management reporting for a £400m turnover business.",
            "responsibilities": [
                "Own the group budgeting and forecasting cycle end to end",
                "Produce monthly management accounts and board reporting packs",
                "Lead a team of six finance analysts",
                "Partner with divisional directors on cost and margin performance",
            ],
        },
        "Finance",
    ),
    (
        "Water Network Maintenance Technician",
        {
            "about_role": "Carry out planned and reactive maintenance on the clean water distribution network.",
            "responsibilities": [
                "Repair burst mains and service pipes",
                "Operate valves and hydrants to manage network pressure",
                "Complete job records on a mobile works management system",
                "Work to confined space and street works safety procedures",
            ],
        },
        None,  # utilities field ops — checked for plausibility, not an exact family
    ),
    (
        "Senior Talent Acquisition Partner",
        {
            "about_role": "Own end-to-end recruitment for corporate functions, acting as the hiring manager's advisor on market and process.",
            "responsibilities": [
                "Run full-cycle recruitment from intake to offer",
                "Build direct-sourcing pipelines and manage agency relationships",
                "Advise hiring managers on assessment and market conditions",
                "Report on time-to-hire and source-of-hire metrics",
            ],
        },
        "Human Resources",
    ),
    (
        "Director, Transfer Pricing",
        {
            "about_role": "Lead the group's transfer pricing strategy, documentation and tax authority negotiations across 14 jurisdictions.",
            "responsibilities": [
                "Set intercompany pricing policy for goods, services and IP",
                "Own local and master file documentation",
                "Lead advance pricing agreement negotiations with tax authorities",
                "Advise on the tax consequences of group restructuring",
            ],
        },
        "Finance",
    ),
]


def main() -> int:
    t0 = time.perf_counter()
    idx = tax_index.build_index(progress=lambda m: print(f"  [index] {m}"))
    levels = taxonomy.load_career_levels()
    print(f"  index ready in {time.perf_counter() - t0:.1f}s: "
          f"{len(idx)} specs / {idx.n_variants} variants / dim {idx.vectors.shape[1]}\n")

    payload = [(f"p{i}", title, content) for i, (title, content, _) in enumerate(CASES)]
    t0 = time.perf_counter()
    results = matcher.match_many(payload, idx, levels, workers=4,
                                 progress=lambda d, t, l: print(f"  [{l}] {d}/{t}"))
    print(f"\n  matched {len(results)} profiles in {time.perf_counter() - t0:.1f}s\n")

    failures = []
    for (title, _, expected), m in zip(CASES, results):
        print(f"=== {title}")
        if not m.matched:
            print(f"    NO MATCH — {m.rationale}")
        else:
            print(f"    -> {m.family_title} > {m.sub_family_title} > {m.spec_title} ({m.spec_code})")
            print(f"       cosine={m.cosine:.3f} confidence={m.confidence:.2f}")
            print(f"       level: {m.level_code} {m.level_title} (conf {m.level_confidence:.2f})")
            print(f"       {m.rationale}")
            if m.runner_up_code:
                print(f"       runner-up: {m.runner_up_title} ({m.runner_up_code})")
        if m.needs_review:
            print(f"       NEEDS REVIEW: {m.review_reasons}")
        top = ", ".join(f"{c.title}({c.cosine:.2f})" for c in m.shortlist[:4])
        print(f"       shortlist top-4: {top}")

        if expected:
            ok = m.matched and expected.lower() in (m.family_title or "").lower()
            print(f"       expected family ~ {expected!r}: {'OK' if ok else 'MISS'}")
            if not ok:
                failures.append((title, expected, m.family_title))
        print()

    print("=== summary ===")
    for k, v in matcher.summarize(results).items():
        print(f"  {k}: {v}")

    # every matched profile must carry a level its specialization actually offers
    specs_by_code = {s.code: s for s in idx.specs}
    for m in results:
        if m.matched and m.level_code:
            offered = {c for c, _ in specs_by_code[m.spec_code].available_levels}
            assert m.level_code in offered, f"{m.profile_title}: {m.level_code} not in {sorted(offered)}"
    print("  all assigned levels are offered by their specialization: OK")

    if failures:
        print(f"\nFAILED {len(failures)} known-answer cases:")
        for t, exp, got in failures:
            print(f"  {t}: expected ~{exp}, got {got}")
        return 1
    print("\nLIVE MATCHING TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
