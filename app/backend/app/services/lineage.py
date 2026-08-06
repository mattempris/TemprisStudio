"""What depends on what, and what a repeat invalidates.

Every step in both studios can be re-run, and doing so makes some of what follows wrong.

What this replaces: `pipeline._invalidate_from` was called from exactly one of the
twenty-six re-runnable steps — dedupe confirmation — and knew only four stages, because it
predated per-tier clustering, the skills and tasks taxonomies, third-party matching and
all seven Work Architecture Studio steps. So re-running dedupe cleared the normalised profiles and
the old flat clustering, and left the per-tier hierarchies, both taxonomies, the matches,
the opportunity assessment, four agent specs and the graph all describing records that no
longer existed. Re-running anything else invalidated nothing at all.

**The graph is declared once, as data.** Adding a step means adding one row here rather
than remembering to update nine call sites, which is the failure mode that produced the
dead function.

**Two verbs, chosen per artifact, and the difference matters.**

  CLEAR       the artifact is meaningless without its input, and leaving it in place
              lets something read it by accident. A clustering whose embeddings changed
              is not "old", it is wrong: its cluster ids no longer refer to anything.
  MARK_STALE  the artifact is expensive and worth keeping — for lineage, for comparison,
              and because a person may want to read it before regenerating. A stale job
              profile is still a real document that was really produced.

Cleared artifacts must not be readable. Stale ones stay readable and exportable, badged.
That is why this is two verbs and not a flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.models.project_state import ProjectState


class Verb(str, Enum):
    CLEAR = "clear"
    MARK_STALE = "mark_stale"


@dataclass(frozen=True)
class Step:
    """One re-runnable step.

    `key` is the canonical id used by the API and the UI. `consumes` names the steps this
    one reads, which is what makes the graph a graph — descendants are derived from it
    rather than listed twice and allowed to disagree.
    """

    key: str
    title: str
    consumes: tuple[str, ...] = ()
    verb: Verb = Verb.CLEAR
    # Human-readable count of what exists, for the confirmation dialog.
    counter: str = ""


# Order is presentation order; the edges are what matters.
STEPS: tuple[Step, ...] = (
    Step("ingest", "Input assets", counter="raw records"),
    Step("strip", "Stripped content", ("ingest",), counter="stripped records"),
    Step("dedupe", "Deduplication", ("strip",), counter="dedupe groups"),
    Step("normalize", "Normalised descriptions", ("dedupe",), counter="normalised profiles"),
    # The job hierarchy. Each tier consumes the one below it, which is already enforced
    # inside `tier_state.save_tier`; declaring it here is what lets everything *outside*
    # the tier engine know too.
    Step("job:profile", "Anchor roles (clustering)", ("normalize",), counter="anchor roles"),
    Step("job:category", "Job categories", ("job:profile",), counter="job categories"),
    Step("job:family", "Job families", ("job:category",), counter="job families"),
    Step(
        "profiles",
        "Anchor role documents",
        ("job:family",),
        verb=Verb.MARK_STALE,
        counter="profile documents",
    ),
    Step(
        "evaluation",
        "Job evaluation",
        ("profiles",),
        verb=Verb.MARK_STALE,
        counter="evaluations",
    ),
    Step("skills:infer", "Inferred skills", ("profiles",), counter="inferred skills"),
    Step("skill:profile", "Skill clusters", ("skills:infer",), counter="skill clusters"),
    Step("skill:category", "Skill categories", ("skill:profile",), counter="skill categories"),
    Step("skill:family", "Skill families", ("skill:category",), counter="skill families"),
    Step("proficiency", "Proficiency mapping", ("skill:family",), counter="proficiency records"),
    Step("tasks:infer", "Inferred tasks", ("profiles",), counter="inferred tasks"),
    Step("task:profile", "Task clusters", ("tasks:infer",), counter="task clusters"),
    Step("task:category", "Task categories", ("task:profile",), counter="task categories"),
    Step("task:family", "Task domains", ("task:category",), counter="task domains"),
    Step("matching", "3rd-party taxonomy match", ("profiles",), counter="matches"),
    # Work Architecture Studio. The graph is derived and cheap, so it is always cleared.
    Step("workforce:graph", "Work architecture", ("task:family", "skill:family", "profiles")),
    Step("opportunity", "AI opportunity", ("task:family",), counter="assessed task clusters"),
    Step(
        "augmentation",
        "Augmentation skills",
        ("opportunity",),
        verb=Verb.MARK_STALE,
        counter="skill files",
    ),
    Step(
        "automation",
        "Agent specifications",
        ("opportunity",),
        verb=Verb.MARK_STALE,
        counter="agent specs",
    ),
    Step("processes", "Uploaded processes", ("task:family",), counter="processes"),
    Step(
        "process-opportunity",
        "Process assessments",
        ("processes", "opportunity"),
        counter="process assessments",
    ),
    Step(
        "future-roles",
        "Future role design",
        ("opportunity",),
        verb=Verb.MARK_STALE,
        counter="role designs",
    ),
    Step(
        "work-design",
        "Designed jobs",
        # Both: a designed job's task hours come from the assessment and its oversight lines
        # come from the agents. Deliberately NOT workforce:graph — the graph is a derived read
        # model, and depending on it would mean rebuilding it invalidates human work product.
        ("opportunity", "automation"),
        verb=Verb.MARK_STALE,
        counter="designed jobs",
    ),
)

BY_KEY: dict[str, Step] = {s.key: s for s in STEPS}


def descendants(key: str) -> list[str]:
    """Every step that transitively depends on `key`, in declaration order.

    Breadth-first over the reverse edges. Declaration order rather than discovery order
    so the confirmation dialog reads top-to-bottom like the wizard does.
    """
    if key not in BY_KEY:
        raise KeyError(f"unknown step {key!r}")
    reverse: dict[str, list[str]] = {}
    for s in STEPS:
        for parent in s.consumes:
            reverse.setdefault(parent, []).append(s.key)
    found: set[str] = set()
    frontier = [key]
    while frontier:
        nxt: list[str] = []
        for k in frontier:
            for child in reverse.get(k, []):
                if child not in found:
                    found.add(child)
                    nxt.append(child)
        frontier = nxt
    return [s.key for s in STEPS if s.key in found]


# ---------------------------------------------------------------------------
# Counting and applying
# ---------------------------------------------------------------------------
def _tier(state: ProjectState, entity: str, tier: str):
    holder = {
        "job": state.clustering_tiers,
        "skill": state.skills.clustering_tiers,
        "task": state.tasks.clustering_tiers,
    }[entity]
    return holder.get(tier)


def count(state: ProjectState, key: str) -> int:
    """How much of this step's output exists right now.

    Only what is *live* counts: an artifact already marked stale is not going to be
    invalidated again, and reporting it a second time would inflate the warning.
    """
    w = state.workforce
    if key == "ingest":
        return len(state.raw_records)
    if key == "strip":
        return len(state.stripped_records)
    if key == "dedupe":
        return len(state.dedupe_groups)
    if key == "normalize":
        return len(state.normalized_profiles)
    if key == "profiles":
        return sum(1 for p in state.job_profiles if not p.stale)
    if key == "evaluation":
        return sum(1 for r in state.je_results if not r.stale)
    if key == "skills:infer":
        return len(state.skills.inferred)
    if key == "tasks:infer":
        return len(state.tasks.inferred)
    if key == "proficiency":
        return len(state.skills.profile_requirements)
    if key == "matching":
        return len(state.matching.matches)
    if key == "opportunity":
        return len(w.opportunity)
    if key == "augmentation":
        return len(w.skills_guidance)
    if key == "automation":
        return len(w.agents)
    if key == "processes":
        return len(w.processes)
    if key == "process-opportunity":
        return len(w.process_assessments)
    if key == "future-roles":
        return len(w.future_roles)
    if key == "work-design":
        return sum(1 for j in state.work_design.jobs if not j.stale)
    if key == "workforce:graph":
        # Derived and stored outside state, so its presence is not answerable here. The
        # route that owns the blob reports it; 0 keeps the dialog honest rather than
        # guessing.
        return 0
    if ":" in key:
        entity, tier = key.split(":", 1)
        t = _tier(state, entity, tier)
        return len(t.names) if t else 0
    return 0


def apply(state: ProjectState, key: str) -> list[dict]:
    """Invalidate everything downstream of `key`. Returns what was affected.

    The return value is what the UI reports, and it is produced by the same walk that
    does the work — so the notification cannot drift from the behaviour, which is the
    bug the old dead function would have had if anyone had called it.
    """
    affected: list[dict] = []
    for child in descendants(key):
        step = BY_KEY[child]
        before = count(state, child)
        if before == 0:
            continue
        _clear(state, child) if step.verb is Verb.CLEAR else _stale(state, child)
        affected.append(
            {
                "step": child,
                "title": step.title,
                "verb": step.verb.value,
                "count": before,
                "counter": step.counter,
            }
        )
    return affected


def preview(state: ProjectState, key: str) -> dict:
    """What `apply` would do, without doing it. Drives the confirmation dialog."""
    items = []
    for child in descendants(key):
        step = BY_KEY[child]
        n = count(state, child)
        if n == 0:
            continue
        items.append(
            {
                "step": child,
                "title": step.title,
                "verb": step.verb.value,
                "count": n,
                "counter": step.counter,
            }
        )
    return {
        "step": key,
        "title": BY_KEY[key].title if key in BY_KEY else key,
        "affected": items,
        "clears": [i for i in items if i["verb"] == Verb.CLEAR.value],
        "marks_stale": [i for i in items if i["verb"] == Verb.MARK_STALE.value],
        # Whether anything irreversible-looking happens, so the UI knows whether to ask
        # at all. Marking stale keeps the artifact readable, so it only needs telling.
        "needs_confirmation": any(i["verb"] == Verb.CLEAR.value for i in items),
    }


def _stale(state: ProjectState, key: str) -> None:
    if key == "profiles":
        for p in state.job_profiles:
            p.stale = True
    elif key == "evaluation":
        for r in state.je_results:
            r.stale = True
    elif key == "work-design":
        # The first honest MARK_STALE in this half of the app. The three below are declared
        # MARK_STALE but clear their lists, because their records have no `stale` field — a
        # clear wearing a stale label. A designed job is a title, a headcount and an
        # arrangement of work a person argued about in a workshop: the only artefact in any of
        # the three studios authored by a human rather than a model, and the only one that
        # cannot be regenerated. So it is badged and kept, and re-saving it clears the badge.
        for j in state.work_design.jobs:
            j.stale = True
            j.stale_reason = (
                "the AI opportunity assessment or the agent specifications behind these "
                "numbers were re-run"
            )
            if j.profile_doc:
                j.profile_doc.stale = True
    elif key in ("augmentation", "automation", "future-roles"):
        # These have no `stale` field: they are files and specs in blob, and a half-marked
        # record would be worse than an honest clear. Cleared from the index, files left
        # in blob so nothing a person downloaded disappears from under them.
        w = state.workforce
        if key == "augmentation":
            w.skills_guidance = []
        elif key == "automation":
            w.agents = []
        else:
            w.future_roles = []


def _clear(state: ProjectState, key: str) -> None:
    w = state.workforce
    if key == "strip":
        state.stripped_records = []
    elif key == "dedupe":
        state.dedupe_groups = []
        state.dedupe_threshold = None
    elif key == "normalize":
        state.normalized_profiles = []
    elif key == "skills:infer":
        state.skills.inferred = []
        state.skills.audit = {}
    elif key == "tasks:infer":
        state.tasks.inferred = []
        state.tasks.audit = {}
    elif key == "proficiency":
        state.skills.profile_requirements = []
        state.skills.cluster_proficiencies = []
    elif key == "matching":
        state.matching.matches = []
        state.matching.summary = {}
        state.matching.computed_at = None
    elif key == "opportunity":
        w.opportunity = []
        w.actions = []
        w.audit = {}
    elif key == "processes":
        w.processes = []
        w.process_assessments = []
    elif key == "process-opportunity":
        w.process_assessments = []
    elif key == "workforce:graph":
        pass  # the blob is owned by the workforce route, which deletes it
    elif ":" in key:
        entity, tier = key.split(":", 1)
        holder = {
            "job": state.clustering_tiers,
            "skill": state.skills.clustering_tiers,
            "task": state.tasks.clustering_tiers,
        }[entity]
        holder.pop(tier, None)
        # The denormalised view is rebuilt from the tiers, so it has to go too or it
        # keeps describing a hierarchy that no longer exists.
        if entity == "job":
            state.clustering = None
        elif entity == "skill":
            state.skills.clustering = None
        else:
            state.tasks.clustering = None
