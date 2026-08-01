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

import asyncio
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


class LLMRequestError(RuntimeError):
    """The request is malformed/unauthorized — retrying will not help.
    Raised for 400/401/403/404 so a bad schema or key surfaces immediately
    instead of after several pointless backoff waits."""


class LLMTransientError(RuntimeError):
    """Retryable failure that exhausted the retry budget (429/5xx/network)."""


# Split by whether a retry could plausibly succeed. The SDK already retries
# 429/5xx a couple of times internally; this outer loop adds longer backoff for
# the sustained-overload case that a pipeline of hundreds of calls will hit.
_NON_RETRYABLE = (
    anthropic.BadRequestError,
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.NotFoundError,
)
_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,  # includes 529 overloaded_error
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.APIStatusError,  # catch-all for other non-2xx
)


# The SDK defaults to a 10-minute request timeout. That's far too long once a
# call sits inside nested retry loops: a hung or trickling stream would burn ten
# minutes before the retry logic even got a chance to cycle. A pipeline stage
# making hundreds of calls needs to fail fast and move on.
REQUEST_TIMEOUT_SECONDS = 240.0


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(
            api_key=get_settings().anthropic_api_key,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=1,  # this module owns retry policy; don't double-retry underneath it
        )
    return _client


def get_async_client() -> anthropic.AsyncAnthropic:
    global _aclient
    if _aclient is None:
        _aclient = anthropic.AsyncAnthropic(
            api_key=get_settings().anthropic_api_key,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=1,
        )
    return _aclient


def _response_text(resp) -> str:
    """Concatenate ALL text blocks, matching the reference implementation.

    Taking only the first text block loses content whenever the model emits the
    response across several blocks, which yields a truncated fragment that then
    fails to parse as JSON for no obvious reason. `thinking` blocks are a
    different block type and are correctly excluded here.
    """
    return "".join(b.text for b in resp.content if b.type == "text")


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
    thinking: str = "adaptive",
) -> str:
    # NOTE: temperature/top_p/top_k are NOT accepted by claude-sonnet-5 (or the
    # rest of the 4.6+ model family) — sending them returns a 400. Determinism
    # for routing/self-consistency comes from effort tuning + majority voting
    # instead (see services/clustering/routing.py), not sampling temperature.
    #
    # `thinking` defaults to "adaptive" and should stay that way for any call
    # that requires actual reasoning. Setting it to "disabled" alongside a
    # constrained json_schema was observed to produce hollow output: on a
    # 3-persona x 20-subfactor job evaluation the model returned every score as
    # the lowest allowed value with empty rationale strings — the grammar forces
    # a well-formed object, and with no thinking budget the model fills each
    # required field with the first token the grammar permits. Reserve
    # "disabled" for genuinely mechanical extraction where latency matters.
    settings = get_settings()
    model = model or settings.anthropic_model
    client = get_client()

    kwargs: dict = dict(model=model, max_tokens=max_tokens, thinking={"type": thinking})
    if system:
        kwargs["system"] = system
    if json_schema is not None:
        kwargs["output_config"] = {"effort": effort, "format": {"type": "json_schema", "schema": json_schema}}
    else:
        kwargs["output_config"] = {"effort": effort}
    kwargs["messages"] = [{"role": "user", "content": prompt}]

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            # Always stream and take the final message. With adaptive thinking,
            # thinking tokens count against max_tokens, so budgets here are
            # large — and non-streaming requests at high max_tokens risk SDK
            # HTTP timeouts (the SDK refuses ones it estimates will run ~10min).
            # Streaming costs nothing when we only want the final text.
            with client.messages.stream(**kwargs) as stream:
                resp = stream.get_final_message()
            if resp.stop_reason == "max_tokens":
                # Truncated mid-JSON. Surface it as retryable rather than
                # letting a half-object reach the parser as a mystery failure.
                raise LLMTransientError(
                    f"response hit max_tokens ({max_tokens}) and was truncated — "
                    "raise max_tokens for this call"
                )
            return _response_text(resp)
        except LLMTransientError:
            raise
        except _NON_RETRYABLE as e:
            # 400/401/403/404 — the request itself is wrong, so retrying just
            # burns backoff. Fail immediately with the message intact so the
            # caller sees what to fix (e.g. an invalid output schema).
            raise LLMRequestError(f"{type(e).__name__}: {e}") from e
        except _RETRYABLE as e:
            last_err = e
            will_retry = attempt < retries - 1
            with _print_lock:
                suffix = f" — retrying in {8 * (attempt + 1)}s" if will_retry else ""
                print(f"  [llm] attempt {attempt + 1}/{retries} failed ({type(e).__name__}){suffix}")
            if will_retry:
                time.sleep(8 * (attempt + 1))
    raise LLMTransientError(f"LLM call failed after {retries} attempts: {last_err}") from last_err


def complete_json(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 8000,
    retries: int = 4,
    json_schema: dict | None = None,
    effort: str = "medium",
    thinking: str = "adaptive",
) -> Any:
    # One shared retry budget covering BOTH failure modes, since either can be
    # transient. Previously this passed retries=1 into complete() and only caught
    # parse errors, so a single 529 killed the call outright despite retries=4.
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            text = complete(
                prompt,
                system=system,
                model=model,
                max_tokens=max_tokens,
                retries=1,  # transport backoff is handled by this loop
                json_schema=json_schema,
                effort=effort,
                thinking=thinking,
            )
            return parse_json(text)
        except LLMRequestError:
            raise  # malformed request — no amount of retrying fixes it
        except (LLMTransientError, ValueError) as e:
            last_err = e
            kind = "JSON parse" if isinstance(e, ValueError) else "transport"
            wait = 8 * (attempt + 1)
            with _print_lock:
                print(f"  [llm] {kind} failure (attempt {attempt + 1}/{retries}): {str(e)[:160]}")
            if attempt < retries - 1:
                time.sleep(wait)
    raise LLMTransientError(f"LLM JSON call failed after {retries} attempts: {last_err}") from last_err


def resolve_workers(requested: int | None = None) -> int:
    """The fan-out width to use: an explicit per-request value, else the
    configured default, clamped to the configured ceiling.

    Centralised so every stage honours the same setting and the same cap — the
    counts used to be hardcoded per call site, which meant tuning one stage did
    nothing for the others.
    """
    settings = get_settings()
    workers = requested if requested and requested > 0 else settings.llm_workers
    return max(1, min(workers, settings.llm_max_workers))


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
async def acomplete_json(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 8000,
    json_schema: dict | None = None,
    effort: str = "medium",
    retries: int = 4,
    thinking: str = "adaptive",
) -> Any:
    """Async counterpart to complete_json, with the same retry semantics —
    non-retryable request errors fail fast; transient errors and parse failures
    share one retry budget with backoff."""
    settings = get_settings()
    model = model or settings.anthropic_model
    client = get_async_client()

    kwargs: dict = dict(model=model, max_tokens=max_tokens, thinking={"type": thinking})
    if system:
        kwargs["system"] = system
    if json_schema is not None:
        kwargs["output_config"] = {"effort": effort, "format": {"type": "json_schema", "schema": json_schema}}
    else:
        kwargs["output_config"] = {"effort": effort}
    kwargs["messages"] = [{"role": "user", "content": prompt}]

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            async with client.messages.stream(**kwargs) as stream:
                resp = await stream.get_final_message()
            if resp.stop_reason == "max_tokens":
                raise ValueError(f"response truncated at max_tokens={max_tokens}")
            return parse_json(_response_text(resp))
        except _NON_RETRYABLE as e:
            raise LLMRequestError(f"{type(e).__name__}: {e}") from e
        except (*_RETRYABLE, ValueError) as e:
            last_err = e
            if attempt < retries - 1:
                await asyncio.sleep(8 * (attempt + 1))
    raise LLMTransientError(f"async LLM JSON call failed after {retries} attempts: {last_err}") from last_err


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
