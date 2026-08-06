"""Step 8 — infer skills per job profile.

instructions.txt: "User can infer 5-10 technical and/or non-technical skills per
job profile from a selected subset (or all) job profiles. This is achieved via
job by job API calls to a performant yet value LLM. Skill names should be 1-3
words as standard. Skill descriptions should be 15-30 words. Where possible,
steer away from task/verb/responsibility style language and focus on attributes
the job holder must have to perform well."

That last constraint is the interesting one and is easy to get wrong: an LLM asked
for "skills" from a job description will happily return the responsibilities back
as gerunds ("Managing stakeholder relationships"). The prompt below contrasts that
explicitly against attribute phrasing, and `audit_skill()` measures it after the
fact so drift is visible rather than silently accepted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services import llm

SKILLS_SCHEMA = {
    "type": "object",
    "properties": {
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "kind": {"type": "string", "enum": ["technical", "non-technical"]},
                },
                "required": ["name", "description", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["skills"],
    "additionalProperties": False,
}

SYSTEM = (
    "You identify the skills a job holder must possess to perform a role well.\n\n"
    "Return 5-10 skills, a mix of technical and non-technical as the role "
    "warrants.\n\n"
    "name: 1-3 words. A noun phrase naming the capability itself, in the form it "
    "would appear in a skills taxonomy.\n\n"
    "description: 15-30 words describing what having this skill means — the "
    "depth, judgement or knowledge involved.\n\n"
    "CRITICAL — describe ATTRIBUTES, not TASKS. A skill is something a person "
    "HAS, not something the job DOES. Do not restate the role's "
    "responsibilities as skills.\n"
    "  Wrong (a task): 'Managing Stakeholder Relationships' / 'Reporting on "
    "programme performance' / 'Developing asset plans'\n"
    "  Right (an attribute): 'Stakeholder Influence' / 'Commercial Judgement' / "
    "'Asset Lifecycle Modelling'\n"
    "Prefer nouns and noun phrases. Avoid starting a name with a gerund "
    "('-ing') or an imperative verb.\n\n"
    "kind: 'technical' for domain/tool/method expertise, 'non-technical' for "
    "behavioural, interpersonal and judgement capabilities."
)


@dataclass
class InferredSkill:
    name: str
    description: str
    kind: str
    source_profile_key: str

    def embedding_text(self) -> str:
        return f"{self.name}. {self.description}"


# Names that read as activities rather than capabilities. Used for auditing, not
# rejection — a legitimate skill can occasionally contain one of these.
_GERUND_START = re.compile(r"^\s*\w+ing\b", re.IGNORECASE)
_TASKY_WORDS = {
    "managing", "reporting", "developing", "delivering", "ensuring", "supporting",
    "coordinating", "monitoring", "maintaining", "implementing", "producing",
    "conducting", "performing", "undertaking", "liaising", "overseeing",
}


@dataclass
class SkillAudit:
    total: int
    name_too_long: list[str]
    description_out_of_range: list[str]
    task_phrased: list[str]

    @property
    def task_phrased_pct(self) -> float:
        return 100.0 * len(self.task_phrased) / self.total if self.total else 0.0

    def summary(self) -> dict:
        return {
            "skills": self.total,
            "name_too_long": len(self.name_too_long),
            "description_out_of_range": len(self.description_out_of_range),
            "task_phrased": len(self.task_phrased),
            "task_phrased_pct": round(self.task_phrased_pct, 1),
        }


def audit_skills(skills: list[InferredSkill]) -> SkillAudit:
    """Check the spec's own constraints so violations surface instead of shipping.

    Deliberately reports rather than raises: a couple of borderline names is
    normal and the user reviews the taxonomy anyway. A high task_phrased_pct is
    the signal that the prompt has drifted back to restating responsibilities.
    """
    too_long, bad_desc, tasky = [], [], []
    for s in skills:
        words = s.name.split()
        if len(words) > 3:
            too_long.append(s.name)
        desc_words = len(s.description.split())
        if not (15 <= desc_words <= 30):
            bad_desc.append(f"{s.name} ({desc_words}w)")
        first = words[0].lower() if words else ""
        if first in _TASKY_WORDS or _GERUND_START.match(s.name):
            tasky.append(s.name)
    return SkillAudit(len(skills), too_long, bad_desc, tasky)


def _profile_prompt(title: str, content: dict, source_text: str | None = None) -> str:
    """The prompt for one anchor role.

    `source_text` is the uploaded job description, present only when this anchor role stands for
    exactly one uploaded record (see `services/provenance`). When it is there it REPLACES the
    generated document, because the document is two model calls downstream of it and every hop
    compresses — a description listing twenty-five responsibilities is already about eight
    phrases by the time the document is written. Passing both would invite the same
    responsibility to be counted twice, once from the summary and once from the source.

    The title still comes from the anchor role rather than the source, because that is the
    confirmed name the taxonomy is keyed on.
    """
    parts = [f"Job profile: {title}\n"]

    if source_text:
        # The guard goes here rather than in SYSTEM for two reasons: it is only true when a source
        # description is in play, and SYSTEM is the cached half of the request — moving it there
        # would invalidate the cache for the document path too.
        #
        # It is needed because this text has only been through the extractive strip step, which
        # removes but never rewrites. Real examples on the demo project still open "Join us as a
        # Test/Software Engineer Lead at Barclays" and carry benefits and hybrid-working blurb.
        # The document path never saw any of that, because normalise and document generation had
        # already rewritten it into neutral register.
        parts.append(
            "The job description as supplied by the organisation. It is the source document, so "
            "it is fuller than a summary — but it is also written to attract candidates. Read "
            "only the substance of the role. Ignore recruitment framing, the employer's name, "
            "benefits, pay, location, working patterns, culture and diversity statements, and "
            "anything about the application process: none of those describe the work.\n\n"
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
    add("Minimum requirements", content.get("requirements"))
    add("Essential skills stated in the profile", content.get("essential_skills"))
    add("Desirable skills stated in the profile", content.get("desirable_skills"))
    add("Reporting line", content.get("reporting_line"))
    add("Budget responsibility", content.get("budget_responsibility"))
    return "\n\n".join(parts)


def infer_for_profile(
    profile_key: str, title: str, content: dict, source_text: str | None = None
) -> list[InferredSkill]:
    result = llm.complete_json(
        _profile_prompt(title, content, source_text),
        system=SYSTEM,
        json_schema=SKILLS_SCHEMA,
        effort="low",
        max_tokens=8000,
    )
    out: list[InferredSkill] = []
    for raw in result.get("skills", []):
        name = str(raw.get("name", "")).strip()
        desc = str(raw.get("description", "")).strip()
        if not name or not desc:
            continue
        out.append(
            InferredSkill(
                name=name,
                description=desc,
                kind=raw.get("kind", "technical"),
                source_profile_key=profile_key,
            )
        )
    return out


def infer_many(
    # (profile_key, title, content, source_text or None)
    profiles: list[tuple[str, str, dict, str | None]],
    *,
    workers: int = 8,
    progress=None,
) -> list[list[InferredSkill]]:
    """One call per profile, per the spec's "job by job API calls"."""
    return llm.pmap(
        lambda p: infer_for_profile(p[0], p[1], p[2], p[3] if len(p) > 3 else None),
        profiles,
        workers=workers,
        label="skills",
        progress=progress,
    )
