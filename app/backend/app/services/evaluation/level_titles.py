"""Suggest level band titles that fit the client, instead of the shipped defaults.

The default bands are converted from the Legacy jaStudio design CSVs and read as
manufacturing/engineering grades — Operative, Technician, Analyst / Engineer, Specialist /
Lead. They are a reasonable neutral ladder and completely wrong for most clients: a bank
levels people Analyst → Associate → AVP → VP → Director → MD, a hospital trust uses Agenda
for Change bands, a retailer talks about Colleague and Store Manager. Left alone, every
project inherits the same eight names and the levelling output reads as generic.

**Names only. The score boundaries are not touched.** The boundaries are the framework's
calibration — they decide which profiles land where, and they were derived from the domain
weightings. Letting a naming call move them would quietly re-level the whole architecture
under the guise of relabelling it. If the model thinks the *number* of bands is wrong it
says so in `note` and the user decides; it cannot act on that itself.

The strongest context here is not the company description — it is the client's own job
architecture. Family names and a spread of real profile titles say what kind of
organisation this is far more reliably than a paragraph of marketing copy, and they come
free from work already done.
"""
from __future__ import annotations

from app.models.project_state import JEFrameworkConfig, LevelBand, ProjectState
from app.services import llm

# Titles per call is small and the output is short, so this stays one call.
SCHEMA = {
    "type": "object",
    "properties": {
        "levels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "name": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["index", "name", "rationale"],
                "additionalProperties": False,
            },
        },
        "ladder_note": {"type": "string"},
    },
    "required": ["levels", "ladder_note"],
    "additionalProperties": False,
}

SYSTEM = (
    "You name the rungs of a job levelling ladder for a specific organisation. You are "
    "given the client's context and its real job architecture, plus the existing bands "
    "with their score ranges.\n\n"
    "Rules:\n"
    "- Return exactly one name per band, in the same order, lowest score first.\n"
    "- Use the terminology this organisation and its sector actually use. A bank does not "
    "have Operatives; a hospital does not have Principals.\n"
    "- Names must form a ladder a reader can order without seeing the scores, and must be "
    "mutually distinct.\n"
    "- These are levels, not job titles: 'Vice President' not 'Vice President, Credit "
    "Risk'. No function names, no department names.\n"
    "- 1-4 words. No score numbers, no letter grades, no 'Level 3'.\n"
    "- If the number of bands looks wrong for this sector, say so in ladder_note and still "
    "name the bands you were given. Do not add or drop bands.\n"
    "- ladder_note is one sentence, or empty if the ladder shape is fine."
)


def _architecture_context(state: ProjectState, max_titles: int = 40) -> str:
    """Family names and a spread of profile titles — what the client actually looks like."""
    lines: list[str] = []
    tiers = state.clustering_tiers
    fam = tiers.get("family")
    if fam and fam.names:
        lines.append("Job families: " + ", ".join(sorted(fam.names.values())))
    cat = tiers.get("category")
    if cat and cat.names:
        names = sorted(cat.names.values())
        lines.append(f"Job categories ({len(names)}): " + ", ".join(names[:30]))

    # Titles spread across the whole set rather than the first N, so the sample carries
    # both the junior and the senior end of the organisation. An alphabetical head would
    # be all Accounts Assistants and no Directors.
    docs = state.job_profiles
    if docs:
        step = max(1, len(docs) // max_titles)
        sample = [d.title for d in docs[::step]][:max_titles]
        lines.append(f"Anchor roles ({len(docs)} total), a sample: " + ", ".join(sample))
    return "\n".join(lines)


def suggest_level_titles(
    state: ProjectState, framework: JEFrameworkConfig
) -> tuple[list[LevelBand], list[str], str]:
    """Proposed band names for the existing bands.

    Returns (bands with new names and original boundaries, per-band rationales, note).
    """
    bands = sorted(framework.level_bands, key=lambda b: b.min_score)
    if not bands:
        raise ValueError("the framework has no level bands to name")

    meta = state.meta
    context = [f"Organisation: {meta.display_name}"]
    if meta.client_company_description:
        context.append(f"About the organisation: {meta.client_company_description}")
    arch = _architecture_context(state)
    if arch:
        context.append(arch)

    listing = "\n".join(
        f"{i}. scores {b.min_score:g}-{b.max_score:g} (currently called {b.name!r})"
        for i, b in enumerate(bands)
    )
    prompt = (
        "\n".join(context)
        + f"\n\nThe levelling ladder has {len(bands)} bands, lowest first:\n{listing}\n\n"
        f"Name all {len(bands)} bands for this organisation."
    )

    data = llm.complete_json(prompt, system=SYSTEM, json_schema=SCHEMA, effort="medium")

    by_index = {int(x["index"]): x for x in data.get("levels", [])}
    named: list[LevelBand] = []
    rationales: list[str] = []
    for i, b in enumerate(bands):
        got = by_index.get(i)
        name = (got or {}).get("name", "").strip()
        # A band the model skipped keeps its existing name rather than becoming blank or
        # shifting the ladder — a missing rung is a worse failure than an unchanged one.
        named.append(LevelBand(name=name or b.name, min_score=b.min_score, max_score=b.max_score))
        rationales.append((got or {}).get("rationale", "").strip() or "unchanged — no suggestion returned")

    # Distinctness is a hard requirement: two bands with the same name make the levelling
    # output ambiguous, and the model does occasionally collapse adjacent senior rungs.
    seen: dict[str, int] = {}
    for i, b in enumerate(named):
        key = b.name.casefold()
        if key in seen:
            b.name = bands[i].name
            rationales[i] = f"duplicate of band {seen[key] + 1} — kept the existing name"
        else:
            seen[key] = i

    return named, rationales, (data.get("ladder_note") or "").strip()
