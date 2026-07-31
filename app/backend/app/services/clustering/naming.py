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


def name_level(
    entity: str,
    level: str,
    blocks: list[str],
    n_expected: int,
    *,
    has_parent_context: bool = False,
) -> dict[int, str]:
    """blocks: one build_cluster_block() string per cluster, in cluster-id order."""
    system = _build_system_prompt(entity, level, has_parent_context=has_parent_context)
    prompt = "Name each cluster:\n\n" + "\n".join(blocks)
    result = llm.complete_json(prompt, system=system, json_schema=NAME_SCHEMA, effort="low", max_tokens=4000)
    names = {c["id"]: c["name"].strip() for c in result["clusters"]}
    missing = set(range(n_expected)) - set(names)
    if missing:
        print(f"  [naming] {entity}/{level}: got {len(names)}/{n_expected} clusters — missing ids {sorted(missing)}")
    return names
