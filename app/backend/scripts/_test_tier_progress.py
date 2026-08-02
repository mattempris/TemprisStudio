"""finalise() must report naming and routing as two separate, visible phases.

The user-visible symptom this covers: "Confirm and name appears to hang with no
progress". Naming a 153-cluster level is minutes of sequential calls, and the bar
was sized to the routed count, so it sat at zero for all of it.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.clustering import naming, routing, tier as tier_engine  # noqa: E402

N_ITEMS, K = 120, 40


def stub_naming(prompt, *, system, json_schema, effort, max_tokens):
    ids = [int(ln.split("]")[0][1:]) for ln in prompt.splitlines() if ln.startswith("[")]
    return {"clusters": [{"id": i, "name": f"Cluster {i}"} for i in ids]}


async def stub_route_all(items, clusters_text, *, entity, concurrency,
                         sc_confidence_threshold, sc_votes, progress=None):
    out = {}
    for n, (idx, _text) in enumerate(items, start=1):
        out[idx] = routing.RouteResult(
            item_index=idx, primary_cluster_id=0, primary_confidence=0.8,
            secondary_cluster_id=None, secondary_confidence=None,
            reasoning="stub", self_consistency=None,
        )
        if progress:
            progress(n, len(items), "routed")
    return out


def check(label, ok):
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    naming.llm.complete_json = stub_naming            # type: ignore[assignment]
    routing.route_all = stub_route_all                # type: ignore[assignment]
    tier_engine.routing.route_all = stub_route_all    # type: ignore[assignment]

    rng = np.random.default_rng(0)
    emb = rng.normal(size=(N_ITEMS, 64)).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    items = tier_engine.TierItems(
        ids=[f"job-{i}" for i in range(N_ITEMS)],
        texts=[f"role number {i} doing work" for i in range(N_ITEMS)],
        embeddings=emb,
    )
    analysis = tier_engine.analyse(items, k=K, n_perturb=10)

    events: list[tuple[str, object]] = []
    gate = 0.58
    result = asyncio.run(
        tier_engine.finalise(
            items, analysis, entity="job", tier="profile", gate=gate,
            naming_progress=lambda d, t: events.append(("named", (d, t))),
            on_phase=lambda label, total: events.append(("phase", (label, total))),
            progress=lambda d, t, lbl: events.append(("routed", (d, t))),
        )
    )

    named = [e for e in events if e[0] == "named"]
    phases = [e for e in events if e[0] == "phase"]
    routed = [e for e in events if e[0] == "routed"]
    n_unstable = analysis.routed_count(gate)

    print(f"{N_ITEMS} items, k={K}, {n_unstable} below gate {gate}")
    print(f"events: {len(named)} naming, {len(phases)} phase, {len(routed)} routing")

    ok = True
    ok &= check(f"naming reported {len(named)} times (expected {-(-K // naming.NAME_BATCH)})",
                len(named) == -(-K // naming.NAME_BATCH))
    ok &= check(f"naming ends at {K}/{K}", named[-1][1] == (K, K))
    ok &= check("exactly one phase switch", len(phases) == 1)
    ok &= check(f"phase fires AFTER naming, BEFORE routing",
                events.index(phases[0]) > events.index(named[-1])
                and (not routed or events.index(phases[0]) < events.index(routed[0])))
    ok &= check(f"phase label/total {phases[0][1]}",
                isinstance(phases[0][1], tuple) and phases[0][1][1] == n_unstable)
    ok &= check(f"routing reported {len(routed)} times for {n_unstable} items",
                len(routed) == n_unstable)
    # This stub routes every re-checked item into cluster 0, which empties the
    # clusters they came from — and an emptied cluster loses its name by design.
    occupied = {m.final_cluster_id for m in result.members}
    ok &= check(f"names match surviving clusters ({len(result.names)} of {K})",
                set(result.names) == occupied)
    ok &= check("no named cluster is empty", all(c in occupied for c in result.names))
    ok &= check(f"n_routed recorded ({result.n_routed})", result.n_routed == n_unstable)

    print("\n" + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
