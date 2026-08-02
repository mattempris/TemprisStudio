"""A cluster the model empties must not survive as a name with nothing behind it.

The symptom: a skill taxonomy containing "Performance and Market Scrutiny" with zero
skills mapped. Ward cannot produce an empty cluster, but routing can — if it moves
out every member Ward put in one, the name is left describing no work at all.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.clustering import naming, routing, tier as tier_engine  # noqa: E402

N, K = 60, 8
EMPTY_ME = 3          # every member of this cluster gets routed elsewhere
DESTINATION = 0


def stub_naming(prompt, *, system, json_schema, effort, max_tokens):
    ids = [int(ln.split("]")[0][1:]) for ln in prompt.splitlines() if ln.startswith("[")]
    return {"clusters": [{"id": i, "name": f"Cluster {i}"} for i in ids]}


def check(label, ok):
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    naming.llm.complete_json = stub_naming  # type: ignore[assignment]

    rng = np.random.default_rng(3)
    emb = rng.normal(size=(N, 32)).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    items = tier_engine.TierItems(
        ids=[f"s-{i}" for i in range(N)],
        texts=[f"skill {i}" for i in range(N)],
        embeddings=emb,
    )
    analysis = tier_engine.analyse(items, k=K, n_perturb=10)
    victims = [i for i in range(N) if analysis.labels[i] == EMPTY_ME]
    print(f"{N} items, k={K}; cluster {EMPTY_ME} holds {len(victims)} of them")
    if not victims:
        print("FAILED — fixture produced no members to move")
        return 1

    async def stub_route_all(pairs, clusters_text, *, entity, concurrency,
                             sc_confidence_threshold, sc_votes, progress=None):
        # Everything in EMPTY_ME is moved out; everything else stays put.
        return {
            idx: routing.RouteResult(
                item_index=idx,
                primary_cluster_id=DESTINATION if idx in victims else int(analysis.labels[idx]),
                primary_confidence=0.8, secondary_cluster_id=None,
                secondary_confidence=None, reasoning="stub", self_consistency=None,
            )
            for idx, _ in pairs
        }

    tier_engine.routing.route_all = stub_route_all  # type: ignore[assignment]

    # Gate of 1.0 routes everything, so the victims are certain to be re-checked.
    result = asyncio.run(
        tier_engine.finalise(items, analysis, entity="skill", tier="profile", gate=1.0)
    )

    sizes = {cid: sum(1 for m in result.members if m.final_cluster_id == cid)
             for cid in range(K)}
    print(f"final sizes: {sizes}")

    ok = True
    ok &= check(f"cluster {EMPTY_ME} is empty after routing", sizes[EMPTY_ME] == 0)
    ok &= check(f"its name is gone ({sorted(result.names)})", EMPTY_ME not in result.names)
    ok &= check(f"k reports surviving clusters, not the requested {K} ({result.k})",
                result.k == len(result.names) == K - 1)
    ok &= check("no surviving cluster is empty",
                all(sizes[cid] > 0 for cid in result.names))
    ok &= check("surviving ids are NOT renumbered — the audit trail still resolves",
                set(result.names) == set(range(K)) - {EMPTY_ME})
    ok &= check("every item still has a home that exists",
                all(m.final_cluster_id in result.names for m in result.members))
    ok &= check("no exemplars left behind for the dropped cluster",
                EMPTY_ME not in result.exemplar_texts)

    # The next tier must not be handed a phantom cluster.
    nxt = tier_engine.items_from_clusters(result, "profile")
    ok &= check(f"the tier above sees {len(nxt)} items, not {K}", len(nxt) == K - 1)
    ok &= check(f"and none of them is the dropped one",
                f"profile:{EMPTY_ME}" not in nxt.ids)

    print("\n" + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
