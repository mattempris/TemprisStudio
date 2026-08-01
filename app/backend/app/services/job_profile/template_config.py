"""Step 7 — the user-defined job profile template.

instructions.txt step 7 opens: "User defines job profile template, Job Evaluation
Framework and level names / JE score mapping".

What "template" means here is deliberately bounded. The user controls **which
sections a profile contains, what each is called, in what order, and what
guidance the LLM gets for it**. They do not edit raw HTML, because the PDF and
DocX renderers build from the same structured JSON rather than converting the
HTML — that was a specific requirement ("DocX must be a real editable Word
document"), and free-form markup would have nothing to render into a Word
heading or list. Sections are therefore chosen from a catalogue, where each key
carries the shape (prose, list, inline pairs) that all three renderers know how
to lay out.

Turning a section off removes it from the structured-output schema entirely, so
the model is never asked for content that would be discarded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SectionShape = Literal["prose", "list", "labelled_list", "badges", "inline"]


@dataclass(frozen=True)
class SectionSpec:
    key: str
    default_heading: str
    shape: SectionShape
    description: str          # shown in the editor
    default_guidance: str     # goes into the generation prompt
    removable: bool = True


# The catalogue. Order here is the default document order.
CATALOGUE: tuple[SectionSpec, ...] = (
    SectionSpec(
        "badges", "Badges", "badges",
        "Short chips under the title — location, contract type, working pattern.",
        "3-5 very short factual chips. No sentences.",
    ),
    SectionSpec(
        "about_role", "About the Role", "prose",
        "Opening prose describing the role's purpose.",
        "2-3 paragraphs. Lead with why the role exists, not a list of duties.",
        removable=False,
    ),
    SectionSpec(
        "requirements", "Minimum Requirements", "list",
        "Hard qualifications or experience thresholds.",
        "Only genuine minimums — things that would disqualify a candidate if absent.",
    ),
    SectionSpec(
        "essential_skills", "Essential Skills", "list",
        "Capabilities the holder must already have.",
        "Capabilities, not duties. 5-8 items.",
    ),
    SectionSpec(
        "desirable_skills", "Desirable Skills", "list",
        "Capabilities that help but are not required.",
        "3-5 items. Genuinely optional ones only.",
    ),
    SectionSpec(
        "tags", "Tags", "badges",
        "Keywords for search and grouping.",
        "5-8 single words or short noun phrases.",
    ),
    SectionSpec(
        "responsibilities", "Key Responsibilities", "list",
        "The main accountabilities of the role.",
        "6-10 items, each a distinct accountability starting with a verb.",
        removable=False,
    ),
    SectionSpec(
        "contribution", "Your Contribution", "list",
        "How the role moves the organisation's goals.",
        "3-5 items connecting the work to organisational outcomes.",
    ),
    SectionSpec(
        "required_of_you", "Required of You", "labelled_list",
        "Behaviours and ways of working expected, each with a short label.",
        "3-5 items about conduct and approach, not technical skill. Each needs a "
        "2-4 word label and a one-sentence value.",
    ),
    SectionSpec(
        "reporting_line", "Reporting line", "inline",
        "Who the role reports to and who reports to it.",
        "One line. Say 'Not stated' if the source material does not say.",
    ),
    SectionSpec(
        "budget_responsibility", "Budget responsibility", "inline",
        "Financial accountability, if any.",
        "One line. Say 'None stated' if absent.",
    ),
)

BY_KEY = {s.key: s for s in CATALOGUE}

# Always generated; not user-controlled because the document has nowhere to put a
# profile without a title, and level_context is derived from the JE result.
ALWAYS = ("title", "level_context")


@dataclass
class SectionConfig:
    key: str
    heading: str
    include: bool = True
    guidance: str = ""


def default_sections() -> list[SectionConfig]:
    return [
        SectionConfig(s.key, s.default_heading, True, s.default_guidance) for s in CATALOGUE
    ]


def validate(sections: list[SectionConfig]) -> list[str]:
    """Problems as human-readable strings; empty means valid."""
    problems: list[str] = []
    seen: set[str] = set()
    for s in sections:
        if s.key not in BY_KEY:
            problems.append(f"unknown section '{s.key}'")
            continue
        if s.key in seen:
            problems.append(f"section '{s.key}' appears more than once")
        seen.add(s.key)
        if s.include and not s.heading.strip() and BY_KEY[s.key].shape != "badges":
            problems.append(f"section '{s.key}' is included but has no heading")

    for spec in CATALOGUE:
        if not spec.removable:
            match = next((s for s in sections if s.key == spec.key), None)
            if match is None or not match.include:
                problems.append(
                    f"'{spec.default_heading}' cannot be removed — a job profile "
                    f"without it is not a job profile"
                )
    if not any(s.include for s in sections):
        problems.append("every section is disabled")
    return problems


def enabled(sections: list[SectionConfig]) -> list[SectionConfig]:
    return [s for s in sections if s.include and s.key in BY_KEY]


def build_schema(sections: list[SectionConfig]) -> dict:
    """Structured-output schema covering only the enabled sections.

    Property definitions are taken from the canonical PROFILE_SCHEMA rather than
    rebuilt from `shape`. Two of them are not the obvious type — `required_of_you`
    is an array of {label, value} objects, and the optional strings are
    ["string", "null"] — and the renderers read those exact shapes. Deriving them
    a second time here is how you get a schema that validates and then produces
    content the DocX renderer crashes on.

    `shape` therefore drives layout and prompt wording only; this is the one
    source of truth for types.
    """
    from app.services.job_profile.generator import PROFILE_SCHEMA

    canonical = PROFILE_SCHEMA["properties"]
    keys = [*ALWAYS, *(s.key for s in enabled(sections) if s.key not in ALWAYS)]

    props: dict = {}
    for key in keys:
        if key in canonical:
            props[key] = canonical[key]

    return {
        "type": "object",
        "properties": props,
        "required": list(props.keys()),
        "additionalProperties": False,
    }


def prompt_section_guide(sections: list[SectionConfig]) -> str:
    """The per-section instructions appended to the generation prompt."""
    lines = []
    for s in enabled(sections):
        spec = BY_KEY[s.key]
        shape = {
            "prose": "array of paragraphs",
            "list": "array of bullet items",
            "labelled_list": "array of {label, value} objects",
            "badges": "array of very short chips",
            "inline": "single short string",
        }[spec.shape]
        guidance = s.guidance.strip() or spec.default_guidance
        lines.append(f"- {s.key} ({shape}), shown as \"{s.heading}\": {guidance}")
    return "\n".join(lines)


def headings(sections: list[SectionConfig]) -> dict[str, str]:
    """key -> heading, for the HTML and DocX renderers."""
    out = {s.key: (s.heading.strip() or BY_KEY[s.key].default_heading) for s in enabled(sections)}
    return out


def order(sections: list[SectionConfig]) -> list[str]:
    return [s.key for s in enabled(sections)]


def filter_content(content: dict, sections: list[SectionConfig]) -> dict:
    """Drop sections the template disables.

    Both renderers show a section whenever its key is present in the content
    dict. That is right for freshly generated profiles, whose schema already
    excluded disabled sections — but re-rendering a profile generated under an
    earlier template would resurrect sections the user has since removed. Doing
    it here keeps HTML, PDF and DocX consistent by construction.

    Keys that are not sections at all (family, category, the always-on ones) pass
    through untouched.
    """
    allowed = set(order(sections)) | set(ALWAYS)
    return {k: v for k, v in content.items() if k not in BY_KEY or k in allowed}
