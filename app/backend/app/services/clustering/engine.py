"""Entity-agnostic clustering engine tying together backbone/stability/routing/
naming/rollup into one pipeline. Used identically for jobs, skills, and tasks —
see plan's "Clustering, Stability Gating & Review" section.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services.clustering import backbone as bb
from app.services.clustering import naming, routing, rollup


@dataclass
class ItemAssignment:
    """Full audit-trail record for one clustered item — mirrors the reference
    CSV's column design (backbone_*, final_*, stability, routed_by_llm, etc.)."""

    item_index: int
    backbone_profile_id: int
    backbone_category_id: int
    backbone_family_id: int
    final_profile_id: int
    final_category_id: int
    final_family_id: int
    stability_score: float | None
    routed_by_llm: bool = False
    route_confidence: float | None = None
    secondary_profile_id: int | None = None
    secondary_confidence: float | None = None
    self_consistency: dict | None = None


@dataclass
class ClusteringResult:
    tree: np.ndarray  # cached Ward linkage tree — reused for any future re-cut
    k_family: int
    k_category: int
    k_profile: int
    assignments: list[ItemAssignment]
    profile_names: dict[int, str] = field(default_factory=dict)
    category_names: dict[int, str] = field(default_factory=dict)
    family_names: dict[int, str] = field(default_factory=dict)
    gate: float = 0.58
    n_unstable: int = 0


def cut_three_tiers(tree: np.ndarray, *, k_family: int, k_category: int, k_profile: int) -> dict[str, np.ndarray]:
    """Guardrails: k_family <= k_category <= k_profile (coarsest to finest)."""
    if not (k_family <= k_category <= k_profile):
        raise ValueError(
            f"cluster counts must satisfy k_family <= k_category <= k_profile, "
            f"got family={k_family} category={k_category} profile={k_profile}"
        )
    return {
        "family": bb.cut_tree(tree, k_family),
        "category": bb.cut_tree(tree, k_category),
        "profile": bb.cut_tree(tree, k_profile),
    }


async def run_clustering_pipeline(
    entity: str,
    item_texts: list[str],
    embeddings: np.ndarray,
    *,
    k_family: int,
    k_category: int,
    k_profile: int,
    gate: float = 0.58,
    n_perturb: int = 50,
    subsample_frac: float = 0.9,
    sc_confidence_threshold: float = 0.45,
    sc_votes: int = 3,
    route_concurrency: int = 8,
    seed: int = 42,
    progress=None,
) -> ClusteringResult:
    n = len(item_texts)
    assert embeddings.shape[0] == n, "item_texts and embeddings length mismatch"

    tree = bb.build_linkage_tree(embeddings)
    cuts = cut_three_tiers(tree, k_family=k_family, k_category=k_category, k_profile=k_profile)

    stability = bb.consensus_stability(
        embeddings, cuts["profile"], k=k_profile, n_perturb=n_perturb, subsample_frac=subsample_frac, seed=seed
    )

    # backbone parent rollups (profile -> category -> family), computed from the
    # ORIGINAL cuts before any routing moves items between profile clusters
    profile_to_category = rollup.majority_vote_parent(cuts["profile"], cuts["category"])
    category_to_family = rollup.majority_vote_parent(cuts["category"], cuts["family"])

    # ---- naming (coarsest first, each finer level gets parent context) ----
    family_exemplars = bb.compute_exemplars(embeddings, cuts["family"])
    family_blocks = [
        naming.build_cluster_block(fid, [item_texts[i] for i in idxs])
        for fid, idxs in family_exemplars.by_cluster.items()
    ]
    family_names = naming.name_level(entity, "family", family_blocks, k_family, has_parent_context=False)

    category_exemplars = bb.compute_exemplars(embeddings, cuts["category"])
    category_blocks = [
        naming.build_cluster_block(
            cid, [item_texts[i] for i in idxs], parent_name=family_names.get(category_to_family.get(cid, -1))
        )
        for cid, idxs in category_exemplars.by_cluster.items()
    ]
    category_names = naming.name_level(entity, "category", category_blocks, k_category, has_parent_context=True)

    profile_exemplars = bb.compute_exemplars(embeddings, cuts["profile"])
    profile_blocks = [
        naming.build_cluster_block(
            pid, [item_texts[i] for i in idxs], parent_name=category_names.get(profile_to_category.get(pid, -1))
        )
        for pid, idxs in profile_exemplars.by_cluster.items()
    ]
    profile_names = naming.name_level(entity, "profile", profile_blocks, k_profile, has_parent_context=True)

    # ---- gate + route the unstable slice ----
    unstable_indices = [i for i in range(n) if not np.isnan(stability.scores[i]) and stability.scores[i] < gate]

    final_profile = cuts["profile"].copy()
    route_results: dict[int, routing.RouteResult] = {}
    if unstable_indices:
        clusters_text = "\n".join(
            f"[{pid}] {profile_names.get(pid, '?')} (under {category_names.get(profile_to_category.get(pid, -1), '?')})"
            f" — e.g. {', '.join(item_texts[i] for i in idxs[:4])}"
            for pid, idxs in profile_exemplars.by_cluster.items()
        )
        items_to_route = [(i, item_texts[i]) for i in unstable_indices]
        route_results = await routing.route_all(
            items_to_route,
            clusters_text,
            entity=entity,
            concurrency=route_concurrency,
            sc_confidence_threshold=sc_confidence_threshold,
            sc_votes=sc_votes,
            progress=progress,
        )
        for idx, result in route_results.items():
            if result.primary_cluster_id in profile_names:
                final_profile[idx] = result.primary_cluster_id
            else:
                print(f"  [routing] item {idx} routed to invalid profile id {result.primary_cluster_id} — keeping backbone")

    # ---- final category/family follow the FINAL (post-routing) profile assignment ----
    assignments: list[ItemAssignment] = []
    for i in range(n):
        final_pid = int(final_profile[i])
        final_cid = profile_to_category.get(final_pid, int(cuts["category"][i]))
        final_fid = category_to_family.get(final_cid, int(cuts["family"][i]))
        route = route_results.get(i)
        assignments.append(
            ItemAssignment(
                item_index=i,
                backbone_profile_id=int(cuts["profile"][i]),
                backbone_category_id=int(cuts["category"][i]),
                backbone_family_id=int(cuts["family"][i]),
                final_profile_id=final_pid,
                final_category_id=final_cid,
                final_family_id=final_fid,
                stability_score=None if np.isnan(stability.scores[i]) else float(stability.scores[i]),
                routed_by_llm=route is not None,
                route_confidence=route.primary_confidence if route else None,
                secondary_profile_id=route.secondary_cluster_id if route else None,
                secondary_confidence=route.secondary_confidence if route else None,
                self_consistency=route.self_consistency if route else None,
            )
        )

    return ClusteringResult(
        tree=tree,
        k_family=k_family,
        k_category=k_category,
        k_profile=k_profile,
        assignments=assignments,
        profile_names=profile_names,
        category_names=category_names,
        family_names=family_names,
        gate=gate,
        n_unstable=len(unstable_indices),
    )
