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
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["id", "name", "description"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clusters"],
    "additionalProperties": False,
}

_ENTITY_VOCAB = {
    "job": {
        "profile": "anchor roles (broad job titles, e.g. 'Software Engineer', 'Procurement Analyst')",
        "category": "job categories — the kind of work a group of profiles does (e.g. 'Software Engineering', 'Advisory', 'Design')",
        "family": "job families — the broadest domain groupings (e.g. 'Technology', 'Finance', 'Risk & Compliance')",
    },
    "skill": {
        "profile": "individual skills (1-3 word attribute names, e.g. 'Data Modelling', 'Stakeholder Influence')",
        "category": "skill clusters — related groups of skills",
        "family": "skill families — the broadest skill groupings",
    },
    "task": {
        "profile": "task clusters — groups of the same activity done across different jobs",
        "category": "task categories — related groups of task clusters",
        "family": "task domains — the broadest task groupings",
    },
}

# Tasks are things people DO, so their names are written as activities. Left to its
# own devices the model produces marketing-flavoured noun phrases — "Proactive
# Customer Support", "Strategic Vendor Management" — where the adjective carries no
# information, cannot be verified from the underlying tasks, and makes two clusters
# doing the same work look different.
_TASK_RULES = (
    "Name each cluster as the ACTIVITY being done, in the active voice:\n"
    "- Start with the verb where it reads naturally: 'Supporting Customers', not "
    "'Proactive Customer Support'. 'Reconciling Invoices', not 'Invoice "
    "Reconciliation Excellence'.\n"
    "- NO adjectives unless the name is wrong without one. 'Proactive', "
    "'Strategic', 'Effective', 'Comprehensive', 'End-to-End', 'Robust' and the like "
    "add nothing and must be dropped. Keep an adjective only where it distinguishes "
    "this cluster from a sibling that would otherwise share its name.\n"
    "- Be terse. Drop any word that does not change the meaning: 'Software Feature "
    "Engineering' is 'Software Engineering'. Two or three words is usually right; "
    "use a fourth only to disambiguate from a sibling.\n"
    "- ONE activity per name. No 'and', no '&', no slashes joining two activities.\n"
    "- Where a cluster contains a single underlying task, adopting that task's own "
    "name is correct and preferred — do not invent a broader label for a group of "
    "one."
)

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
    if entity == "job":
        rules = _JOB_LEVEL_RULES.get(level)
    elif entity == "task":
        # The same activity-voice rules apply at all three task levels; only the
        # breadth differs, and the level label above already carries that.
        rules = _TASK_RULES
    else:
        rules = None
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
        "quotes. Return a name for every cluster id.\n\n"
        + _DESCRIPTION_RULES
    )


# A name of two to five words cannot carry what a group actually contains, and two
# neighbouring clusters routinely end up with names a reader cannot choose between.
# The description is what makes them distinguishable without opening each one.
#
# Written from the items shown rather than from the name: a description that only
# restates the label in longer words adds nothing, and the failure is silent because
# it still reads as a sentence.
_DESCRIPTION_RULES = (
    "For each cluster also write `description`: ONE sentence, 15-30 words, saying what "
    "the items in it have in common and what distinguishes it from its siblings.\n"
    "- Write it from the items you were shown, not from the name you chose. It must "
    "tell a reader something the name does not.\n"
    "- Do not begin by restating the name, and do not begin with 'This cluster', "
    "'This group' or 'A group of'. Nor with 'Covers', 'Involves', 'Includes', "
    "'Focuses on', 'Encompasses' or 'Relates to' — those say only that a group "
    "contains its contents, and cost two of the words you have. Open on the work "
    "itself: 'Managing digital media files and resolving playback faults', not "
    "'Focuses on managing digital media files'.\n"
    "- Be concrete and neutral. No marketing adjectives, no claims about quality, "
    "importance or strategic value.\n"
    "- Where the cluster holds a single item, describe that item rather than "
    "inventing a broader theme around it."
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

    Each cluster now also carries a one-sentence description, which is ~35 tokens of
    output — so the per-cluster allowance rose from 400 to 600. Under-budgeting here
    truncates the JSON mid-array, and the ids that come back short are then re-asked
    for; the retry masks the shortfall but pays for the level twice.
    """
    return min(32_000, 3_000 + 600 * n)


def name_level(
    entity: str,
    level: str,
    blocks: list[str],
    n_expected: int,
    *,
    has_parent_context: bool = False,
    progress=None,
) -> tuple[dict[int, str], dict[int, str]]:
    """(names, descriptions) keyed by cluster id.

    blocks: one build_cluster_block() string per cluster, in cluster-id order.

    Names and descriptions come from one call rather than two. The model is already
    looking at the exemplars and at every sibling name it is producing, which is
    exactly what a distinguishing sentence needs — and a second pass would double the
    cost of the step that already dominates a level's spend.

    `progress(named, total)` is called after each batch — naming a large level takes
    minutes, and without it the UI shows a stalled bar through the whole thing.
    """
    system = _build_system_prompt(entity, level, has_parent_context=has_parent_context)
    names: dict[int, str] = {}
    descriptions: dict[int, str] = {}

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
        wanted = {int(b.split("]")[0][1:]) for b in batch}
        got_names, got_descs = _name_batch(prompt, system, batch, wanted)
        names.update(got_names)
        descriptions.update(got_descs)
        if progress:
            progress(min(len(names), n_expected), n_expected)

    return _ensure_complete_and_unique(names, descriptions, blocks, entity, level)


def _name_batch(
    prompt: str, system: str, batch: list[str], wanted: set[int]
) -> tuple[dict[int, str], dict[int, str]]:
    """One naming call, retried once for whatever it failed to name.

    The model occasionally returns fewer rows than it was asked for. That used to be
    logged and shrugged off, which left a cluster with members and no name — and
    because the tier above iterates the *names*, its members were then silently
    orphaned out of the hierarchy with no parent at all. It is a correctness problem,
    not a cosmetic one, so the ids that came back short are asked for again.
    """
    out: dict[int, str] = {}
    descs: dict[int, str] = {}
    for attempt in range(2):
        result = llm.complete_json(
            prompt,
            system=system,
            json_schema=NAME_SCHEMA,
            effort="low",
            max_tokens=_token_budget(len(batch)),
        )
        for c in result["clusters"]:
            cid, name = c["id"], c["name"].strip()
            if cid in wanted and name:
                out[cid] = name
                # A missing description is not worth a retry — the name is what the
                # hierarchy depends on, and re-asking a whole batch to recover a
                # sentence would pay for the level twice over cosmetics.
                desc = str(c.get("description") or "").strip()
                if desc:
                    descs[cid] = desc

        missing = wanted - set(out)
        if not missing:
            break
        if attempt == 0:
            retry = [b for b in batch if int(b.split("]")[0][1:]) in missing]
            print(f"  [naming] {len(missing)} cluster(s) came back unnamed — asking again for {sorted(missing)}")
            prompt = "Name each cluster:\n\n" + "\n".join(retry)
            batch = retry
    return out, descs


def _ensure_complete_and_unique(
    names: dict[int, str], descriptions: dict[int, str], blocks: list[str], entity: str, level: str
) -> tuple[dict[int, str], dict[int, str]]:
    """Every cluster ends up named, and no two names collide.

    Both guarantees are enforced here rather than trusted from the model, because
    both failures are silent and both corrupt the hierarchy:

      - an unnamed cluster is dropped by the tier above, orphaning its members;
      - two clusters sharing a name are indistinguishable in every view and every
        export, and read as the same group listed twice.

    A cluster the model would not name falls back to its own leading exemplar, which
    is a worse name than the model's but an honest one. A duplicate keeps the first
    occurrence and suffixes the rest — visible, so it can be corrected by hand,
    rather than two identical rows nobody can tell apart.
    """
    by_id = {int(b.split("]")[0][1:]): b for b in blocks}

    unnamed = sorted(set(by_id) - set(names))
    for cid in unnamed:
        # "[12] first exemplar; second exemplar" -> "First exemplar"
        detail = by_id[cid].split("] ", 1)[-1].split(";")[0].strip().rstrip(".")
        fallback = (detail[:60] or f"Cluster {cid}")
        names[cid] = fallback[0].upper() + fallback[1:]
        print(f"  [naming] {entity}/{level}: cluster {cid} unnamed after retry — using {names[cid]!r}")

    # Descriptions are optional by design: a cluster with a name and no sentence is
    # usable, one with neither is not. So they are neither invented nor deduplicated —
    # two clusters may legitimately be described in similar terms, and a suffix on a
    # sentence would read as a defect rather than as the disambiguation it is on a name.
    descriptions = {cid: d for cid, d in descriptions.items() if cid in names}

    seen: dict[str, int] = {}
    for cid in sorted(names):
        key = names[cid].strip().lower()
        if key in seen:
            n = 2
            while f"{key} ({n})" in seen:
                n += 1
            names[cid] = f"{names[cid]} ({n})"
            seen[names[cid].strip().lower()] = cid
            print(f"  [naming] {entity}/{level}: cluster {cid} duplicated "
                  f"cluster {seen[key]}'s name — renamed to {names[cid]!r}")
        else:
            seen[key] = cid
    return names, descriptions
