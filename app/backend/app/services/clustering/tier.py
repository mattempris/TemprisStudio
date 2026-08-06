"""One tier of the hierarchy: cluster a set of items, gate, route, name.

instructions.txt steps 5/6 ask the user to choose cluster counts for job profiles,
categories and families and then name them. This module runs exactly one of those
tiers, so the same code drives all three and each becomes a step the user confirms
separately.

**Why per-tier rather than one tree cut three times.** The earlier design cut a
single Ward tree at three heights and derived the coarser tiers by majority-vote
rollup of the finest tier's labels. That preserves nesting, but it means the
category boundaries are whatever the job-level geometry implies, and a category is
never actually *chosen* — it falls out. Clustering each tier over the previous
tier's centroids instead asks the right question at each level ("which of these
profiles belong together?"), and nesting becomes structural: every job has exactly
one profile, every profile exactly one category, every category exactly one
family. There is nothing left for a rollup to disagree about.

The cost of that is one Ward tree per tier instead of one overall — but tiers 2
and 3 run over hundreds and then tens of centroids, so they are trivial compared
with the job-level tree.

**Two phases, deliberately.** `analyse()` does everything that costs no LLM spend:
the tree, the cut, and the bootstrap stability scores. `finalise()` does the parts
that do: routing the unstable slice and naming. Splitting them is what lets the UI
show "at this gate, N items go to the model" *before* the user commits to paying
for it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services.clustering import backbone as bb
from app.services.clustering import naming, routing

# Tier names as used by the API and the wizard. "profile" clusters the raw items;
# "category" clusters profiles; "family" clusters categories.
TIERS = ("profile", "category", "family")


@dataclass
class TierItems:
    """The things being clustered at this tier.

    For the profile tier these are the normalised jobs. For the category tier each
    item is a profile cluster, represented by the centroid of its members and a
    text block combining its confirmed name with a few example members — the name
    alone is too thin for the router to reason about, and the members alone lose
    the naming decision the user already confirmed.
    """

    ids: list[str]           # stable identifier per item (record id, or "profile:3")
    texts: list[str]
    embeddings: np.ndarray

    def __len__(self) -> int:
        return len(self.ids)


@dataclass
class TierAnalysis:
    """Result of the no-LLM phase: what the geometry says, before any spend."""

    k: int
    labels: np.ndarray            # backbone cluster per item
    stability: np.ndarray         # per-item stability, NaN where undefined
    sizes: list[int]

    def routed_count(self, gate: float) -> int:
        return int(np.sum(~np.isnan(self.stability) & (self.stability < gate)))

    def distribution(self, bins: int = 10) -> list[dict]:
        """Stability histogram, so the gate slider can be positioned against the
        actual shape of the data rather than a remembered default."""
        valid = self.stability[~np.isnan(self.stability)]
        if valid.size == 0:
            return []
        edges = np.linspace(0.0, 1.0, bins + 1)
        counts, _ = np.histogram(valid, bins=edges)
        return [
            {"from": round(float(edges[i]), 2), "to": round(float(edges[i + 1]), 2), "count": int(counts[i])}
            for i in range(bins)
        ]


@dataclass
class TierMemberOutcome:
    item_id: str
    backbone_cluster_id: int
    final_cluster_id: int
    stability_score: float | None
    routed_by_llm: bool
    route_confidence: float | None = None
    secondary_cluster_id: int | None = None
    secondary_confidence: float | None = None
    self_consistency: dict | None = None


@dataclass
class TierResult:
    k: int
    gate: float
    names: dict[int, str]
    members: list[TierMemberOutcome]
    n_routed: int
    n_moved: int              # routed AND actually reassigned
    low_confidence: int
    multi_home: int
    centroids: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))
    exemplar_texts: dict[int, list[str]] = field(default_factory=dict)
    # One sentence per cluster, written from its exemplars at naming time. Sparse by
    # design and defaulted: a cluster the model named but did not describe keeps its
    # name, and the identity-tier path constructs a result without descriptions at all.
    descriptions: dict[int, str] = field(default_factory=dict)


def analyse(items: TierItems, *, k: int, n_perturb: int = 50, subsample_frac: float = 0.9,
            seed: int = 42, tree: np.ndarray | None = None) -> TierAnalysis:
    """Cut at k and score stability. No LLM calls, so this is safe to re-run while
    the user explores cluster counts."""
    if k < 2:
        raise ValueError("k must be at least 2")
    if k >= len(items):
        raise ValueError(f"k must be less than the number of items ({len(items)})")

    tree = tree if tree is not None else bb.build_linkage_tree(items.embeddings)
    labels = bb.cut_tree(tree, k)
    stability = bb.consensus_stability(
        items.embeddings, labels, k=k, n_perturb=n_perturb, subsample_frac=subsample_frac, seed=seed
    ).scores
    sizes = np.bincount(labels, minlength=int(labels.max()) + 1).tolist()
    return TierAnalysis(k=k, labels=labels, stability=stability, sizes=sizes)


async def finalise(
    items: TierItems,
    analysis: TierAnalysis,
    *,
    entity: str,
    tier: str,
    gate: float,
    parent_names: dict[str, str] | None = None,
    sc_confidence_threshold: float = 0.45,
    sc_votes: int = 3,
    route_concurrency: int = 8,
    progress=None,
    naming_progress=None,
    on_phase=None,
) -> TierResult:
    """Name the clusters, then route the items the geometry was unsure about.

    Naming happens BEFORE routing because the router picks a cluster by name — it
    cannot choose between "cluster 4" and "cluster 11". `parent_names` is unused at
    the profile tier and carries the already-confirmed child names upward at the
    coarser tiers, which is what makes naming bottom-up: a category is named from
    the profiles it contains rather than from a family that does not exist yet.

    Two separately-reported phases, because at real scale naming is minutes of work
    on its own: `naming_progress(done, total)` covers it, then `on_phase(label,
    total)` re-baselines the caller's progress bar before routing starts.
    """
    exemplars = bb.compute_exemplars(items.embeddings, analysis.labels)
    blocks = [
        naming.build_cluster_block(cid, [items.texts[i] for i in idxs])
        for cid, idxs in exemplars.by_cluster.items()
    ]
    names, descriptions = naming.name_level(
        entity, tier, blocks, analysis.k, has_parent_context=False, progress=naming_progress
    )

    final = analysis.labels.copy()
    unstable = [
        i for i in range(len(items))
        if not np.isnan(analysis.stability[i]) and analysis.stability[i] < gate
    ]

    if on_phase:
        on_phase(
            f"Re-checking {len(unstable)} uncertain {tier} assignments with the model"
            if unstable
            else "No uncertain assignments to re-check",
            len(unstable),
        )

    route_results: dict[int, routing.RouteResult] = {}
    if unstable:
        clusters_text = "\n".join(
            f"[{cid}] {names.get(cid, '?')} — e.g. {', '.join(items.texts[i][:120] for i in idxs[:4])}"
            for cid, idxs in exemplars.by_cluster.items()
        )
        route_results = await routing.route_all(
            [(i, items.texts[i]) for i in unstable],
            clusters_text,
            entity=entity,
            concurrency=route_concurrency,
            sc_confidence_threshold=sc_confidence_threshold,
            sc_votes=sc_votes,
            progress=progress,
        )
        for idx, result in route_results.items():
            if result.primary_cluster_id in names:
                final[idx] = result.primary_cluster_id
            else:
                # A hallucinated cluster id is kept out rather than crashing the
                # tier; the backbone assignment stands and the audit shows it.
                print(
                    f"  [tier:{tier}] item {idx} routed to unknown cluster "
                    f"{result.primary_cluster_id} — keeping backbone assignment"
                )

    # Routing can empty a cluster: if the model moves out every item Ward put in
    # one, its name survives with nothing behind it and the taxonomy carries a
    # group that describes no work at all. Dropping the name is the honest
    # outcome — the cluster no longer exists, and keeping it would put an empty
    # branch in the browser and an empty column in every export.
    #
    # The ids of surviving clusters are left alone rather than renumbered: the
    # audit trail, the stored centroids and the tier above all reference them.
    occupied = set(int(c) for c in np.unique(final))
    emptied = sorted(set(names) - occupied)
    for cid in emptied:
        print(f"  [tier:{tier}] dropping '{names[cid]}' — routing moved out every member")
        del names[cid]

    members = [
        TierMemberOutcome(
            item_id=items.ids[i],
            backbone_cluster_id=int(analysis.labels[i]),
            final_cluster_id=int(final[i]),
            stability_score=None if np.isnan(analysis.stability[i]) else float(analysis.stability[i]),
            routed_by_llm=i in route_results,
            route_confidence=route_results[i].primary_confidence if i in route_results else None,
            secondary_cluster_id=route_results[i].secondary_cluster_id if i in route_results else None,
            secondary_confidence=route_results[i].secondary_confidence if i in route_results else None,
            self_consistency=route_results[i].self_consistency if i in route_results else None,
        )
        for i in range(len(items))
    ]

    centroids = _centroids(items.embeddings, final, analysis.k)
    exemplar_texts = {
        cid: [items.texts[i] for i in idxs[:4]]
        for cid, idxs in exemplars.by_cluster.items()
        if cid in names
    }

    return TierResult(
        # The count the user confirmed, minus anything routing emptied. Reporting
        # the requested k here would claim clusters that no longer exist.
        k=len(names),
        gate=gate,
        names=names,
        members=members,
        n_routed=len(route_results),
        n_moved=sum(1 for m in members if m.routed_by_llm and m.backbone_cluster_id != m.final_cluster_id),
        low_confidence=sum(
            1 for m in members
            if m.route_confidence is not None and m.route_confidence < sc_confidence_threshold
        ),
        multi_home=sum(1 for m in members if m.secondary_cluster_id is not None),
        centroids=centroids,
        exemplar_texts=exemplar_texts,
        descriptions=descriptions,
    )


def _centroids(embeddings: np.ndarray, labels: np.ndarray, k: int) -> np.ndarray:
    """Unit-normalised mean per cluster — the next tier's item vectors.

    Re-normalising matters: the mean of unit vectors is not a unit vector, and the
    next tier compares with cosine, so leaving them unnormalised would let cluster
    tightness leak in as apparent magnitude.
    """
    dim = embeddings.shape[1]
    out = np.zeros((k, dim), dtype=np.float32)
    for cid in range(k):
        rows = embeddings[labels == cid]
        if rows.size == 0:
            continue  # an empty cluster keeps a zero vector rather than NaN
        mean = rows.mean(axis=0)
        norm = float(np.linalg.norm(mean))
        out[cid] = mean / norm if norm > 0 else mean
    return out


def items_from_clusters(
    result: TierResult, tier_label: str, *, max_members: int = 4
) -> TierItems:
    """Turn a confirmed tier into the next tier's items.

    Each item is one cluster: its centroid as the vector, and its confirmed name
    plus a few member examples as the text. Both parts are needed — the name is
    the decision the user just made, the examples are the evidence behind it.
    """
    ids, texts, vectors = [], [], []
    for cid in sorted(result.names):
        name = result.names[cid]
        members = result.exemplar_texts.get(cid, [])[:max_members]
        detail = "; ".join(m.split(".")[0][:90] for m in members)
        texts.append(f"{name}" + (f". Includes: {detail}" if detail else ""))
        ids.append(f"{tier_label}:{cid}")
        vectors.append(result.centroids[cid])
    return TierItems(ids=ids, texts=texts, embeddings=np.vstack(vectors) if vectors else np.empty((0, 0)))
