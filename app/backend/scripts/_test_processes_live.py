"""Steps 2, 4 and 7 live, against the HR process fixtures and the real taxonomy.

The offline test proves the routing arithmetic. This proves the things it cannot: that a
real process diagram yields recognisable steps, that those steps land in sensible task
clusters, and that a role redesign reads like the role rather than like a template.

Runs the services directly rather than through HTTP, so it does not need a server and
does not write to project state.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import embeddings as emb  # noqa: E402
from app.services.clustering import tier_state  # noqa: E402
from app.services.ingestion import parsers  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.workforce import future_roles as fr  # noqa: E402
from app.services.workforce import processes as proc  # noqa: E402

CLIENT, PROJECT = "banking-demo", "full-ja"
FIXTURE = Path(__file__).parent / "fixtures" / "processes" / "offer-to-hire-as-is.html"


def main() -> int:
    svc = ProjectService()
    state = svc.load_state(CLIENT, PROJECT)
    c = state.tasks.clustering
    if c is None:
        print("no task taxonomy on this project")
        return 1

    print(f"=== Step 2: {FIXTURE.name} ===")
    text = parsers.extract_text(FIXTURE.name, FIXTURE.read_bytes())
    print(f"parsed {len(text.split())} words")
    process = proc.infer_process(text, filename=FIXTURE.name)
    print(f"\n{process.process_name}  (ordering confidence: {process.ordering_confidence})")
    print(f"{process.summary}\n")
    print(f"{len(process.steps)} steps · {process.manual_steps} manual · "
          f"{len(process.actors)} actors · "
          f"{sum(1 for s in process.steps if s.sign_off)} sign-offs · "
          f"{sum(1 for s in process.steps if s.handoff)} handoffs")
    print(f"actors: {', '.join(process.actors)}\n")

    print("=== Step 2: mapping onto the task taxonomy ===")
    spec = tier_state.spec("task")
    matrix = svc.load_array(CLIENT, f"{PROJECT}/artifacts/{spec.array_name}.npy")
    ids = svc.load_index(CLIENT, f"{PROJECT}/artifacts/{spec.array_name}_index.json")
    if matrix is None or ids is None:
        print("task embeddings are not cached — cannot map")
        return 1
    candidates = proc.cluster_centroids(
        matrix, ids, {a.item_id: a.final_profile_id for a in c.assignments}, dict(c.profile_names)
    )
    print(f"{len(candidates)} cluster centroids from {matrix.shape[0]} cached task vectors")

    service = emb.get_embedding_service()
    vectors = service.embed_documents("task", [s.embedding_text() for s in process.steps])
    matches = proc.match_steps(process.steps, vectors, candidates, confirm=proc.confirm_match)

    by_seq = {m.sequence: m for m in matches}
    for s in process.steps:
        m = by_seq[s.sequence]
        how = (
            "geometry" if not m.routed_by_llm and m.matched
            else "model" if m.routed_by_llm else "NO MATCH"
        )
        target = m.cluster_name or "—"
        print(f"  {s.sequence:>2}. {s.name[:38]:38s} -> {target[:34]:34s} "
              f"cos {m.cosine:.2f} [{how}]")
        if not m.matched:
            print(f"        {m.reasoning}")

    matched = sum(1 for m in matches if m.matched)
    routed = sum(1 for m in matches if m.routed_by_llm)
    print(f"\n{matched}/{len(matches)} matched · {routed} needed the model · "
          f"{len(matches) - matched} are work the job descriptions never mentioned")

    print("\n=== Step 4: as-is / to-be ===")
    scores = {
        o.task_cluster_id: (o.automation_pct, o.augmentation_pct)
        for o in state.workforce.opportunity
    }
    a = proc.assess_process(process, matches, scores)
    print(f"steps              {len(process.steps)} -> {a.to_be_steps}")
    print(f"manual touchpoints {process.manual_steps} -> {a.to_be_manual_touchpoints}")
    print(f"actors             {len(process.actors)} -> {a.to_be_actors}")
    print(f"sign-offs          {sum(1 for s in process.steps if s.sign_off)} -> {a.to_be_sign_offs}")
    print(f"effort  -{a.effort_reduction_pct:.0f}%   elapsed  -{a.elapsed_reduction_pct:.0f}%")
    print(f"\nas-is:  {a.as_is_narrative}")
    print(f"\nto-be:  {a.to_be_narrative}")
    print("\nwhat changes:")
    for x in a.what_changes:
        print(f"  - {x}")
    print("risks:")
    for x in a.risks:
        print(f"  - {x}")
    print("prerequisites:")
    for x in a.prerequisites:
        print(f"  - {x}")

    print("\n=== Step 7: future role design ===")
    if not scores:
        print("no opportunity assessment — skipping")
        return 0
    cluster_of = {x.item_id: x.final_profile_id for x in c.assignments}
    profile = {d.profile_key: d for d in state.job_profiles}
    grouped: dict[str, dict[int, float]] = {}
    for t in state.tasks.inferred:
        cid = cluster_of.get(t.id)
        if cid is None or cid not in scores:
            continue
        g = grouped.setdefault(t.source_profile_key, {})
        g[cid] = g.get(cid, 0.0) + t.proportion
    if not grouped:
        print("no role has an assessed task — skipping")
        return 0
    key = max(grouped, key=lambda k: sum(grouped[k].values()))
    doc = profile.get(key)
    tasks = [
        (c.profile_names.get(cid, str(cid)), prop, scores[cid][0], scores[cid][1])
        for cid, prop in grouped[key].items()
    ]
    covered = sum(p for _n, p, _a, _g in tasks)
    inp = fr.FutureRoleInput(
        profile_key=key,
        title=doc.title if doc else key,
        purpose=str((doc.content or {}).get("about_role", ""))[:600] if doc else "",
        automation_pct=round(sum(p * a for _n, p, a, _g in tasks) / covered, 1),
        augmentation_pct=round(sum(p * g for _n, p, _a, g in tasks) / covered, 1),
        tasks=tasks,
    )
    role = fr.design_role(inp)
    print(f"\n{role.title} — {role.automation_pct:.0f}% automatable, "
          f"{role.time_released_pct:.0f}% of the week released")
    print(f"\nToday:      {role.evolution_today}")
    print(f"\nFirst:      {role.evolution_after_automation}")
    print(f"\nBecomes:    {role.evolution_future}")
    print(f"\nPurpose:    {role.future_purpose}")
    for label, items in (
        ("Future responsibilities", role.future_responsibilities),
        ("Absorbed (computed)", role.absorbed_tasks),
        ("Deepened", role.deepened_tasks),
        ("Skills to build", role.skills_to_build),
        ("Deliberate practice", role.deliberate_practice),
    ):
        print(f"\n{label}:")
        for x in items:
            print(f"  - {x}")

    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
