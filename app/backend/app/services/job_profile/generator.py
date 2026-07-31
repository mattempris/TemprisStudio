"""Step 7a — generate Job Profile documents per job-profile cluster.

instructions.txt: "User triggers process to pass each job profile cluster to an
LLM via anthropic API to create Job Profile html documents".

The LLM returns structured JSON only; `render_html()` renders it through the
fixed Jinja2 skeleton. See the template's header comment for why.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import DEFAULTS_DIR
from app.services import llm

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "badges": {"type": "array", "items": {"type": "string"}},
        "level_context": {"type": ["string", "null"]},
        "about_role": {"type": "array", "items": {"type": "string"}},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "essential_skills": {"type": "array", "items": {"type": "string"}},
        "desirable_skills": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "responsibilities": {"type": "array", "items": {"type": "string"}},
        "contribution": {"type": "array", "items": {"type": "string"}},
        "required_of_you": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
                "required": ["label", "value"],
                "additionalProperties": False,
            },
        },
        "reporting_line": {"type": ["string", "null"]},
        "budget_responsibility": {"type": ["string", "null"]},
    },
    "required": [
        "title",
        "badges",
        "level_context",
        "about_role",
        "requirements",
        "essential_skills",
        "desirable_skills",
        "tags",
        "responsibilities",
        "contribution",
        "required_of_you",
        "reporting_line",
        "budget_responsibility",
    ],
    "additionalProperties": False,
}

SYSTEM = (
    "You write a consolidated Job Profile for a JOB PROFILE CLUSTER — a group of "
    "related roles that a job-architecture exercise has determined represent one "
    "profile. You are given the cluster's name, its place in the job "
    "architecture, and the normalised summaries of every role in it.\n\n"
    "Write ONE profile describing the shared role, at the level of generality "
    "appropriate to the whole cluster. Do not describe the members as separate "
    "roles and do not simply reproduce the largest member.\n\n"
    "Fields:\n"
    "- title: the profile's job title. Use the cluster name unless it reads "
    "awkwardly as a job title.\n"
    "- badges: 2-4 short topic tags for the header (e.g. 'Asset Planning', "
    "'TOTEX').\n"
    "- level_context: a brief seniority descriptor if the members clearly share "
    "one (e.g. 'Mid-senior level'); null if they span levels.\n"
    "- about_role: 2-3 paragraphs on purpose, scope and context.\n"
    "- requirements: minimum education/experience/background requirements.\n"
    "- essential_skills / desirable_skills: 3-5 each.\n"
    "- tags: 3-6 domain keywords.\n"
    "- responsibilities: 5-8 key accountabilities.\n"
    "- contribution: 3-4 statements of the effort and focus the role demands.\n"
    "- required_of_you: working-condition items as label/value pairs (e.g. "
    "label 'Travel', value 'Occasional regional travel'). Include only items the "
    "source material supports — omit rather than invent.\n"
    "- reporting_line / budget_responsibility: null unless the source indicates "
    "it. Do not invent an org structure or a budget figure.\n\n"
    "Write in neutral, professional, present-tense register. Do not include "
    "salary, benefits, closing dates, or recruitment logistics."
)


@dataclass
class ProfileGenerationInput:
    profile_key: str
    cluster_name: str
    family_name: str | None
    category_name: str | None
    # normalised member profiles: (purpose_statement, key_tasks, management_line, budget)
    members: list[tuple[str, list[str], str | None, str | None]] = field(default_factory=list)
    headcount: int | None = None


def _member_block(n: int, member: tuple[str, list[str], str | None, str | None]) -> str:
    purpose, tasks, mgmt, budget = member
    lines = [f"--- Role {n} ---", f"Purpose: {purpose}"]
    if tasks:
        lines.append("Key tasks: " + "; ".join(tasks))
    if mgmt:
        lines.append(f"Management/reporting: {mgmt}")
    if budget:
        lines.append(f"Budget responsibility: {budget}")
    return "\n".join(lines)


def build_prompt(spec: ProfileGenerationInput) -> str:
    location = " > ".join(p for p in [spec.family_name, spec.category_name, spec.cluster_name] if p)
    header = [
        f"Job profile cluster: {spec.cluster_name}",
        f"Position in job architecture: {location}",
        f"Roles in this cluster: {len(spec.members)}",
    ]
    if spec.headcount is not None:
        header.append(f"Total headcount across the cluster: {spec.headcount}")
    blocks = [_member_block(i, m) for i, m in enumerate(spec.members, start=1)]
    return "\n".join(header) + "\n\n" + "\n\n".join(blocks)


def generate_content(spec: ProfileGenerationInput) -> dict:
    """Returns the structured profile content dict (not HTML)."""
    result = llm.complete_json(
        build_prompt(spec),
        system=SYSTEM,
        json_schema=PROFILE_SCHEMA,
        effort="medium",
        max_tokens=8000,
    )
    # carry the architecture position into the content so the renderer can show
    # the breadcrumb without needing separate arguments
    result["family"] = spec.family_name
    result["category"] = spec.category_name
    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
_env: Environment | None = None


def _get_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(DEFAULTS_DIR)),
            autoescape=select_autoescape(["html", "j2"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _env


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "1d4ed8"  # fall back to the Tempris accent rather than emitting invalid CSS
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _darken(rgb: tuple[int, int, int], factor: float = 0.55) -> str:
    return "#%02x%02x%02x" % tuple(max(0, int(c * factor)) for c in rgb)


def render_html(
    content: dict,
    *,
    accent_color: str = "#1d4ed8",
    company_name: str | None = None,
    job_level: str | None = None,
    diversity_statement: str | None = None,
    about_company: str | None = None,
) -> str:
    """Render structured content into the themed HTML skeleton.

    `about_company` and `diversity_statement` are passed in rather than generated:
    they're organisation-level boilerplate the client supplies once, and having
    the model invent them would fabricate claims about a real company.
    """
    r, g, b = _hex_to_rgb(accent_color)
    ctx = dict(content)
    if about_company is not None:
        ctx["about_company"] = about_company
    if diversity_statement is not None:
        ctx["diversity_statement"] = diversity_statement

    template = _get_env().get_template("job_profile.html.j2")
    return template.render(
        content=ctx,
        company_name=company_name,
        job_level=job_level,
        accent=accent_color,
        accent_deep=_darken((r, g, b)),
        accent_soft=f"rgba({r}, {g}, {b}, 0.08)",
        accent_glow=f"rgba({r}, {g}, {b}, 0.7)",
        accent_border=f"rgba({r}, {g}, {b}, 0.35)",
        accent_border_soft=f"rgba({r}, {g}, {b}, 0.15)",
        accent_wash_a=f"rgba({r}, {g}, {b}, 0.03)",
        accent_wash_b=f"rgba({r}, {g}, {b}, 0.02)",
    )


def generate_many(
    specs: list[ProfileGenerationInput],
    *,
    workers: int = 6,
    progress=None,
) -> list[dict]:
    return llm.pmap(generate_content, specs, workers=workers, label="profile-gen", progress=progress)
