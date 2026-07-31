"""Majority-vote parent rollup — the 3-tier adaptation of taxonomy_run.py's
`fine_to_coarse` mechanism (see plan's "Adapting the reference's 2-tier method"
section).

The reference cuts one Ward tree at 2 heights (coarse/fine) and derives each fine
cluster's coarse parent by majority vote of its members' coarse-cut labels. We
need 3 tiers (family/category/profile for jobs), so this same majority-vote
mechanism is applied TWICE: profile (finest) -> category (middle), then
category -> family (coarsest). Critically, after LLM routing moves some items
between finest-tier clusters, the parent rollup is recomputed from the FINAL
(post-routing) finest-tier labels — not the original backbone labels — so
cross-cluster LLM moves stay hierarchically consistent (this is exactly the fix
the reference's production run made after its own prototype hit the "levels can
disagree" bug).
"""
from __future__ import annotations

import numpy as np


def majority_vote_parent(child_labels: np.ndarray, parent_cut_labels: np.ndarray) -> dict[int, int]:
    """For each distinct child cluster id, find which parent-cut cluster id most of
    its members fall into (by the ORIGINAL backbone cut, before any LLM routing —
    routing only ever moves items between child clusters, so the parent-cut
    membership of any given item never changes; only which child cluster claims
    that item changes).

    Returns {child_id: parent_id}.
    """
    mapping: dict[int, int] = {}
    for child_id in range(int(child_labels.max()) + 1):
        members = np.where(child_labels == child_id)[0]
        if len(members) == 0:
            continue
        counts = np.bincount(parent_cut_labels[members])
        mapping[child_id] = int(counts.argmax())
    return mapping


def rederive_parent_for_item(item_parent_cut_label: int, child_to_parent: dict[int, int], child_id: int) -> int:
    """After routing moves an item to `child_id`, its coarser-tier assignment
    follows `child_id`'s majority-vote parent — NOT the item's own original
    parent-cut label. This is what keeps the hierarchy consistent after
    cross-cluster LLM moves."""
    return child_to_parent[child_id]
