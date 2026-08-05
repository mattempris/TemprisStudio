"""AI opportunity assessment — Work Architecture Studio step 3.

One LLM call per task cluster returns the 3-5 **actions** that cluster is made of,
each scored on two axes. Everything above the action — the cluster's score, a role's
score, capacity released — is arithmetic over those actions and is computed here in
code, never asked of the model. That split is the same one job evaluation and dedupe
already use: the model supplies judgement at the level it can actually judge, and the
aggregation is auditable.

**Why the action level.** "Handling Customer Complaints" is not automatable or not.
Inside it, drafting the acknowledgement largely is, reconciling the account history
mostly is, and deciding redress is not. Scoring the cluster directly produces a
mid-range number that is true of nothing; scoring its actions and weighting by effort
produces the same headline with a defensible breakdown behind it.

**Why two scores.** `automation_pct` is what AI can carry unattended. `augmentation_pct`
is how much faster a person is with AI help while still owning the outcome. They
diverge, and the divergence is the useful part: contract review automates badly
because a missed clause is a real liability someone must answer for, and augments well
because a model that flags every deviation from the standard form removes most of the
reading. Step 6 ranks agents by automation; step 5 ranks prompts by augmentation. With
one score, step 5's list would be ordered by how replaceable each task is, and the
tasks a good prompt helps most would sink to the bottom.

Calibration guidance is ported from `Insurance Demo/pipeline/gen_tasks.py`, which is
explicit and well-tuned, extended with the augmentation axis and with the
domain-specific insurance examples generalised — this runs against any client's
taxonomy, not one broker's.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services import llm

# The full range is allowed. An earlier version capped both axes at 80 on the grounds
# that nothing in real work is fully absorbed — there is always a hand-off, an exception,
# or someone accountable. That reasoning conflated two different things, and the cap was
# doing the wrong job:
#
#   - Some work genuinely is fully automatable. Transferring a figure between two systems
#     against a written rule has no residual human part, and a ceiling made that
#     unreachable by construction, so an agent could never be shown absorbing a task
#     outright however mechanical it was.
#   - The residual human part of automatable work is real, but it is *supervision*, and it
#     is now modelled explicitly: an agent specification carries its own oversight tasks
#     with time estimates, which Work Design adds back as visible task lines.
#
# So the cap has been replaced by a harder question in the prompt and a visible add-back
# downstream. Two mechanisms for one cost would understate every lever; see the prompt's
# note telling the model NOT to deduct for review time.
SCORE_MIN = 0
SCORE_MAX = 100

ACTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "definition": {"type": "string"},
                    "pct_of_task": {"type": "integer"},
                    "automation_pct": {"type": "integer"},
                    "augmentation_pct": {"type": "integer"},
                },
                "required": [
                    "name",
                    "definition",
                    "pct_of_task",
                    "automation_pct",
                    "augmentation_pct",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["actions"],
    "additionalProperties": False,
}
# NOTE: no `minimum`/`maximum` on the integers — this API rejects them outright, as
# the JE schema work already found. Ranges are enforced by validation below, which is
# where they would have to be checked anyway.

SYSTEM = (
    "You are a workforce analyst who decomposes work into its constituent actions "
    "and estimates, realistically and specifically, what current AI can do with "
    "each. You use UK English.\n\n"
    "You are given one cluster of tasks from a job architecture, with the real task "
    "names and descriptions that fall inside it. Return 3-5 actions: the concrete "
    "steps a person performs when doing this kind of work.\n\n"
    "name: 2-5 words, active voice, naming the step ('Drafting Response Letters', "
    "'Reconciling Account History'). One action per name — never join two with "
    "'and' or a slash.\n\n"
    "definition: one sentence, 15-30 words, concrete about what is worked on and "
    "produced. Ground it in the tasks you were given rather than describing the "
    "category in general terms.\n\n"
    "pct_of_task: integer share of the cluster's total effort this action takes. "
    "The actions MUST sum to exactly 100.\n\n"
    "automation_pct (0-100): the share of THIS action that AI systems available and "
    "deployable TODAY — a capable language model with access to the relevant systems "
    "and documents, or an agent built on one — would carry out unattended, to a "
    "standard accepted without a person redoing the work.\n\n"
    "augmentation_pct (0-100): how much faster a competent person is at THIS action "
    "with AI assistance, when the person stays in the loop and remains accountable "
    "for the outcome.\n\n"
    "The two scores are different questions and often diverge sharply. Reviewing a "
    "contract automates badly (a missed clause is a liability someone must answer "
    "for) and augments well (a model that flags every deviation from the standard "
    "form removes most of the reading). Chasing an outstanding document automates "
    "well and augments little, because there was never much thinking in it. Chairing "
    "a disciplinary hearing scores near zero on both.\n\n"
    "The test for a HIGH automation score is specific: would you be comfortable with "
    "NO PERSON LOOKING at the output before it takes effect? If someone must read every "
    "one, the action is moderate at best, however mechanical it looks.\n\n"
    "Do NOT deduct for the time a person spends reviewing, correcting or supervising "
    "the AI. That cost is estimated separately and added back elsewhere; subtracting it "
    "here as well would count it twice. Score what the AI does to the work itself.\n\n"
    "Calibration — be discriminating, and be sceptical. A set of actions all scored "
    "40-55 is a failed assessment: it tells the client nothing and is almost certainly "
    "wrong about both the easy and the hard parts. Err downwards on anything you are "
    "unsure about — an overstated number is worth less than nothing, because the client "
    "will find the one you cannot defend and stop believing the rest.\n"
    "NEAR ZERO (0-10): physical and manual work, face-to-face negotiation, holding a "
    "relationship, difficult conversations, judgement about people, and anything whose "
    "entire point is that a named person is accountable.\n"
    "LOW (10-30): regulated or personal advice, safety-critical decisions, work "
    "requiring context that exists only in someone's head or in a conversation.\n"
    "MODERATE (30-55): a clear method but real consequences if it is wrong, so a person "
    "reads every output before it counts. Most analytical and drafting work sits here.\n"
    "HIGH (55-85): mechanical transformation of known inputs — transferring data between "
    "systems, producing a document from a template and a record, checking one document "
    "against another, chasing, summarising, structured comparison, classification "
    "against a written rulebook.\n"
    "FULL (85-100): the action is deterministic, its inputs are complete and structured, "
    "the rule is written down, there is no judgement in it, and being wrong is cheap and "
    "self-evident. Issuing a certificate from a completed training record, sending a "
    "reminder when a date passes, moving a figure from one system to another, and "
    "generating a standing weekly report from a query all belong here — a person adds "
    "nothing by touching them, and pretending otherwise understates the case as badly as "
    "inflating the difficult work overstates it.\n"
    "Be sceptical about the difficult end, not about this one. Scepticism is for work "
    "involving judgement, accountability or people; it is not a reason to mark down work "
    "that is genuinely mechanical. A clerical action scored 45 because 'someone might "
    "want to look' is as wrong as a negotiation scored 70.\n"
    "Most actions in most organisations are LOW or MODERATE, and a minority are genuinely "
    "FULL. Use the whole range: an assessment where nothing exceeds 60 has almost "
    "certainly failed to identify the mechanical work, just as one where half the actions "
    "exceed 60 has failed to identify the hard work.\n"
    "Augmentation is usually higher than automation for analytical, drafting and "
    "research work, similar for routine administration, and near zero for physical "
    "or purely interpersonal work — AI cannot carry a box or hold a difficult "
    "conversation on your behalf."
)


@dataclass
class ClusterInput:
    """One task cluster, as put to the model.

    `tasks` are the real inferred task names and descriptions inside the cluster —
    the difference between the model scoring a label and scoring the actual work.
    """

    cluster_id: int
    name: str
    category: str
    domain: str
    tasks: list[tuple[str, str]] = field(default_factory=list)
    n_roles: int = 0
    # Share of a single job holder's time, summed over every role that does this
    # work. Included as context on how much of the organisation this touches.
    proportion_sum: float = 0.0

    def prompt(self, *, max_tasks: int = 14) -> str:
        shown = self.tasks[:max_tasks]
        lines = [
            f"TASK CLUSTER: {self.name}",
            f"Sits within: {self.domain} › {self.category}",
            f"Performed by {self.n_roles} role{'s' if self.n_roles != 1 else ''} "
            f"in this organisation.",
            "",
            f"The {len(self.tasks)} tasks in this cluster"
            + (f" (showing {len(shown)})" if len(shown) < len(self.tasks) else "")
            + ":",
        ]
        for name, desc in shown:
            lines.append(f"- {name}: {desc}" if desc else f"- {name}")
        lines.append(
            "\nDecompose this cluster into the actions that make it up, and score each."
        )
        return "\n".join(lines)


def cluster_inputs(state) -> list[ClusterInput]:
    """Every task cluster on the project, ready to assess, biggest first.

    Ordered by total time share so that a partial run — a `limit` for a live
    calibration check, or a threshold — assesses the work that most of the
    organisation actually spends its week on rather than an arbitrary slice.

    Task names are de-duplicated within a cluster: the same task inferred for
    fourteen roles is one piece of evidence about what the cluster is, and fourteen
    copies of it would crowd out the variety the model needs to find the real actions.
    """
    c = state.tasks.clustering
    if c is None:
        return []
    task_by_id = {t.id: t for t in state.tasks.inferred}
    out: dict[int, ClusterInput] = {}
    seen: dict[int, set[str]] = {}
    roles: dict[int, set[str]] = {}
    for a in c.assignments:
        t = task_by_id.get(a.item_id)
        if t is None:
            continue
        cid = a.final_profile_id
        ci = out.get(cid)
        if ci is None:
            ci = out[cid] = ClusterInput(
                cluster_id=cid,
                name=c.profile_names.get(cid, f"cluster {cid}"),
                category=c.category_names.get(a.final_category_id, "—"),
                domain=c.family_names.get(a.final_family_id, "—"),
            )
            seen[cid], roles[cid] = set(), set()
        key = t.name.strip().lower()
        if key not in seen[cid]:
            seen[cid].add(key)
            ci.tasks.append((t.name, t.description))
        ci.proportion_sum += t.proportion
        roles[cid].add(t.source_profile_key)
    for cid, ci in out.items():
        ci.n_roles = len(roles[cid])
        ci.proportion_sum = round(ci.proportion_sum, 2)
    return sorted(out.values(), key=lambda x: -x.proportion_sum)


@dataclass
class Action:
    name: str
    definition: str
    pct_of_task: float
    automation_pct: float
    augmentation_pct: float


@dataclass
class ClusterAssessment:
    cluster_id: int
    cluster_name: str
    actions: list[Action]
    raw_pct_sum: float
    clamped: bool
    attempts: int

    @property
    def automation_pct(self) -> float:
        return _weighted(self.actions, "automation_pct")

    @property
    def augmentation_pct(self) -> float:
        return _weighted(self.actions, "augmentation_pct")


def _weighted(actions: list[Action], attr: str) -> float:
    """Effort-weighted mean of an action score. The roll-up, in one line."""
    return round(
        sum(a.pct_of_task * getattr(a, attr) for a in actions) / 100.0, 1
    ) if actions else 0.0


class OpportunityError(RuntimeError):
    """The model returned nothing usable for this cluster, after retries."""


def _normalise_pcts(actions: list[Action]) -> float:
    """Rescale `pct_of_task` to sum to exactly 100. Returns the raw sum.

    Deterministic rather than retried, deliberately, and this is a considered
    deviation from the plan's "reject and retry" for both validations. Task inference
    already learned the lesson: a model asked for integers summing to 100 returns 97
    or 103 often enough that retrying is just paying twice for the same near-miss,
    and the sum is a guarantee code can simply provide. Out-of-range *scores* are a
    different matter — those are a calibration failure a retry can genuinely fix, so
    they are retried (see `assess_cluster`).

    The largest action absorbs the rounding residual so the total is exactly 100
    rather than 99.99, which matters once these are aggregated across a workforce.
    """
    raw = sum(a.pct_of_task for a in actions)
    if not actions or raw <= 0:
        return raw
    scale = 100.0 / raw
    for a in actions:
        a.pct_of_task = round(a.pct_of_task * scale, 2)
    residual = round(100.0 - sum(a.pct_of_task for a in actions), 2)
    if residual:
        largest = max(actions, key=lambda a: a.pct_of_task)
        largest.pct_of_task = round(largest.pct_of_task + residual, 2)
    return raw


def _parse(raw: dict) -> list[Action]:
    out: list[Action] = []
    for item in raw.get("actions", []) or []:
        name = str(item.get("name", "")).strip()
        if not name:
            continue

        def num(key: str) -> float:
            try:
                return float(item.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        out.append(
            Action(
                name=name,
                definition=str(item.get("definition", "")).strip(),
                pct_of_task=max(0.0, num("pct_of_task")),
                automation_pct=num("automation_pct"),
                augmentation_pct=num("augmentation_pct"),
            )
        )
    return out


def _out_of_range(actions: list[Action]) -> list[str]:
    bad: list[str] = []
    for a in actions:
        for attr in ("automation_pct", "augmentation_pct"):
            v = getattr(a, attr)
            if v < SCORE_MIN or v > SCORE_MAX:
                bad.append(f"{a.name}.{attr}={v:g}")
    return bad


def assess_cluster(inp: ClusterInput, *, attempts: int = 2) -> ClusterAssessment:
    """Assess one task cluster. One LLM call, or two if the first is out of range.

    A score outside 0-100 is rejected and the call repeated — that is now only reachable
    by a genuine mistake (a negative, or a number above 100) rather than by
    misunderstanding a house cap. If the repeat is also out of range the values are pulled
    into the band and the assessment is flagged `clamped`, rather than failing the cluster
    outright: one stubborn cluster should not cost a 750-cluster run, and a clamped score
    that is marked as clamped can be reviewed, whereas a missing one cannot.
    """
    prompt = inp.prompt()
    last_bad: list[str] = []
    for attempt in range(1, attempts + 1):
        p = prompt
        if last_bad:
            p += (
                "\n\nYour previous answer put these outside the permitted "
                f"{SCORE_MIN}-{SCORE_MAX} range: {', '.join(last_bad[:6])}. "
                "Both are percentages of a single action and must sit between "
                f"{SCORE_MIN} and {SCORE_MAX}."
            )
        raw = llm.complete_json(
            p,
            system=SYSTEM,
            json_schema=ACTIONS_SCHEMA,
            effort="medium",
            max_tokens=6000,
        )
        actions = _parse(raw)
        if not actions:
            last_bad = []
            continue
        bad = _out_of_range(actions)
        if bad and attempt < attempts:
            last_bad = bad
            continue

        clamped = bool(bad)
        if clamped:
            for a in actions:
                a.automation_pct = min(SCORE_MAX, max(float(SCORE_MIN), a.automation_pct))
                a.augmentation_pct = min(SCORE_MAX, max(float(SCORE_MIN), a.augmentation_pct))
        raw_sum = _normalise_pcts(actions)
        return ClusterAssessment(
            cluster_id=inp.cluster_id,
            cluster_name=inp.name,
            actions=actions,
            raw_pct_sum=round(raw_sum, 2),
            clamped=clamped,
            attempts=attempt,
        )
    raise OpportunityError(f"no usable actions returned for cluster {inp.cluster_id} ({inp.name})")


def assess_many(
    inputs: list[ClusterInput],
    *,
    workers: int = 8,
    progress=None,
) -> list[ClusterAssessment | None]:
    """Fan out across clusters, tolerating per-cluster failures.

    `tolerate_errors=True` so one cluster the model will not answer for does not
    discard the other 749. A credit, key or schema failure still stops the run and
    cancels the queue — `pmap` never tolerates `LLMRequestError`, which is what keeps
    "no credit" from reporting success with 750 quiet failures.
    """
    return llm.pmap(
        assess_cluster,
        inputs,
        workers=workers,
        label="opportunity",
        progress=progress,
        tolerate_errors=True,
    )


# ---------------------------------------------------------------------------
# Roll-ups — arithmetic, not judgement
# ---------------------------------------------------------------------------
@dataclass
class RoleOpportunity:
    """One role's opportunity, rolled up from its tasks.

    `coverage_pct` is the share of the role's time whose task cluster has actually
    been assessed. Without it a partially assessed role looks like a low-opportunity
    one, which is the sort of quiet lie that ends up in a client deck.
    """

    profile_key: str
    title: str
    headcount: int | None
    automation_pct: float
    augmentation_pct: float
    coverage_pct: float
    n_tasks: int
    fte_released: float | None
    hours_per_week: float | None


def role_opportunity(
    *,
    profile_key: str,
    title: str,
    headcount: int | None,
    tasks: list[tuple[float, int]],
    cluster_scores: dict[int, tuple[float, float]],
    hours_per_fte_week: float = 37.5,
) -> RoleOpportunity:
    """Roll task-cluster scores up to one role.

    `tasks` is (proportion of this role's time, task cluster id) per inferred task.
    A role's automation is the time-weighted mean of its clusters' automation, which
    reads directly as "this share of the job could be absorbed".

    Scores are weighted over *assessed* time only and `coverage_pct` reports how much
    that was. Weighting over the whole role would silently score unassessed time as
    zero opportunity, making an unfinished run look like a finding.
    """
    assessed = [(p, cluster_scores[c]) for p, c in tasks if c in cluster_scores]
    covered = sum(p for p, _ in assessed)
    total = sum(p for p, _ in tasks)
    if covered <= 0:
        return RoleOpportunity(
            profile_key=profile_key,
            title=title,
            headcount=headcount,
            automation_pct=0.0,
            augmentation_pct=0.0,
            coverage_pct=0.0,
            n_tasks=len(tasks),
            fte_released=None,
            hours_per_week=None,
        )
    automation = round(sum(p * s[0] for p, s in assessed) / covered, 1)
    augmentation = round(sum(p * s[1] for p, s in assessed) / covered, 1)
    fte = round(automation / 100.0 * headcount, 2) if headcount else None
    return RoleOpportunity(
        profile_key=profile_key,
        title=title,
        headcount=headcount,
        automation_pct=automation,
        augmentation_pct=augmentation,
        coverage_pct=round(100.0 * covered / total, 1) if total else 0.0,
        n_tasks=len(tasks),
        fte_released=fte,
        hours_per_week=round(fte * hours_per_fte_week, 1) if fte is not None else None,
    )


@dataclass
class Audit:
    assessed: int
    failed: int
    clamped: int
    retried: int
    max_pct_drift: float
    mean_automation: float
    mean_augmentation: float
    # The spread matters more than the mean: a run where every cluster lands between
    # 40 and 55 has not discriminated, whatever its average says.
    automation_p10: float
    automation_p90: float

    def summary(self) -> dict:
        return {
            "clusters_assessed": self.assessed,
            "clusters_failed": self.failed,
            "clamped": self.clamped,
            "retried": self.retried,
            "max_pct_drift": self.max_pct_drift,
            "mean_automation": self.mean_automation,
            "mean_augmentation": self.mean_augmentation,
            "automation_p10": self.automation_p10,
            "automation_p90": self.automation_p90,
            "discriminating": self.automation_p90 - self.automation_p10 >= 15,
        }


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return round(s[i], 1)


def audit(results: list[ClusterAssessment | None], *, requested: int) -> Audit:
    ok = [r for r in results if r is not None]
    autos = [r.automation_pct for r in ok]
    return Audit(
        assessed=len(ok),
        failed=requested - len(ok),
        clamped=sum(1 for r in ok if r.clamped),
        retried=sum(1 for r in ok if r.attempts > 1),
        max_pct_drift=round(max((abs(r.raw_pct_sum - 100.0) for r in ok), default=0.0), 2),
        mean_automation=round(sum(autos) / len(autos), 1) if autos else 0.0,
        mean_augmentation=round(
            sum(r.augmentation_pct for r in ok) / len(ok), 1
        ) if ok else 0.0,
        automation_p10=_pct(autos, 0.10),
        automation_p90=_pct(autos, 0.90),
    )


# ---------------------------------------------------------------------------
# Cost preview
# ---------------------------------------------------------------------------
# Measured shape of one call rather than a guess: the prompt is the system block
# (~950 tokens) plus a cluster's tasks (~250), and the response is 3-5 actions with
# adaptive thinking (~900). Rounded up, since a preview that under-promises spend is
# worse than one that over-promises it.
EST_INPUT_TOKENS = 1_300
EST_OUTPUT_TOKENS = 1_000
# claude-sonnet-5 list price, $/1M. Stated here rather than hidden in a calculation
# so it is obvious what needs updating when pricing moves.
PRICE_INPUT = 3.00
PRICE_OUTPUT = 15.00


def cost_estimate(n_clusters: int) -> dict:
    dollars = n_clusters * (
        EST_INPUT_TOKENS * PRICE_INPUT + EST_OUTPUT_TOKENS * PRICE_OUTPUT
    ) / 1_000_000
    return {
        "clusters": n_clusters,
        "calls": n_clusters,
        "est_input_tokens": n_clusters * EST_INPUT_TOKENS,
        "est_output_tokens": n_clusters * EST_OUTPUT_TOKENS,
        "est_usd": round(dollars, 2),
        "basis": (
            f"~{EST_INPUT_TOKENS} input + ~{EST_OUTPUT_TOKENS} output tokens per "
            f"cluster at ${PRICE_INPUT:.2f}/${PRICE_OUTPUT:.2f} per 1M"
        ),
    }
