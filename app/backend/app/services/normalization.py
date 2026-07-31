"""Step 3 — create normalised job description text.

instructions.txt: "Create normalised job description text using parallel API calls
with value model. normalised description is 1 sentence defining purpose and
responsibilities of role, 5-10 key tasks (inferred is fine), management
responsibility / reporting line (if available), budget responsibility. This should
take single or multiple 'duplicate' records as input."

Two things distinguish this from step 1 (stripping):
  - Inference IS allowed here ("inferred is fine"), unlike the strictly
    extractive stripping stage.
  - Input is a dedupe *group*, not a record. A multi-member group must be
    synthesised into ONE profile representing the common role, not a
    concatenation and not an arbitrary member's text.

The output is what gets embedded for clustering (step 4), so it doubles as the
canonical de-noised representation of each distinct job.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services import llm

NORMALIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "purpose_statement": {"type": "string"},
        "key_tasks": {"type": "array", "items": {"type": "string"}},
        "management_line": {"type": ["string", "null"]},
        "budget_responsibility": {"type": ["string", "null"]},
    },
    "required": ["purpose_statement", "key_tasks", "management_line", "budget_responsibility"],
    "additionalProperties": False,
}

SYSTEM = (
    "You write a normalised, structured summary of a job from its job "
    "description(s). Output fields:\n\n"
    "purpose_statement: ONE sentence defining the purpose and core "
    "responsibilities of the role. Dense and specific — it must distinguish this "
    "role from adjacent ones, since it is used for semantic comparison across a "
    "whole workforce.\n\n"
    "key_tasks: 5-10 key tasks. Reasonable inference is allowed where the source "
    "implies a task without stating it. Each task is a short phrase, not a "
    "sentence. Focus on what the role actually does day to day.\n\n"
    "management_line: the reporting line and/or people-management "
    "responsibility, if the source indicates it (e.g. 'Reports to Head of Asset "
    "Management; leads a team of 4 engineers'). null if the source gives no "
    "indication — do not guess at an org structure.\n\n"
    "budget_responsibility: budget or financial accountability if indicated "
    "(e.g. 'Owns £2m annual capital programme'). null if not indicated.\n\n"
    "Write in neutral, factual, present-tense register. Do not include company "
    "marketing, benefits, or recruitment logistics. Do not name the employer."
)

MULTI_RECORD_NOTE = (
    "\n\nNOTE: you are given MULTIPLE job descriptions that have been identified "
    "as duplicates or near-duplicates of the same underlying role (e.g. the same "
    "job advertised in different regions, or at slightly different wordings). "
    "Synthesise ONE normalised profile representing their common core role. Do "
    "not concatenate them, do not describe them as separate roles, and do not "
    "simply copy whichever is longest. Where they differ in detail, prefer what "
    "is common across them."
)


@dataclass
class NormalizedResult:
    purpose_statement: str
    key_tasks: list[str]
    management_line: str | None
    budget_responsibility: str | None

    def embedding_text(self) -> str:
        """Canonical text for clustering embeddings — purpose plus tasks.

        Deliberately excludes management_line/budget_responsibility: those track
        seniority, and including them pulls clustering toward grouping by level
        rather than by function. Job *families* should group a junior and a
        senior version of the same discipline together; levelling is handled
        separately by the Job Evaluation stage.
        """
        tasks = " ".join(self.key_tasks)
        return f"{self.purpose_statement} {tasks}".strip()


def normalize_group(
    member_texts: list[tuple[str, str]],  # (job_title, stripped_text)
) -> NormalizedResult:
    system = SYSTEM + (MULTI_RECORD_NOTE if len(member_texts) > 1 else "")

    if len(member_texts) == 1:
        title, text = member_texts[0]
        prompt = f"Job title: {title}\n\nJob description:\n\n{text}"
    else:
        blocks = [
            f"--- Record {n} — job title: {title} ---\n{text}"
            for n, (title, text) in enumerate(member_texts, start=1)
        ]
        prompt = (
            f"{len(member_texts)} duplicate/near-duplicate records for the same role:\n\n"
            + "\n\n".join(blocks)
        )

    result = llm.complete_json(
        prompt,
        system=system,
        json_schema=NORMALIZE_SCHEMA,
        effort="low",
        max_tokens=4000,
    )

    tasks = [t.strip() for t in result.get("key_tasks", []) if t.strip()]
    if len(tasks) < 5:
        print(f"  [normalization] only {len(tasks)} key tasks returned (spec asks for 5-10)")

    return NormalizedResult(
        purpose_statement=result["purpose_statement"].strip(),
        key_tasks=tasks,
        management_line=_none_if_blank(result.get("management_line")),
        budget_responsibility=_none_if_blank(result.get("budget_responsibility")),
    )


def _none_if_blank(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"null", "none", "n/a", "not specified", "not indicated"}:
        return None
    return cleaned


def normalize_many(
    groups: list[list[tuple[str, str]]],
    *,
    workers: int = 8,
    progress=None,
) -> list[NormalizedResult]:
    """One LLM call per dedupe group, pmap-parallel, order preserved."""
    return llm.pmap(
        normalize_group,
        groups,
        workers=workers,
        label="normalize",
        progress=progress,
    )
