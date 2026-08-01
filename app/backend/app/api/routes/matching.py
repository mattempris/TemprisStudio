"""Phase 4 routes — instructions.txt step 11 (3rd-party taxonomy matching).

"Enable matching of job profiles into 3rd party taxonomy (use functionality in
./jobMatching)."
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.project_state import (
    MatchingState,
    ProjectState,
    TaxonomyCandidateRecord,
    TaxonomyMatchRecord,
)
from app.services.matching import index as tax_index
from app.services.matching import matcher, taxonomy
from app.services import llm
from app.services.orchestrator import JobAlreadyRunning, ProgressReporter, get_registry, run_job
from app.services.project_service import ProjectService

router = APIRouter(tags=["matching"])


def _load(client_slug: str, project_slug: str) -> tuple[ProjectService, ProjectState]:
    svc = ProjectService()
    try:
        return svc, svc.load_state(client_slug, project_slug)
    except LookupError as e:
        raise HTTPException(404, f"project not found: {client_slug}/{project_slug}") from e


def _start_job(client_slug: str, project_slug: str, stage: str, work) -> dict:
    registry = get_registry()
    try:
        job = registry.create(client_slug, project_slug, stage)
    except JobAlreadyRunning as e:
        raise HTTPException(
            409, {"message": str(e), "existing_job_id": e.job.job_id, "existing_stage": e.job.stage}
        ) from e
    asyncio.create_task(run_job(job, work))
    return {"job_id": job.job_id, "stage": stage, "websocket_url": f"/ws/pipeline/{job.job_id}"}


# ── Taxonomy metadata (project-independent) ──────────────────────────────────


@router.get("/api/matching/industries")
def matching_industries() -> dict:
    """The 19 atomic industries. Used to scope a project's match to the client's
    own sectors, which both sharpens matches and shrinks the index."""
    try:
        industries = taxonomy.list_industries()
    except taxonomy.TaxonomyUnavailable as e:
        raise HTTPException(503, str(e)) from e
    return {"industries": industries}


@router.get("/api/matching/taxonomy-info")
def taxonomy_info(industries: str | None = None) -> dict:
    """Size of the taxonomy under a given industry scope, without embedding it —
    lets the UI show the user what they're about to match against."""
    scope = [i for i in (industries or "").split(",") if i.strip()] or None
    try:
        specs = taxonomy.load_specializations(scope)
        levels = taxonomy.load_career_levels()
    except taxonomy.TaxonomyUnavailable as e:
        raise HTTPException(503, str(e)) from e
    families: dict[str, int] = {}
    for s in specs:
        families[s.family_title] = families.get(s.family_title, 0) + 1
    return {
        "industries": scope,
        "specializations": len(specs),
        "title_variants": sum(len(s.variant_texts()) for s in specs),
        "families": sorted(
            ({"name": k, "specializations": v} for k, v in families.items()),
            key=lambda x: -x["specializations"],
        ),
        "career_levels": [
            {"code": c, "name": l.name, "stream": l.stream} for c, l in sorted(levels.items())
        ],
    }


# ── Per-project matching ─────────────────────────────────────────────────────

project_router = APIRouter(
    prefix="/api/projects/{client_slug}/{project_slug}/matching", tags=["matching"]
)


class MatchingSummary(BaseModel):
    matched_profiles: int = 0
    total_profiles: int = 0
    industries: list[str] = Field(default_factory=list)
    computed_at: datetime | None = None
    summary: dict = Field(default_factory=dict)


@project_router.get("/summary")
def matching_summary(client_slug: str, project_slug: str) -> MatchingSummary:
    _, state = _load(client_slug, project_slug)
    m = state.matching
    return MatchingSummary(
        matched_profiles=len(m.matches),
        total_profiles=len([p for p in state.job_profiles if not p.stale]),
        industries=m.industries,
        computed_at=m.computed_at,
        summary=m.summary,
    )


class MatchRequest(BaseModel):
    profile_keys: list[str] | None = None
    industries: list[str] | None = None
    shortlist_size: int = Field(default=matcher.DEFAULT_SHORTLIST, ge=3, le=30)
    assign_level: bool = True


@project_router.post("/run")
async def run_matching(
    client_slug: str, project_slug: str, req: MatchRequest, workers: int | None = None
) -> dict:
    svc, state = _load(client_slug, project_slug)
    _workers = llm.resolve_workers(workers)
    available = [p for p in state.job_profiles if not p.stale]
    if not available:
        raise HTTPException(400, "no job profiles yet — generate job profiles first")
    selected = (
        [p for p in available if p.profile_key in set(req.profile_keys)]
        if req.profile_keys
        else available
    )
    if not selected:
        raise HTTPException(400, "none of the requested profile_keys matched a current job profile")

    payload = [(p.profile_key, p.title, p.content) for p in selected]

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(len(payload), "Preparing the 3rd-party taxonomy index")
        try:
            idx = tax_index.build_index(req.industries, progress=reporter.message)
            levels = taxonomy.load_career_levels()
        except taxonomy.TaxonomyUnavailable as e:
            raise RuntimeError(str(e)) from e

        reporter.message(
            f"Matching {len(payload)} job profiles against {len(idx)} specializations "
            f"({idx.n_variants} title variants)"
        )
        results = matcher.match_many(
            payload,
            idx,
            levels,
            shortlist_size=req.shortlist_size,
            assign_level=req.assign_level,
            workers=_workers,
            progress=reporter.pmap_callback(),
        )
        summary = matcher.summarize(results)

        fresh = svc.load_state(client_slug, project_slug)
        # Re-running for a subset replaces only those profiles' matches; anything
        # matched in an earlier run against other profiles is left intact.
        replaced = {m.profile_key for m in results}
        kept = [m for m in fresh.matching.matches if m.profile_key not in replaced]
        fresh.matching = MatchingState(
            industries=req.industries or [],
            shortlist_size=req.shortlist_size,
            matches=kept + [_to_record(m) for m in results],
            summary=summary,
            computed_at=datetime.now(timezone.utc),
        )
        svc.save_state(
            fresh,
            action="match-taxonomy",
            lineage_payload={
                "profiles": len(payload),
                "industries": req.industries,
                "shortlist_size": req.shortlist_size,
                **summary,
            },
        )
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "review", work)


def _to_record(m: matcher.ProfileMatch) -> TaxonomyMatchRecord:
    d = m.to_dict()
    d["shortlist"] = [TaxonomyCandidateRecord(**c) for c in d["shortlist"]]
    return TaxonomyMatchRecord(**d)


@project_router.get("/matches")
def list_matches(client_slug: str, project_slug: str, review_only: bool = False) -> dict:
    _, state = _load(client_slug, project_slug)
    matches = state.matching.matches
    if review_only:
        matches = [m for m in matches if m.needs_review]
    return {
        "matches": [m.model_dump() for m in matches],
        "summary": state.matching.summary,
        "industries": state.matching.industries,
    }


@project_router.get("/browse")
def browse_matches(client_slug: str, project_slug: str) -> dict:
    """The client's profiles arranged under the 3rd-party taxonomy's own tree.

    This is the view that answers "where does our organisation sit in the market
    structure" — the reverse of the internal hierarchy, and the reason the match
    is worth running at all. Headcount rolls up so a family's weight is
    people, not profile count.
    """
    _, state = _load(client_slug, project_slug)
    headcount = _profile_headcount(state)

    families: dict[str, dict] = {}
    unmatched: list[dict] = []
    for m in state.matching.matches:
        row = {
            "profile_key": m.profile_key,
            "profile_title": m.profile_title,
            "spec_code": m.spec_code,
            "spec_title": m.spec_title,
            "level_code": m.level_code,
            "level_title": m.level_title,
            "level_stream": m.level_stream,
            "cosine": m.cosine,
            "confidence": m.confidence,
            "needs_review": m.needs_review,
            "review_reasons": m.review_reasons,
            "overridden_by_user": m.overridden_by_user,
            "headcount": headcount.get(m.profile_key),
        }
        if not m.matched:
            unmatched.append(row)
            continue
        fam = families.setdefault(
            m.family_title or "—", {"name": m.family_title or "—", "sub_families": {}}
        )
        sub = fam["sub_families"].setdefault(
            m.sub_family_title or "—", {"name": m.sub_family_title or "—", "specializations": {}}
        )
        spec = sub["specializations"].setdefault(
            m.spec_code,
            {"code": m.spec_code, "title": m.spec_title, "profiles": []},
        )
        spec["profiles"].append(row)

    def roll(rows: list[dict]) -> dict:
        hc = [r["headcount"] for r in rows if r["headcount"]]
        return {
            "profile_count": len(rows),
            "headcount": sum(hc) if hc else None,
            "needs_review": sum(1 for r in rows if r["needs_review"]),
        }

    out = []
    for fam in families.values():
        subs, fam_rows = [], []
        for sub in fam["sub_families"].values():
            specs, sub_rows = [], []
            for spec in sub["specializations"].values():
                specs.append({**spec, **roll(spec["profiles"])})
                sub_rows.extend(spec["profiles"])
            specs.sort(key=lambda x: -x["profile_count"])
            subs.append({"name": sub["name"], "specializations": specs, **roll(sub_rows)})
            fam_rows.extend(sub_rows)
        subs.sort(key=lambda x: -x["profile_count"])
        out.append({"name": fam["name"], "sub_families": subs, **roll(fam_rows)})
    out.sort(key=lambda x: -x["profile_count"])

    return {
        "families": out,
        "unmatched": unmatched,
        "has_headcount": bool(headcount),
        "summary": state.matching.summary,
    }


def _profile_headcount(state: ProjectState) -> dict[str, int]:
    """Headcount per job profile, rolled up through dedupe groups.

    Same derivation the tasks taxonomy uses: a profile's headcount is the sum
    over every raw record that fed the cluster it came from.
    """
    if not state.clustering:
        return {}
    hc_by_record = {r.id: r.headcount for r in state.raw_records}
    group_members = {g.group_id: g.member_ids for g in state.dedupe_groups}
    cluster_to_key = {d.profile_cluster_id: d.profile_key for d in state.job_profiles}
    out: dict[str, int] = {}
    for a in state.clustering.assignments:
        key = cluster_to_key.get(a.final_profile_id)
        if not key:
            continue
        total = sum(
            h
            for h in (hc_by_record.get(m) for m in group_members.get(a.item_id, [a.item_id]))
            if h
        )
        if total:
            out[key] = out.get(key, 0) + total
    return out


class OverrideRequest(BaseModel):
    spec_code: str
    level_code: str | None = None


@project_router.post("/matches/{profile_key}/override")
def override_match(
    client_slug: str, project_slug: str, profile_key: str, req: OverrideRequest
) -> dict:
    """Accept a user's correction to a match.

    The whole point of surfacing cosine scores, the shortlist and a rationale is
    that a consultant can disagree. An override clears `needs_review` (a human
    has now looked) and sets `overridden_by_user`, so the audit trail keeps
    machine and human decisions distinguishable.
    """
    svc, state = _load(client_slug, project_slug)
    record = next((m for m in state.matching.matches if m.profile_key == profile_key), None)
    if record is None:
        raise HTTPException(404, f"no match recorded for profile {profile_key}")

    try:
        specs = taxonomy.load_specializations(state.matching.industries or None)
        levels = taxonomy.load_career_levels()
    except taxonomy.TaxonomyUnavailable as e:
        raise HTTPException(503, str(e)) from e

    spec = next((s for s in specs if s.code == req.spec_code), None)
    if spec is None:
        raise HTTPException(422, f"unknown specialization code: {req.spec_code}")

    if req.level_code:
        valid = {c: t for c, t in spec.available_levels}
        if req.level_code not in valid:
            raise HTTPException(
                422,
                f"level {req.level_code} is not offered for {spec.code}; "
                f"available: {sorted(valid)}",
            )
        record.level_code = req.level_code
        record.level_title = valid[req.level_code]
        record.level_stream = levels[req.level_code].stream if req.level_code in levels else None
        record.level_confidence = 1.0
        record.level_rationale = "Set by user."

    record.matched = True
    record.spec_code = spec.code
    record.spec_title = spec.title
    record.family_title = spec.family_title
    record.sub_family_title = spec.sub_family_title
    record.confidence = 1.0
    record.rationale = "Set by user."
    record.overridden_by_user = True
    record.needs_review = False
    record.review_reasons = []
    # The cosine of a user's pick is genuinely unknown without re-embedding, and
    # leaving the old value would misattribute the machine's score to a human
    # choice.
    record.cosine = None

    state.matching.summary = _resummarize(state.matching.matches)
    svc.save_state(
        state,
        action="override-taxonomy-match",
        lineage_payload={"profile_key": profile_key, "spec_code": spec.code, "level": req.level_code},
    )
    return record.model_dump()


def _resummarize(records: list[TaxonomyMatchRecord]) -> dict:
    matched = [m for m in records if m.matched]
    scored = [m for m in matched if m.cosine is not None]
    return {
        "profiles": len(records),
        "matched": len(matched),
        "unmatched": len(records) - len(matched),
        "needs_review": sum(1 for m in records if m.needs_review),
        "levelled": sum(1 for m in matched if m.level_code),
        "overridden": sum(1 for m in records if m.overridden_by_user),
        "mean_confidence": round(sum(m.confidence for m in matched) / len(matched), 3)
        if matched
        else 0.0,
        "mean_cosine": round(sum(m.cosine or 0.0 for m in scored) / len(scored), 3)
        if scored
        else None,
        "families": len({m.family_title for m in matched if m.family_title}),
    }


@project_router.get("/search")
def search_taxonomy(client_slug: str, project_slug: str, q: str, limit: int = 20) -> dict:
    """Substring search over the taxonomy, backing the override picker.

    Deliberately lexical rather than semantic: a user reaching for the override
    control already knows what they want and is looking it up by name, so exact
    text beats a second opinion from the same embedding model that just got it
    wrong.
    """
    _, state = _load(client_slug, project_slug)
    needle = q.strip().lower()
    if len(needle) < 2:
        raise HTTPException(422, "query must be at least 2 characters")
    try:
        specs = taxonomy.load_specializations(state.matching.industries or None)
    except taxonomy.TaxonomyUnavailable as e:
        raise HTTPException(503, str(e)) from e

    hits = []
    for s in specs:
        haystack = f"{s.title} {s.family_title} {s.sub_family_title} {' '.join(s.typical_titles)}"
        if needle in haystack.lower():
            hits.append(
                {
                    "code": s.code,
                    "title": s.title,
                    "family_title": s.family_title,
                    "sub_family_title": s.sub_family_title,
                    # Title hits rank above typical-title hits.
                    "_rank": 0 if needle in s.title.lower() else 1,
                    "levels": [{"code": c, "title": t} for c, t in s.available_levels],
                }
            )
    hits.sort(key=lambda h: (h["_rank"], h["title"]))
    for h in hits:
        h.pop("_rank")
    return {"query": q, "total": len(hits), "results": hits[:limit]}
