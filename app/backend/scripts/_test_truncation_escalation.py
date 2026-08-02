"""Truncation must grow the budget, not re-pay for the same truncated response.

The bug this covers: naming 150 clusters hit max_tokens, which was raised as a
plain LLMTransientError, so complete_json retried the identical request four times
with 8s/16s/24s sleeps in between and then failed. From the UI that is a hang
followed by an unexplained error.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import llm  # noqa: E402


class _Resp:
    def __init__(self, stop_reason: str, text: str = ""):
        self.stop_reason = stop_reason
        self.content = [type("B", (), {"type": "text", "text": text})()]


class _Stream:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._resp


class _FakeClient:
    """Truncates below `succeed_at` tokens, returns valid JSON at or above it."""

    def __init__(self, succeed_at: int):
        self.succeed_at = succeed_at
        self.calls: list[int] = []
        self.messages = self

    def stream(self, **kwargs):
        budget = kwargs["max_tokens"]
        self.calls.append(budget)
        if budget < self.succeed_at:
            return _Stream(_Resp("max_tokens", '{"clusters": [{"id": 0, "na'))
        return _Stream(_Resp("end_turn", '{"clusters": [{"id": 0, "name": "Ops"}]}'))


def check(label: str, ok: bool) -> bool:
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    passed = True
    slept: list[float] = []
    llm.time.sleep = lambda s: slept.append(s)  # type: ignore[assignment]

    # --- escalates and succeeds ---
    fake = _FakeClient(succeed_at=16_000)
    llm.get_client = lambda: fake  # type: ignore[assignment]
    result = llm.complete_json("name these", max_tokens=4000, json_schema={"type": "object"})
    print("=== escalation ===")
    passed &= check(f"budgets tried {fake.calls} == [4000, 8000, 16000]", fake.calls == [4000, 8000, 16000])
    passed &= check("returned the parsed object", result == {"clusters": [{"id": 0, "name": "Ops"}]})
    passed &= check(f"no backoff sleeps ({slept})", slept == [])

    # --- gives up at the ceiling instead of looping ---
    slept.clear()
    fake2 = _FakeClient(succeed_at=10**9)
    llm.get_client = lambda: fake2  # type: ignore[assignment]
    print("=== ceiling ===")
    try:
        llm.complete_json("name these", max_tokens=8000, json_schema={"type": "object"}, retries=9)
        passed &= check("raised at the ceiling", False)
    except llm.LLMTruncatedError as e:
        passed &= check(f"raised LLMTruncatedError at {e.max_tokens}", e.max_tokens == llm.MAX_TOKEN_CEILING)
        passed &= check(
            f"stopped at the ceiling, budgets {fake2.calls}",
            fake2.calls == [8000, 16_000, 32_000],
        )
        passed &= check("still no backoff sleeps", slept == [])

    print("\n" + ("PASSED" if passed else "FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
