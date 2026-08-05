"""Processes — Work Architecture Studio steps 2 and 4.

Step 2 takes an uploaded process document, infers its ordered steps, and maps each one
onto the task taxonomy. Step 4 assesses the process as-is and to-be.

**The honest limit, stated once here and again in the UI.** A process *diagram* yields
its step labels and whatever ordering the file happens to carry. It does not yield its
arrows: connector geometry would have to be matched back to boxes by coordinate, which
is a different and far less reliable problem. So the model reads labels and prose and
infers the sequence from them, which works well for a document that describes a flow
and less well for hand-drawn boxes with no text ordering. That is a property of the
input, not a defect to hide.

**Why steps get mapped to task clusters at all.** The whole point of Work Architecture Studio
is one work architecture. A process that floats free of the task taxonomy is a second,
disconnected picture of the same organisation. Mapping is by embedding similarity with
an LLM confirmation on the uncertain tail — the same stability-gated shape the
clustering engine uses, for the same reason: the geometry is right most of the time and
cheap, and the model is worth paying for only where it is not.

**Steps with no plausible task** are the interesting output, not an error. Work that
exists in a process but never appeared in any job description is exactly what
job-description-derived inference is blind to, so those become their own node type
rather than being forced into the nearest cluster.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services import llm

# Above this cosine the geometry is trusted on its own; below it the model is asked.
# 0.62 rather than the clustering gate's 0.58: a step-to-cluster match is a different
# comparison from item-to-peers, and being wrong here plants a step in the wrong part
# of the architecture rather than merely misfiling one task among thousands.
MATCH_GATE = 0.62
# Below this, nothing in the taxonomy is a plausible home and the step becomes an
# automated-task node. Set low deliberately: the claim "this work is invisible to the
# job descriptions" should require the taxonomy to be genuinely far away.
NO_MATCH_CEILING = 0.42

STEPS_SCHEMA = {
    "type": "object",
    "properties": {
        "process_name": {"type": "string"},
        "summary": {"type": "string"},
        "ordering_confidence": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "actor": {"type": "string"},
                    "system": {"type": "string"},
                    "automated": {"type": "boolean"},
                    "handoff": {"type": "boolean"},
                    "sign_off": {"type": "boolean"},
                },
                "required": [
                    "name",
                    "description",
                    "actor",
                    "system",
                    "automated",
                    "handoff",
                    "sign_off",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["process_name", "summary", "ordering_confidence", "steps"],
    "additionalProperties": False,
}

STEPS_SYSTEM = (
    "You read a process document and set out the process it describes as an ordered "
    "list of steps. You use UK English.\n\n"
    "The document may be a diagram exported as text, a written procedure, or a "
    "spreadsheet. Labels arrive in whatever order the file carried, which is often but "
    "not always the order of the process.\n\n"
    "process_name: the process, named as the organisation would name it.\n\n"
    "summary: two sentences on what the process achieves and where it starts and ends.\n\n"
    "ordering_confidence: 'high' if the document states the sequence explicitly, "
    "'medium' if you inferred it from wording or layout, 'low' if the labels carried "
    "no reliable order and you have arranged them by what makes sense. Say low when it "
    "is low — a confidently wrong sequence is worse than an admitted guess.\n\n"
    "steps: 6-20, in order. For each:\n"
    "  name: 3-6 words, the action taken.\n"
    "  description: one sentence on what happens and what it produces.\n"
    "  actor: the role or team that performs it, as the document names them. Use "
    "'unspecified' rather than inventing one.\n"
    "  system: the system or tool used, or 'none' where the step is a conversation, a "
    "decision or manual handling. Never invent a system the document does not mention.\n"
    "  automated: true only where the document indicates the step already runs without "
    "a person — an integration, a scheduled job, an existing bot. Not 'could be'.\n"
    "  handoff: true where work passes between teams, systems or people. Handoffs are "
    "where process time is lost, so they matter more than they look.\n"
    "  sign_off: true where an approval or authorisation is required to proceed.\n\n"
    "Describe only what the document supports. If a stage is referenced but not "
    "detailed, give it one step and say so in the description rather than inventing "
    "sub-steps."
)


@dataclass
class InferredStep:
    name: str
    description: str
    actor: str
    system: str
    automated: bool
    handoff: bool
    sign_off: bool
    sequence: int

    def embedding_text(self) -> str:
        return f"{self.name}. {self.description}"


@dataclass
class InferredProcess:
    process_name: str
    summary: str
    ordering_confidence: str
    steps: list[InferredStep] = field(default_factory=list)

    @property
    def manual_steps(self) -> int:
        return sum(1 for s in self.steps if not s.automated)

    @property
    def actors(self) -> list[str]:
        seen: list[str] = []
        for s in self.steps:
            a = s.actor.strip()
            if a and a.lower() != "unspecified" and a not in seen:
                seen.append(a)
        return seen


class ProcessError(RuntimeError):
    pass


def infer_process(text: str, *, filename: str, max_chars: int = 40_000) -> InferredProcess:
    """One call per document."""
    body = text[:max_chars]
    prompt = (
        f"DOCUMENT: {filename}\n"
        + (
            "\n(The document was longer than could be sent; this is the first part.)\n"
            if len(text) > max_chars
            else ""
        )
        + f"\n{body}\n\nSet out the process this document describes."
    )
    raw = llm.complete_json(
        prompt, system=STEPS_SYSTEM, json_schema=STEPS_SCHEMA, effort="medium", max_tokens=12_000
    )
    steps = []
    for i, s in enumerate(raw.get("steps") or []):
        name = str(s.get("name", "")).strip()
        if not name:
            continue
        steps.append(
            InferredStep(
                name=name,
                description=str(s.get("description", "")).strip(),
                actor=str(s.get("actor", "unspecified")).strip() or "unspecified",
                system=str(s.get("system", "none")).strip() or "none",
                automated=bool(s.get("automated")),
                handoff=bool(s.get("handoff")),
                sign_off=bool(s.get("sign_off")),
                sequence=len(steps) + 1,
            )
        )
    if not steps:
        raise ProcessError(f"no process steps could be inferred from {filename}")
    confidence = str(raw.get("ordering_confidence", "low")).lower()
    return InferredProcess(
        process_name=str(raw.get("process_name", "")).strip() or filename,
        summary=str(raw.get("summary", "")).strip(),
        ordering_confidence=confidence if confidence in ("high", "medium", "low") else "low",
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Mapping steps onto the task taxonomy
# ---------------------------------------------------------------------------
CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {
        "cluster_id": {"type": "integer"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
        "no_match": {"type": "boolean"},
    },
    "required": ["cluster_id", "confidence", "reasoning", "no_match"],
    "additionalProperties": False,
}

CONFIRM_SYSTEM = (
    "You decide which cluster of tasks a process step belongs to, choosing from a "
    "shortlist. You use UK English.\n\n"
    "Pick the cluster that describes the same work. Set `no_match` to true, with "
    "cluster_id -1, when none of them does — a process often contains work that no job "
    "description mentioned, and recording that honestly is more useful than forcing it "
    "into the closest-looking bucket.\n\n"
    "confidence is 0 to 1 and should reflect real uncertainty: a step that could "
    "reasonably sit in two of the shortlisted clusters is not a 0.9."
)


@dataclass
class StepMatch:
    sequence: int
    cluster_id: int | None
    cluster_name: str
    cosine: float
    routed_by_llm: bool = False
    confidence: float | None = None
    reasoning: str = ""

    @property
    def matched(self) -> bool:
        return self.cluster_id is not None


@dataclass
class ClusterCandidate:
    cluster_id: int
    name: str
    centroid: np.ndarray


def cluster_centroids(
    embeddings: np.ndarray, item_ids: list[str], assignments: dict[str, int], names: dict[int, str]
) -> list[ClusterCandidate]:
    """Unit-normalised mean vector per task cluster.

    Centroids rather than exemplars: a cluster's meaning is the middle of its members,
    and picking one member to stand for it makes the match depend on which one.
    """
    index = {item_id: i for i, item_id in enumerate(item_ids)}
    grouped: dict[int, list[int]] = {}
    for item_id, cid in assignments.items():
        row = index.get(item_id)
        if row is not None:
            grouped.setdefault(cid, []).append(row)
    out: list[ClusterCandidate] = []
    for cid, rows in grouped.items():
        v = embeddings[rows].mean(axis=0)
        norm = float(np.linalg.norm(v))
        if norm > 0:
            v = v / norm
        out.append(ClusterCandidate(cid, names.get(cid, str(cid)), v.astype(np.float32)))
    return out


def match_steps(
    steps: list[InferredStep],
    step_vectors: np.ndarray,
    candidates: list[ClusterCandidate],
    *,
    gate: float = MATCH_GATE,
    no_match_ceiling: float = NO_MATCH_CEILING,
    shortlist: int = 8,
    confirm=None,
) -> list[StepMatch]:
    """Geometry first, model only on the uncertain tail.

    Three outcomes per step, and the middle one is the only one that costs anything:
      - above the gate: the nearest cluster is taken as read.
      - below the no-match ceiling: nothing in the taxonomy is close, so the step is
        recorded as unmatched and becomes an automated-task node.
      - between: a shortlist goes to the model, which may still return no match.

    `confirm` is injected so the routing can be tested without a model.
    """
    if not candidates:
        return [
            StepMatch(s.sequence, None, "", 0.0, reasoning="the task taxonomy has no clusters")
            for s in steps
        ]

    matrix = np.vstack([c.centroid for c in candidates])
    out: list[StepMatch] = []
    for step, vec in zip(steps, step_vectors):
        norm = float(np.linalg.norm(vec))
        v = vec / norm if norm > 0 else vec
        sims = matrix @ v
        order = np.argsort(-sims)
        best = candidates[int(order[0])]
        best_sim = float(sims[int(order[0])])

        if best_sim >= gate:
            out.append(StepMatch(step.sequence, best.cluster_id, best.name, round(best_sim, 4)))
            continue
        if best_sim < no_match_ceiling:
            out.append(
                StepMatch(
                    step.sequence,
                    None,
                    "",
                    round(best_sim, 4),
                    reasoning=(
                        "no task cluster is close to this step — it is work the job "
                        "descriptions did not describe"
                    ),
                )
            )
            continue

        top = [candidates[int(i)] for i in order[:shortlist]]
        if confirm is None:
            # No model available: fall back to the geometry rather than dropping the
            # step, and say that is what happened.
            out.append(
                StepMatch(
                    step.sequence, best.cluster_id, best.name, round(best_sim, 4),
                    reasoning="taken from similarity alone; no confirmation was run",
                )
            )
            continue
        decision = confirm(step, top)
        cid = decision.get("cluster_id", -1)
        no_match = bool(decision.get("no_match")) or cid is None or int(cid) < 0
        chosen = next((c for c in top if c.cluster_id == int(cid)), None) if not no_match else None
        out.append(
            StepMatch(
                sequence=step.sequence,
                cluster_id=chosen.cluster_id if chosen else None,
                cluster_name=chosen.name if chosen else "",
                cosine=round(best_sim, 4),
                routed_by_llm=True,
                confidence=float(decision.get("confidence", 0.0) or 0.0),
                reasoning=str(decision.get("reasoning", "")),
            )
        )
    return out


def confirm_match(step: InferredStep, candidates: list[ClusterCandidate]) -> dict:
    lines = [
        f"PROCESS STEP: {step.name}",
        f"What happens: {step.description}",
        f"Performed by: {step.actor}   System: {step.system}",
        "",
        "Candidate task clusters:",
    ]
    for c in candidates:
        lines.append(f"[{c.cluster_id}] {c.name}")
    lines.append("\nWhich cluster is this the same work as, if any?")
    return llm.complete_json(
        "\n".join(lines),
        system=CONFIRM_SYSTEM,
        json_schema=CONFIRM_SCHEMA,
        effort="low",
        max_tokens=2_000,
    )


# ---------------------------------------------------------------------------
# Step 4 — as-is / to-be assessment
# ---------------------------------------------------------------------------
ASSESS_SCHEMA = {
    "type": "object",
    "properties": {
        "as_is_narrative": {"type": "string"},
        "to_be_narrative": {"type": "string"},
        "what_changes": {"type": "array", "items": {"type": "string"}},
        "to_be_steps": {"type": "integer"},
        "to_be_manual_touchpoints": {"type": "integer"},
        "to_be_actors": {"type": "integer"},
        "to_be_sign_offs": {"type": "integer"},
        "effort_reduction_pct": {"type": "integer"},
        "elapsed_reduction_pct": {"type": "integer"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "prerequisites": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "as_is_narrative",
        "to_be_narrative",
        "what_changes",
        "to_be_steps",
        "to_be_manual_touchpoints",
        "to_be_actors",
        "to_be_sign_offs",
        "effort_reduction_pct",
        "elapsed_reduction_pct",
        "risks",
        "prerequisites",
    ],
    "additionalProperties": False,
}

ASSESS_SYSTEM = (
    "You assess a business process for what AI and automation would change about it. "
    "You use UK English and you are specific to the process described.\n\n"
    "You are given the process as it runs today — its steps, who performs each, which "
    "are already automated, where work is handed off and where sign-off is required — "
    "together with the AI opportunity already assessed for the kind of work each step "
    "involves.\n\n"
    "as_is_narrative: 3-4 sentences on how the process runs now and where the effort "
    "and the delay actually sit. Name the steps.\n\n"
    "to_be_narrative: 3-4 sentences on how it would run with the automatable work "
    "absorbed. Be concrete about what a person still does.\n\n"
    "what_changes: 3-6 items, each naming a step and what happens to it — absorbed, "
    "assisted, merged with another, or unchanged.\n\n"
    "The to_be counts are the future state: how many steps remain, how many still need "
    "a person to touch them, how many distinct actors are involved, how many sign-offs "
    "are still required. Sign-offs should rarely fall to zero: an approval usually "
    "exists because somebody must be accountable, and automating the approver away is "
    "the change a client will refuse.\n\n"
    "effort_reduction_pct and elapsed_reduction_pct (0-70): the reduction in handler "
    "effort and in end-to-end elapsed time. They differ — removing a two-day queue "
    "between handoffs cuts elapsed time and almost no effort. Ceiling of 70 because a "
    "process retains coordination, exceptions and accountability.\n\n"
    "risks: 2-4 specific to this process, not generic AI risks.\n\n"
    "prerequisites: 2-4 things that must be true first — an integration, a data "
    "quality fix, a policy decision, a change of control."
)


@dataclass
class ProcessAssessment:
    as_is_narrative: str
    to_be_narrative: str
    what_changes: list[str]
    to_be_steps: int
    to_be_manual_touchpoints: int
    to_be_actors: int
    to_be_sign_offs: int
    effort_reduction_pct: float
    elapsed_reduction_pct: float
    risks: list[str]
    prerequisites: list[str]


def assess_process(
    process: InferredProcess,
    matches: list[StepMatch],
    cluster_scores: dict[int, tuple[float, float]],
) -> ProcessAssessment:
    """One call per process. The as-is counts are measured from the steps, not asked."""
    lines = [
        f"PROCESS: {process.process_name}",
        f"{process.summary}",
        "",
        f"As it runs today: {len(process.steps)} steps, "
        f"{process.manual_steps} needing a person, "
        f"{len(process.actors)} distinct actors, "
        f"{sum(1 for s in process.steps if s.sign_off)} sign-offs, "
        f"{sum(1 for s in process.steps if s.handoff)} handoffs.",
        "",
        "The steps:",
    ]
    by_seq = {m.sequence: m for m in matches}
    for s in process.steps:
        m = by_seq.get(s.sequence)
        score = cluster_scores.get(m.cluster_id) if m and m.cluster_id is not None else None
        flags = [
            "already automated" if s.automated else "manual",
            *(["handoff"] if s.handoff else []),
            *(["sign-off required"] if s.sign_off else []),
        ]
        opportunity = (
            f" — this kind of work is {score[0]:.0f}% automatable, {score[1]:.0f}% augmentable"
            if score
            else " — no task-level assessment for this work"
        )
        lines.append(
            f"{s.sequence}. {s.name} [{', '.join(flags)}] "
            f"actor: {s.actor}, system: {s.system}. {s.description}{opportunity}"
        )
    if process.ordering_confidence == "low":
        lines.append(
            "\nNote: the source document did not carry a reliable order, so the sequence "
            "above is inferred. Do not rely on adjacency."
        )
    lines.append("\nAssess the process as-is and to-be.")

    d = llm.complete_json(
        "\n".join(lines),
        system=ASSESS_SYSTEM,
        json_schema=ASSESS_SCHEMA,
        effort="high",
        max_tokens=10_000,
    )

    def clamp(key: str, ceiling: float = 70.0) -> float:
        try:
            return max(0.0, min(ceiling, float(d.get(key, 0) or 0)))
        except (TypeError, ValueError):
            return 0.0

    def count(key: str, ceiling: int) -> int:
        try:
            # Never more than today: a to-be with more steps than the as-is is the model
            # having lost track of which state it was describing.
            return max(0, min(ceiling, int(d.get(key, 0) or 0)))
        except (TypeError, ValueError):
            return 0

    return ProcessAssessment(
        as_is_narrative=str(d.get("as_is_narrative", "")).strip(),
        to_be_narrative=str(d.get("to_be_narrative", "")).strip(),
        what_changes=[str(x).strip() for x in (d.get("what_changes") or []) if str(x).strip()],
        to_be_steps=count("to_be_steps", len(process.steps)),
        to_be_manual_touchpoints=count("to_be_manual_touchpoints", process.manual_steps),
        to_be_actors=count("to_be_actors", max(1, len(process.actors))),
        to_be_sign_offs=count("to_be_sign_offs", sum(1 for s in process.steps if s.sign_off)),
        effort_reduction_pct=clamp("effort_reduction_pct"),
        elapsed_reduction_pct=clamp("elapsed_reduction_pct"),
        risks=[str(x).strip() for x in (d.get("risks") or []) if str(x).strip()],
        prerequisites=[str(x).strip() for x in (d.get("prerequisites") or []) if str(x).strip()],
    )
