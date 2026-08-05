"""Work Design Studio — composing new job definitions out of the work architecture.

The first two studios *describe* work: what jobs exist, how they cluster, what tasks they
contain, and how much of each task AI could absorb or accelerate. This one changes it. A user
filters the workforce to a slice, applies AI levers to free up time, and allocates what is
left into new job definitions.

**It is an allocation exercise, not a browser, and that decides the whole design.** The task
pool is a *budget* of work to be re-allocated, not a description of what people do today. So
it drains: every hour is either absorbed by a lever or assigned to a job definition, and
"finished" means the pool is empty. The invariant that makes it trustworthy, and the first
thing to assert when a number looks wrong:

    as_is_total == absorbed_by_agents + saved_by_augmentations
                   + allocated_to_jobs + unreviewed_remaining

**One currency: absolute hours per week.** The pool aggregates over possibly hundreds of
people while a designed job's capacity is `headcount x hours_per_fte_week`. A percentage of one
FTE-week cannot express a 5-FTE job against a 400-FTE sample without a hidden second scaling,
so every quantity here is `hours_per_week` and `fte` is a derived display field.

**Everything is computed from `wf.Facts`, never from the state blob.** The facets and the lever
list are controls that fire on every interaction, and `load_state` deep-copies a 42.5 MB
Pydantic tree on every call. `Facts` already carries ancestry, headcount, role-by-cluster time,
the assessment and — since this studio — the agents, augmentations and business units too.
"""
from __future__ import annotations

from app.models.project_state import ProjectState
from app.services.workforce import graph as wf


def readiness(state: ProjectState, *, graph_built: bool) -> dict:
    """What this studio actually reads, and nothing else.

    Deliberately does **not** check that the opportunity assessment has run, even though
    every number here depends on it. That check would be redundant: `_agent_inputs` and
    `_skill_inputs` both return nothing without `state.workforce.opportunity`, so neither an
    agent nor an augmentation can exist without it. Testing a condition and its consequence
    separately is how a gate ends up disagreeing with itself.

    Nor is there a coverage threshold. Assessing the forty biggest clusters, building a few
    agents and designing against them is a legitimate way to work, and a "95% assessed" gate
    would lock it out. Coverage is reported instead — per sample, where it can say which hours
    are unassessed rather than only refusing entry.
    """
    c = state.tasks.clustering
    n_clusters = len(c.profile_names) if c else 0
    n_inferred = len(state.tasks.inferred)
    n_agents = len(state.workforce.agents)
    n_augs = len(state.workforce.skills_guidance)
    assessed = {
        o.task_cluster_id
        for o in state.workforce.opportunity
        if not c or o.task_cluster_id in c.profile_names
    }

    checks: list[tuple[str, bool, str]] = [
        (
            "Task taxonomy",
            bool(n_clusters and n_inferred),
            f"{n_clusters} task clusters, {n_inferred:,} inferred tasks",
        ),
        (
            "Work architecture built",
            graph_built,
            "" if graph_built else "build it in Work Architecture Studio — seconds, no model calls",
        ),
        (
            "An agent or augmentation to apply",
            n_agents + n_augs > 0,
            f"{n_agents} agents, {n_augs} augmentations",
        ),
    ]
    missing = [name for name, ok, _ in checks if not ok]
    return {
        "ready": not missing,
        "missing": missing,
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
        # Advisory, never gating.
        "task_clusters": n_clusters,
        "clusters_assessed": len(assessed),
        "coverage_pct": round(100.0 * len(assessed) / n_clusters, 1) if n_clusters else 0.0,
        "agents": n_agents,
        "augmentations": n_augs,
        "designed_jobs": len(state.work_design.jobs),
        "has_headcount": bool(wf.profile_headcount(state)),
        "has_business_framework": wf.has_business_framework(state),
        "hours_per_fte_week": state.workforce.hours_per_fte_week,
        "augmentation_uplift": state.work_design.augmentation_uplift,
    }


def facet_options(facts: wf.Facts) -> dict:
    """The filter options, with a count of matching job profiles against each.

    Not `GET /workforce/graph/filters`: that counts *leaves* beneath a family, which is the
    wrong denominator here — this studio filters a sample of job profiles, and it has a
    business-framework axis the graph knows nothing about.

    Counts are computed with each group's own selection ignored when the caller applies one,
    which is standard faceted-search behaviour. Doing otherwise zeroes an option's siblings
    the moment one is picked, and the control becomes a dead end.
    """
    jf = facts.entities.get("job")
    tf = facts.entities.get("task")
    out: dict = {
        "level_titles": wf.LEVEL_TITLES,
        "has_business_framework": facts.has_business_framework,
        "has_headcount": facts.has_headcount,
    }

    def groups(ef: wf.EntityFacts | None, entity: str) -> dict:
        if ef is None:
            return {"family": [], "category": []}
        fam: dict[int, int] = {}
        cat: dict[int, int] = {}
        parent: dict[int, int] = {}
        for leaf, (c, f) in ef.ancestry.items():
            fam[f] = fam.get(f, 0) + 1
            cat[c] = cat.get(c, 0) + 1
            parent[c] = f
        return {
            "family": [
                {"id": i, "name": wf.label_of(ef, "family", i), "leaves": n}
                for i, n in sorted(fam.items(), key=lambda kv: -kv[1])
            ],
            "category": [
                {
                    "id": i,
                    "name": wf.label_of(ef, "category", i),
                    "leaves": n,
                    "family": parent.get(i),
                }
                for i, n in sorted(cat.items(), key=lambda kv: -kv[1])
            ],
        }

    out["job"] = groups(jf, "job")
    out["task"] = groups(tf, "task")

    # Business framework: values rather than ids, and each level carries its parent so the
    # narrower dropdowns can follow the broader one.
    if facts.has_business_framework:
        l1: dict[str, int] = {}
        l2: dict[tuple[str, str], int] = {}
        l3: dict[tuple[str, str, str], int] = {}
        for b in facts.business_units:
            if b.level_1:
                l1[b.level_1] = l1.get(b.level_1, 0) + b.headcount
            if b.level_2:
                l2[(b.level_1, b.level_2)] = l2.get((b.level_1, b.level_2), 0) + b.headcount
            if b.level_3:
                key = (b.level_1, b.level_2, b.level_3)
                l3[key] = l3.get(key, 0) + b.headcount
        out["business_framework"] = {
            "level_1": [
                {"value": v, "headcount": n} for v, n in sorted(l1.items(), key=lambda kv: -kv[1])
            ],
            "level_2": [
                {"value": v, "parent": p, "headcount": n}
                for (p, v), n in sorted(l2.items(), key=lambda kv: -kv[1])
            ],
            "level_3": [
                {"value": v, "parent": p2, "grandparent": p1, "headcount": n}
                for (p1, p2, v), n in sorted(l3.items(), key=lambda kv: -kv[1])
            ],
        }
    return out
