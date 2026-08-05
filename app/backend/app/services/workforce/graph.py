"""The work architecture graph — Work Architecture Studio step 1.

One asset unifying the three hierarchies Job Architecture Studio produces: job profiles, skill
clusters and task clusters, with the relationships between them. Later steps add
process, agent and action nodes to the same structure.

**Why a fact table and not a graph.** A real project is large: the reference build
has 565 job profiles, 550 skill clusters and 750 task clusters, which is over 1,800
nodes and several thousand edges before anything is inferred. A force-directed layout
stops being readable somewhere in the low hundreds, and shipping the whole thing to a
browser on every view is wasteful when almost none of it is being looked at.

So the expensive, deterministic part happens once: every leaf-level relationship is
computed with its weight and persisted. A *view* is then a roll-up of that table to
whatever resolution is being asked for — job families against skill families, say —
which is a dictionary aggregation over ~15,000 rows, in single-digit milliseconds, and
returns a few hundred nodes instead of two thousand. Aggregating is not a compromise
for scale; it is cheaper than the alternative at every size.

Edge weight aggregates by summing, which is what makes a thick edge between
"Technology" and "Engineering Skills" mean something rather than being decoration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models.project_state import ProjectState

# Coarse to fine. The internal tier names are shared across all three hierarchies;
# what each level is *called* differs per entity and is carried in `LEVEL_TITLES`.
LEVELS: tuple[str, str, str] = ("family", "category", "profile")

ENTITIES: tuple[str, str, str] = ("job", "skill", "task")

# Actions are not a fourth hierarchy — they hang off a single task cluster and have
# no levels of their own. They appear only when their cluster is expanded at the
# finest resolution, which is where "what within this work is automatable" is a
# question worth drawing. Kept out of ENTITIES so the roll-up code never has to
# special-case a hierarchy that has one level.
ACTION_ENTITY = "action"
# Processes are not a hierarchy either: one node per uploaded document, plus one per step
# that matched no task cluster.
PROCESS_ENTITY = "process"
UNMAPPED_ENTITY = "unmapped"
GRAPH_ENTITIES: tuple[str, ...] = ENTITIES + (ACTION_ENTITY, PROCESS_ENTITY, UNMAPPED_ENTITY)

LEVEL_TITLES: dict[str, dict[str, str]] = {
    "job": {"family": "Job family", "category": "Job category", "profile": "Job profile"},
    "skill": {"family": "Skill family", "category": "Skill category", "profile": "Skill cluster"},
    "task": {"family": "Task domain", "category": "Task category", "profile": "Task cluster"},
}

# What a node's size means, per entity. Job profiles are people; skill clusters are
# skills; task clusters are the time those tasks consume, which is the only one of the
# three that is a rate rather than a count.
#
# Headcount is optional — an HRIS import without that column leaves it empty — so the
# titles come in pairs and the second is used when it is absent. Sizing by number of
# distinct jobs instead of people is a different statement, and labelling it "headcount"
# either way would be a quiet lie.
METRIC_TITLES: dict[str, str] = {
    "job": "headcount",
    "skill": "skills",
    "task": "FTE",
}
METRIC_TITLES_NO_HEADCOUNT: dict[str, str] = {
    "job": "jobs",
    "skill": "skills",
    "task": "role-shares",
}


# What a cluster with no parent is called. An id of -1 means the tier above never
# assigned this cluster — it happens when a cluster was left unnamed at the time its
# parent tier was confirmed, and the members are real work that has to stay visible.
# Rendering it as the bare string "-1" made it look like a rendering fault; naming it
# says what it is and points at the fix.
UNPARENTED = "(no parent assigned)"


def label_of(ef: "EntityFacts", level: str, cluster_id: int) -> str:
    name = ef.labels.get(level, {}).get(cluster_id)
    if name:
        return name
    return UNPARENTED if cluster_id < 0 else f"{level} {cluster_id}"


class GraphNotReady(RuntimeError):
    """The graph needs all three hierarchies confirmed and profiles generated."""


@dataclass
class EntityFacts:
    """One hierarchy, flattened.

    `ancestry` maps a leaf cluster id to its (category, family). `metrics` is the
    per-leaf quantity that node size is drawn from, summed on roll-up.
    """

    labels: dict[str, dict[int, str]] = field(default_factory=dict)  # level -> id -> name
    ancestry: dict[int, tuple[int, int]] = field(default_factory=dict)  # leaf -> (cat, fam)
    metrics: dict[int, float] = field(default_factory=dict)  # leaf -> size quantity
    members: dict[int, int] = field(default_factory=dict)  # leaf -> underlying record count


@dataclass
class ActionFact:
    """One action of one task cluster, with both opportunity scores."""

    task_cluster: int
    name: str
    definition: str
    pct_of_task: float
    automation_pct: float
    augmentation_pct: float


@dataclass
class ProcessFact:
    """One uploaded process, and how its steps landed on the task taxonomy."""

    process_id: str
    name: str
    steps: int
    mapped_steps: int
    ordering_confidence: str
    # (task cluster, number of steps landing there)
    task_links: list[tuple[int, int]] = field(default_factory=list)
    # Steps that matched nothing: (step name, actor). The interesting output of step 2.
    unmapped: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Facts:
    """The whole graph at leaf resolution. Serialised to one blob."""

    version: int
    built_at: str
    entities: dict[str, EntityFacts]
    # Whether any headcount was available, which decides what node size means.
    has_headcount: bool = False
    # (job profile cluster, other cluster) -> weight, per relationship
    job_skill: list[tuple[int, int, float]] = field(default_factory=list)
    job_task: list[tuple[int, int, float]] = field(default_factory=list)
    # Step 3. Absent until the opportunity assessment has run, which is why every
    # consumer treats an unscored cluster as unknown rather than as zero.
    actions: list[ActionFact] = field(default_factory=list)
    # task cluster -> (automation_pct, augmentation_pct), the action-weighted means
    task_opportunity: dict[int, tuple[float, float]] = field(default_factory=dict)
    # job profile -> (automation_pct, augmentation_pct, coverage_pct). Derived from
    # the profile's task mix, so it says "this share of this job could be absorbed".
    # `coverage_pct` is how much of the role's time sits in an assessed cluster —
    # without it, a half-assessed role reads as a low-opportunity one.
    job_opportunity: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    # Step 2. Empty until a process has been uploaded and mapped.
    processes: list[ProcessFact] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "version": self.version,
            "built_at": self.built_at,
            "has_headcount": self.has_headcount,
            "entities": {
                e: {
                    "labels": {lvl: {str(k): v for k, v in ids.items()} for lvl, ids in f.labels.items()},
                    "ancestry": {str(k): list(v) for k, v in f.ancestry.items()},
                    "metrics": {str(k): v for k, v in f.metrics.items()},
                    "members": {str(k): v for k, v in f.members.items()},
                }
                for e, f in self.entities.items()
            },
            "job_skill": [list(x) for x in self.job_skill],
            "job_task": [list(x) for x in self.job_task],
            "actions": [
                [a.task_cluster, a.name, a.definition, a.pct_of_task, a.automation_pct, a.augmentation_pct]
                for a in self.actions
            ],
            "task_opportunity": {str(k): list(v) for k, v in self.task_opportunity.items()},
            "job_opportunity": {str(k): list(v) for k, v in self.job_opportunity.items()},
            "processes": [
                {
                    "process_id": p.process_id,
                    "name": p.name,
                    "steps": p.steps,
                    "mapped_steps": p.mapped_steps,
                    "ordering_confidence": p.ordering_confidence,
                    "task_links": [list(x) for x in p.task_links],
                    "unmapped": [list(x) for x in p.unmapped],
                }
                for p in self.processes
            ],
        }

    @staticmethod
    def from_json(d: dict) -> "Facts":
        return Facts(
            version=d["version"],
            built_at=d["built_at"],
            has_headcount=bool(d.get("has_headcount")),
            entities={
                e: EntityFacts(
                    labels={lvl: {int(k): v for k, v in ids.items()} for lvl, ids in f["labels"].items()},
                    ancestry={int(k): (v[0], v[1]) for k, v in f["ancestry"].items()},
                    metrics={int(k): float(v) for k, v in f["metrics"].items()},
                    members={int(k): int(v) for k, v in f["members"].items()},
                )
                for e, f in d["entities"].items()
            },
            job_skill=[(int(a), int(b), float(w)) for a, b, w in d["job_skill"]],
            job_task=[(int(a), int(b), float(w)) for a, b, w in d["job_task"]],
            # `.get` with a default rather than `[...]`: a facts blob written before
            # step 3 existed is still valid, it just has no opportunity in it.
            actions=[
                ActionFact(int(c), str(n), str(df), float(p), float(au), float(ag))
                for c, n, df, p, au, ag in d.get("actions", [])
            ],
            task_opportunity={
                int(k): (float(v[0]), float(v[1]))
                for k, v in (d.get("task_opportunity") or {}).items()
            },
            job_opportunity={
                int(k): (float(v[0]), float(v[1]), float(v[2]))
                for k, v in (d.get("job_opportunity") or {}).items()
            },
            processes=[
                ProcessFact(
                    process_id=str(x["process_id"]),
                    name=str(x["name"]),
                    steps=int(x["steps"]),
                    mapped_steps=int(x["mapped_steps"]),
                    ordering_confidence=str(x.get("ordering_confidence", "low")),
                    task_links=[(int(a), int(b)) for a, b in x.get("task_links", [])],
                    unmapped=[(str(a), str(b)) for a, b in x.get("unmapped", [])],
                )
                for x in d.get("processes", [])
            ],
        )

    def leaf_counts(self) -> dict[str, int]:
        return {e: len(f.ancestry) for e, f in self.entities.items()}

    def actions_by_cluster(self) -> dict[int, list[ActionFact]]:
        out: dict[int, list[ActionFact]] = {}
        for a in self.actions:
            out.setdefault(a.task_cluster, []).append(a)
        return out


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------
def readiness(state: ProjectState) -> dict:
    """What is present and what is missing, for the gate.

    Reported as a list of unmet requirements rather than one boolean, so the button
    can say which asset is missing instead of being inertly disabled.
    """
    checks = [
        ("Job hierarchy", bool(state.clustering and state.clustering.family_names)),
        ("Job profile documents", bool([p for p in state.job_profiles if not p.stale])),
        ("Skills taxonomy", bool(state.skills.clustering and state.skills.clustering.profile_names)),
        ("Tasks taxonomy", bool(state.tasks.clustering and state.tasks.clustering.profile_names)),
    ]
    missing = [name for name, ok in checks if not ok]
    return {
        "ready": not missing,
        "missing": missing,
        "checks": [{"name": n, "ok": ok} for n, ok in checks],
    }


def profile_headcount(state: ProjectState) -> dict[str, int]:
    """Headcount per job profile_key, rolled up through dedupe groups.

    Optional: an HRIS import without a headcount column leaves this empty, and node
    size falls back to member counts. Better to say so than to invent a number.
    """
    hc = {r.id: r.headcount for r in state.raw_records}
    groups = {g.group_id: g.member_ids for g in state.dedupe_groups}
    key_of_cluster = {d.profile_cluster_id: d.profile_key for d in state.job_profiles}
    out: dict[str, int] = {}
    if not state.clustering:
        return out
    for a in state.clustering.assignments:
        key = key_of_cluster.get(a.final_profile_id)
        if not key:
            continue
        total = sum(h for h in (hc.get(m) for m in groups.get(a.item_id, [a.item_id])) if h)
        if total:
            out[key] = out.get(key, 0) + total
    return out


def _entity_facts(state: ProjectState, entity: str) -> EntityFacts:
    clustering = {
        "job": state.clustering,
        "skill": state.skills.clustering,
        "task": state.tasks.clustering,
    }[entity]
    if clustering is None:
        raise GraphNotReady(f"the {entity} hierarchy is not clustered")

    f = EntityFacts(
        labels={
            "profile": dict(clustering.profile_names),
            "category": dict(clustering.category_names),
            "family": dict(clustering.family_names),
        }
    )
    for a in clustering.assignments:
        f.ancestry[a.final_profile_id] = (a.final_category_id, a.final_family_id)
        f.members[a.final_profile_id] = f.members.get(a.final_profile_id, 0) + 1
    return f


def build(state: ProjectState, *, version: int = 1) -> Facts:
    """Compute the whole graph at leaf resolution.

    Deterministic: derived from state alone, no model calls, no embeddings. Cheap
    enough to recompute whenever anything upstream changes, which is why it is
    versioned and rebuilt rather than incrementally patched.
    """
    r = readiness(state)
    if not r["ready"]:
        raise GraphNotReady("missing: " + ", ".join(r["missing"]))

    facts = Facts(
        version=version,
        built_at=datetime.now(timezone.utc).isoformat(),
        entities={e: _entity_facts(state, e) for e in ENTITIES},
    )

    headcount = profile_headcount(state)
    key_of_cluster = {d.profile_cluster_id: d.profile_key for d in state.job_profiles}
    cluster_of_key = {v: k for k, v in key_of_cluster.items()}

    facts.has_headcount = bool(headcount)
    # People where we know them; otherwise the number of distinct source jobs, which
    # is a different measure and is labelled as one.
    jf = facts.entities["job"]
    for pid in jf.ancestry:
        key = key_of_cluster.get(pid)
        heads = headcount.get(key, 0) if key else 0
        jf.metrics[pid] = float(heads) if facts.has_headcount else float(jf.members.get(pid, 0))

    # ---- job profile <-> skill cluster ------------------------------------
    # Weight is how many of this profile's skills fall in that cluster: the strength
    # of the "this role needs this kind of capability" relationship.
    skill_cluster_of = {a.item_id: a.final_profile_id for a in state.skills.clustering.assignments}
    sf = facts.entities["skill"]
    js: dict[tuple[int, int], float] = {}
    for s in state.skills.inferred:
        sc = skill_cluster_of.get(s.id)
        jp = cluster_of_key.get(s.source_profile_key)
        if sc is None:
            continue
        sf.metrics[sc] = sf.metrics.get(sc, 0.0) + 1.0
        if jp is not None:
            js[(jp, sc)] = js.get((jp, sc), 0.0) + 1.0
    facts.job_skill = [(a, b, w) for (a, b), w in sorted(js.items())]

    # ---- job profile <-> task cluster -------------------------------------
    # Weight is the share of that role's time, so an edge means "this role spends
    # this much of its week on this kind of work". Task node size is the same
    # quantity scaled by headcount — FTE-equivalent time, the honest measure of how
    # much of the organisation's capacity a task absorbs.
    task_cluster_of = {a.item_id: a.final_profile_id for a in state.tasks.clustering.assignments}
    tf = facts.entities["task"]
    jt: dict[tuple[int, int], float] = {}
    for t in state.tasks.inferred:
        tc = task_cluster_of.get(t.id)
        if tc is None:
            continue
        jp = cluster_of_key.get(t.source_profile_key)
        share = float(t.proportion) / 100.0
        heads = headcount.get(t.source_profile_key, 0)
        tf.metrics[tc] = tf.metrics.get(tc, 0.0) + share * (heads or 1)
        if jp is not None:
            jt[(jp, tc)] = jt.get((jp, tc), 0.0) + float(t.proportion)
    facts.job_task = [(a, b, round(w, 2)) for (a, b), w in sorted(jt.items())]

    # ---- step 3: actions and opportunity -----------------------------------
    # Only clusters that still exist are carried over. Re-clustering tasks changes
    # the cluster ids, so an assessment of a cluster that is gone is stale, and
    # showing it against whatever now holds that id would be worse than showing
    # nothing at all.
    live = set(tf.ancestry)
    facts.actions = [
        ActionFact(
            task_cluster=a.task_cluster_id,
            name=a.name,
            definition=a.definition,
            pct_of_task=a.pct_of_task,
            automation_pct=a.automation_pct,
            augmentation_pct=a.augmentation_pct,
        )
        for a in state.workforce.actions
        if a.task_cluster_id in live
    ]
    facts.task_opportunity = {
        o.task_cluster_id: (o.automation_pct, o.augmentation_pct)
        for o in state.workforce.opportunity
        if o.task_cluster_id in live
    }

    # A role's opportunity is the time-weighted mean of its task clusters', over the
    # part of its time that has been assessed. The job_task edge weight is already
    # that role's percentage on that cluster, so this is the roll-up in three lines.
    if facts.task_opportunity:
        by_job: dict[int, list[tuple[float, tuple[float, float]]]] = {}
        totals: dict[int, float] = {}
        for jp, tc, w in facts.job_task:
            totals[jp] = totals.get(jp, 0.0) + w
            score = facts.task_opportunity.get(tc)
            if score:
                by_job.setdefault(jp, []).append((w, score))
        for jp, parts in by_job.items():
            covered = sum(w for w, _ in parts)
            if covered <= 0:
                continue
            facts.job_opportunity[jp] = (
                round(sum(w * s[0] for w, s in parts) / covered, 1),
                round(sum(w * s[1] for w, s in parts) / covered, 1),
                round(100.0 * covered / totals[jp], 1) if totals.get(jp) else 0.0,
            )

    # ---- step 2: uploaded processes ----------------------------------------
    for pr in state.workforce.processes:
        counts: dict[int, int] = {}
        unmapped: list[tuple[str, str]] = []
        for st in pr.steps:
            if st.task_cluster_id is not None and st.task_cluster_id in live:
                counts[st.task_cluster_id] = counts.get(st.task_cluster_id, 0) + 1
            elif pr.mapped_at:
                # Only a *mapped* process can say a step matched nothing. Before mapping
                # every step is unmatched, and drawing them all as gaps would invent a
                # finding out of work not yet done.
                unmapped.append((st.name, st.actor))
        facts.processes.append(
            ProcessFact(
                process_id=pr.id,
                name=pr.process_name,
                steps=len(pr.steps),
                mapped_steps=sum(1 for x in pr.steps if x.task_cluster_id is not None),
                ordering_confidence=pr.ordering_confidence,
                task_links=sorted(counts.items()),
                unmapped=unmapped,
            )
        )

    return facts


# ---------------------------------------------------------------------------
# Cuts
# ---------------------------------------------------------------------------
def _display_level(
    entity_facts: EntityFacts, leaf: int, base: str, expanded: set[str], entity: str
) -> tuple[str, int]:
    """Which node a leaf is displayed as, given the resolution and what is expanded.

    A leaf shows at the finest level whose every coarser ancestor has been expanded.
    Starting at the base resolution, an expanded ancestor drops one level finer — so
    expanding a single family reveals its categories while its siblings stay whole.
    """
    cat, fam = entity_facts.ancestry.get(leaf, (-1, -1))
    at: dict[str, int] = {"family": fam, "category": cat, "profile": leaf}
    order = LEVELS[LEVELS.index(base) :]
    level = order[0]
    for nxt in order[1:]:
        if f"{entity}:{level}:{at[level]}" in expanded:
            level = nxt
        else:
            break
    return level, at[level]


def metric_titles(facts: Facts) -> dict[str, str]:
    return METRIC_TITLES if facts.has_headcount else METRIC_TITLES_NO_HEADCOUNT


def _leaf_filter(
    ef: EntityFacts, keep: set[int] | None, level: str
) -> set[int] | None:
    """Which leaves survive a filter expressed at `level`.

    Filtering is done on leaves rather than on the finished nodes because an edge is a
    leaf-to-leaf fact: dropping a node after aggregation would leave its edge weight
    folded into a neighbour, and the picture would quietly overstate what is left.
    """
    if not keep:
        return None
    idx = LEVELS.index(level)
    out = set()
    for leaf, (cat, fam) in ef.ancestry.items():
        at = fam if idx == 0 else cat if idx == 1 else leaf
        if at in keep:
            out.add(leaf)
    return out


def cut(
    facts: Facts,
    *,
    levels: dict[str, str],
    expanded: set[str] | None = None,
    show: tuple[str, ...] = ENTITIES,
    filters: dict[str, tuple[str, set[int]]] | None = None,
) -> dict:
    """Roll the fact table up to one view.

    `levels` is the requested resolution per entity; `expanded` is the set of node ids
    whose children should be shown.

    `show` is which hierarchies to draw at all. Skills and tasks answer different
    questions about the same jobs — what a role needs to know against what it spends its
    week doing — and drawing both at once triples the edge count for a picture nobody
    reads either half of. One at a time is the legible default.

    `filters` narrows each hierarchy to a chosen set of ancestors, e.g.
    `{"job": ("family", {3, 7})}`. Applied to leaves before aggregation, so the weights
    that survive are the weights that belong to what is on screen.
    """
    expanded = expanded or set()
    filters = filters or {}
    show = tuple(e for e in ENTITIES if e in show) or ENTITIES
    kept: dict[str, set[int] | None] = {}
    for entity in ENTITIES:
        spec = filters.get(entity)
        kept[entity] = (
            _leaf_filter(facts.entities[entity], spec[1], spec[0]) if spec else None
        )
    titles = metric_titles(facts)
    nodes: dict[str, dict] = {}
    mapped: dict[str, dict[int, str]] = {e: {} for e in ENTITIES}
    actions_by_cluster = facts.actions_by_cluster()

    # Opportunity, per entity, keyed by leaf. Skills carry none: a capability is not
    # automatable, the work done with it is, and inventing a skill-level score would
    # be a number with nothing behind it.
    scores: dict[str, dict[int, tuple[float, float]]] = {
        "job": {k: (v[0], v[1]) for k, v in facts.job_opportunity.items()},
        "skill": {},
        "task": facts.task_opportunity,
    }

    for entity in show:
        ef = facts.entities[entity]
        base = levels.get(entity, "family")
        if base not in LEVELS:
            raise ValueError(f"unknown level {base!r}; expected one of {list(LEVELS)}")
        allowed = kept[entity]
        for leaf in ef.ancestry:
            if allowed is not None and leaf not in allowed:
                continue
            level, node_id = _display_level(ef, leaf, base, expanded, entity)
            key = f"{entity}:{level}:{node_id}"
            mapped[entity][leaf] = key
            n = nodes.get(key)
            if n is None:
                n = nodes[key] = {
                    "id": key,
                    "entity": entity,
                    "level": level,
                    "cluster_id": node_id,
                    "name": label_of(ef, level, node_id),
                    "level_title": LEVEL_TITLES[entity][level],
                    "metric": 0.0,
                    "metric_title": titles[entity],
                    "members": 0,
                    "leaves": 0,
                    # A task cluster has no children in the hierarchy but does have
                    # actions once step 3 has run, so at the finest level it stays
                    # openable. Everything else: only a node with something beneath
                    # it, so the UI never offers an expand that would do nothing.
                    "expandable": level != "profile"
                    or (entity == "task" and node_id in actions_by_cluster),
                    "expanded": key in expanded,
                    # Weighted accumulators, resolved to percentages below.
                    "_auto": 0.0,
                    "_aug": 0.0,
                    "_scored_weight": 0.0,
                    "_weight": 0.0,
                }
            m = ef.metrics.get(leaf, 0.0)
            n["metric"] += m
            n["members"] += ef.members.get(leaf, 0)
            n["leaves"] += 1
            # Weight opportunity by the node's own metric — headcount for roles, time
            # for tasks — so a big role's automation counts for more than a
            # single-holder one, and fall back to equal weight where the metric is
            # zero rather than silently dropping the leaf out of the average.
            w = m if m > 0 else 1.0
            n["_weight"] += w
            score = scores[entity].get(leaf)
            if score:
                n["_auto"] += w * score[0]
                n["_aug"] += w * score[1]
                n["_scored_weight"] += w

    edges: dict[tuple[str, str], float] = {}
    for rel, pairs in (("skill", facts.job_skill), ("task", facts.job_task)):
        for a, b, w in pairs:
            ka, kb = mapped["job"].get(a), mapped[rel].get(b)
            if ka and kb:
                edges[(ka, kb)] = edges.get((ka, kb), 0.0) + w

    for n in nodes.values():
        n["metric"] = round(n["metric"], 2)
        sw = n.pop("_scored_weight")
        total_w = n.pop("_weight")
        auto, aug = n.pop("_auto"), n.pop("_aug")
        # None, not zero. An unassessed node has unknown opportunity, and the
        # difference matters the moment anything colours a graph by it.
        n["automation"] = round(auto / sw, 1) if sw else None
        n["augmentation"] = round(aug / sw, 1) if sw else None
        n["opportunity_coverage"] = round(100.0 * sw / total_w, 1) if total_w else 0.0

    # ---- action nodes ------------------------------------------------------
    # Only for task clusters the user has explicitly opened. Adding them wholesale
    # would put 2,000-3,000 nodes into a view that is deliberately a few hundred.
    action_nodes: list[dict] = []
    for key, n in list(nodes.items()):
        if n["entity"] != "task" or n["level"] != "profile" or key not in expanded:
            continue
        for i, a in enumerate(actions_by_cluster.get(n["cluster_id"], [])):
            aid = f"{ACTION_ENTITY}:{n['cluster_id']}:{i}"
            action_nodes.append(
                {
                    "id": aid,
                    "entity": ACTION_ENTITY,
                    "level": "profile",
                    "cluster_id": n["cluster_id"],
                    "name": a.name,
                    "level_title": "Action",
                    # The action's share of the cluster's own metric, so an action
                    # node's size is on the same scale as every other task node.
                    "metric": round(n["metric"] * a.pct_of_task / 100.0, 2),
                    "metric_title": titles["task"],
                    "members": 0,
                    "leaves": 1,
                    "expandable": False,
                    "expanded": False,
                    "automation": a.automation_pct,
                    "augmentation": a.augmentation_pct,
                    "opportunity_coverage": 100.0,
                    "pct_of_task": a.pct_of_task,
                    "definition": a.definition,
                }
            )
            edges[(key, aid)] = a.pct_of_task
    for a in action_nodes:
        nodes[a["id"]] = a

    # ---- process nodes -----------------------------------------------------
    # Always drawn when they exist: there are a handful at most, they were uploaded
    # deliberately, and a process missing from the one unifying view is the whole reason
    # this was added. Their edges roll up with whatever task resolution is showing.
    for pr in facts.processes:
        pid = f"{PROCESS_ENTITY}:0:{pr.process_id}"
        nodes[pid] = {
            "id": pid,
            "entity": PROCESS_ENTITY,
            "level": "profile",
            "cluster_id": 0,
            "name": pr.name,
            "level_title": "Process",
            "metric": float(pr.steps),
            "metric_title": "steps",
            "members": pr.steps,
            "leaves": 1,
            "expandable": False,
            "expanded": False,
            "automation": None,
            "augmentation": None,
            "opportunity_coverage": 0.0,
            "mapped_steps": pr.mapped_steps,
            "unmapped_steps": len(pr.unmapped),
            "ordering_confidence": pr.ordering_confidence,
        }
        for cluster, count in pr.task_links:
            target = mapped["task"].get(cluster)
            if target:
                edges[(pid, target)] = edges.get((pid, target), 0.0) + count
        for i, (step_name, actor) in enumerate(pr.unmapped):
            uid = f"{UNMAPPED_ENTITY}:0:{pr.process_id}-{i}"
            nodes[uid] = {
                "id": uid,
                "entity": UNMAPPED_ENTITY,
                "level": "profile",
                "cluster_id": 0,
                "name": step_name,
                "level_title": "Step with no matching task",
                "metric": 1.0,
                "metric_title": "step",
                "members": 1,
                "leaves": 1,
                "expandable": False,
                "expanded": False,
                "automation": None,
                "augmentation": None,
                "opportunity_coverage": 0.0,
                "definition": (
                    f"Performed by {actor}. No task cluster matched, so this is work "
                    "the job descriptions never described."
                ),
            }
            edges[(pid, uid)] = 1.0

    return {
        "levels": {e: levels.get(e, "family") for e in ENTITIES},
        "shown": list(show),
        "expanded": sorted(expanded),
        "nodes": sorted(nodes.values(), key=lambda n: (n["entity"], -n["metric"], n["name"])),
        "edges": [
            {"source": a, "target": b, "weight": round(w, 2)}
            for (a, b), w in sorted(edges.items(), key=lambda kv: -kv[1])
        ],
        "has_headcount": facts.has_headcount,
        "has_opportunity": bool(facts.task_opportunity),
        "totals": {
            "nodes": len(nodes),
            "edges": len(edges),
            "actions": len(action_nodes),
            "processes": len(facts.processes),
            "unmapped_steps": sum(len(p.unmapped) for p in facts.processes),
            "leaves": facts.leaf_counts(),
        },
    }


def filter_options(facts: Facts) -> dict:
    """Every family and category per hierarchy, with how many leaves sit under each.

    The counts are the point: a filter list that does not say how much of the graph each
    option covers makes choosing one guesswork.
    """
    out: dict[str, dict] = {}
    for entity in ENTITIES:
        ef = facts.entities[entity]
        fam: dict[int, int] = {}
        cat: dict[int, tuple[int, int]] = {}
        for leaf, (c, f) in ef.ancestry.items():
            fam[f] = fam.get(f, 0) + 1
            n, _ = cat.get(c, (0, f))
            cat[c] = (n + 1, f)
        out[entity] = {
            "level_titles": LEVEL_TITLES[entity],
            "family": sorted(
                (
                    {"id": f, "name": label_of(ef, "family", f), "leaves": n}
                    for f, n in fam.items()
                ),
                key=lambda x: x["name"],
            ),
            "category": sorted(
                (
                    {
                        "id": c,
                        "name": label_of(ef, "category", c),
                        "leaves": n,
                        "family": f,
                    }
                    for c, (n, f) in cat.items()
                ),
                key=lambda x: x["name"],
            ),
        }
    return out


def node_detail(facts: Facts, node_id: str) -> dict:
    """Everything behind one node, for its modal.

    Later steps extend this rather than replacing it: a job profile gains its AI
    opportunity, its generated skills and its future design as those are produced.
    """
    # Process and unmapped-step ids carry a string tail rather than an integer, so they
    # are matched before the integer parse below rather than crashing it.
    if node_id.startswith((f"{PROCESS_ENTITY}:", f"{UNMAPPED_ENTITY}:")):
        return _process_detail(facts, node_id)

    try:
        entity, level, raw = node_id.split(":")
        cid = int(raw)
    except ValueError:
        raise ValueError(f"malformed node id {node_id!r}") from None

    # An action node's id carries its cluster and its ordinal rather than a level,
    # because an action has no hierarchy of its own — it is one row of one cluster.
    if entity == ACTION_ENTITY:
        return _action_detail(facts, cluster=int(level), index=cid)

    if entity not in ENTITIES or level not in LEVELS:
        raise ValueError(f"unknown node {node_id!r}")

    ef = facts.entities[entity]
    # Which leaves sit under this node, at whatever level it is.
    idx = LEVELS.index(level)
    leaves = [
        leaf
        for leaf, (cat, fam) in ef.ancestry.items()
        if (leaf if idx == 2 else cat if idx == 1 else fam) == cid
    ]

    children_level = LEVELS[idx + 1] if idx < 2 else None
    children: list[dict] = []
    if children_level:
        seen: dict[int, dict] = {}
        for leaf in leaves:
            cat, fam = ef.ancestry[leaf]
            child = leaf if children_level == "profile" else cat
            c = seen.setdefault(
                child,
                {
                    "id": f"{entity}:{children_level}:{child}",
                    "name": label_of(ef, children_level, child),
                    "metric": 0.0,
                    "members": 0,
                },
            )
            c["metric"] += ef.metrics.get(leaf, 0.0)
            c["members"] += ef.members.get(leaf, 0)
        children = sorted(seen.values(), key=lambda c: -c["metric"])
        for c in children:
            c["metric"] = round(c["metric"], 2)

    # The strongest relationships into the other two hierarchies, resolved to leaf
    # names — this is what makes a modal answer "what does this connect to?".
    related: dict[str, list[dict]] = {}
    leaf_set = set(leaves)
    for rel, pairs, side in (
        ("skill", facts.job_skill, "b"),
        ("task", facts.job_task, "b"),
    ):
        if entity == "job":
            hits: dict[int, float] = {}
            for a, b, w in pairs:
                if a in leaf_set:
                    hits[b] = hits.get(b, 0.0) + w
            related[rel] = _top(hits, facts.entities[rel])
        elif entity == rel:
            hits = {}
            for a, b, w in pairs:
                if b in leaf_set:
                    hits[a] = hits.get(a, 0.0) + w
            related["job"] = _top(hits, facts.entities["job"])

    # ---- step 3 ------------------------------------------------------------
    # The modal grows as later steps run, per the instructions: a task cluster gains
    # its actions and both scores, a role gains the opportunity its task mix implies.
    scores = (
        {k: (v[0], v[1]) for k, v in facts.job_opportunity.items()}
        if entity == "job"
        else facts.task_opportunity
        if entity == "task"
        else {}
    )
    weights = {x: (ef.metrics.get(x, 0.0) or 1.0) for x in leaves}
    scored = sum(weights[x] for x in leaves if x in scores)
    opportunity: dict | None = None
    if scored:
        opportunity = {
            "automation": round(
                sum(weights[x] * scores[x][0] for x in leaves if x in scores) / scored, 1
            ),
            "augmentation": round(
                sum(weights[x] * scores[x][1] for x in leaves if x in scores) / scored, 1
            ),
            "coverage": round(100.0 * scored / sum(weights.values()), 1) if weights else 0.0,
        }

    actions: list[dict] = []
    if entity == "task":
        by_cluster = facts.actions_by_cluster()
        # At a coarser level this is every action under the node, which is why they
        # are ordered by contribution and the cluster is named on each one.
        for leaf in leaves:
            for a in by_cluster.get(leaf, []):
                actions.append(
                    {
                        "name": a.name,
                        "definition": a.definition,
                        "cluster": label_of(ef, "profile", leaf),
                        "pct_of_task": a.pct_of_task,
                        "automation": a.automation_pct,
                        "augmentation": a.augmentation_pct,
                        # Effort share x cluster size: how much of the node's total
                        # capacity this action accounts for.
                        "weight": round(ef.metrics.get(leaf, 0.0) * a.pct_of_task / 100.0, 2),
                    }
                )
        actions.sort(key=lambda a: -a["weight"])

    return {
        "id": node_id,
        "entity": entity,
        "level": level,
        "level_title": LEVEL_TITLES[entity][level],
        "name": label_of(ef, level, cid),
        "metric": round(sum(ef.metrics.get(x, 0.0) for x in leaves), 2),
        "metric_title": metric_titles(facts)[entity],
        "members": sum(ef.members.get(x, 0) for x in leaves),
        "leaves": len(leaves),
        "children_title": LEVEL_TITLES[entity][children_level] if children_level else None,
        "children": children[:60],
        "related": related,
        "opportunity": opportunity,
        "actions": actions[:40],
    }


def _process_detail(facts: Facts, node_id: str) -> dict:
    """A process node's modal, or one unmapped step's."""
    entity, _lvl, tail = node_id.split(":", 2)
    if entity == UNMAPPED_ENTITY:
        process_id, _, idx = tail.rpartition("-")
        pr = next((p for p in facts.processes if p.process_id == process_id), None)
        if pr is None or not idx.isdigit() or int(idx) >= len(pr.unmapped):
            raise ValueError(f"no unmapped step {node_id!r}")
        name, actor = pr.unmapped[int(idx)]
        return {
            "id": node_id,
            "entity": UNMAPPED_ENTITY,
            "level": "profile",
            "level_title": "Step with no matching task",
            "name": name,
            "definition": (
                f"Performed by {actor}. No task cluster matched this step, so it is work "
                "that exists in the process but that no job description described."
            ),
            "metric": 1,
            "metric_title": "step",
            "members": 1,
            "leaves": 1,
            "children_title": None,
            "children": [],
            "related": {},
            "opportunity": None,
            "actions": [],
            "parent": {"id": f"{PROCESS_ENTITY}:0:{pr.process_id}", "name": pr.name},
        }

    pr = next((p for p in facts.processes if p.process_id == tail), None)
    if pr is None:
        raise ValueError(f"no process {node_id!r}")
    tf = facts.entities["task"]
    children = sorted(
        (
            {
                "id": f"task:profile:{cluster}",
                "name": label_of(tf, "profile", cluster),
                "metric": count,
                "members": count,
            }
            for cluster, count in pr.task_links
        ),
        key=lambda c: -c["metric"],
    )
    return {
        "id": node_id,
        "entity": PROCESS_ENTITY,
        "level": "profile",
        "level_title": "Process",
        "name": pr.name,
        "definition": (
            f"{pr.steps} steps, {pr.mapped_steps} mapped onto the task taxonomy, "
            f"{len(pr.unmapped)} matching nothing. Ordering confidence: "
            f"{pr.ordering_confidence}."
        ),
        "metric": pr.steps,
        "metric_title": "steps",
        "members": pr.steps,
        "leaves": 1,
        "children_title": "Task cluster" if children else None,
        "children": children,
        "related": {
            "unmapped": [{"name": n, "weight": 1} for n, _actor in pr.unmapped],
        },
        "opportunity": None,
        "actions": [],
    }


def _action_detail(facts: Facts, *, cluster: int, index: int) -> dict:
    """One action's modal. Its siblings come with it, since an action's percentage
    only means something against the rest of the cluster."""
    siblings = facts.actions_by_cluster().get(cluster, [])
    if not 0 <= index < len(siblings):
        raise ValueError(f"no action {index} on task cluster {cluster}")
    a = siblings[index]
    tf = facts.entities["task"]
    cluster_name = label_of(tf, "profile", cluster)
    return {
        "id": f"{ACTION_ENTITY}:{cluster}:{index}",
        "entity": ACTION_ENTITY,
        "level": "profile",
        "level_title": "Action",
        "name": a.name,
        "definition": a.definition,
        "metric": round(tf.metrics.get(cluster, 0.0) * a.pct_of_task / 100.0, 2),
        "metric_title": metric_titles(facts)["task"],
        "members": 0,
        "leaves": 1,
        "children_title": None,
        "children": [],
        "related": {},
        "parent": {"id": f"task:profile:{cluster}", "name": cluster_name},
        "opportunity": {
            "automation": a.automation_pct,
            "augmentation": a.augmentation_pct,
            "coverage": 100.0,
        },
        "actions": [
            {
                "name": s.name,
                "definition": s.definition,
                "cluster": cluster_name,
                "pct_of_task": s.pct_of_task,
                "automation": s.automation_pct,
                "augmentation": s.augmentation_pct,
                "weight": round(tf.metrics.get(cluster, 0.0) * s.pct_of_task / 100.0, 2),
                "current": i == index,
            }
            for i, s in enumerate(siblings)
        ],
    }


def _top(hits: dict[int, float], ef: EntityFacts, limit: int = 12) -> list[dict]:
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])[:limit]
    return [
        {"name": label_of(ef, "profile", cid), "weight": round(w, 2)}
        for cid, w in ranked
    ]
