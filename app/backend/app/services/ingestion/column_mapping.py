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

from dataclasses import dataclass

from app.services import llm
from app.services.ingestion.hris import ColumnProfile

MAPPING_SCHEMA = {
    "type": "object",
    "properties": {
        "job_title_column": {"type": ["string", "null"]},
        "job_description_column": {"type": ["string", "null"]},
        "job_level_column": {"type": ["string", "null"]},
        "headcount_column": {"type": ["string", "null"]},
        "confidence": {
            "type": "object",
            "properties": {
                "job_title_column": {"type": "number"},
                "job_description_column": {"type": "number"},
                "job_level_column": {"type": "number"},
                "headcount_column": {"type": "number"},
            },
            "required": [
                "job_title_column",
                "job_description_column",
                "job_level_column",
                "headcount_column",
            ],
            "additionalProperties": False,
        },
        "reasoning": {
            "type": "object",
            "properties": {
                "job_title_column": {"type": "string"},
                "job_description_column": {"type": "string"},
                "job_level_column": {"type": "string"},
                "headcount_column": {"type": "string"},
            },
            "required": [
                "job_title_column",
                "job_description_column",
                "job_level_column",
                "headcount_column",
            ],
            "additionalProperties": False,
        },
    },
    "required": ["job_title_column", "job_description_column", "job_level_column", "headcount_column", "confidence", "reasoning"],
    "additionalProperties": False,
}

SYSTEM = (
    "You map columns in an HR/HRIS spreadsheet export onto four target fields "
    "for a job-architecture build:\n"
    "- job_title_column: the role/position title (e.g. 'Senior Analyst, Pricing')\n"
    "- job_description_column: substantive prose describing the role's purpose, "
    "responsibilities or duties. Must be real descriptive text, not a title, "
    "code, department name, or one-word category.\n"
    "- job_level_column: the role's seniority/grade/band/career level "
    "(e.g. 'M3', 'Band 7', 'Senior Manager', 'P2'). Not a pay amount and not a "
    "job title.\n"
    "- headcount_column: how many people hold this role — a count. Not an "
    "employee id, not a salary, not an FTE percentage.\n\n"
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
    confidence: dict[str, float]
    reasoning: dict[str, str]


def _build_prompt(profiles: list[ColumnProfile], row_count: int) -> str:
    lines = [f"Spreadsheet has {row_count} data rows and these columns:\n"]
    for p in profiles:
        samples = "; ".join(f'"{v}"' for v in p.sample_values) if p.sample_values else "(all empty)"
        lines.append(f'- "{p.name}" [{p.dtype}, {p.non_null_count} non-empty] samples: {samples}')
    lines.append("\nMap these columns onto the four target fields.")
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

    return ColumnMappingSuggestion(
        job_title_col=_validated("job_title_column"),
        job_description_col=_validated("job_description_column"),
        job_level_col=_validated("job_level_column"),
        headcount_col=_validated("headcount_column"),
        confidence=_rekey(result.get("confidence") or {}, float),
        reasoning=_rekey(result.get("reasoning") or {}, str),
    )
