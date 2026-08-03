"""Live calibration check for step 3, on real task clusters.

The offline test proves the arithmetic. This proves the only thing the arithmetic
cannot: that the prompt *discriminates*. A run where every cluster lands between 40
and 55 is a failed assessment however well the roll-ups add up, and the only way to
find that out is to spend a few pounds asking.

Deliberately small — a handful of clusters chosen to span the range, not a sample of
the biggest — because the question is whether judgement work scores differently from
document work, and that needs contrast rather than volume.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.project_service import ProjectService  # noqa: E402
from app.services.workforce import opportunity as opp  # noqa: E402

CLIENT, PROJECT = "banking-demo", "full-ja"
N = 8

# Words that suggest a cluster is one end of the spectrum or the other. Only used to
# pick a contrasting sample to look at — nothing in the pipeline reads these.
JUDGEMENT = ("negotiat", "coach", "advis", "stakeholder", "relationship", "leader",
             "mentor", "influenc", "escalat", "counsel")
CLERICAL = ("report", "document", "data", "record", "administ", "process", "reconcil",
            "draft", "monitor", "audit", "compil")


def main() -> int:
    svc = ProjectService()
    state = svc.load_state(CLIENT, PROJECT)
    inputs = opp.cluster_inputs(state)
    if not inputs:
        print("no task clusters on this project")
        return 1
    print(f"{len(inputs)} task clusters on {CLIENT}/{PROJECT}")

    def pick(words, k):
        return [i for i in inputs if any(w in i.name.lower() for w in words)][:k]

    sample = pick(JUDGEMENT, 3) + pick(CLERICAL, 3)
    sample += [i for i in inputs if i not in sample][: N - len(sample)]
    sample = sample[:N]

    results = opp.assess_many(sample, workers=4)
    audit = opp.audit(results, requested=len(sample))

    print()
    for r in results:
        if r is None:
            print("  FAILED")
            continue
        print(f"  {r.cluster_name}")
        print(f"    automation {r.automation_pct:>5.1f}%   augmentation {r.augmentation_pct:>5.1f}%"
              f"   ({len(r.actions)} actions"
              + (f", raw sum {r.raw_pct_sum:g}" if abs(r.raw_pct_sum - 100) > 0.5 else "")
              + (", CLAMPED" if r.clamped else "") + ")")
        for a in r.actions:
            print(f"      {a.pct_of_task:>5.1f}% of task  auto {a.automation_pct:>4.0f}  "
                  f"aug {a.augmentation_pct:>4.0f}   {a.name}")
            print(f"                     {a.definition}")
        print()

    s = audit.summary()
    print(f"assessed {s['clusters_assessed']}/{len(sample)} · failed {s['clusters_failed']} · "
          f"clamped {s['clamped']} · retried {s['retried']}")
    print(f"automation mean {s['mean_automation']}%  p10 {s['automation_p10']}%  "
          f"p90 {s['automation_p90']}%  spread {s['automation_p90'] - s['automation_p10']:.1f}pp")
    print(f"augmentation mean {s['mean_augmentation']}%")
    print(f"max percentage drift {s['max_pct_drift']}")
    print()
    print("PASS — discriminating" if s["discriminating"] else
          "FAIL — scores are uniformly mid-range; the prompt is not discriminating")
    return 0 if s["discriminating"] and s["clusters_assessed"] == len(sample) else 1


if __name__ == "__main__":
    raise SystemExit(main())
