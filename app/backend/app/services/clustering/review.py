"""On-demand quality review passes: critique (merge/split/missing suggestions) and
catch-all/grab-bag detection.

Adapted from `taxonomy_run.py::critique()` and the reference's manual six-subagent
catch-all review (`method/catch_all_review.md`) — here reimplemented as N
independent parallel LLM calls per cluster (not ad hoc subagent sessions), so it's
a repeatable product feature. Both passes are advisory: findings are surfaced to
the user for Accept/Reject/Edit, never auto-applied (see plan's methodology
section).
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass

from app.services import llm

CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {
        "merges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"clusters": {"type": "array", "items": {"type": "string"}}, "why": {"type": "string"}},
                "required": ["clusters", "why"],
                "additionalProperties": False,
            },
        },
        "splits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"cluster": {"type": "string"}, "why": {"type": "string"}},
                "required": ["cluster", "why"],
                "additionalProperties": False,
            },
        },
        "missing": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "why": {"type": "string"}},
                "required": ["name", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["merges", "splits", "missing"],
    "additionalProperties": False,
}

CRITIQUE_SYSTEM = (
    "You are auditing a draft taxonomy. Given the clusters (with sizes and example "
    "items) grouped under parent clusters, identify quality issues ONLY where "
    "clear: (a) pairs that should MERGE (near-duplicates), (b) clusters that should "
    "SPLIT (two distinct themes lumped together), (c) MISSING clusters a coherent "
    "taxonomy of this set should have but that are absent. Be conservative — report "
    "only confident, actionable items. This is advisory; nothing is auto-applied."
)


def critique(tree_text: str) -> dict:
    return llm.complete_json(
        f"Draft taxonomy:\n\n{tree_text}",
        system=CRITIQUE_SYSTEM,
        json_schema=CRITIQUE_SCHEMA,
        effort="medium",
        max_tokens=4000,
    )


CATCH_ALL_SCHEMA = {
    "type": "object",
    "properties": {
        "coherent": {"type": "boolean"},
        "severity": {"type": "string", "enum": ["NONE", "LOW", "MED", "HIGH"]},
        "rationale": {"type": "string"},
        "proposed_children": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["coherent", "severity", "rationale", "proposed_children"],
    "additionalProperties": False,
}

_CALIBRATION_EXAMPLE = (
    "Calibration example of a confirmed grab-bag (severity HIGH): a cluster named "
    "'HR Business Partnering & Operations' containing HR business partnering, "
    "talent acquisition & recruiting, HRIS & people analytics, and people "
    "operations & admin all fused together — these are four distinct disciplines "
    "that happen to share an HR umbrella, not one coherent function."
)


def _catch_all_system() -> str:
    return (
        "You review one cluster from a taxonomy and judge whether it reads as one "
        "coherent theme or as a catch-all / grab-bag that fused several distinct "
        "themes together. "
        f"{_CALIBRATION_EXAMPLE} "
        "If you flag it as a grab-bag (severity MED or HIGH), propose 2-5 child "
        "cluster names it should be split into. Be honest — most clusters are fine; "
        "only flag ones a domain expert would genuinely object to."
    )


async def _one_reviewer(cluster_name: str, member_summaries: list[str]) -> dict:
    prompt = f"Cluster: {cluster_name}\n\nMembers:\n" + "\n".join(f"- {m}" for m in member_summaries)
    return await llm.acomplete_json(
        prompt, system=_catch_all_system(), json_schema=CATCH_ALL_SCHEMA, effort="medium", max_tokens=1000
    )


@dataclass
class CatchAllVerdict:
    cluster_name: str
    severity: str  # aggregated: NONE/LOW/MED/HIGH by majority
    reviews: list[dict]
    proposed_children: list[str]


async def catch_all_review(cluster_name: str, member_summaries: list[str], *, n_reviewers: int = 5) -> CatchAllVerdict:
    reviews = await asyncio.gather(*[_one_reviewer(cluster_name, member_summaries) for _ in range(n_reviewers)])
    severities = [r["severity"] for r in reviews]
    majority_severity, _count = Counter(severities).most_common(1)[0]
    # collect proposed child names from any reviewer who flagged MED/HIGH, dedup preserving order
    proposed: list[str] = []
    for r in reviews:
        if r["severity"] in ("MED", "HIGH"):
            for name in r["proposed_children"]:
                if name not in proposed:
                    proposed.append(name)
    return CatchAllVerdict(cluster_name=cluster_name, severity=majority_severity, reviews=reviews, proposed_children=proposed)


async def catch_all_review_all(
    clusters: list[tuple[str, list[str]]], *, n_reviewers: int = 5, concurrency: int = 4
) -> list[CatchAllVerdict]:
    """clusters: list of (cluster_name, member_summaries)."""
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(name: str, members: list[str]) -> CatchAllVerdict:
        async with sem:
            return await catch_all_review(name, members, n_reviewers=n_reviewers)

    return await asyncio.gather(*[_bounded(name, members) for name, members in clusters])
