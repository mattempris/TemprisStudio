"""Skipping an optional step.

Two kinds of step can be skipped, and the difference decides what skipping *does*:

**Identity steps** produce an artefact the rest of the pipeline reads, so they cannot simply
be omitted — the chain would break at the next step. Skipping one performs the trivial version
of it instead: deduplication with nothing merged is one group per record; clustering anchor
roles one-to-one is one cluster per normalised job, named from the title the spreadsheet
already carried. Downstream reads the same shapes it always read and contains no branch for
"was this skipped?".

**Omission steps** have nothing downstream depending on them — a job evaluation, a taxonomy
match, a proficiency mapping. Skipping one records the decision and does nothing else.

That split is the point. Threading a `skipped` flag through the ten places that read
`dedupe_groups` would be ten chances to disagree; writing the identity grouping is none. The
`skipped_steps` list exists so the app can *say* what was skipped, not so it can behave
differently — a summary reading "142 distinct jobs" when nobody deduplicated anything is a
claim the project cannot support.

Every skip is reversible: running the real step overwrites the trivial artefact and clears the
marker, which is the same revisitable flow the wizard has everywhere else.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from app.models.project_state import (
    DedupeGroup,
    ProjectState,
)


@dataclass(frozen=True)
class SkippableStep:
    """One step a user may decline, and what declining it means."""

    id: str
    label: str
    kind: str  # "identity" | "omission"
    # Shown on the control, so the consequence is on screen before the click rather than
    # discovered afterwards.
    consequence: str


# Which steps are optional is a property of the pipeline, not a preference: a step is optional
# when the project can reach a complete job architecture without it. `normalize` is absent
# because everything downstream is clustered from what it writes, and `profiles` because the
# skills, tasks, evaluation and matching steps all read the documents it generates.
SKIPPABLE: tuple[SkippableStep, ...] = (
    SkippableStep(
        "strip",
        "Strip irrelevant content",
        "identity",
        "The uploaded text is carried through unchanged. Boilerplate, benefits and "
        "recruitment logistics stay in, and will influence how jobs group together.",
    ),
    SkippableStep(
        "dedupe",
        "Deduplicate",
        "identity",
        "Every record is treated as its own distinct job. Nothing is merged, so two "
        "spellings of the same role stay two roles.",
    ),
    SkippableStep(
        "cluster",
        "Anchor roles",
        "identity",
        "Each normalised job becomes its own anchor role, named from the job title in the "
        "upload. No grouping and no naming call — a one-to-one mapping.",
    ),
    SkippableStep(
        "categories",
        "Job categories",
        "identity",
        "Each anchor role becomes its own category. The hierarchy keeps its shape but adds "
        "no grouping at this level.",
    ),
    SkippableStep(
        "families",
        "Job families",
        "identity",
        "Each category becomes its own family. The architecture ends up flat.",
    ),
    SkippableStep(
        "evaluation",
        "Job evaluation",
        "omission",
        "No roles are scored and no levels are assigned. Everything else is unaffected.",
    ),
    SkippableStep(
        "skills",
        "Skills taxonomy",
        "omission",
        "No skills are inferred or clustered. The work architecture graph will show roles "
        "and tasks but no skills.",
    ),
    SkippableStep(
        "tasks",
        "Task taxonomy",
        "omission",
        "No tasks are inferred. This also leaves Work Architecture and Work Design Studio "
        "with nothing to analyse, since both are built on the task structure.",
    ),
    SkippableStep(
        "matching",
        "3rd-party taxonomy match",
        "omission",
        "Roles are not placed in the external market taxonomy and get no career level "
        "from it.",
    ),
)

BY_ID: dict[str, SkippableStep] = {s.id: s for s in SKIPPABLE}


def is_skipped(state: ProjectState, step_id: str) -> bool:
    return step_id in state.skipped_steps


def mark(state: ProjectState, step_id: str) -> None:
    if step_id not in state.skipped_steps:
        state.skipped_steps.append(step_id)


def unmark(state: ProjectState, step_id: str) -> None:
    """Clear the marker. Called when the real step runs, so a skip is never sticky."""
    state.skipped_steps = [s for s in state.skipped_steps if s != step_id]


# ---- identity operations ----------------------------------------------------------------


def skip_strip(state: ProjectState) -> int:
    """Carry the raw text through as if it had been stripped, removing nothing.

    `removed_sections` stays empty and the model is named for what happened, so an export or
    a report can tell a passthrough from a strip that found nothing to remove. Those are
    different facts and only one of them is a judgement about the source text.
    """
    from app.models.project_state import JobRecordStripped

    now = datetime.now(timezone.utc)
    state.stripped_records = [
        JobRecordStripped(
            id=r.id,
            stripped_text=r.raw_text,
            removed_sections=[],
            model="skipped",
            generated_at=now,
        )
        for r in state.raw_records
    ]
    return len(state.stripped_records)


def skip_dedupe(state: ProjectState) -> int:
    """One group per stripped record — the identity grouping.

    Group ids follow the same `grp-NNNN` form the real step writes, because
    `NormalizedProfile.id` is a dedupe group id and a different scheme here would make the
    two paths produce differently-shaped keys for the same thing.

    `avg_similarity` is 1.0: a group of one is perfectly self-similar, and that happens to be
    the same value the manual-grouping path uses. `user_confirmed` is True because skipping
    *is* the user's decision — leaving it False would show the step as still awaiting one.
    """
    state.dedupe_groups = [
        DedupeGroup(
            group_id=f"grp-{i:04d}",
            member_ids=[r.id],
            representative_id=r.id,
            avg_similarity=1.0,
            user_confirmed=True,
        )
        for i, r in enumerate(state.stripped_records)
    ]
    # No threshold was chosen, and inventing one would put a number in the summary line that
    # no one picked. The UI reads None as "not deduplicated".
    state.dedupe_threshold = None
    return len(state.dedupe_groups)


def source_titles(state: ProjectState) -> dict[str, str]:
    """Normalised-profile id -> the job title the upload carried.

    The route from a normalised profile back to a spreadsheet cell runs through the dedupe
    group: `NormalizedProfile.id` *is* a group id, and the group's members are raw record
    ids. Where a group holds several records they were judged the same job, so any member's
    title describes the group; the representative's is the one the dedupe step already chose
    to stand for it, which makes this consistent with what the rest of the app shows.
    """
    by_id = {r.id: r for r in state.raw_records}
    out: dict[str, str] = {}
    for g in state.dedupe_groups:
        rep = by_id.get(g.representative_id)
        if rep is None:
            # A representative that no longer exists means the group predates a re-ingest.
            # Fall back to any surviving member rather than dropping the title entirely.
            rep = next((by_id[m] for m in g.member_ids if m in by_id), None)
        if rep is not None:
            out[g.group_id] = rep.job_title
    return out


def identity_tier_result(
    ids: list[str],
    texts: list[str],
    embeddings: np.ndarray,
    names: list[str],
    *,
    gate: float,
):
    """Each item its own cluster, in the shape `tier_state.save_tier` expects.

    Built directly rather than by calling `tier.finalise` with k == len(items). Three reasons,
    and they are the same reason from three angles: `analyse` rejects k >= len(items) by
    design, the consensus stability of a singleton cluster is not a meaningful number, and the
    naming call is the exact cost being avoided. Constructing the result here is honest about
    there being no geometry and no model involved.

    `stability_score` is None rather than 1.0. A cluster of one cannot be perturbed into
    disagreeing with itself, so a perfect score would be a measurement nobody took — and the
    review UI already renders None as "not scored".

    Centroids are the item embeddings themselves, which is what makes a skipped tier
    composable: the tier above clusters *these* centroids, so it receives exactly the vectors
    it would have received had this tier been run.
    """
    from app.services.clustering import tier as tier_engine

    n = len(ids)
    return tier_engine.TierResult(
        k=n,
        gate=gate,
        names={i: (names[i].strip() if i < len(names) and names[i].strip() else f"Item {i + 1}")
               for i in range(n)},
        members=[
            tier_engine.TierMemberOutcome(
                item_id=item_id,
                backbone_cluster_id=i,
                final_cluster_id=i,
                stability_score=None,
                routed_by_llm=False,
            )
            for i, item_id in enumerate(ids)
        ],
        n_routed=0,
        n_moved=0,
        low_confidence=0,
        multi_home=0,
        centroids=np.asarray(embeddings, dtype=np.float32).copy(),
        exemplar_texts={i: [texts[i]] for i in range(n) if i < len(texts)},
    )
