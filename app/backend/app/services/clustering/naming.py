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
        "profile": "job profiles (specific role titles, e.g. 'DevOps Engineer', 'Procurement Analyst')",
        "category": "job categories — broader functional groupings above job profiles (e.g. 'Application Engineering', 'Procurement')",
        "family": "job families — the broadest, division-style groupings (e.g. 'Digital & Technology', 'Finance')",
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
        else " These are the broadest, top-level groupings — keep names general "
        "enough to plausibly contain several more specific sub-groups."
    )
    return (
        f"You name clusters to form part of a taxonomy of {label}. Each cluster is "
        "given as representative example items. Produce a short, specific label "
        "(2-5 words) for each, capturing the shared theme. Every name MUST be "
        "clearly distinct from the others in this set; sharpen close ones by "
        "function, specialism, or domain rather than using near-duplicate wording."
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
