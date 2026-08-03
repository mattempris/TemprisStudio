"""Future role design — Workforce Studio step 7.

Per role: what it is today, what it becomes once the automatable work is absorbed, and
what the person should be getting better at. The output the instructions ask for is a
narrative in three movements plus the concrete lists behind it.

**The field that stops this being a deskilling document.** `deliberate_practice` names
what the person should keep doing by hand and why. A role redesign that hands every
routine judgement to an agent produces someone who cannot check the agent's work in two
years, and that is a real and well-understood failure of AI-assisted work rather than a
theoretical one. The prompt asks for it explicitly and the schema requires it.

**Absorbed does not mean gone.** The absorbed list is drawn from the role's own tasks
weighted by their automation score, so it says "this share of your week changes shape",
not "your job is 40% deleted". The narrative is instructed accordingly — a redesign a
person would recognise as theirs is more useful than one that reads as a redundancy
case.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services import llm

FUTURE_ROLE_SCHEMA = {
    "type": "object",
    "properties": {
        "evolution_today": {"type": "string"},
        "evolution_after_automation": {"type": "string"},
        "evolution_future": {"type": "string"},
        "future_purpose": {"type": "string"},
        "future_responsibilities": {"type": "array", "items": {"type": "string"}},
        "deepened_tasks": {"type": "array", "items": {"type": "string"}},
        "skills_to_build": {"type": "array", "items": {"type": "string"}},
        "deliberate_practice": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "evolution_today",
        "evolution_after_automation",
        "evolution_future",
        "future_purpose",
        "future_responsibilities",
        "deepened_tasks",
        "skills_to_build",
        "deliberate_practice",
    ],
    "additionalProperties": False,
}

SYSTEM = (
    "You redesign a role around AI, for the person who holds it. You use UK English "
    "and you write about this role specifically, never about roles in general.\n\n"
    "You are given the role, how its week divides across tasks, and how automatable "
    "and augmentable each of those tasks is.\n\n"
    "evolution_today: 2-3 sentences on how the week is spent now, naming the tasks that "
    "dominate it.\n\n"
    "evolution_after_automation: 2-3 sentences on what changes first — the routine, "
    "high-automation work, and what that frees.\n\n"
    "evolution_future: 2-3 sentences on what the role becomes: where the person's time "
    "goes and what they are accountable for that they were not before.\n\n"
    "future_purpose: one sentence. The role's reason to exist, restated for the future "
    "state.\n\n"
    "future_responsibilities: 4-6 items. What the person owns. Written as "
    "responsibilities a manager could put in a job description.\n\n"
    "deepened_tasks: 3-5 of the role's own tasks that grow rather than shrink — the "
    "judgement, relationship and accountability work that AI cannot take and that the "
    "person now has more room for. Use the task names you were given.\n\n"
    "skills_to_build: 4-6 specific capabilities, including how to direct and check AI "
    "output in this domain. Not 'AI literacy' — say what they must be able to do.\n\n"
    "deliberate_practice: 3-4 items naming work the person should keep doing by hand, "
    "and why. This matters: someone who hands every routine judgement to an agent "
    "cannot review that agent's work two years later. Be concrete about what to keep "
    "sharp and how often.\n\n"
    "Write a redesign the person would recognise as their own job, made better. Do not "
    "write a redundancy case, do not imply the role shrinks, and do not promise a "
    "saving — the freed time goes somewhere and you should say where."
)


@dataclass
class FutureRoleInput:
    profile_key: str
    title: str
    purpose: str = ""
    automation_pct: float = 0.0
    augmentation_pct: float = 0.0
    # (task cluster name, share of week, automation, augmentation)
    tasks: list[tuple[str, float, float, float]] = field(default_factory=list)
    # Agents already specified against this role's task clusters.
    agents: list[str] = field(default_factory=list)
    strategic_context: str = ""

    @property
    def absorbed(self) -> list[str]:
        """Tasks where most of the work is automatable — what changes shape first."""
        return [name for name, _p, auto, _aug in self.tasks if auto >= 40.0]

    @property
    def time_released_pct(self) -> float:
        """Share of the week the automatable portion of each task adds up to."""
        return round(sum(p / 100.0 * auto for _n, p, auto, _aug in self.tasks), 1)

    def prompt(self) -> str:
        lines = [f"ROLE: {self.title}"]
        if self.purpose:
            lines.append(f"WHAT THE ROLE IS FOR: {self.purpose}")
        lines += [
            f"Overall {self.automation_pct:.0f}% of the week is automatable and "
            f"{self.augmentation_pct:.0f}% is augmentable.",
            "",
            "How the week divides, with how automatable and augmentable each task is:",
        ]
        for name, prop, auto, aug in sorted(self.tasks, key=lambda t: -t[1]):
            lines.append(f"- {name}: {prop:.0f}% of the week, {auto:.0f}% automatable, {aug:.0f}% augmentable")
        if self.agents:
            lines += ["", "Agents already specified against this role's work:"]
            lines += [f"- {a}" for a in self.agents]
        lines.append("\nRedesign the role.")
        return "\n".join(lines)


@dataclass
class FutureRole:
    profile_key: str
    title: str
    evolution_today: str
    evolution_after_automation: str
    evolution_future: str
    future_purpose: str
    future_responsibilities: list[str]
    absorbed_tasks: list[str]
    deepened_tasks: list[str]
    skills_to_build: list[str]
    deliberate_practice: list[str]
    automation_pct: float
    time_released_pct: float


class FutureRoleError(RuntimeError):
    pass


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [ln for ln in (l.strip("- ").strip() for l in value.splitlines()) if ln]
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def design_role(inp: FutureRoleInput) -> FutureRole:
    """One call per role.

    `strategic_context` — how the organisation wants freed-up time used — is passed as a
    cache prefix: it is identical across every role in a run, so it is written once and
    read back cheaply for the rest of the fan-out.
    """
    d = llm.complete_json(
        inp.prompt(),
        system=SYSTEM,
        cache_prefix=inp.strategic_context or None,
        json_schema=FUTURE_ROLE_SCHEMA,
        effort="high",
        max_tokens=10_000,
    )
    practice = _strings(d.get("deliberate_practice"))
    responsibilities = _strings(d.get("future_responsibilities"))
    if not responsibilities:
        raise FutureRoleError(f"no future responsibilities returned for {inp.title}")
    if not practice:
        # Not fatal, but recorded as a gap rather than silently absent: the whole point
        # of the field is that a redesign without it deskills the role.
        practice = [
            "Not specified by the model for this role — decide what this person should "
            "keep doing by hand before adopting the redesign."
        ]
    return FutureRole(
        profile_key=inp.profile_key,
        title=inp.title,
        evolution_today=str(d.get("evolution_today", "")).strip(),
        evolution_after_automation=str(d.get("evolution_after_automation", "")).strip(),
        evolution_future=str(d.get("evolution_future", "")).strip(),
        future_purpose=str(d.get("future_purpose", "")).strip(),
        future_responsibilities=responsibilities,
        absorbed_tasks=inp.absorbed,
        deepened_tasks=_strings(d.get("deepened_tasks")),
        skills_to_build=_strings(d.get("skills_to_build")),
        deliberate_practice=practice,
        automation_pct=inp.automation_pct,
        time_released_pct=inp.time_released_pct,
    )


def design_many(
    inputs: list[FutureRoleInput], *, workers: int = 6, progress=None
) -> list[FutureRole | None]:
    return llm.pmap(
        design_role, inputs, workers=workers, label="future-roles", progress=progress,
        tolerate_errors=True,
    )


EST_INPUT_TOKENS = 1_200
EST_OUTPUT_TOKENS = 2_200
PRICE_INPUT = 3.00
PRICE_OUTPUT = 15.00


def cost_estimate(n: int) -> dict:
    dollars = n * (EST_INPUT_TOKENS * PRICE_INPUT + EST_OUTPUT_TOKENS * PRICE_OUTPUT) / 1_000_000
    return {
        "roles": n,
        "calls": n,
        "est_usd": round(dollars, 2),
        "basis": (
            f"~{EST_INPUT_TOKENS} input + ~{EST_OUTPUT_TOKENS} output tokens per role "
            f"at ${PRICE_INPUT:.2f}/${PRICE_OUTPUT:.2f} per 1M"
        ),
    }
