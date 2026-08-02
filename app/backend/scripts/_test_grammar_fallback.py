"""A grammar-compilation timeout must not be fatal.

It arrives as a 400, which is normally "the request is wrong, retrying is
pointless" — and that classification killed a job-evaluation stage mid-run on a
schema that had already worked. So: retry once, then state the schema in the prompt
instead of enforcing it as a grammar.
"""
from __future__ import annotations

import sys
from pathlib import Path

import anthropic
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import llm  # noqa: E402

GRAMMAR_MSG = "Grammar compilation timed out."
OTHER_400 = "output_config.format.schema: properties maximum, minimum are not supported"


def _bad_request(message: str) -> anthropic.BadRequestError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(400, request=req, json={"type": "error", "error": {"message": message}})
    return anthropic.BadRequestError(message, response=resp, body=None)


class _Resp:
    def __init__(self, text):
        self.stop_reason = "end_turn"
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
    """Raises `message` on the first `fail_times` calls, then returns valid JSON."""

    def __init__(self, message: str, fail_times: int):
        self.message, self.fail_times = message, fail_times
        self.calls: list[dict] = []
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.fail_times:
            raise _bad_request(self.message)
        return _Stream(_Resp('{"ok": true}'))


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def check(label, ok):
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    return ok


def has_schema(call) -> bool:
    return "format" in call.get("output_config", {})


def main() -> int:
    passed = True
    slept: list[float] = []
    llm.time.sleep = lambda s: slept.append(s)  # type: ignore[assignment]

    print("=== transient: one timeout, then succeeds with the grammar intact ===")
    f = _FakeClient(GRAMMAR_MSG, fail_times=1)
    llm.get_client = lambda: f  # type: ignore[assignment]
    r = llm.complete_json("go", json_schema=SCHEMA)
    passed &= check("returned the parsed object", r == {"ok": True})
    passed &= check(f"2 calls ({len(f.calls)})", len(f.calls) == 2)
    passed &= check("retry KEPT the grammar", has_schema(f.calls[1]))
    passed &= check(f"short wait, not the 8s transport backoff ({slept})", slept == [4])

    print("=== persistent: falls back to the schema stated in the prompt ===")
    slept.clear()
    f2 = _FakeClient(GRAMMAR_MSG, fail_times=2)
    llm.get_client = lambda: f2  # type: ignore[assignment]
    r = llm.complete_json("go", json_schema=SCHEMA)
    passed &= check("still returned the parsed object", r == {"ok": True})
    passed &= check(f"3 calls ({len(f2.calls)})", len(f2.calls) == 3)
    passed &= check("3rd call sent NO grammar", not has_schema(f2.calls[2]))
    third = f2.calls[2]["messages"][0]["content"]
    passed &= check("3rd call states the schema in the prompt", '"required": ["ok"]' in third or '"required":["ok"]' in third)
    passed &= check("original prompt still present", third.startswith("go"))

    print("=== never recovers: raises, does not hang ===")
    f3 = _FakeClient(GRAMMAR_MSG, fail_times=99)
    llm.get_client = lambda: f3  # type: ignore[assignment]
    try:
        llm.complete_json("go", json_schema=SCHEMA, retries=4)
        passed &= check("raised", False)
    except llm.LLMGrammarError:
        # 3, not the full retry budget: once the request carries no grammar, a
        # further grammar error is not something more attempts can fix.
        passed &= check(f"raised LLMGrammarError after {len(f3.calls)} calls "
                        "(with-grammar, with-grammar, without)", len(f3.calls) == 3)
        passed &= check("is caught by existing LLMTransientError handlers",
                        issubclass(llm.LLMGrammarError, llm.LLMTransientError))

    print("=== a genuinely bad request is still fatal on the first call ===")
    f4 = _FakeClient(OTHER_400, fail_times=99)
    llm.get_client = lambda: f4  # type: ignore[assignment]
    try:
        llm.complete_json("go", json_schema=SCHEMA)
        passed &= check("raised", False)
    except llm.LLMRequestError:
        passed &= check(f"LLMRequestError after exactly 1 call ({len(f4.calls)})", len(f4.calls) == 1)

    print("\n" + ("PASSED" if passed else "FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
