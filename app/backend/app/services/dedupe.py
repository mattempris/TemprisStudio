"""Step 2 — dedupe the dataset with a parameterised cosine-similarity threshold,
using the fine-tuned jobQWEN embeddings.

Two-part design so the threshold can be a live slider:
  - `build_similarity_graph()` embeds once and computes the candidate pair list.
    Expensive (GPU inference), runs once per dataset.
  - `group_at_threshold()` derives duplicate groups from that cached pair list by
    union-find. Pure CPU set arithmetic over a pre-sorted array, so re-grouping
    at a new threshold is effectively free and can be called on every slider drag.

Groups (including singletons) feed step 3, which is specified to accept "single or
multiple 'duplicate' records as input".
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Pairs below this are never candidates regardless of the user's threshold, so
# the cached pair list stays small on big datasets. Well below any threshold a
# user would plausibly choose for "these are the same job".
MIN_CANDIDATE_SIMILARITY = 0.5


@dataclass
class SimilarityGraph:
    """Cached, threshold-independent similarity structure."""

    item_ids: list[str]
    # parallel arrays of candidate pairs, sorted by descending similarity
    pair_i: np.ndarray
    pair_j: np.ndarray
    pair_sim: np.ndarray

    def pair_count(self) -> int:
        return int(len(self.pair_sim))


@dataclass
class DuplicateGroup:
    group_id: str
    member_ids: list[str]
    representative_id: str
    avg_similarity: float
    # per-member similarity to the representative, for the review UI
    member_similarities: dict[str, float] = field(default_factory=dict)


def build_similarity_graph(item_ids: list[str], embeddings: np.ndarray) -> SimilarityGraph:
    """Compute all candidate duplicate pairs once.

    Embeddings from EmbeddingService are already L2-normalized, so the dot
    product is cosine similarity. Defensive re-normalization anyway, since a
    caller could pass raw vectors.
    """
    assert len(item_ids) == embeddings.shape[0], "item_ids/embeddings length mismatch"
    n = len(item_ids)
    if n < 2:
        empty = np.array([], dtype=np.int64)
        return SimilarityGraph(item_ids, empty, empty, np.array([], dtype=np.float32))

    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    X = embeddings / np.where(norms > 0, norms, 1.0)
    sim = X @ X.T

    # upper triangle only (i < j), excluding the diagonal
    iu, ju = np.triu_indices(n, k=1)
    sims = sim[iu, ju]

    keep = sims >= MIN_CANDIDATE_SIMILARITY
    iu, ju, sims = iu[keep], ju[keep], sims[keep]

    order = np.argsort(-sims)
    return SimilarityGraph(
        item_ids=item_ids,
        pair_i=iu[order].astype(np.int64),
        pair_j=ju[order].astype(np.int64),
        pair_sim=sims[order].astype(np.float32),
    )


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def group_at_threshold(
    graph: SimilarityGraph,
    threshold: float,
    *,
    embeddings: np.ndarray | None = None,
) -> list[DuplicateGroup]:
    """Derive duplicate groups at `threshold` from the cached graph.

    Cheap enough to call per slider drag — no embedding, no matrix multiply.
    Every item ends up in exactly one group; items with no duplicate above the
    threshold become singleton groups, which step 3 handles identically.

    `embeddings` is optional and only used to pick the representative as the
    member closest to the group centroid (a better choice than an arbitrary
    member). Without it, the representative is the member with the highest mean
    similarity to its groupmates.
    """
    n = len(graph.item_ids)
    uf = _UnionFind(n)

    above = graph.pair_sim >= threshold
    for i, j in zip(graph.pair_i[above], graph.pair_j[above]):
        uf.union(int(i), int(j))

    clusters: dict[int, list[int]] = {}
    for idx in range(n):
        clusters.setdefault(uf.find(idx), []).append(idx)

    # pairwise sims within the kept set, for representative choice + reporting
    kept_pairs: dict[tuple[int, int], float] = {}
    for i, j, s in zip(graph.pair_i[above], graph.pair_j[above], graph.pair_sim[above]):
        kept_pairs[(int(i), int(j))] = float(s)

    groups: list[DuplicateGroup] = []
    # deterministic ordering: by descending size, then by first member index
    for order, (_root, members) in enumerate(
        sorted(clusters.items(), key=lambda kv: (-len(kv[1]), min(kv[1])))
    ):
        members = sorted(members)
        rep_idx = _pick_representative(members, kept_pairs, embeddings)

        member_sims: dict[str, float] = {}
        for m in members:
            if m == rep_idx:
                member_sims[graph.item_ids[m]] = 1.0
            else:
                key = (min(m, rep_idx), max(m, rep_idx))
                member_sims[graph.item_ids[m]] = kept_pairs.get(key, _direct_sim(m, rep_idx, embeddings))

        intra = [
            kept_pairs[(min(a, b), max(a, b))]
            for ai, a in enumerate(members)
            for b in members[ai + 1 :]
            if (min(a, b), max(a, b)) in kept_pairs
        ]
        groups.append(
            DuplicateGroup(
                group_id=f"grp-{order:04d}",
                member_ids=[graph.item_ids[m] for m in members],
                representative_id=graph.item_ids[rep_idx],
                avg_similarity=float(np.mean(intra)) if intra else 1.0,
                member_similarities=member_sims,
            )
        )
    return groups


def _direct_sim(a: int, b: int, embeddings: np.ndarray | None) -> float:
    if embeddings is None:
        return 0.0
    va, vb = embeddings[a], embeddings[b]
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va @ vb / denom) if denom else 0.0


def _pick_representative(
    members: list[int],
    kept_pairs: dict[tuple[int, int], float],
    embeddings: np.ndarray | None,
) -> int:
    if len(members) == 1:
        return members[0]

    if embeddings is not None:
        sub = embeddings[members]
        centroid = sub.mean(axis=0)
        dists = np.linalg.norm(sub - centroid, axis=1)
        return members[int(np.argmin(dists))]

    best, best_score = members[0], -1.0
    for m in members:
        scores = [
            kept_pairs.get((min(m, o), max(m, o)), 0.0) for o in members if o != m
        ]
        mean_score = float(np.mean(scores)) if scores else 0.0
        if mean_score > best_score:
            best, best_score = m, mean_score
    return best


@dataclass
class DedupeSummary:
    threshold: float
    total_items: int
    group_count: int
    duplicate_group_count: int  # groups with >1 member
    items_merged_away: int  # total_items - group_count
    groups: list[DuplicateGroup]


def summarize(graph: SimilarityGraph, threshold: float, *, embeddings: np.ndarray | None = None) -> DedupeSummary:
    groups = group_at_threshold(graph, threshold, embeddings=embeddings)
    return DedupeSummary(
        threshold=threshold,
        total_items=len(graph.item_ids),
        group_count=len(groups),
        duplicate_group_count=sum(1 for g in groups if len(g.member_ids) > 1),
        items_merged_away=len(graph.item_ids) - len(groups),
        groups=groups,
    )
