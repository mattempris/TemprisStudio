"""Anthropic LLM helpers shared across every pipeline stage.

Ported from `Insurance Demo/pipeline/llm.py` (complete/complete_json/pmap/parse_json)
and extended with:
  - an async client + semaphore-bounded concurrency (`amap`), matching the pattern
    proven in `Additional methodology context/code/taxonomy_run.py::route_all()`
  - Batch API helpers (submit/poll/collect), matching `taxonomy_run.py`'s
    `run_route_batch`/`collect_batch` — ~50% cheaper, used for large fan-outs
  - a progress-callback hook replacing bare `print()`, so the FastAPI layer can
    forward progress onto a WebSocket without this module knowing about sockets
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, TypeVar

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from app.core.config import get_settings

T = TypeVar("T")
R = TypeVar("R")

ProgressCallback = Callable[[int, int, str], None]

_print_lock = threading.Lock()
_client: anthropic.Anthropic | None = None
_aclient: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)
    return _client


def get_async_client() -> anthropic.AsyncAnthropic:
    global _aclient
    if _aclient is None:
        _aclient = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    return _aclient


def _first_text(resp) -> str:
    return next(b.text for b in resp.content if b.type == "text")


# ---------------------------------------------------------------------------
# JSON extraction — robust to markdown-fenced or preamble/postamble-wrapped output
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json(text: str) -> Any:
    fence_match = _FENCE_RE.search(text)
    candidate = fence_match.group(1) if fence_match else text
    candidate = candidate.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # fall back to the outermost {...} or [...] bracket pair
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = candidate.find(open_ch)
        end = candidate.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"could not parse JSON from response: {text[:200]!r}")


# ---------------------------------------------------------------------------
# Synchronous single-call helpers (retry/backoff), thread-pool fan-out
# ---------------------------------------------------------------------------
def complete(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 8000,
    retries: int = 4,
    json_schema: dict | None = None,
    effort: str = "medium",
) -> str:
    # NOTE: temperature/top_p/top_k are NOT accepted by claude-sonnet-5 (or the
    # rest of the 4.6+ model family) — sending them returns a 400. Determinism
    # for routing/self-consistency comes from effort tuning + majority voting
    # instead (see services/clustering/routing.py), not sampling temperature.
    settings = get_settings()
    model = model or settings.anthropic_model
    client = get_client()

    kwargs: dict = dict(model=model, max_tokens=max_tokens)
    if system:
        kwargs["system"] = system
    if json_schema is not None:
        kwargs["thinking"] = {"type": "disabled"}
        kwargs["output_config"] = {"effort": effort, "format": {"type": "json_schema", "schema": json_schema}}
    kwargs["messages"] = [{"role": "user", "content": prompt}]

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.messages.create(**kwargs)
            return _first_text(resp)
        except Exception as e:  # noqa: BLE001 - deliberately broad, retried below
            last_err = e
            wait = 8 * (attempt + 1)
            with _print_lock:
                print(f"  [llm] attempt {attempt + 1}/{retries} failed: {e} — retrying in {wait}s")
            if attempt < retries - 1:
                time.sleep(wait)
    raise RuntimeError(f"LLM call failed after {retries} attempts: {last_err}") from last_err


def complete_json(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 8000,
    retries: int = 4,
    json_schema: dict | None = None,
    effort: str = "medium",
) -> Any:
    last_err: Exception | None = None
    for attempt in range(retries):
        text = complete(
            prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            retries=1,
            json_schema=json_schema,
            effort=effort,
        )
        try:
            return parse_json(text)
        except ValueError as e:
            last_err = e
            with _print_lock:
                print(f"  [llm] JSON parse failed (attempt {attempt + 1}/{retries}): {text[:150]!r}")
    raise RuntimeError(f"LLM JSON call failed after {retries} attempts: {last_err}") from last_err


def pmap(
    fn: Callable[[T], R],
    items: list[T],
    *,
    workers: int = 6,
    label: str = "",
    progress: ProgressCallback | None = None,
) -> list[R]:
    """Parallel map preserving input order, thread-pool based (mirrors llm.py's pmap)."""
    results: list[R | None] = [None] * len(items)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
        for future in as_completed(futures):
            i = futures[future]
            results[i] = future.result()
            done += 1
            msg = f"{label} {done}/{len(items)}"
            with _print_lock:
                print(f"  {msg}")
            if progress:
                progress(done, len(items), label)
    return results  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Async fan-out with bounded concurrency — used for routing/self-consistency
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402


async def acomplete_json(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 8000,
    json_schema: dict | None = None,
    effort: str = "medium",
) -> Any:
    settings = get_settings()
    model = model or settings.anthropic_model
    client = get_async_client()

    kwargs: dict = dict(model=model, max_tokens=max_tokens)
    if system:
        kwargs["system"] = system
    if json_schema is not None:
        kwargs["thinking"] = {"type": "disabled"}
        kwargs["output_config"] = {"effort": effort, "format": {"type": "json_schema", "schema": json_schema}}
    kwargs["messages"] = [{"role": "user", "content": prompt}]

    resp = await client.messages.create(**kwargs)
    return parse_json(_first_text(resp))


async def amap(
    fn: Callable[[T], "asyncio.Future[R]"],
    items: list[T],
    *,
    concurrency: int = 8,
    progress: ProgressCallback | None = None,
    label: str = "",
) -> list[R]:
    """Async parallel map with a semaphore bound — mirrors taxonomy_run.py::route_all."""
    sem = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()

    async def _run(item: T) -> R:
        nonlocal done
        async with sem:
            result = await fn(item)
        async with lock:
            done += 1
            if progress:
                progress(done, len(items), label)
        return result

    return await asyncio.gather(*[_run(item) for item in items])


# ---------------------------------------------------------------------------
# Batch API — ~50% cheaper, async, up to 24h turnaround. Used for large fan-outs
# (routing/self-consistency over hundreds+ of items) per taxonomy_run.py's pattern.
# ---------------------------------------------------------------------------
def submit_batch(requests: list[tuple[str, dict]]) -> str:
    """requests: list of (custom_id, create_params_dict). Returns batch id."""
    client = get_client()
    batch_requests = [
        Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**params)) for cid, params in requests
    ]
    batch = client.messages.batches.create(requests=batch_requests)
    return batch.id


def poll_batch(batch_id: str, *, poll_seconds: int = 30) -> None:
    client = get_client()
    while True:
        b = client.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            return
        time.sleep(poll_seconds)


def collect_batch(batch_id: str, *, poll_seconds: int = 30) -> dict[str, Any]:
    """Poll to completion (no-op if already ended) and return {custom_id: parsed_json}."""
    client = get_client()
    poll_batch(batch_id, poll_seconds=poll_seconds)
    out: dict[str, Any] = {}
    for res in client.messages.batches.results(batch_id):
        if res.result.type == "succeeded":
            text = next(blk.text for blk in res.result.message.content if blk.type == "text")
            out[res.custom_id] = parse_json(text)
        else:
            with _print_lock:
                print(f"  [batch] {res.custom_id}: {res.result.type}")
    return out


def save(path, obj: Any) -> None:
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def load(path) -> Any:
    from pathlib import Path

    return json.loads(Path(path).read_text(encoding="utf-8"))
