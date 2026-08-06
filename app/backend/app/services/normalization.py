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

# A fixed ladder rather than free text, because this string is embedded and clustered.
#
# "Reports to the Head of Asset Management and leads a team of four" and "Senior individual
# contributor with no direct reports" both describe level, but as embedding input they share
# almost no surface form — so two jobs at the same rung land far apart and the level signal
# averages away to nothing. A closed vocabulary gives every job at a rung a byte-identical
# phrase, which is what makes the signal survive into the geometry.
#
# Nine rungs, deliberately generic. This runs before the job evaluation step, so it cannot use
# the project's own level bands; those are configured later and mapped from scores. This is a
# clustering aid, not a grade — the JE step remains the authority on levelling.
LEVEL_LADDER: tuple[str, ...] = (
    "support",
    "entry",
    "experienced",
    "senior",
    "lead specialist",
    "manager",
    "senior manager",
    "director",
    "executive",
)

NORMALIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "purpose_statement": {"type": "string"},
        "key_tasks": {"type": "array", "items": {"type": "string"}},
        "level_indicator": {"type": "string", "enum": list(LEVEL_LADDER)},
        "level_evidence": {"type": "string"},
        "qualifications": {"type": ["string", "null"]},
        "management_line": {"type": ["string", "null"]},
        "budget_responsibility": {"type": ["string", "null"]},
    },
    "required": [
        "purpose_statement",
        "key_tasks",
        "level_indicator",
        "level_evidence",
        "qualifications",
        "management_line",
        "budget_responsibility",
    ],
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
    "level_indicator: the role's level, as ONE of exactly these rungs:\n"
    "  support        — assists others; closely directed; limited independent judgement\n"
    "  entry          — first professional role; learning the discipline; work is checked\n"
    "  experienced    — delivers independently within an established remit\n"
    "  senior         — deep expertise; sets approach on their own work; guides others "
    "informally\n"
    "  lead specialist— the recognised authority on a discipline; sets technical direction; "
    "typically no direct reports\n"
    "  manager        — accountable for a team's output and for its people\n"
    "  senior manager — manages managers, or a function of several teams\n"
    "  director       — accountable for a whole function or business area\n"
    "  executive      — accountable for the organisation or a major division\n"
    "Judge on scope, autonomy and accountability — NOT on the job title's adjectives. Titles "
    "inflate: a 'Senior Analyst' doing checked work within a set remit is 'experienced', and a "
    "'Consultant' who sets the firm's technical direction is 'lead specialist'. Where the source "
    "genuinely does not indicate level, infer the most probable rung for that kind of work "
    "rather than defaulting to the middle.\n\n"
    "level_evidence: ONE short clause giving the basis for that rung — the scope, autonomy, "
    "reporting or accountability that decided it (e.g. 'owns the credit policy for the division; "
    "no reports'). Do not restate the rung name.\n\n"
    "qualifications: a TERSE name for a formal qualification that GATES the role — one without "
    "which a person cannot lawfully or professionally hold the post. Apply this test: would "
    "appointing someone without it be impossible, rather than merely unusual? If yes, name it "
    "(e.g. 'qualified solicitor', 'ACA or ACCA', 'CFA charterholder', 'chartered engineer', "
    "'registered nurse', 'qualified actuary', 'medical licence'). Infer it where the role plainly "
    "implies one even if the source omits it — a solicitor post requires a solicitor's "
    "qualification whether or not the description says so.\n"
    "Otherwise null, and null is the common answer. In particular, null for ALL of these even "
    "when the source lists them: a degree or any level of degree; a preferred, desirable or "
    "'or equivalent' certification; a vendor or technology certificate; years of experience; "
    "a security clearance; a driving licence. Naming those would assert a barrier to entry that "
    "does not exist, and this field is used to separate roles from one another — so a "
    "qualification invented from a degree preference makes two unrelated roles look alike.\n"
    "Several genuine gating qualifications may be listed, comma-separated. Name the "
    "qualification only: no 'preferred', no 'or equivalent', no parenthetical alternatives.\n\n"
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
    # Added after the first real client read. Defaulted so a NormalizedResult rebuilt from a
    # state blob written before this change still constructs — see `tier_state.base_items`.
    level_indicator: str | None = None
    level_evidence: str | None = None
    qualifications: str | None = None

    def embedding_text(self) -> str:
        """Canonical text for clustering embeddings — purpose, tasks, level, qualifications.

        **This reverses an earlier decision, deliberately.** The original version excluded
        anything level-bearing, on the reasoning that job *families* should hold a junior and a
        senior version of one discipline together and that levelling belongs to the Job
        Evaluation step. That reasoning was sound for families and wrong for anchor roles: an
        anchor role is the canonical role a set of source titles collapses onto, and a Graduate
        Analyst and a Head of Analytics are not the same canonical role. Excluding level meant
        the finest tier grouped purely on function and produced anchor roles spanning four
        grades, which is what the client read surfaced.

        Two things keep the reversal from re-creating the problem it was guarding against:

        The level phrase comes from a closed ladder, so it is a short, consistent token rather
        than a paragraph — it can separate rungs without dominating a text whose bulk is still
        purpose and tasks. Free-text seniority prose, which is what the original comment was
        really objecting to, would have swamped the function signal.

        And the signal dilutes upward by construction. The category and family tiers cluster the
        *centroids* of the tier below, and a category holding a junior and a senior anchor role
        averages their level components away. So families still group by discipline; only the
        tier that should be level-aware is.

        Qualifications go in for the same reason and with the same shape: "qualified solicitor"
        is a hard boundary between two roles that otherwise read alike, and it is a short
        canonical phrase rather than prose.

        `management_line` and `budget_responsibility` stay out. They are genuinely free-form,
        they are the diffuse version of what `level_indicator` now says precisely, and including
        them would add noise on the axis this change is trying to sharpen.

        **The level and qualification phrases lead, and that position is load-bearing.** The
        router builds its cluster list by truncating each exemplar to 120 characters
        (`tier.finalise`), so anything at the tail of a text whose bulk is purpose and tasks
        never reaches the prompt. Trailing them would have put level in the *item* being routed
        and not in the *clusters* it chooses between — the model asked to weigh a criterion it
        could only see on one side, which is worse than not asking. Leading them costs a little
        of the exemplar's function detail, and the cluster's confirmed name carries that anyway.

        Naming reads the same texts untruncated, so anchor-role names can now reflect level too.
        That follows from the change rather than working against it: if a cluster is
        level-homogeneous, "Senior Credit Analyst" is a better name for it than "Credit Analyst".
        """
        head = []
        if self.level_indicator:
            # A fixed phrase, so every job at a rung shares a byte-identical prefix.
            head.append(f"Level: {self.level_indicator}.")
        if self.qualifications:
            head.append(f"Qualifications: {self.qualifications}.")
        parts = [*head, self.purpose_statement, " ".join(self.key_tasks)]
        return " ".join(p for p in parts if p and p.strip()).strip()


def result_of(profile) -> NormalizedResult:
    """Rebuild a NormalizedResult from a stored NormalizedProfile.

    Exists because two places need the embedding text of an already-normalised job — the cluster
    build step and `tier_state.base_items` — and they had each written the constructor out by
    hand. That was fine until a field was added: one of them was updated and the other was not,
    so the build step would have embedded without the level signal while the tier engine read
    the tree as though it were there. Vectors and tree silently out of step is not a failure
    that announces itself.

    Takes the Pydantic model without importing it, since this module sits below the model layer.
    """
    return NormalizedResult(
        purpose_statement=profile.purpose_statement,
        key_tasks=profile.key_tasks,
        management_line=profile.management_line,
        budget_responsibility=profile.budget_responsibility,
        level_indicator=profile.level_indicator,
        level_evidence=profile.level_evidence,
        qualifications=profile.qualifications,
    )


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

    # The enum makes the API reject anything off-ladder, so a value here is already valid.
    # Guarded anyway: an off-ladder string reaching the embedding text would be a rung nothing
    # else shares, which is worse than no rung at all.
    level = (result.get("level_indicator") or "").strip().lower()
    if level not in LEVEL_LADDER:
        if level:
            print(f"  [normalization] discarding off-ladder level {level!r}")
        level = None

    return NormalizedResult(
        purpose_statement=result["purpose_statement"].strip(),
        key_tasks=tasks,
        management_line=_none_if_blank(result.get("management_line")),
        budget_responsibility=_none_if_blank(result.get("budget_responsibility")),
        level_indicator=level,
        level_evidence=_none_if_blank(result.get("level_evidence")),
        qualifications=_none_if_blank(result.get("qualifications")),
    )


def _none_if_blank(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {
        "null", "none", "n/a", "not specified", "not indicated",
        # Phrasings the model reaches for instead of null on the qualifications field. Left as
        # text they would embed as a shared token across every unqualified role, inventing a
        # similarity between a cleaner and a project manager.
        "none required", "no formal qualifications", "no formal qualification required",
        "not applicable",
    }:
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
