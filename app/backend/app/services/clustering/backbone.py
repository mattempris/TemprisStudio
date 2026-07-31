"""Ward backbone + consensus stability scoring.

Direct port of the algorithm in
`Additional methodology context/code/taxonomy_run.py::backbone()`, generalized to:
  - be entity-agnostic (no hardcoded file paths / dataframe columns)
  - support 3 hierarchy tiers (finest/middle/coarsest) instead of the reference's
    2 (coarse/fine), cut from the SAME cached tree
  - return plain data structures rather than writing CSVs, so the API layer owns
    persistence (see app/core/blob_store.py's artifacts/ layout)

Stability is computed at the FINEST tier only (see plan's methodology section) —
that's what naming and downstream generation key off. Coarser tiers are derived
from the finest tier's cluster membership via majority-vote rollup (rollup.py),
not independently stability-scored.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage


def build_linkage_tree(embeddings: np.ndarray) -> np.ndarray:
    """Unit-normalize (so Ward/Euclidean tracks cosine similarity for text
    embeddings — embeddings from EmbeddingService are already normalized, but this
    is defensive) and build one Ward agglomerative tree."""
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normalized = embeddings / np.where(norms > 0, norms, 1.0)
    return linkage(normalized, method="ward")


def cut_tree(tree: np.ndarray, k: int) -> np.ndarray:
    """Cut the tree into k flat clusters. Returns 0-indexed labels."""
    return fcluster(tree, t=k, criterion="maxclust") - 1


@dataclass
class StabilityResult:
    scores: np.ndarray  # per-item stability, NaN if the item had no peers to compare against
    n_perturb: int
    subsample_frac: float


def consensus_stability(
    embeddings: np.ndarray,
    ref_labels: np.ndarray,
    *,
    k: int,
    n_perturb: int = 50,
    subsample_frac: float = 0.9,
    seed: int = 42,
) -> StabilityResult:
    """Bootstrap consensus stability, sidestepping the cluster-label-permutation
    problem by only ever asking "did these two specific items co-occur," never
    "which numbered cluster is this" — exact port of taxonomy_run.py::backbone()'s
    stability computation.
    """
    n = len(embeddings)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    X = embeddings / np.where(norms > 0, norms, 1.0)

    m = int(n * subsample_frac)
    co = np.zeros((n, n), dtype=np.float32)
    pres = np.zeros((n, n), dtype=np.float32)

    for p in range(n_perturb):
        rng = np.random.default_rng(seed + p)
        idx = np.sort(rng.choice(n, size=m, replace=False))
        sub_tree = linkage(X[idx], method="ward")
        labels = fcluster(sub_tree, t=k, criterion="maxclust")
        same = (labels[:, None] == labels[None, :]).astype(np.float32)
        ii = np.ix_(idx, idx)
        co[ii] += same
        pres[ii] += 1.0

    consensus = co / np.where(pres > 0, pres, 1)
    stability = np.full(n, np.nan)
    for i in range(n):
        peers = np.where(ref_labels == ref_labels[i])[0]
        peers = peers[peers != i]
        if len(peers):
            stability[i] = float(consensus[i, peers].mean())

    return StabilityResult(scores=stability, n_perturb=n_perturb, subsample_frac=subsample_frac)


@dataclass
class ClusterExemplars:
    """Nearest-to-centroid representative items per cluster, used to build naming/
    routing/review prompts without sending every item to the LLM."""

    by_cluster: dict[int, list[int]] = field(default_factory=dict)  # cluster_id -> item indices, nearest first


def compute_exemplars(embeddings: np.ndarray, labels: np.ndarray, *, k_per_cluster: int = 6) -> ClusterExemplars:
    result = ClusterExemplars()
    for cid in range(int(labels.max()) + 1):
        members = np.where(labels == cid)[0]
        if len(members) == 0:
            result.by_cluster[cid] = []
            continue
        centroid = embeddings[members].mean(axis=0)
        order = members[np.argsort(np.linalg.norm(embeddings[members] - centroid, axis=1))]
        result.by_cluster[cid] = order[:k_per_cluster].tolist()
    return result
