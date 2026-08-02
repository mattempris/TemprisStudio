"""Prompt caching: the request shape, the minimum, and the cold-start stampede.

The cost this exists for: routing re-sends the whole cluster list with every item
it re-checks. At 934 skill clusters that is ~19,500 tokens per call, so routing 840
items sends ~16M input tokens of pure repetition.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import llm  # noqa: E402
from app.services.clustering import routing  # noqa: E402


class _Usage:
    def __init__(self, inp, write, read):
        self.input_tokens, self.cache_creation_input_tokens, self.cache_read_input_tokens = inp, write, read


class _Resp:
    def __init__(self, text, usage):
        self.stop_reason = "end_turn"
        self.content = [type("B", (), {"type": "text", "text": text})()]
        self.usage = usage


class _AStream:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_final_message(self):
        return self._resp


class _FakeAsync:
    """Simulates the cache: the first request carrying a given prefix writes it,
    later ones read it."""

    def __init__(self):
        self.calls: list[dict] = []
        self.seen: set[str] = set()
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        prefix = ""
        for b in kwargs.get("system") or []:
            if isinstance(b, dict) and b.get("cache_control"):
                prefix = b["text"]
        n = len(prefix) // 4
        hit = prefix in self.seen
        if prefix:
            self.seen.add(prefix)
        body = '{"primary": {"id": 0, "confidence": 0.9}, "secondary": null, "reasoning": "x"}'
        return _AStream(_Resp(body, _Usage(50, 0 if hit else n, n if hit else 0)))


def check(label, ok):
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    passed = True

    print("=== the breakpoint only goes on a prefix big enough to cache ===")
    big = "x" * (llm.MIN_CACHEABLE_TOKENS * 4 + 100)
    small = "y" * 200
    bigp = llm._system_param("sys", big)
    smallp = llm._system_param("sys", small)
    passed &= check("large prefix: two blocks, breakpoint on the second",
                    len(bigp) == 2 and "cache_control" not in bigp[0] and bigp[1]["cache_control"]["type"] == "ephemeral")
    passed &= check("short instructions come FIRST so one marker covers both",
                    bigp[0]["text"] == "sys" and bigp[1]["text"] == big)
    passed &= check("small prefix: still sent, no marker",
                    len(smallp) == 2 and all("cache_control" not in b for b in smallp))
    passed &= check("no prefix: plain string system, unchanged",
                    llm._system_param("sys", None) == "sys")

    print("=== routing sends the cluster list as the cached prefix ===")
    fake = _FakeAsync()
    llm.get_async_client = lambda: fake  # type: ignore[assignment]
    llm.reset_cache_stats()
    clusters = "\n".join(f"[{i}] Cluster {i} — e.g. some example text here" for i in range(600))
    items = [(i, f"item {i}") for i in range(20)]
    asyncio.run(routing.route_all(items, clusters, entity="skill", concurrency=8, sc_votes=1))

    passed &= check(f"one call per item ({len(fake.calls)})", len(fake.calls) == 20)
    sysblocks = fake.calls[0]["system"]
    passed &= check("cluster list is in system, not the user message",
                    any(clusters in b["text"] for b in sysblocks)
                    and clusters not in fake.calls[0]["messages"][0]["content"])
    passed &= check("the varying item IS in the user message",
                    "item 0" in fake.calls[0]["messages"][0]["content"])
    passed &= check("every call sends a byte-identical prefix",
                    len({b["text"] for c in fake.calls for b in c["system"] if b.get("cache_control")}) == 1)

    st = llm.cache_stats()
    print(f"       {st.summary()}")
    # Derived from the prefix actually sent, which carries a header line on top of
    # the cluster list.
    prefix = next(b["text"] for b in sysblocks if b.get("cache_control"))
    one = len(prefix) // 4
    passed &= check(f"exactly ONE cache write of {one:,} tokens, {st.calls - 1} reads — warm-up worked",
                    st.cache_writes == one and st.cache_reads == 19 * one)
    passed &= check(f"{st.saved_fraction:.0%} of the repeated context came from cache",
                    st.saved_fraction > 0.94)

    print("=== without warm-up, 8 concurrent calls would all write ===")
    # Same fan-out, but the prefix too small to cache: the warm-up is skipped, and
    # this is what the cold-start looks like when it is not there.
    fake2 = _FakeAsync()
    llm.get_async_client = lambda: fake2  # type: ignore[assignment]
    llm.reset_cache_stats()
    asyncio.run(routing.route_all(items, "tiny cluster list", entity="skill", concurrency=8, sc_votes=1))
    passed &= check("short cluster list: no marker, no warm-up, nothing cached",
                    llm.cache_stats().cache_writes == 0 and llm.cache_stats().cache_reads == 0)
    passed &= check(f"still routed all {len(fake2.calls)} items", len(fake2.calls) == 20)

    print("=== job evaluation caches the framework, not the profile ===")
    import json as _json

    from app.services.evaluation import job_evaluation as je

    sync_calls: list[dict] = []

    class _SyncStream:
        def __init__(self, resp):
            self._resp = resp

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return self._resp

    class _FakeSync:
        def __init__(self):
            self.messages = self

        def stream(self, **kwargs):
            sync_calls.append(kwargs)
            fw = je.load_default_framework()
            body = _json.dumps({
                "scores": [
                    {"domain": d.name, "subfactor": sub.name,
                     "balanced": 3, "generous": 4, "harsh": 2}
                    for d in fw.domains for sub in d.subdomains
                ],
                "rationales": [
                    {"domain": d.name, "persona": p, "text": "because"}
                    for d in fw.domains for p in je.PERSONAS
                ],
            })
            return _SyncStream(_Resp(body, _Usage(80, 0, 1500)))

    llm.get_client = lambda: _FakeSync()  # type: ignore[assignment]
    je.evaluate_one("k", "Test Analyst", {"about_role": ["does testing"]},
                    je.load_default_framework())
    sysb = sync_calls[0]["system"]
    passed &= check("framework sent as a cached system block",
                    isinstance(sysb, list) and any(b.get("cache_control") for b in sysb))
    passed &= check("the rubric is in that block",
                    any("FRAMEWORK:" in b["text"] for b in sysb))
    passed &= check("the profile stays in the user message and is NOT cached",
                    "Test Analyst" in sync_calls[0]["messages"][0]["content"]
                    and not any("Test Analyst" in b["text"] for b in sysb))

    print("\n" + ("PASSED" if passed else "FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
