"""Batched, set-aware cluster naming.

One structured-output call per hierarchy level, naming ALL clusters at that level
together — this forces mutual distinctiveness (the model sees every sibling name
it's about to produce, not one cluster in isolation). Ported from
`taxonomy_run.py::name_level()` / `Legacy jaStudio/Hierarchical/cluster_jobs.py`'s
NAME_SYSTEM, generalized per entity type (job/skill/task) with distinct naming
vocabulary and parent-context threading for the 3-tier hierarchy.
"""
from __future__ import annotations

from app.services import llm

NAME_SCHEMA = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                "required": ["id", "name"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clusters"],
    "additionalProperties": False,
}

_ENTITY_VOCAB = {
    "job": {
        "profile": "job profiles (broad job titles, e.g. 'Software Engineer', 'Procurement Analyst')",
        "category": "job categories — the kind of work a group of profiles does (e.g. 'Software Engineering', 'Advisory', 'Design')",
        "family": "job families — the broadest domain groupings (e.g. 'Technology', 'Finance', 'Risk & Compliance')",
    },
    "skill": {
        "profile": "individual skills (1-3 word attribute names, e.g. 'Data Modelling', 'Stakeholder Influence')",
        "category": "skill clusters — related groups of skills",
        "family": "skill families — the broadest skill groupings",
    },
    "task": {
        "profile": "individual tasks (2-4 word action names, e.g. 'Invoice Reconciliation')",
        "category": "task categories — related groups of tasks",
        "family": "task domains — the broadest task groupings",
    },
}

# Seniority is a property of an individual job, not of a field of work — a category
# called "Administration & Entry Level Support" describes the grade of the people in
# it rather than what they do, and the same work reappears under a different label
# the moment someone is promoted. So the category and family tiers name the work
# only. The single exception is the board: an executive office genuinely IS a
# distinct field of work, not a senior version of another one.
_NO_SENIORITY = (
    "- Never refer to seniority, grade or career stage. No 'Senior', 'Junior', "
    "'Entry Level', 'Head of', 'Lead', 'Trainee', 'Apprentice', 'Assistant', "
    "'Support Staff', 'Graduate' — and never as an appended qualifier either: "
    "'Administration' is right, 'Administration & Entry Level Support' is wrong. "
    "Name the work, not the level of the people doing it.\n"
    "- The ONLY exception is the board: an executive office at that level is its own "
    "field of work, so 'Executive Leadership' or 'Executive Office' is allowed for a "
    "cluster of genuine board-level executives (CEO, CFO, COO, Chief Risk Officer, "
    "Managing Director of a division). Do not use it for senior management below "
    "the board.\n"
)

# What a name at each tier has to BE, not merely what it is about. Separate from
# _ENTITY_VOCAB because the three job tiers want three different kinds of noun, and
# saying "short, specific label" at all three produced the wrong thing at two of
# them: abstract function nouns where a job title belonged ("Design" rather than
# "Designer"), and title-shaped category names duplicating their own children.
_JOB_LEVEL_RULES = {
    "profile": (
        "Name each cluster as a JOB TITLE — what you would put on the profile "
        "document and in an org chart. A person could hold this title.\n"
        "- Use the role noun, never the abstract function: 'Designer' not 'Design', "
        "'Tester' not 'Testing', 'Underwriter' not 'Underwriting'.\n"
        "- Seniority and shape words are wanted where the cluster shares them: "
        "Head of ..., Director, Manager, Lead, Analyst, Adviser, Engineer, Officer, "
        "Specialist, Administrator. Prefer 'Head of Risk' over 'Risk Leadership' and "
        "'Compliance Manager' over 'Compliance Oversight'.\n"
        "- The title must cover EVERY role in the cluster, at the level of generality "
        "that does. If the members span seniorities, drop the seniority word rather "
        "than picking one member's ('Pricing Analyst', not 'Senior Pricing Analyst').\n"
        "- Do NOT compound two roles. No 'and', no '&', no slashes joining different "
        "jobs — 'Design and Test Engineer' or 'Underwriter / Claims Handler' are "
        "wrong. Choose the single closest common title instead.\n"
        "- Two to four words. No department names, no location, no grade codes."
    ),
    "category": (
        "Name each cluster as a FIELD OF WORK, not as a job title — this tier groups "
        "job titles, so a title here would collide with its own children.\n"
        "- Use the discipline or activity: 'Software Engineering', 'Advisory', "
        "'Design', 'Underwriting', 'Credit Risk', 'Customer Service', "
        "'Administration'.\n"
        "- Never a person noun: 'Engineering' not 'Engineer', 'Advisory' not 'Adviser'.\n"
        f"{_NO_SENIORITY}"
        "- One to three words, specific enough to distinguish it from its siblings."
    ),
    "family": (
        "Name each cluster as a BROAD DOMAIN — the handful of top-level groupings a "
        "whole organisation divides into.\n"
        "- Division-scale: 'Technology', 'Finance', 'Risk & Compliance', "
        "'Operations', 'Commercial', 'People', 'Administration'.\n"
        "- General enough to plausibly contain several fields of work beneath it, and "
        "recognisable to someone outside the function.\n"
        f"{_NO_SENIORITY}"
        "- One to three words. Not a job title and not a specific discipline."
    ),
}

_LEVEL_ORDER = ["family", "category", "profile"]  # coarsest -> finest


def _level_label(entity: str, level: str) -> str:
    return _ENTITY_VOCAB[entity][level]


def _build_system_prompt(entity: str, level: str, *, has_parent_context: bool) -> str:
    label = _level_label(entity, level)
    parent_note = (
        " Each cluster is shown with its parent's name for context — make sure your "
        "names are clearly MORE SPECIFIC than the parent, and distinct from names "
        "used at the parent level."
        if has_parent_context
        else ""
    )
    rules = _JOB_LEVEL_RULES.get(level) if entity == "job" else None
    if rules is None:
        rules = (
            "Produce a short, specific label (2-5 words) for each, capturing the "
            "shared theme."
            + (
                ""
                if has_parent_context
                else " These are the broadest, top-level groupings — keep names "
                "general enough to plausibly contain several more specific sub-groups."
            )
        )
    return (
        f"You name clusters to form part of a taxonomy of {label}. Each cluster is "
        f"given as representative example items.\n\n{rules}\n\n"
        "Every name MUST be clearly distinct from the others in this set; sharpen "
        "close ones by function, specialism, or domain rather than using "
        "near-duplicate wording."
        f"{parent_note} Use plain business English, title case, no numbering, no "
        "quotes. Return a name for every cluster id."
    )


def build_cluster_block(
    cluster_id: int,
    exemplar_texts: list[str],
    *,
    parent_name: str | None = None,
) -> str:
    parent_note = f" (parent: {parent_name})" if parent_name else ""
    items = "; ".join(exemplar_texts)
    return f"[{cluster_id}]{parent_note} {items}"


# Clusters named per call. Naming a whole level in one call is what forces mutual
# distinctiveness, so this wants to be as large as it can be — but a real client
# taxonomy has 150+ job profiles, and one call for all of them means a giant prompt,
# minutes of silence, and an output that overruns any sane token budget. Batching at
# this size keeps each call a manageable unit of progress while still giving the
# model enough siblings at once to differentiate between; names already assigned in
# earlier batches are passed forward so distinctiveness holds across the whole level.
NAME_BATCH = 30


def _token_budget(n: int) -> int:
    """Output budget for naming `n` clusters.

    A name is ~10 tokens, but with adaptive thinking the reasoning shares this
    budget and grows with the number of siblings being kept distinct. A fixed 4000
    was the original value and truncated at around 40 clusters.
    """
    return min(24_000, 3_000 + 400 * n)


def name_level(
    entity: str,
    level: str,
    blocks: list[str],
    n_expected: int,
    *,
    has_parent_context: bool = False,
    progress=None,
) -> dict[int, str]:
    """blocks: one build_cluster_block() string per cluster, in cluster-id order.

    `progress(named, total)` is called after each batch — naming a large level takes
    minutes, and without it the UI shows a stalled bar through the whole thing.
    """
    system = _build_system_prompt(entity, level, has_parent_context=has_parent_context)
    names: dict[int, str] = {}

    for start in range(0, len(blocks), NAME_BATCH):
        batch = blocks[start : start + NAME_BATCH]
        prompt = "Name each cluster:\n\n" + "\n".join(batch)
        if names:
            # Sequential batches, not parallel, precisely so this list exists: the
            # model can only avoid near-duplicates it has been shown.
            used = "; ".join(sorted(names.values()))
            prompt = (
                "These names are already in use by other clusters in this same "
                f"taxonomy level. Yours must be clearly distinct from all of them:\n{used}\n\n"
                + prompt
            )
        result = llm.complete_json(
            prompt,
            system=system,
            json_schema=NAME_SCHEMA,
            effort="low",
            max_tokens=_token_budget(len(batch)),
        )
        names.update({c["id"]: c["name"].strip() for c in result["clusters"]})
        if progress:
            progress(min(len(names), n_expected), n_expected)

    missing = set(range(n_expected)) - set(names)
    if missing:
        print(f"  [naming] {entity}/{level}: got {len(names)}/{n_expected} clusters — missing ids {sorted(missing)}")
    return names
