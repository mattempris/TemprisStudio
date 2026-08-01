"""Step 9 (second half) — proficiency definitions and mapping back to job profiles.

instructions.txt: "User can generate Proficiency definition across the taxonomy
based on a proficiency template that they can edit (default: Entry, Intermediate,
Advanced, Expert with proficiency criteria you generate once during build). User
can then trigger auto-map (deterministic based on skills roll up) these back to
the job profiles at the skill cluster level, where the job level proficiency level
is assigned using an API call."

Three distinct operations, and the split matters:
  1. `generate_cluster_definitions` — per skill cluster, write what each template
     level looks like *for that cluster*. One LLM call per cluster.
  2. `rollup_clusters_to_profiles` — DETERMINISTIC. A job profile requires a skill
     cluster if any skill inferred from that profile landed in it. No LLM.
  3. `assign_levels` — for each (profile, cluster) pair the rollup produced, one
     LLM call to pick which template level the job needs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import DEFAULTS_DIR
from app.services import llm


@dataclass
class ProficiencyLevel:
    name: str
    ordinal: int
    criteria: str
    typical_autonomy: str = ""


@dataclass
class ProficiencyTemplate:
    levels: list[ProficiencyLevel]

    def level_names(self) -> list[str]:
        return [level.name for level in sorted(self.levels, key=lambda x: x.ordinal)]

    def rubric_text(self) -> str:
        return "\n".join(
            f"{level.ordinal}. {level.name}: {level.criteria}"
            + (f" (Autonomy: {level.typical_autonomy})" if level.typical_autonomy else "")
            for level in sorted(self.levels, key=lambda x: x.ordinal)
        )


def load_default_template() -> ProficiencyTemplate:
    raw = json.loads((Path(DEFAULTS_DIR) / "proficiency_template_default.json").read_text(encoding="utf-8"))
    return ProficiencyTemplate(
        levels=[
            ProficiencyLevel(
                name=level["name"],
                ordinal=level["ordinal"],
                criteria=level["criteria"],
                typical_autonomy=level.get("typical_autonomy", ""),
            )
            for level in raw["levels"]
        ]
    )


def validate_template(template: ProficiencyTemplate) -> list[str]:
    problems: list[str] = []
    if len(template.levels) < 2:
        problems.append("a proficiency scale needs at least 2 levels")
    ordinals = sorted(level.ordinal for level in template.levels)
    if ordinals != list(range(1, len(ordinals) + 1)):
        problems.append(f"level ordinals must be consecutive from 1, got {ordinals}")
    if len({level.name.strip().lower() for level in template.levels}) != len(template.levels):
        problems.append("level names must be unique")
    for level in template.levels:
        if not level.criteria.strip():
            problems.append(f"level '{level.name}' has no criteria")
    return problems


# ---------------------------------------------------------------------------
# 1. Per-cluster proficiency definitions
# ---------------------------------------------------------------------------
def _definitions_schema(template: ProficiencyTemplate) -> dict:
    return {
        "type": "object",
        "properties": {
            "definitions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "level": {"type": "string", "enum": template.level_names()},
                        "definition": {"type": "string"},
                    },
                    "required": ["level", "definition"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["definitions"],
        "additionalProperties": False,
    }


DEFINITIONS_SYSTEM = (
    "You write proficiency definitions for one skill cluster, against a fixed "
    "organisational proficiency scale.\n\n"
    "For every level in the scale, describe what that level of proficiency looks "
    "like specifically for THIS skill cluster — concrete and observable, so a "
    "manager could tell two adjacent levels apart. 25-45 words each.\n\n"
    "Stay faithful to the scale's own criteria: the distinction between adjacent "
    "levels must be the one the scale describes (autonomy, complexity, breadth of "
    "influence), expressed in the language of this skill. Do not invent a "
    "different progression, and do not simply restate the generic criteria with "
    "the skill name inserted.\n\n"
    "Return one definition per level, using the level names exactly as given."
)


@dataclass
class ClusterProficiency:
    cluster_id: int
    cluster_name: str
    definitions: dict[str, str] = field(default_factory=dict)  # level name -> definition


def generate_cluster_definitions(
    cluster_id: int,
    cluster_name: str,
    member_skills: list[tuple[str, str]],  # (name, description)
    template: ProficiencyTemplate,
) -> ClusterProficiency:
    members = "\n".join(f"- {n}: {d}" for n, d in member_skills[:15])
    prompt = (
        f"Skill cluster: {cluster_name}\n\n"
        f"Skills in this cluster:\n{members}\n\n"
        f"Proficiency scale:\n{template.rubric_text()}"
    )
    result = llm.complete_json(
        prompt,
        system=DEFINITIONS_SYSTEM,
        json_schema=_definitions_schema(template),
        effort="low",
        max_tokens=6000,
    )
    return ClusterProficiency(
        cluster_id=cluster_id,
        cluster_name=cluster_name,
        definitions={
            d["level"]: d["definition"].strip()
            for d in result.get("definitions", [])
            if d.get("definition", "").strip()
        },
    )


def generate_definitions_many(
    clusters: list[tuple[int, str, list[tuple[str, str]]]],
    template: ProficiencyTemplate,
    *,
    workers: int = 6,
    progress=None,
) -> list[ClusterProficiency]:
    return llm.pmap(
        lambda c: generate_cluster_definitions(c[0], c[1], c[2], template),
        clusters,
        workers=workers,
        label="proficiency-defs",
        progress=progress,
    )


# ---------------------------------------------------------------------------
# 2. Deterministic rollup — no LLM
# ---------------------------------------------------------------------------
@dataclass
class ProfileSkillRequirement:
    profile_key: str
    cluster_id: int
    cluster_name: str
    # the skills originally inferred from this profile that landed in this cluster,
    # which is both the evidence for the requirement and the input to level assignment
    contributing_skills: list[tuple[str, str]] = field(default_factory=list)
    assigned_level: str | None = None
    rationale: str | None = None


def rollup_clusters_to_profiles(
    skill_assignments: list[tuple[str, str, str, int]],  # (profile_key, skill_name, skill_desc, cluster_id)
    cluster_names: dict[int, str],
) -> list[ProfileSkillRequirement]:
    """Deterministic auto-map, exactly as the spec requires.

    A job profile requires a skill cluster if any skill inferred from that profile
    was clustered into it. Several of a profile's skills often land in the same
    cluster, which is why they're collected as contributing evidence rather than
    producing duplicate requirements.
    """
    grouped: dict[tuple[str, int], ProfileSkillRequirement] = {}
    for profile_key, skill_name, skill_desc, cluster_id in skill_assignments:
        key = (profile_key, cluster_id)
        req = grouped.get(key)
        if req is None:
            req = ProfileSkillRequirement(
                profile_key=profile_key,
                cluster_id=cluster_id,
                cluster_name=cluster_names.get(cluster_id, f"Cluster {cluster_id}"),
            )
            grouped[key] = req
        req.contributing_skills.append((skill_name, skill_desc))

    return [grouped[k] for k in sorted(grouped, key=lambda k: (k[0], k[1]))]


# ---------------------------------------------------------------------------
# 3. Level assignment — one call per (profile, cluster) pair
# ---------------------------------------------------------------------------
def _assignment_schema(template: ProficiencyTemplate) -> dict:
    return {
        "type": "object",
        "properties": {
            "level": {"type": "string", "enum": template.level_names()},
            "rationale": {"type": "string"},
        },
        "required": ["level", "rationale"],
        "additionalProperties": False,
    }


ASSIGNMENT_SYSTEM = (
    "You decide what level of proficiency in one skill cluster a specific job "
    "requires.\n\n"
    "Judge the level the job NEEDS to be performed well — not the best level a "
    "post-holder could conceivably have, and not a reward for seniority. A senior "
    "role can require only Intermediate proficiency in a skill that is peripheral "
    "to it, and a junior role can require Advanced proficiency in the one skill "
    "that defines it.\n\n"
    "Choose from the cluster-specific level definitions given. Return the level "
    "name exactly as written, and one sentence of rationale citing what in the "
    "job drives that level."
)


def assign_level(
    requirement: ProfileSkillRequirement,
    profile_title: str,
    profile_content: dict,
    cluster_proficiency: ClusterProficiency,
    template: ProficiencyTemplate,
) -> ProfileSkillRequirement:
    definitions = "\n".join(
        f"- {name}: {cluster_proficiency.definitions.get(name, '(no definition)')}"
        for name in template.level_names()
    )
    contributing = "; ".join(f"{n}" for n, _ in requirement.contributing_skills)
    responsibilities = profile_content.get("responsibilities") or []
    about = profile_content.get("about_role") or []

    prompt = (
        f"Job: {profile_title}\n"
        f"About the role: {' '.join(about)[:900]}\n"
        f"Key responsibilities: {'; '.join(responsibilities)[:900]}\n"
        f"Reporting line: {profile_content.get('reporting_line') or 'not stated'}\n\n"
        f"Skill cluster: {requirement.cluster_name}\n"
        f"Skills this job showed in this cluster: {contributing}\n\n"
        f"Proficiency levels for this cluster:\n{definitions}"
    )
    result = llm.complete_json(
        prompt,
        system=ASSIGNMENT_SYSTEM,
        json_schema=_assignment_schema(template),
        effort="low",
        max_tokens=2000,
    )
    requirement.assigned_level = result["level"]
    requirement.rationale = result.get("rationale", "").strip() or None
    return requirement


def assign_levels_many(
    requirements: list[ProfileSkillRequirement],
    profile_lookup: dict[str, tuple[str, dict]],  # profile_key -> (title, content)
    cluster_proficiencies: dict[int, ClusterProficiency],
    template: ProficiencyTemplate,
    *,
    workers: int = 8,
    progress=None,
) -> list[ProfileSkillRequirement]:
    def one(req: ProfileSkillRequirement) -> ProfileSkillRequirement:
        title, content = profile_lookup.get(req.profile_key, (req.profile_key, {}))
        prof = cluster_proficiencies.get(
            req.cluster_id, ClusterProficiency(req.cluster_id, req.cluster_name)
        )
        return assign_level(req, title, content, prof, template)

    return llm.pmap(one, requirements, workers=workers, label="proficiency-levels", progress=progress)
