"""Step 1 — strip irrelevant content from job descriptions.

instructions.txt: "strip irrelevant content from job descriptions (company
description, equality statement) using an API call. Output existing content only
- don't infer anything"

That last clause is the whole design constraint: this stage is *extractive*, not
generative. The model may only delete; every character it returns must already
exist in the input. `removed_sections` gives short labels for what was cut, which
drives the before/after review UI and the lineage audit trail.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from app.services import llm

STRIP_SCHEMA = {
    "type": "object",
    "properties": {
        "stripped_text": {"type": "string"},
        "removed_sections": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["stripped_text", "removed_sections"],
    "additionalProperties": False,
}

SYSTEM = (
    "You remove non-role-specific boilerplate from a job description, leaving "
    "the substance of the role itself.\n\n"
    "REMOVE:\n"
    "- Company/organisation descriptions and 'about us' marketing copy\n"
    "- Equality, diversity, inclusion and equal-opportunity statements\n"
    "- Recruitment-process logistics: closing dates, how to apply, interview "
    "process, agency notices, reference numbers\n"
    "- Benefits and perks lists, salary and pay ranges, employment type, "
    "contracted hours, office location\n"
    "- Legal disclaimers, privacy notices, and any 'this job description is not "
    "exhaustive' style caveats\n\n"
    "KEEP:\n"
    "- The role's purpose, responsibilities, duties and accountabilities\n"
    "- Required and desirable skills, experience, qualifications\n"
    "- Reporting lines, team context, budget or people responsibility\n"
    "- Role-specific working conditions (travel, shift work, physical demands, "
    "site/field work) — these describe the job, not the employment package\n\n"
    "CRITICAL CONSTRAINT: this is an extraction task, not a writing task. Every "
    "character of stripped_text MUST appear verbatim in the input. Copy the text "
    "you are keeping exactly — do not reword, summarise, re-order, expand, "
    "correct, retitle, or add connecting text. Do not add anything that is not "
    "already there. Your only permitted operation is deleting whole passages.\n\n"
    "removed_sections: a short label (2-5 words) for each passage you removed, "
    "e.g. 'Closing date', 'Salary and hours', 'Company description', "
    "'Diversity statement'."
)


@dataclass
class StripResult:
    stripped_text: str
    removed_sections: list[str]
    # fraction of the stripped text's tokens that were found in the source. 1.0
    # means purely extractive; lower means the model invented wording.
    extractive_fidelity: float
    fidelity_warning: str | None = None


def _extractive_fidelity(source: str, stripped: str) -> float:
    """How much of the output is genuinely present in the input.

    Uses a token-level sequence match rather than exact substring containment,
    because legitimate deletion of an interior passage leaves the remaining text
    non-contiguous — so a naive `stripped in source` check would fail on correct
    output. Whitespace/line-structure changes are also expected and shouldn't
    count against fidelity, so both sides are compared as bare token lists.
    """
    src_tokens = source.split()
    out_tokens = stripped.split()
    if not out_tokens:
        return 0.0
    matcher = difflib.SequenceMatcher(a=src_tokens, b=out_tokens, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(out_tokens)


# Below this, the model has rewritten rather than extracted, which violates the
# "output existing content only" requirement. Not fatal (the user reviews the
# result), but it must be surfaced rather than silently accepted.
FIDELITY_THRESHOLD = 0.95


def strip_one(raw_text: str, *, job_title: str | None = None) -> StripResult:
    header = f"Job title: {job_title}\n\n" if job_title else ""
    prompt = f"{header}Job description to strip:\n\n{raw_text}"

    result = llm.complete_json(
        prompt,
        system=SYSTEM,
        json_schema=STRIP_SCHEMA,
        effort="low",
        max_tokens=16000,
    )

    stripped = result["stripped_text"].strip()
    fidelity = _extractive_fidelity(raw_text, stripped)
    warning = None
    if fidelity < FIDELITY_THRESHOLD:
        warning = (
            f"only {fidelity:.0%} of the stripped text was found in the source — "
            "the model may have rewritten rather than extracted. Review before accepting."
        )
        print(f"  [stripping] {warning}")

    return StripResult(
        stripped_text=stripped,
        removed_sections=[s.strip() for s in result.get("removed_sections", []) if s.strip()],
        extractive_fidelity=fidelity,
        fidelity_warning=warning,
    )


def strip_many(
    records: list[tuple[str, str]],  # (job_title, raw_text)
    *,
    workers: int = 8,
    progress=None,
) -> list[StripResult]:
    """pmap-parallel across records — one LLM call each, order preserved."""
    return llm.pmap(
        lambda rec: strip_one(rec[1], job_title=rec[0]),
        records,
        workers=workers,
        label="strip",
        progress=progress,
    )
