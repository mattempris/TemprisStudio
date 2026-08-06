"""Step 10 — infer tasks per job profile, with time proportions.

instructions.txt: "perform steps 8/9/4/5/6 for Tasks (Task name 2-4 words as
standard, Proportion of job estimate that sums to 100% for each job) using
taskQWEN embeddings model."

So this mirrors the skills flow, with one extra constraint that needs real
handling rather than trust: the proportions for a single job must sum to 100%.
LLMs reliably produce sets that sum to 97 or 103, so `normalize_proportions()`
rescales deterministically and reports how far off the model was — the guarantee
comes from code, the estimate from the model.

Unlike skills, tasks ARE activities, so the attribute-vs-task steer that matters
in skills/inference.py is deliberately absent here.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services import llm

TASKS_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "proportion": {"type": "number"},
                },
                "required": ["name", "description", "proportion"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tasks"],
    "additionalProperties": False,
}

SYSTEM = (
    "You break a job down into the distinct tasks that make up the work, and "
    "estimate how the job holder's time divides across them.\n\n"
    "Return 5-12 tasks covering the whole job.\n\n"
    "name: 2-4 words naming the activity (e.g. 'Asset Plan Development', "
    "'Stakeholder Reporting', 'Incident Response'). These are activities, so "
    "verb-noun or noun-noun phrasing is correct here.\n\n"
    "ONE ACTIVITY PER TASK. Never join two activities with 'and', '&' or a slash: "
    "'Design & Deliver Training' is two tasks, not one — 'Training Design' and "
    "'Training Delivery' — and they carry different proportions of the week, need "
    "different skills, and are automatable to different degrees. If a name needs a "
    "conjunction to be accurate, split it and give each part its own proportion. "
    "The only acceptable use of 'and' is inside a single indivisible artefact name "
    "(e.g. 'Health and Safety Inspection'), where the two words name one thing "
    "rather than two activities.\n\n"
    "description: 15-35 words on what the task actually involves.\n\n"
    "proportion: the percentage of the job holder's working time this task "
    "consumes. The proportions across all tasks MUST sum to exactly 100. Weight "
    "them realistically — most jobs have two or three tasks that dominate rather "
    "than an even split, and a task worth less than about 3% is usually better "
    "merged into a neighbouring one.\n\n"
    "Cover the whole job: every significant activity should appear, and the set "
    "should account for a full working week including routine and administrative "
    "work, not only the headline responsibilities."
)


@dataclass
class InferredTask:
    name: str
    description: str
    proportion: float
    source_profile_key: str

    def embedding_text(self) -> str:
        return f"{self.name}. {self.description}"


@dataclass
class ProportionFix:
    """How far the model's raw proportions were from summing to 100."""

    profile_key: str
    raw_sum: float
    adjusted: bool

    @property
    def drift(self) -> float:
        return abs(self.raw_sum - 100.0)


def normalize_proportions(
    tasks: list[InferredTask], profile_key: str
) -> tuple[list[InferredTask], ProportionFix]:
    """Rescale a job's task proportions to sum to exactly 100.

    The spec requires the sum, so it's enforced here rather than hoped for. The
    largest task absorbs any residual rounding so the total is exact rather than
    99.99, which matters once these are aggregated across a workforce.
    """
    raw_sum = sum(t.proportion for t in tasks)
    if not tasks or raw_sum <= 0:
        return tasks, ProportionFix(profile_key, raw_sum, adjusted=False)

    scale = 100.0 / raw_sum
    for t in tasks:
        t.proportion = round(t.proportion * scale, 2)

    residual = round(100.0 - sum(t.proportion for t in tasks), 2)
    if residual:
        largest = max(tasks, key=lambda t: t.proportion)
        largest.proportion = round(largest.proportion + residual, 2)

    return tasks, ProportionFix(profile_key, raw_sum, adjusted=abs(raw_sum - 100.0) > 0.01)


@dataclass
class TaskAudit:
    total: int
    name_out_of_range: list[str]
    proportion_fixes: list[ProportionFix]

    @property
    def max_drift(self) -> float:
        return max((f.drift for f in self.proportion_fixes), default=0.0)

    def summary(self) -> dict:
        return {
            "tasks": self.total,
            "name_out_of_range": len(self.name_out_of_range),
            "jobs_needing_proportion_fix": sum(1 for f in self.proportion_fixes if f.adjusted),
            "max_proportion_drift": round(self.max_drift, 2),
        }


def audit_tasks(tasks: list[InferredTask], fixes: list[ProportionFix]) -> TaskAudit:
    bad_names = [t.name for t in tasks if not (2 <= len(t.name.split()) <= 4)]
    return TaskAudit(total=len(tasks), name_out_of_range=bad_names, proportion_fixes=fixes)


def _profile_prompt(title: str, content: dict, source_text: str | None = None) -> str:
    """The prompt for one anchor role.

    `source_text` is the uploaded job description, present only when this anchor role stands for
    exactly one uploaded record (see `services/provenance`). When it is there it REPLACES the
    generated document, because the document is two model calls downstream of it and every hop
    compresses — a description listing twenty-five responsibilities is already about eight
    phrases by the time the document is written. Passing both would invite the same piece of work
    to be counted twice, once from the summary and once from the source, which matters more here
    than for skills: task proportions must sum to 100, so a double-counted duty distorts every
    other task's share of the week.

    The title still comes from the anchor role rather than the source, because that is the
    confirmed name the taxonomy is keyed on.
    """
    parts = [f"Job profile: {title}\n"]

    if source_text:
        # See the twin of this in skills/inference: the guard lives in the user prompt because it
        # is only true when a source description is in play, and SYSTEM is the cached half.
        #
        # It matters more here than for skills. Proportions must sum to 100, so a benefits
        # paragraph read as a duty does not just add a spurious task — it takes a share of the
        # week away from every real one.
        parts.append(
            "The job description as supplied by the organisation. It is the source document, so "
            "it is fuller than a summary — but it is also written to attract candidates. Read "
            "only the substance of the role. Ignore recruitment framing, the employer's name, "
            "benefits, pay, location, working patterns, culture and diversity statements, and "
            "anything about the application process: none of those are work the job holder "
            "does, and none should receive a share of the week.\n\n"
            + source_text.strip()
        )
        return "\n\n".join(parts)

    def add(label: str, value) -> None:
        if not value:
            return
        if isinstance(value, list):
            if value and isinstance(value[0], dict):
                items = "; ".join(f"{i.get('label')}: {i.get('value')}" for i in value)
            else:
                items = "; ".join(str(v) for v in value)
            parts.append(f"{label}: {items}")
        else:
            parts.append(f"{label}: {value}")

    add("About the role", content.get("about_role"))
    add("Key responsibilities", content.get("responsibilities"))
    add("Effort and focus", content.get("contribution"))
    add("Working conditions", content.get("required_of_you"))
    add("Reporting line", content.get("reporting_line"))
    return "\n\n".join(parts)


def infer_for_profile(
    profile_key: str, title: str, content: dict, source_text: str | None = None
) -> tuple[list[InferredTask], ProportionFix]:
    result = llm.complete_json(
        _profile_prompt(title, content, source_text),
        system=SYSTEM,
        json_schema=TASKS_SCHEMA,
        effort="low",
        max_tokens=8000,
    )
    tasks: list[InferredTask] = []
    for raw in result.get("tasks", []):
        name = str(raw.get("name", "")).strip()
        desc = str(raw.get("description", "")).strip()
        if not name:
            continue
        try:
            proportion = float(raw.get("proportion", 0))
        except (TypeError, ValueError):
            proportion = 0.0
        tasks.append(
            InferredTask(
                name=name,
                description=desc,
                proportion=max(0.0, proportion),
                source_profile_key=profile_key,
            )
        )
    return normalize_proportions(tasks, profile_key)


def infer_many(
    # (profile_key, title, content, source_text or None)
    profiles: list[tuple[str, str, dict, str | None]],
    *,
    workers: int = 8,
    progress=None,
) -> list[tuple[list[InferredTask], ProportionFix]]:
    return llm.pmap(
        lambda p: infer_for_profile(p[0], p[1], p[2], p[3] if len(p) > 3 else None),
        profiles,
        workers=workers,
        label="tasks",
        progress=progress,
    )
