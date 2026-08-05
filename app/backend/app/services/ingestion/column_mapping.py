"""LLM-backed HRIS column mapping.

instructions.txt: "AI estimate / user confirm Job Title, job description and job
level columns --> optionally map headcount against each job where this column
exists (for final analytics)".

This is deliberately LLM-backed rather than heuristic. `jobMatching`'s existing
ColumnDetectionService constructs an AzureOpenAI client but never calls it — its
`detect()` is 100% regex-on-header-name plus value-shape scoring. That fails on
exactly the distinctions that matter here: "Job Description" vs "Role Summary"
vs "Purpose", or "Level" vs "Grade" vs "Band" vs "Pay Scale", where the header
name alone is ambiguous and the *cell content* is what disambiguates.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services import llm
from app.services.ingestion.hris import ColumnProfile

# Every field the model is asked to map, in the order it is asked about them. One list
# rather than the five it used to take: the schema named each field in `properties`, in
# `confidence`, in `reasoning`, and in three separate `required` arrays, so adding a field
# meant five edits and forgetting one of them would silently stop the field being asked
# for at all.
#
# `job_title` is the only one the pipeline cannot proceed without. The rest are optional
# in the sense that a sheet may not have them — the model returns null and the app carries
# on — but they are all `required` in the *schema*, because the API's structured output
# needs a value present and null is that value.
TARGET_FIELDS = (
    "job_title",
    "job_description",
    "job_level",
    "headcount",
    "business_level_1",
    "business_level_2",
    "business_level_3",
)

# The schema names fields `*_column` while the rest of the app uses `*_col`. Kept because
# it reads better in a prompt; normalised in `_rekey` below.
_COLS = tuple(f"{f}_column" for f in TARGET_FIELDS)


def _keyed(value_schema: dict) -> dict:
    """An object with one entry per target field, all required."""
    return {
        "type": "object",
        "properties": {c: value_schema for c in _COLS},
        "required": list(_COLS),
        "additionalProperties": False,
    }


MAPPING_SCHEMA = {
    "type": "object",
    "properties": {
        **{c: {"type": ["string", "null"]} for c in _COLS},
        "confidence": _keyed({"type": "number"}),
        "reasoning": _keyed({"type": "string"}),
    },
    "required": [*_COLS, "confidence", "reasoning"],
    "additionalProperties": False,
}

SYSTEM = (
    "You map columns in an HR/HRIS spreadsheet export onto target fields "
    "for a job-architecture build:\n"
    "- job_title_column: the role/position title (e.g. 'Senior Analyst, Pricing')\n"
    "- job_description_column: substantive prose describing the role's purpose, "
    "responsibilities or duties. Must be real descriptive text, not a title, "
    "code, department name, or one-word category.\n"
    "- job_level_column: the role's seniority/grade/band/career level "
    "(e.g. 'M3', 'Band 7', 'Senior Manager', 'P2'). Not a pay amount and not a "
    "job title.\n"
    "- headcount_column: how many people hold this role — a count. Not an "
    "employee id, not a salary, not an FTE percentage.\n"
    "- business_level_1_column, business_level_2_column, business_level_3_column: the "
    "organisation's own reporting cascade, broadest first — for example 'Corporate "
    "Functions' then 'Finance' then 'Procurement'. These are the columns an HR "
    "structure hangs off: function, sub-function, division, department, business unit, "
    "cost centre name. Assign them by BREADTH, not by column order in the sheet: the "
    "one with the fewest distinct values is level 1. Map only as many as the sheet "
    "genuinely has — two levels is common and three is not required. A cost centre "
    "CODE is not a level; its NAME may be. Do not use location or region unless the "
    "organisation is plainly structured by geography.\n\n"
    "Reason from the SAMPLE VALUES, not just the column name — names are often "
    "ambiguous or misleading. Return null for any field that genuinely has no "
    "matching column rather than forcing a poor match, and give it confidence 0. "
    "Confidence is 0-1 and should be calibrated: high only when the samples "
    "clearly confirm the field."
)


@dataclass
class ColumnMappingSuggestion:
    job_title_col: str | None
    job_description_col: str | None
    job_level_col: str | None
    headcount_col: str | None
    # The organisation's own reporting cascade, broadest first. Optional and often absent;
    # Work Design uses them as a facet and hides the control when none were mapped.
    business_level_1_col: str | None = None
    business_level_2_col: str | None = None
    business_level_3_col: str | None = None
    confidence: dict[str, float] = field(default_factory=dict)
    reasoning: dict[str, str] = field(default_factory=dict)


def _build_prompt(profiles: list[ColumnProfile], row_count: int) -> str:
    lines = [f"Spreadsheet has {row_count} data rows and these columns:\n"]
    for p in profiles:
        samples = "; ".join(f'"{v}"' for v in p.sample_values) if p.sample_values else "(all empty)"
        lines.append(f'- "{p.name}" [{p.dtype}, {p.non_null_count} non-empty] samples: {samples}')
    lines.append("\nMap these columns onto the target fields.")
    return "\n".join(lines)


def suggest_mapping(profiles: list[ColumnProfile], row_count: int) -> ColumnMappingSuggestion:
    result = llm.complete_json(
        _build_prompt(profiles, row_count),
        system=SYSTEM,
        json_schema=MAPPING_SCHEMA,
        effort="low",
        max_tokens=2000,
    )

    valid = {p.name for p in profiles}

    def _validated(key: str) -> str | None:
        """Guard against a hallucinated column name — if the model returns a
        column that isn't in the sheet, treat the field as unmapped rather than
        letting a bad reference reach downstream ingestion."""
        value = result.get(key)
        if value is None:
            return None
        if value not in valid:
            print(f"  [column_mapping] model returned unknown column {value!r} for {key} — treating as unmapped")
            return None
        return value

    # The schema names fields `*_column` but the rest of the app (and the API
    # response) uses `*_col`. Normalise here so `confidence` and `reasoning` are
    # keyed the same way as the mapping itself — otherwise a caller reading
    # confidence["job_title_col"] silently gets nothing.
    def _rekey(d: dict, cast) -> dict:
        out = {}
        for k, v in d.items():
            try:
                out[k.replace("_column", "_col")] = cast(v)
            except (TypeError, ValueError):
                continue
        return out

    # Built from TARGET_FIELDS so a new field needs no edit here either — the dataclass
    # field names are exactly the schema's with `_column` normalised to `_col`.
    return ColumnMappingSuggestion(
        **{f"{f}_col": _validated(f"{f}_column") for f in TARGET_FIELDS},
        confidence=_rekey(result.get("confidence") or {}, float),
        reasoning=_rekey(result.get("reasoning") or {}, str),
    )
