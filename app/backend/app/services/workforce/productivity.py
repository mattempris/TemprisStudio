"""Personal productivity — Workforce Studio step 5.

For one role, the tasks where a prompt helps that person most, and a downloadable
Claude Skill for each. The output is deliberately a file rather than a screen: the
whole point is that someone uploads it into Claude and uses it on Monday.

**Why augmentation, not automation.** The tasks ranked here are ordered by
`augmentation × share of the role's week` — how much of *this person's* time a good
prompt gives back. Step 6 ranks by automation, and the two lists come out in
genuinely different orders. Contract review is the standing example: it automates
badly because someone must own a missed clause, and augments well because a model
that flags every deviation from the standard form removes most of the reading. Rank
this list by automation and that task sinks, which is precisely backwards.

**Shape of the output** is ported from `Insurance Demo/report/data/skills.json`, which
is already essentially an Anthropic Skill: a kebab-case name, a one-sentence
description, a hook saying what you get, when to use it, when not to, and a body of
instructions. The `when_not_to_use` field does more work than it looks: it is where
the regulated, judgement-bearing and accountability boundaries get written down, and
a skill without it is the one that gets someone in trouble.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.services import llm

SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "hook": {"type": "string"},
        "when_to_use": {"type": "array", "items": {"type": "string"}},
        "when_not_to_use": {"type": "array", "items": {"type": "string"}},
        "body": {"type": "string"},
    },
    "required": ["name", "description", "hook", "when_to_use", "when_not_to_use", "body"],
    "additionalProperties": False,
}
# `when_to_use` / `when_not_to_use` are arrays here where the reference had one
# markdown string. The bullets are then rendered by code, so a model that forgets to
# write "- " does not produce a run-on paragraph in the middle of the file.

SYSTEM = (
    "You write Claude Skills: self-contained instruction files a working person "
    "uploads into Claude and then uses on a real task. You use UK English.\n\n"
    "You are given one task a specific role spends part of its week on, and the "
    "actions that task breaks into. Write ONE skill covering the part of that task "
    "where AI assistance genuinely helps, with the person still doing the job and "
    "owning the outcome.\n\n"
    "name: kebab-case, 3-6 words, starting with a verb — 'frame-renewal-strategy-"
    "brief', 'reconcile-daily-cash-position'. This becomes the filename.\n\n"
    "description: one sentence, 20-35 words, saying what the skill gathers and what "
    "it produces. Written for someone scanning a list of thirty skills.\n\n"
    "hook: one sentence starting 'You get…' naming the concrete artefact that comes "
    "out — a brief, a checklist, a drafted response, a reconciliation summary.\n\n"
    "when_to_use: 3-4 items, each a phrase the person would actually type or say. "
    "Real requests in their own words, not descriptions of situations.\n\n"
    "when_not_to_use: 3-4 items, each naming a boundary and why it exists. This is "
    "the most important field. Include the places where judgement, accountability, "
    "regulated advice, or a decision that must carry a named person's authority stop "
    "the skill from being appropriate — and the inputs that must be in hand first. "
    "Be specific to this work rather than writing generic caution.\n\n"
    "body: the instructions Claude follows, in markdown, 250-500 words. Structure it "
    "as: the inputs to ask the user for (a bulleted list, prompting conversationally "
    "for anything missing), then the output to produce with its numbered sections, "
    "then how to handle missing information — which is to list what is still needed "
    "explicitly rather than filling gaps with plausible content. Use the role's real "
    "vocabulary and name the real artefacts and systems from the task description. "
    "Never instruct Claude to invent facts, figures or citations.\n\n"
    "Write for this role and this task specifically. A skill that would suit any "
    "office job is a failed skill."
)


@dataclass
class SkillInput:
    """One (role, task cluster) pair to generate a skill for."""

    profile_key: str
    role_title: str
    task_cluster_id: int
    cluster_name: str
    domain: str
    category: str
    # The role's own task names in this cluster — what this person calls the work.
    task_names: list[str] = field(default_factory=list)
    task_descriptions: list[str] = field(default_factory=list)
    # From step 3, so the skill targets the actions that actually augment well.
    actions: list[tuple[str, str, float, float]] = field(default_factory=list)
    proportion: float = 0.0
    augmentation_pct: float = 0.0
    role_purpose: str = ""

    @property
    def rank_score(self) -> float:
        """Where a prompt helps this person most: how augmentable, weighted by how
        much of their week it is."""
        return round(self.proportion / 100.0 * self.augmentation_pct, 2)

    def prompt(self) -> str:
        lines = [
            f"ROLE: {self.role_title}",
        ]
        if self.role_purpose:
            lines.append(f"WHAT THE ROLE IS FOR: {self.role_purpose}")
        lines += [
            f"TASK: {self.cluster_name}  ({self.domain} › {self.category})",
            f"This task takes about {self.proportion:.0f}% of this role's working week.",
            "",
            "How this role describes the work:",
        ]
        for name, desc in zip(self.task_names, self.task_descriptions):
            lines.append(f"- {name}: {desc}" if desc else f"- {name}")
        if self.actions:
            lines += ["", "The actions within it, with how much AI assistance helps each:"]
            for name, definition, _auto, aug in sorted(self.actions, key=lambda a: -a[3]):
                lines.append(f"- {name} ({aug:.0f}% augmentable): {definition}")
            lines.append(
                "\nAim the skill at the actions with the highest augmentation. Where an "
                "action scores low, that is usually a boundary for when_not_to_use."
            )
        lines.append("\nWrite the skill.")
        return "\n".join(lines)


@dataclass
class GeneratedSkill:
    profile_key: str
    task_cluster_id: int
    name: str
    description: str
    hook: str
    when_to_use: list[str]
    when_not_to_use: list[str]
    body: str

    @property
    def filename(self) -> str:
        return f"{self.name}.md"


_KEBAB_STRIP = re.compile(r"[^a-z0-9]+")


def kebab(text: str) -> str:
    """A safe, stable filename stem. Also the skill's identity in Claude."""
    slug = _KEBAB_STRIP.sub("-", text.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug) or "skill"


_H2_OR_HIGHER = re.compile(r"^(#{1,2})(?=\s)", re.MULTILINE)


def _demote_headings(body: str) -> str:
    """Push the body's own headings below the file's section headings.

    The model reliably writes `## Inputs to ask for` inside the body, which lands at
    the same level as the `## Instructions` heading wrapping it and flattens the
    structure. Fixed here rather than in the prompt: heading level is exactly the kind
    of instruction a model drops when it is concentrating on content, and the
    correction is one regex.
    """
    return _H2_OR_HIGHER.sub(lambda m: "#" * (len(m.group(1)) + 1), body)


def to_markdown(skill: GeneratedSkill) -> str:
    """The downloadable artefact: YAML frontmatter plus the instruction body.

    Frontmatter scalars are emitted as JSON strings, which are valid double-quoted
    YAML. It looks fussy next to a bare `description: Gathers…`, but a colon or a
    quote anywhere in a model-written sentence produces a file that will not parse,
    and this file is going somewhere that parses it.
    """
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {i.lstrip('- ').strip()}" for i in items if i.strip())

    return (
        "---\n"
        f"name: {json.dumps(skill.name)}\n"
        f"description: {json.dumps(skill.description)}\n"
        "---\n\n"
        f"{skill.hook}\n\n"
        "## When to use this\n\n"
        f"{bullets(skill.when_to_use)}\n\n"
        "## When not to use this\n\n"
        f"{bullets(skill.when_not_to_use)}\n\n"
        "## Instructions\n\n"
        f"{_demote_headings(skill.body.strip())}\n"
    )


class SkillError(RuntimeError):
    pass


def generate_skill(inp: SkillInput) -> GeneratedSkill:
    raw = llm.complete_json(
        inp.prompt(),
        system=SYSTEM,
        json_schema=SKILL_SCHEMA,
        effort="medium",
        max_tokens=8000,
    )
    name = kebab(str(raw.get("name", "")).strip())
    body = str(raw.get("body", "")).strip()
    if not name or not body:
        raise SkillError(f"no usable skill returned for {inp.cluster_name} / {inp.role_title}")

    def strings(key: str) -> list[str]:
        value = raw.get(key) or []
        if isinstance(value, str):
            # Tolerated rather than rejected: the field is a list in the schema, but a
            # markdown bullet string is the same information and a retry would cost
            # more than splitting it.
            return [ln for ln in (l.strip() for l in value.splitlines()) if ln]
        return [str(v).strip() for v in value if str(v).strip()]

    return GeneratedSkill(
        profile_key=inp.profile_key,
        task_cluster_id=inp.task_cluster_id,
        name=name,
        description=str(raw.get("description", "")).strip(),
        hook=str(raw.get("hook", "")).strip(),
        when_to_use=strings("when_to_use"),
        when_not_to_use=strings("when_not_to_use"),
        body=body,
    )


def generate_many(
    inputs: list[SkillInput], *, workers: int = 8, progress=None
) -> list[GeneratedSkill | None]:
    return llm.pmap(
        generate_skill,
        inputs,
        workers=workers,
        label="skills",
        progress=progress,
        tolerate_errors=True,
    )


def dedupe_names(
    skills: list[GeneratedSkill], taken: set[str]
) -> list[GeneratedSkill]:
    """Make filenames unique within a role.

    Two tasks in the same role can legitimately produce the same skill name, and the
    second would silently overwrite the first — the file is keyed by name. Suffixed
    rather than regenerated: the content differs even where the name collided.
    """
    for s in skills:
        if s.name not in taken:
            taken.add(s.name)
            continue
        n = 2
        while f"{s.name}-{n}" in taken:
            n += 1
        s.name = f"{s.name}-{n}"
        taken.add(s.name)
    return skills


# One call per (role, task). Output is the long part — a 250-500 word body plus the
# framing — so the ratio is the other way round from step 3.
EST_INPUT_TOKENS = 1_100
EST_OUTPUT_TOKENS = 1_800
PRICE_INPUT = 3.00
PRICE_OUTPUT = 15.00


def cost_estimate(n: int) -> dict:
    dollars = n * (EST_INPUT_TOKENS * PRICE_INPUT + EST_OUTPUT_TOKENS * PRICE_OUTPUT) / 1_000_000
    return {
        "skills": n,
        "calls": n,
        "est_usd": round(dollars, 2),
        "basis": (
            f"~{EST_INPUT_TOKENS} input + ~{EST_OUTPUT_TOKENS} output tokens per skill "
            f"at ${PRICE_INPUT:.2f}/${PRICE_OUTPUT:.2f} per 1M"
        ),
    }
