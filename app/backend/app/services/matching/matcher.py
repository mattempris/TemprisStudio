"""Match job profiles into the 3rd-party taxonomy (instructions.txt step 11).

Three stages, mirroring `jobMatching`'s pipeline:

  1. Shortlist   — jobQWEN cosine, top-N specializations. Free, instant.
  2. Rerank      — one LLM call picks the best of the N and gives a confidence
                   and a rationale. Replaces the reference's Voyage reranker: an
                   LLM that must justify its pick produces an auditable match,
                   which matters more here than the last few points of ranking
                   accuracy, and it needs no extra vendor.
  3. Career level — a second call places the profile on the specialization's
                   available levels using the level definitions.

Level assignment is deliberately a separate call. Folding it into the rerank
makes level a matching signal — the model starts preferring specializations whose
levels fit, which is backwards. Splitting them also means a user can correct a
level without invalidating the match, and vice versa.

Cosine score is carried through to the output unchanged, so a low-similarity
shortlist that the LLM nonetheless picked from is visible rather than laundered
into a confident-looking match.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np

from app.services import llm
from app.services.embeddings import get_embedding_service
from app.services.matching import taxonomy
from app.services.matching.index import TaxonomyIndex

DEFAULT_SHORTLIST = 12

# Below this cosine, the shortlist is unlikely to contain a real match at all.
# Recorded as a flag rather than used to suppress the match — "nothing in the
# taxonomy fits this role" is a finding the user needs to see, and the same
# missing-coverage signal the clustering stage treats as a taxonomy gap.
WEAK_SHORTLIST_COSINE = 0.55

# Below this rerank confidence the match is surfaced for review rather than
# accepted silently. Mirrors the clustering engine's self-consistency threshold.
REVIEW_CONFIDENCE = 0.55

RERANK_SCHEMA = {
    "type": "object",
    "properties": {
        "choice": {"type": "integer"},  # 1-based index into the shortlist; 0 = none fit
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        "runner_up": {"type": "integer"},  # 0 when there is no credible second
    },
    "required": ["choice", "confidence", "rationale", "runner_up"],
    "additionalProperties": False,
}

LEVEL_SCHEMA = {
    "type": "object",
    "properties": {
        "level_code": {"type": "string"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["level_code", "confidence", "rationale"],
    "additionalProperties": False,
}

RERANK_SYSTEM = (
    "You map an organisation's job profile onto a standard external job "
    "taxonomy.\n\n"
    "You are given the profile and a numbered shortlist of candidate "
    "specializations, each shown as Family > Sub-family > Specialization with "
    "typical titles.\n\n"
    "Choose the candidate whose SCOPE OF WORK matches the profile. Judge on what "
    "the role actually does, not on title-word overlap — 'Business Partner' in "
    "HR and 'Business Partner' in Finance share a title and nothing else.\n\n"
    "choice: the 1-based number of the best candidate, or 0 if none is a "
    "defensible match. Returning 0 is correct and useful when the taxonomy has "
    "no bucket for this role; do not stretch to the nearest option.\n"
    "confidence: 0.0-1.0. Reserve above 0.85 for matches you would defend to the "
    "client unprompted. Use below 0.5 when you are picking the least-bad option.\n"
    "runner_up: the number of a genuine second-best candidate, or 0 if there "
    "isn't one. Use this when the role legitimately straddles two "
    "specializations.\n"
    "rationale: one or two sentences, naming the specific scope evidence that "
    "decided it."
)

LEVEL_SYSTEM = (
    "You place a job profile on a standard career-level scale.\n\n"
    "You are given the profile and the career levels available for its matched "
    "specialization, each with a definition.\n\n"
    "Judge on scope, autonomy, impact and management responsibility — not on the "
    "seniority word in the job title, which varies wildly between "
    "organisations.\n\n"
    "level_code: exactly one of the codes offered. Do not invent a code.\n"
    "confidence: 0.0-1.0.\n"
    "rationale: one sentence naming the deciding evidence."
)


@dataclass
class Candidate:
    code: str
    title: str
    family_title: str
    sub_family_title: str
    cosine: float


@dataclass
class ProfileMatch:
    profile_key: str
    profile_title: str
    matched: bool
    spec_code: str | None = None
    spec_title: str | None = None
    family_title: str | None = None
    sub_family_title: str | None = None
    cosine: float | None = None
    confidence: float = 0.0
    rationale: str = ""
    runner_up_code: str | None = None
    runner_up_title: str | None = None
    level_code: str | None = None
    level_title: str | None = None
    level_stream: str | None = None
    level_confidence: float = 0.0
    level_rationale: str = ""
    # Auditability: the full shortlist the LLM chose from, plus why this match
    # needs a human look. Same philosophy as the clustering audit columns.
    shortlist: list[Candidate] = field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["shortlist"] = [asdict(c) for c in self.shortlist]
        return d


def profile_text(title: str, content: dict) -> str:
    """The text a profile is matched on.

    Title plus purpose plus responsibilities. Requirements and skills are left
    out on purpose — they describe the person, and including them pulls matches
    toward specializations that share a qualification rather than a scope.
    """
    parts = [title]
    if content.get("about_role"):
        parts.append(str(content["about_role"]))
    resp = content.get("responsibilities") or []
    if resp:
        items = [r.get("value") if isinstance(r, dict) else str(r) for r in resp]
        parts.append(" ".join(str(i) for i in items if i))
    return " ".join(parts).strip()


def _render_shortlist(cands: list[Candidate], specs: list[taxonomy.Specialization]) -> str:
    lines = []
    for i, (c, spec) in enumerate(zip(cands, specs), start=1):
        titles = ", ".join(spec.typical_titles[:8]) if spec.typical_titles else "—"
        lines.append(
            f"{i}. {c.family_title} > {c.sub_family_title} > {c.title}\n"
            f"   typical titles: {titles}"
        )
    return "\n".join(lines)


def _valid_choice(raw, n: int) -> int:
    """Coerce the model's index to a valid 1..n or 0. Out-of-range means no match
    rather than an exception — the model occasionally overshoots the list."""
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 0
    return v if 1 <= v <= n else 0


def _clamp01(raw) -> float:
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def match_profile(
    profile_key: str,
    title: str,
    content: dict,
    index: TaxonomyIndex,
    levels: dict[str, taxonomy.CareerLevel],
    *,
    shortlist_size: int = DEFAULT_SHORTLIST,
    assign_level: bool = True,
) -> ProfileMatch:
    text = profile_text(title, content)
    vec = get_embedding_service().embed_documents("job", [text])
    idx, scores = taxonomy.cosine_shortlist(
        np.asarray(vec, dtype=np.float32), index.vectors, index.offsets, shortlist_size
    )
    picked = [index.specs[i] for i in idx[0]]
    cands = [
        Candidate(s.code, s.title, s.family_title, s.sub_family_title, float(sc))
        for s, sc in zip(picked, scores[0])
    ]

    match = ProfileMatch(
        profile_key=profile_key, profile_title=title, matched=False, shortlist=cands
    )

    prompt = (
        f"JOB PROFILE\n{text}\n\n"
        f"CANDIDATE SPECIALIZATIONS\n{_render_shortlist(cands, picked)}"
    )
    result = llm.complete_json(
        prompt, system=RERANK_SYSTEM, json_schema=RERANK_SCHEMA,
        effort="low", max_tokens=4000,
    )

    choice = _valid_choice(result.get("choice"), len(cands))
    if choice == 0:
        match.rationale = str(result.get("rationale", "")).strip() or (
            "No specialization in the shortlist is a defensible match."
        )
        match.needs_review = True
        match.review_reasons.append("no_match")
        return match

    chosen, chosen_spec = cands[choice - 1], picked[choice - 1]
    match.matched = True
    match.spec_code = chosen.code
    match.spec_title = chosen.title
    match.family_title = chosen.family_title
    match.sub_family_title = chosen.sub_family_title
    match.cosine = chosen.cosine
    match.confidence = _clamp01(result.get("confidence"))
    match.rationale = str(result.get("rationale", "")).strip()

    runner = _valid_choice(result.get("runner_up"), len(cands))
    if runner and runner != choice:
        match.runner_up_code = cands[runner - 1].code
        match.runner_up_title = cands[runner - 1].title

    if match.confidence < REVIEW_CONFIDENCE:
        match.needs_review = True
        match.review_reasons.append("low_confidence")
    if cands[0].cosine < WEAK_SHORTLIST_COSINE:
        match.needs_review = True
        match.review_reasons.append("weak_shortlist")

    if assign_level and chosen_spec.available_levels:
        _assign_level(match, text, chosen_spec, levels)

    return match


def _assign_level(
    match: ProfileMatch,
    text: str,
    spec: taxonomy.Specialization,
    levels: dict[str, taxonomy.CareerLevel],
) -> None:
    offered = []
    for code, level_title in spec.available_levels:
        definition = levels.get(code)
        desc = definition.description if definition else ""
        stream = f" [{definition.stream}]" if definition else ""
        offered.append(f"{code}{stream} — {level_title}: {desc}".rstrip(": "))

    if len(offered) == 1:
        # Only one level exists for this specialization; asking the model to
        # "choose" would be theatre.
        code, level_title = spec.available_levels[0]
        match.level_code, match.level_title = code, level_title
        match.level_stream = levels.get(code).stream if code in levels else None
        match.level_confidence = 1.0
        match.level_rationale = "Only one career level exists for this specialization."
        return

    result = llm.complete_json(
        f"JOB PROFILE\n{text}\n\n"
        f"MATCHED SPECIALIZATION\n{spec.family_title} > {spec.sub_family_title} > {spec.title}\n\n"
        f"AVAILABLE CAREER LEVELS\n" + "\n".join(offered),
        system=LEVEL_SYSTEM, json_schema=LEVEL_SCHEMA, effort="low", max_tokens=3000,
    )
    code = str(result.get("level_code", "")).strip().upper()
    valid = {c: t for c, t in spec.available_levels}
    if code not in valid:
        match.needs_review = True
        match.review_reasons.append("invalid_level")
        return
    match.level_code = code
    match.level_title = valid[code]
    match.level_stream = levels.get(code).stream if code in levels else None
    match.level_confidence = _clamp01(result.get("confidence"))
    match.level_rationale = str(result.get("rationale", "")).strip()
    if match.level_confidence < REVIEW_CONFIDENCE:
        match.needs_review = True
        match.review_reasons.append("low_level_confidence")


def match_many(
    profiles: list[tuple[str, str, dict]],  # (profile_key, title, content)
    index: TaxonomyIndex,
    levels: dict[str, taxonomy.CareerLevel],
    *,
    shortlist_size: int = DEFAULT_SHORTLIST,
    assign_level: bool = True,
    workers: int = 6,
    progress=None,
) -> list[ProfileMatch]:
    return llm.pmap(
        lambda p: match_profile(
            p[0], p[1], p[2], index, levels,
            shortlist_size=shortlist_size, assign_level=assign_level,
        ),
        profiles,
        workers=workers,
        label="taxonomy match",
        progress=progress,
    )


def summarize(matches: list[ProfileMatch]) -> dict:
    matched = [m for m in matches if m.matched]
    return {
        "profiles": len(matches),
        "matched": len(matched),
        "unmatched": len(matches) - len(matched),
        "needs_review": sum(1 for m in matches if m.needs_review),
        "levelled": sum(1 for m in matched if m.level_code),
        "mean_confidence": round(
            sum(m.confidence for m in matched) / len(matched), 3
        ) if matched else 0.0,
        "mean_cosine": round(
            sum(m.cosine or 0.0 for m in matched) / len(matched), 3
        ) if matched else 0.0,
        "families": len({m.family_title for m in matched if m.family_title}),
    }
