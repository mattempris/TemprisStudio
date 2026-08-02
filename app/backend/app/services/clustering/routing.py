"""LLM routing of the unstable (low-stability) slice, with inline self-consistency.

Direct port of `taxonomy_run.py::route_one()`/`route_all()`, generalized per entity
type. Note: `temperature` is NOT accepted by claude-sonnet-5 (or the rest of the
4.6+ model family) — sending it returns a 400 (see `services/llm.py`). Unlike the
reference implementation, which pinned it low for routing determinism, this build
has no sampling-temperature lever at all; determinism instead comes from a fixed
low `effort` plus the majority-vote self-consistency pass below, which is exactly
the mechanism the reference used to guard against non-determinism anyway.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from app.services import llm

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "primary": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "confidence": {"type": "number"}},
            "required": ["id", "confidence"],
            "additionalProperties": False,
        },
        "secondary": {
            "type": ["object", "null"],
            "properties": {"id": {"type": "integer"}, "confidence": {"type": "number"}},
            "required": ["id", "confidence"],
            "additionalProperties": False,
        },
        "reasoning": {"type": "string"},
    },
    "required": ["primary", "secondary", "reasoning"],
    "additionalProperties": False,
}

_ENTITY_NOUN = {"job": "job role", "skill": "skill", "task": "task"}


def _route_system_prompt(entity: str) -> str:
    noun = _ENTITY_NOUN[entity]
    return (
        f"You assign one ambiguous {noun} to the cluster it best belongs to, "
        "choosing from a fixed set of clusters. Reason about its core function and "
        "substance, not surface wording or seniority/scale. Return the primary "
        "cluster id with a confidence 0-1. If it genuinely spans two clusters, "
        "also return a secondary cluster id with its confidence; otherwise "
        "secondary must be null. Be calibrated: low confidence when it is a poor "
        "fit for every cluster — that is a signal the cluster set is missing a "
        "bucket, not a failure to guess."
    )


@dataclass
class RouteResult:
    item_index: int
    primary_cluster_id: int
    primary_confidence: float
    secondary_cluster_id: int | None
    secondary_confidence: float | None
    reasoning: str
    self_consistency: dict | None = None  # {"votes": [...], "agreement": float} if SC ran


async def route_one(item_index: int, item_text: str, clusters_text: str, *, entity: str) -> dict:
    """One routing decision.

    The cluster list goes in as a cache prefix rather than inside the user message.
    It is byte-identical for every item in a run and dwarfs everything else in the
    request — a 934-cluster skill taxonomy carries ~19,500 tokens of it — so sending
    it as cached system content turns the repetition from full price into a cache
    read at a tenth of that. The item itself, the only part that varies, stays in
    the user message where it cannot disturb the cached prefix.
    """
    system = _route_system_prompt(entity)
    return await llm.acomplete_json(
        f"Item to assign:\n{item_text}",
        system=system,
        cache_prefix=f"The clusters to choose from:\n{clusters_text}",
        json_schema=ROUTE_SCHEMA,
        effort="low",
        max_tokens=600,
    )


async def route_all(
    items: list[tuple[int, str]],  # (item_index, item_text)
    clusters_text: str,
    *,
    entity: str,
    concurrency: int = 8,
    sc_confidence_threshold: float = 0.45,
    sc_votes: int = 3,
    progress=None,
) -> dict[int, RouteResult]:
    """Route every item once, then run (sc_votes - 1) extra votes on any item whose
    first-pass confidence falls below sc_confidence_threshold, majority-voting the
    final answer. Mirrors taxonomy_run.py::route_all()."""

    async def _first_pass(pair: tuple[int, str]) -> dict:
        idx, text = pair
        res = await route_one(idx, text, clusters_text, entity=entity)
        return res

    # Route the first item alone before fanning out. Nothing is in the cache yet, so
    # launching the full width immediately would have every one of those calls miss
    # and every one of them write the same prefix — paying the 1.25x write premium N
    # times instead of once. One call first means one write and the rest read.
    warmed: list[dict] = []
    if items and llm.is_cacheable(clusters_text):
        warmed.append(await _first_pass(items[0]))
        if progress:
            progress(1, len(items), "route")

    rest = items[len(warmed) :]
    # amap counts only what it was given, so the warm-up call has to be added back
    # or the bar restarts one short of the real total.
    offset = len(warmed)
    total = len(items)

    def _offset_progress(done: int, _sub_total: int, label: str) -> None:
        if progress:
            progress(done + offset, total, label)

    first = warmed + await llm.amap(
        _first_pass, rest, concurrency=concurrency,
        progress=_offset_progress if progress else None, label="route",
    )
    out: dict[int, dict] = {idx: res for (idx, _text), res in zip(items, first)}

    low = [(idx, text) for (idx, text), res in zip(items, first) if res["primary"]["confidence"] < sc_confidence_threshold]
    if low:
        print(f"  [routing] self-consistency on {len(low)} low-confidence routes ({sc_votes}x)...")

        async def _extra_vote(pair: tuple[int, str]) -> dict:
            idx, text = pair
            return await route_one(idx, text, clusters_text, entity=entity)

        expanded = [pair for pair in low for _ in range(sc_votes - 1)]
        extra_results = await llm.amap(_extra_vote, expanded, concurrency=concurrency, label="route-sc")

        by_item: dict[int, list[dict]] = {idx: [out[idx]] for idx, _ in low}
        k = sc_votes - 1
        for j, (idx, _text) in enumerate(low):
            by_item[idx].extend(extra_results[j * k : (j + 1) * k])

        for idx, votes in by_item.items():
            ids = [v["primary"]["id"] for v in votes]
            winner = Counter(ids).most_common(1)[0][0]
            agreeing_confs = [v["primary"]["confidence"] for v in votes if v["primary"]["id"] == winner]
            chosen = dict(next(v for v in votes if v["primary"]["id"] == winner))
            chosen["primary"] = {"id": winner, "confidence": float(np.mean(agreeing_confs))}
            chosen["self_consistency"] = {"votes": ids, "agreement": ids.count(winner) / len(ids)}
            out[idx] = chosen

    return {
        idx: RouteResult(
            item_index=idx,
            primary_cluster_id=res["primary"]["id"],
            primary_confidence=res["primary"]["confidence"],
            secondary_cluster_id=(res.get("secondary") or {}).get("id"),
            secondary_confidence=(res.get("secondary") or {}).get("confidence"),
            reasoning=res.get("reasoning", ""),
            self_consistency=res.get("self_consistency"),
        )
        for idx, res in out.items()
    }
