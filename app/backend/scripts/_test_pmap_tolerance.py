"""One failed item must not discard the whole stage's paid-for work."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import llm  # noqa: E402


def check(label, ok):
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    passed = True

    def flaky(i: int) -> str:
        if i in (3, 7):
            raise llm.LLMGrammarError("grammar compilation timed out")
        return f"result-{i}"

    seen: list[tuple[int, int]] = []
    out = llm.pmap(flaky, list(range(10)), workers=4, label="je",
                   progress=lambda d, t, l: seen.append((d, t)), tolerate_errors=True)

    print("=== tolerate_errors=True ===")
    passed &= check(f"one entry per input ({len(out)})", len(out) == 10)
    passed &= check("failures are None, in place", out[3] is None and out[7] is None)
    passed &= check(f"8 survivors kept ({sum(1 for r in out if r)})", sum(1 for r in out if r) == 8)
    passed &= check("order preserved", out[0] == "result-0" and out[9] == "result-9")
    passed &= check(f"progress still counted every item ({seen[-1]})", seen[-1] == (10, 10))

    print("=== default (unchanged: a failure is fatal) ===")
    try:
        llm.pmap(flaky, list(range(10)), workers=4, label="strip")
        passed &= check("raised", False)
    except llm.LLMGrammarError:
        passed &= check("still raises without the flag", True)

    print("\n" + ("PASSED" if passed else "FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
