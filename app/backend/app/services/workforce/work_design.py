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
from app.services.workforce.agents import ABSORPTION_THRESHOLD


def readiness(state: ProjectState, *, graph_built: bool, graph_version: int = 0) -> dict:
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
            # Current, not merely present. A graph built before this studio existed loads
            # without error but carries no agents, no augmentations and no business units, so
            # every lever list would come back empty on a project that has plenty. Reported as
            # "rebuild" rather than left to look like "you have no agents".
            "Work architecture built",
            graph_built and graph_version >= wf.FACTS_VERSION,
            ""
            if graph_built and graph_version >= wf.FACTS_VERSION
            else (
                "rebuild it in Work Architecture Studio — it predates this studio and carries "
                "no levers"
                if graph_built
                else "build it in Work Architecture Studio — seconds, no model calls"
            ),
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
        "graph_version": graph_version,
        "graph_version_required": wf.FACTS_VERSION,
        "has_headcount": bool(wf.profile_headcount(state)),
        "has_business_framework": wf.has_business_framework(state),
        "hours_per_fte_week": state.workforce.hours_per_fte_week,
        "augmentation_uplift": state.work_design.augmentation_uplift,
    }


class Facets:
    """A resolved filter selection. Empty means everything, per the app's convention."""

    __slots__ = (
        "job_family_ids",
        "job_category_ids",
        "task_family_ids",
        "task_category_ids",
        "business_level_1",
        "business_level_2",
        "business_level_3",
    )

    def __init__(
        self,
        *,
        job_family_ids: list[int] | None = None,
        job_category_ids: list[int] | None = None,
        task_family_ids: list[int] | None = None,
        task_category_ids: list[int] | None = None,
        business_level_1: list[str] | None = None,
        business_level_2: list[str] | None = None,
        business_level_3: list[str] | None = None,
    ) -> None:
        self.job_family_ids = set(job_family_ids or [])
        self.job_category_ids = set(job_category_ids or [])
        self.task_family_ids = set(task_family_ids or [])
        self.task_category_ids = set(task_category_ids or [])
        self.business_level_1 = set(business_level_1 or [])
        self.business_level_2 = set(business_level_2 or [])
        self.business_level_3 = set(business_level_3 or [])

    @property
    def has_business(self) -> bool:
        return bool(self.business_level_1 or self.business_level_2 or self.business_level_3)

    def to_json(self) -> dict:
        return {
            "job_family_ids": sorted(self.job_family_ids),
            "job_category_ids": sorted(self.job_category_ids),
            "task_family_ids": sorted(self.task_family_ids),
            "task_category_ids": sorted(self.task_category_ids),
            "business_level_1": sorted(self.business_level_1),
            "business_level_2": sorted(self.business_level_2),
            "business_level_3": sorted(self.business_level_3),
        }


def _job_sample(facts: wf.Facts, f: Facets) -> tuple[dict[int, float], int]:
    """The job profile clusters matching the facets, and how many people each contributes.

    Returns (cluster -> heads, count of profiles whose headcount was only partly included).

    The partial case is the whole reason `business_units` is a cross-tab. A job profile spans
    departments, so filtering by department includes *some* of its people — reporting the
    profile's full headcount would overstate the sample, and excluding the profile entirely
    would understate it. Both are wrong in ways nobody would notice.
    """
    jf = facts.entities.get("job")
    if jf is None:
        return {}, 0

    matched: dict[int, float] = {}
    partial = 0
    # Pre-index the cross-tab so a departmental filter is one pass rather than a scan per job.
    by_cluster: dict[int, list[wf.BusinessUnitFact]] = {}
    for b in facts.business_units:
        by_cluster.setdefault(b.job_cluster, []).append(b)

    for leaf, (cat, fam) in jf.ancestry.items():
        if f.job_family_ids and fam not in f.job_family_ids:
            continue
        if f.job_category_ids and cat not in f.job_category_ids:
            continue

        full = float(jf.metrics.get(leaf, 0.0))
        if not f.has_business:
            matched[leaf] = full
            continue

        rows = by_cluster.get(leaf, [])
        if not rows:
            # A business-framework filter is on and this profile has no framework data at
            # all, so it cannot be said to be inside or outside the selection. Excluded,
            # and counted in the sample's `unmapped_profiles` so the omission is visible.
            continue
        share = sum(
            b.headcount
            for b in rows
            if (not f.business_level_1 or b.level_1 in f.business_level_1)
            and (not f.business_level_2 or b.level_2 in f.business_level_2)
            and (not f.business_level_3 or b.level_3 in f.business_level_3)
        )
        if share <= 0:
            continue
        total = sum(b.headcount for b in rows) or 1
        if share < total:
            partial += 1
        # Scaled to the metric the graph uses, so a project with no headcount column still
        # gets a sensible fraction of its notional one-per-job measure.
        matched[leaf] = full * (share / total) if total else full
    return matched, partial


def _task_in_scope(facts: wf.Facts, f: Facets) -> set[int]:
    """Task clusters surviving the task facets."""
    tf = facts.entities.get("task")
    if tf is None:
        return set()
    if not (f.task_family_ids or f.task_category_ids):
        return set(tf.ancestry)
    out = set()
    for leaf, (cat, fam) in tf.ancestry.items():
        if f.task_family_ids and fam not in f.task_family_ids:
            continue
        if f.task_category_ids and cat not in f.task_category_ids:
            continue
        out.add(leaf)
    return out


def pool(facts: wf.Facts, f: Facets, *, hours_per_fte_week: float) -> dict:
    """The as-is work of the filtered sample, per task cluster, in hours per week.

    Headcount-weighted, not an unweighted mean of proportions. Three reasons, in order of
    force: the middle panel's capacity is in hours, so mixing units makes a drag
    meaningless; an unweighted mean ranks a cluster taking 60% of two people's week above
    one taking 5% of four hundred people's, which is the same argument `_agent_inputs`
    makes for ranking by absorbable rather than by automation_pct; and `graph.build`
    already weights task metrics by headcount, so a third differently-weighted view of one
    quantity would be the defect.

    Degrades exactly as the rest of the app does. With no headcount column the job metric is
    already a count of distinct jobs, so the arithmetic is byte-identical and only the unit
    label changes — never both units, never a synthesised headcount.
    """
    heads, partial = _job_sample(facts, f)
    scope = _task_in_scope(facts, f)
    tf = facts.entities.get("task")
    hpw = hours_per_fte_week

    # cluster -> hours, and the roles behind it so a line can later be carved along a real
    # boundary in the data rather than by an arbitrary percentage.
    hours: dict[int, float] = {}
    roles: dict[int, dict[int, float]] = {}
    assigned_in_scope = 0.0
    for jp, tc, share_pct in facts.job_task:
        h = heads.get(jp)
        if h is None:
            continue
        contribution = (share_pct / 100.0) * h * hpw
        assigned_in_scope += contribution
        if tc not in scope:
            continue
        hours[tc] = hours.get(tc, 0.0) + contribution
        roles.setdefault(tc, {})[jp] = roles.setdefault(tc, {}).get(jp, 0.0) + contribution

    sample_heads = sum(heads.values())
    shown = sum(hours.values())
    clusters = []
    for tc, h in sorted(hours.items(), key=lambda kv: -kv[1]):
        cat, fam = (tf.ancestry.get(tc, (-1, -1)) if tf else (-1, -1))
        opp = facts.task_opportunity.get(tc)
        clusters.append(
            {
                "cluster_id": tc,
                "name": wf.label_of(tf, "profile", tc) if tf else str(tc),
                "category_id": cat,
                "category": wf.label_of(tf, "category", cat) if tf else "",
                "domain_id": fam,
                "domain": wf.label_of(tf, "family", fam) if tf else "",
                "hours_per_week": round(h, 2),
                "fte": round(h / hpw, 2) if hpw else None,
                "share_pct": round(100.0 * h / shown, 2) if shown else 0.0,
                # The rate a drop uses: one typical holder's time on this work. Supplied here
                # rather than derived client-side so the two panels cannot end up in
                # different units — the single most likely place for that to happen.
                "hours_per_holder_week": round(h / sample_heads, 3) if sample_heads else 0.0,
                # None, never 0. An unassessed cluster has unknown opportunity, and the
                # difference matters the moment anything colours a treemap by it.
                "assessed": opp is not None,
                "automation": opp[0] if opp else None,
                "augmentation": opp[1] if opp else None,
                "n_roles": len(roles.get(tc, {})),
                "roles": [
                    {
                        "job_cluster": jp,
                        "profile_key": facts.job_profile_keys.get(jp, ("", ""))[0],
                        "title": facts.job_profile_keys.get(jp, ("", ""))[1],
                        "hours_per_week": round(v, 2),
                    }
                    for jp, v in sorted(roles.get(tc, {}).items(), key=lambda kv: -kv[1])
                ],
            }
        )

    unassessed_hours = sum(c["hours_per_week"] for c in clusters if not c["assessed"])
    n_unassessed = sum(1 for c in clusters if not c["assessed"])
    warnings: list[str] = []
    if n_unassessed:
        warnings.append(
            f"{n_unassessed} of {len(clusters)} task clusters in this sample have not been "
            f"assessed ({unassessed_hours:,.0f} hours a week). Their hours are shown, but no "
            f"agent or augmentation can act on them."
        )
    if partial:
        warnings.append(
            f"{partial} job profiles contributed only part of their headcount, because the "
            f"business-framework filter matched some of their records and not others."
        )
    # Task facets remove part of every job's week, so the shown total stops being the
    # sample's whole week. Said out loud, because a 12% cluster would otherwise look like 30%.
    if shown < assigned_in_scope - 0.5:
        warnings.append(
            f"Showing {100.0 * shown / assigned_in_scope:.0f}% of the sample's week — the task "
            f"filter excludes the rest."
        )

    return {
        "facets": f.to_json(),
        "unit": "FTE" if facts.has_headcount else "role-weeks",
        "has_headcount": facts.has_headcount,
        "hours_per_fte_week": hpw,
        "basis": (
            "Headcount from the HRIS"
            if facts.has_headcount
            else "No headcount column was mapped, so each distinct job profile counts as one "
            "notional holder"
        ),
        "sample": {
            "job_profiles": len(heads),
            "headcount": round(sample_heads, 2),
            "capacity_hours_per_week": round(sample_heads * hpw, 2),
            "shown_hours_per_week": round(shown, 2),
            "sample_hours_per_week": round(assigned_in_scope, 2),
            "shown_pct_of_week": round(100.0 * shown / assigned_in_scope, 1) if assigned_in_scope else 0.0,
            "task_clusters": len(clusters),
            "assessed_clusters": len(clusters) - n_unassessed,
            "unassessed_hours_per_week": round(unassessed_hours, 2),
            "partial_profiles": partial,
        },
        "clusters": clusters,
        "warnings": warnings,
    }


def _residual_augmentation(actions: list[wf.ActionFact], absorbed: set[str]) -> tuple[float, float]:
    """Augmentation of the hours that survive automation, and the share that survives.

    "Augmentation applies to the remaining time" is right but not sufficient, because *which*
    hours remain is not neutral. A cluster's stored `augmentation_pct` is the effort-weighted
    mean over all its actions. The actions an agent absorbs are the high-automation ones —
    drafting, summarising, transferring data — and those are precisely the actions that also
    score highest on augmentation. So the hours that survive absorption are systematically
    *less* augmentable than the cluster average, and applying the whole-cluster percentage to
    them overstates the saving on exactly the clusters where both levers are pulled.

    Two properties make this safe to introduce, and both are asserted in the offline test:

      - With nothing absorbed it reduces *exactly* to the cluster's stored augmentation_pct,
        because the expression becomes the same effort-weighted mean. So the correction is
        invisible when there is no collision and self-corrects when there is.
      - The surviving share can now reach zero, since the automation ceiling was removed. A
        fully automated cluster has no hours left to augment, so the guard returns 0 rather
        than dividing by it.
    """
    surviving = 0.0
    weighted = 0.0
    for a in actions:
        share = a.pct_of_task / 100.0
        if a.name in absorbed:
            share *= 1.0 - a.automation_pct / 100.0
        surviving += share
        weighted += share * a.augmentation_pct
    if surviving <= 0:
        return 0.0, 0.0
    return weighted / surviving, surviving


def apply_levers(
    facts: wf.Facts,
    pool_result: dict,
    *,
    agent_ids: list[str],
    skill_ids: list[str],
    uplift: float,
) -> dict:
    """Apply automation and augmentation to a pool, in that order.

    The order is not arbitrary. If an agent absorbs an action, the human does not perform it,
    so there is no human speed left to improve — augmenting absorbed work augments nobody.
    Reversing it would also compute the agent's saving against an already-shrunk base,
    understating automation, and automation is the number quoted against that agent
    everywhere else in the app. One agent must not release different amounts on different
    screens.

    The two effects stay separate fields and are never summed into one "time saved". They are
    different claims — one is work that has gone, the other is the same work done faster — and
    a single figure would let a deck say "we removed 900 hours" about hours still being worked.
    """
    by_cluster: dict[int, list[wf.ActionFact]] = {}
    for a in facts.actions:
        by_cluster.setdefault(a.task_cluster, []).append(a)

    agents = {a.agent_id: a for a in facts.agents if a.agent_id in set(agent_ids)}
    skills = {s.skill_id: s for s in facts.augmentations if s.skill_id in set(skill_ids)}
    in_pool = {c["cluster_id"] for c in pool_result["clusters"]}
    hpw = pool_result["hours_per_fte_week"]

    # Silently ignoring a checked lever whose cluster is outside the filter is the same class
    # of quiet lie as scoring unassessed time as zero.
    skipped_agents = [
        {"id": a.agent_id, "name": a.name, "reason": "its task cluster is not in this sample"}
        for a in agents.values()
        if a.task_cluster not in in_pool
    ]
    skipped_skills = [
        {"id": s.skill_id, "name": s.name, "reason": "its task cluster is not in this sample"}
        for s in skills.values()
        if s.task_cluster not in in_pool
    ]

    agents_by_cluster: dict[int, list[wf.AgentFact]] = {}
    for a in agents.values():
        if a.task_cluster in in_pool:
            agents_by_cluster.setdefault(a.task_cluster, []).append(a)
    skills_by_cluster: dict[int, list[wf.AugmentationFact]] = {}
    for s in skills.values():
        if s.task_cluster in in_pool:
            skills_by_cluster.setdefault(s.task_cluster, []).append(s)

    clusters: list[dict] = []
    added: list[dict] = []
    agent_rows: dict[str, dict] = {}
    tot_removed = tot_oversight = tot_freed = 0.0

    for c in pool_result["clusters"]:
        cid = c["cluster_id"]
        h = c["hours_per_week"]
        actions = by_cluster.get(cid, [])
        cluster_agents = agents_by_cluster.get(cid, [])
        cluster_skills = skills_by_cluster.get(cid, [])

        # A UNION over agents, never a sum: two agents on one cluster absorb the same
        # actions, and summing would remove the same hours twice.
        absorbed = {
            a.name for a in actions if a.automation_pct >= ABSORPTION_THRESHOLD
        } if cluster_agents else set()
        removed = (
            h
            * sum(
                (a.pct_of_task / 100.0) * (a.automation_pct / 100.0)
                for a in actions
                if a.name in absorbed
            )
            if absorbed
            else 0.0
        )
        # The automatable part of the work the agent was explicitly told not to attempt. The
        # fastest answer to "why isn't this bigger?", so it is surfaced rather than implied.
        retained_automatable = h * sum(
            (a.pct_of_task / 100.0) * (a.automation_pct / 100.0)
            for a in actions
            if a.name not in absorbed
        )
        after_auto = max(0.0, h - removed)

        residual_aug, _ = _residual_augmentation(actions, absorbed)
        # Scoped to the role each skill was written for, not to the whole cluster. A skill
        # written for one role does not speed up the other thirteen, and applying it as if it
        # did would overstate the saving on a 565-role project by an order of magnitude.
        role_hours = {r["job_cluster"]: r["hours_per_week"] for r in c["roles"]}
        key_to_cluster = {v[0]: k for k, v in facts.job_profile_keys.items()}
        freed = 0.0
        augmented_roles: set[int] = set()
        for s in cluster_skills:
            jp = key_to_cluster.get(s.profile_key)
            if jp is None or jp in augmented_roles:
                continue
            base = role_hours.get(jp, 0.0) * (after_auto / h if h else 0.0)
            freed += base * (residual_aug / 100.0) * uplift
            augmented_roles.add(jp)
        freed = min(freed, after_auto)

        to_be = max(0.0, after_auto - freed)
        tot_removed += removed
        tot_freed += freed

        for a in cluster_agents:
            share = removed / len(cluster_agents) if cluster_agents else 0.0
            oversight = share * a.oversight_fraction
            tot_oversight += oversight
            agent_rows[a.agent_id] = {
                "id": a.agent_id,
                "name": a.name,
                "cluster_id": cid,
                "cluster": c["name"],
                "automation": a.automation_pct,
                "human_in_the_loop": a.human_in_the_loop,
                "removed_hours_per_week": round(share, 2),
                "oversight_hours_per_week": round(oversight, 2),
                "oversight_fraction": a.oversight_fraction,
                "oversight_source": a.oversight_source,
                "net_hours_per_week": round(oversight - share, 2),
            }
            # One line per oversight task where the specification named them, so the design
            # carries real work rather than a single generic "oversee the agent".
            tasks = a.oversight_tasks or [
                (f"Overseeing {a.name}", f"Checking and correcting what {a.name} produces.", 100.0)
            ]
            denom = sum(t[2] for t in tasks) or 100.0
            for name, definition, pct in tasks:
                added.append(
                    {
                        "id": f"oversight:{a.agent_id}:{name}",
                        "name": name,
                        "description": definition,
                        "origin": "agent_oversight",
                        "agent_id": a.agent_id,
                        "task_cluster_id": cid,
                        "cluster_name": c["name"],
                        "hours_per_week": round(oversight * (pct / denom), 2),
                        "basis": (
                            "from the agent's specification"
                            if a.oversight_source == "specification"
                            else "house assumption — this specification predates oversight tasks"
                        ),
                    }
                )

        clusters.append(
            {
                **c,
                "as_is_hours_per_week": round(h, 2),
                "removed_by_automation_hours_per_week": round(removed, 2),
                "freed_by_augmentation_hours_per_week": round(freed, 2),
                "to_be_hours_per_week": round(to_be, 2),
                "retained_automatable_hours_per_week": round(retained_automatable, 2),
                "residual_augmentation_pct": round(residual_aug, 1),
                "absorbed_by": [a.agent_id for a in cluster_agents],
                "augmented_by": [s.skill_id for s in cluster_skills],
                "augmentation_coverage_pct": (
                    round(100.0 * len(augmented_roles) / len(role_hours), 1) if role_hours else 0.0
                ),
                "roles_augmented": len(augmented_roles),
            }
        )

    as_is = pool_result["sample"]["shown_hours_per_week"]
    to_be_total = sum(c["to_be_hours_per_week"] for c in clusters) + sum(
        a["hours_per_week"] for a in added
    )
    return {
        "unit": pool_result["unit"],
        "has_headcount": pool_result["has_headcount"],
        "hours_per_fte_week": hpw,
        "threshold": ABSORPTION_THRESHOLD,
        "uplift": uplift,
        "agents": list(agent_rows.values()),
        "augmentations": [
            {
                "id": s.skill_id,
                "name": s.name,
                "role_title": s.role_title,
                "profile_key": s.profile_key,
                "cluster_id": s.task_cluster,
                "rank_score": s.rank_score,
            }
            for s in skills.values()
            if s.task_cluster in in_pool
        ],
        "clusters": clusters,
        "added": added,
        "totals": {
            "as_is_hours_per_week": round(as_is, 2),
            "removed_by_automation_hours_per_week": round(tot_removed, 2),
            "freed_by_augmentation_hours_per_week": round(tot_freed, 2),
            "oversight_hours_per_week": round(tot_oversight, 2),
            "to_be_hours_per_week": round(to_be_total, 2),
            "net_change_hours_per_week": round(to_be_total - as_is, 2),
            "net_change_pct": round(100.0 * (to_be_total - as_is) / as_is, 1) if as_is else 0.0,
            "net_fte": round((as_is - to_be_total) / hpw, 2) if hpw else None,
        },
        "skipped_agents": skipped_agents,
        "skipped_augmentations": skipped_skills,
        "warnings": list(pool_result.get("warnings", []))
        + (
            [
                f"{len(skipped_agents) + len(skipped_skills)} selected levers target work "
                f"outside this filter and were ignored."
            ]
            if skipped_agents or skipped_skills
            else []
        ),
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
