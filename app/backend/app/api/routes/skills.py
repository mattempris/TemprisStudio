"""Phase 2 routes — instructions.txt steps 8-9 (skills and proficiency).

Reuses the Phase 1 machinery deliberately: the same clustering engine (with
skillQWEN instead of jobQWEN), the same orchestrator/WebSocket progress, the same
blob persistence and lineage. Only the entity type and the naming vocabulary
differ, which is why `services/clustering/` was written entity-agnostic.
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.project_state import (
    ClusterProficiencyRecord,
    InferredSkillRecord,
    ProficiencyTemplateConfig,
    ProfileSkillRequirementRecord,
    ProjectState,
)
from app.services import llm
from app.services.orchestrator import JobAlreadyRunning, ProgressReporter, get_registry, run_job
from app.services.project_service import ProjectService
from app.services.skills import inference, proficiency

router = APIRouter(prefix="/api/projects/{client_slug}/{project_slug}/skills", tags=["skills"])


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


class SkillsSummary(BaseModel):
    inferred_skills: int = 0
    profiles_covered: int = 0
    clustered: bool = False
    k_families: int | None = None
    k_categories: int | None = None
    k_clusters: int | None = None
    named: bool = False
    proficiency_definitions: int = 0
    profile_requirements: int = 0
    levels_assigned: int = 0
    audit: dict = Field(default_factory=dict)


@router.get("/summary")
def skills_summary(client_slug: str, project_slug: str) -> SkillsSummary:
    _, state = _load(client_slug, project_slug)
    s = state.skills
    c = s.clustering
    return SkillsSummary(
        inferred_skills=len(s.inferred),
        profiles_covered=len({sk.source_profile_key for sk in s.inferred}),
        clustered=c is not None,
        k_families=c.k_families if c else None,
        k_categories=c.k_categories if c else None,
        k_clusters=c.k_profiles if c else None,
        named=bool(c and c.profile_names),
        proficiency_definitions=len(s.cluster_proficiencies),
        profile_requirements=len(s.profile_requirements),
        levels_assigned=sum(1 for r in s.profile_requirements if r.assigned_level),
        audit=s.audit,
    )


# ===========================================================================
# Step 8 — infer skills
# ===========================================================================
class InferRequest(BaseModel):
    # instructions.txt: "from a selected subset (or all) job profiles"
    profile_keys: list[str] | None = None


@router.post("/infer")
async def infer_skills(
    client_slug: str, project_slug: str, req: InferRequest, workers: int | None = None
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
        reporter.stage_start(len(payload), f"Inferring skills for {len(payload)} job profiles")
        per_profile = inference.infer_many(payload, workers=_workers, progress=reporter.pmap_callback())
        flat = [s for group in per_profile for s in group]
        audit = inference.audit_skills(flat)

        fresh = svc.load_state(client_slug, project_slug)
        fresh.skills.inferred = [
            InferredSkillRecord(
                id=f"skill-{uuid.uuid4().hex[:8]}",
                name=s.name,
                description=s.description,
                kind=s.kind,
                source_profile_key=s.source_profile_key,
            )
            for s in flat
        ]
        fresh.skills.audit = audit.summary()
        # re-inferring invalidates the taxonomy built from the previous skill set
        fresh.skills.clustering = None
        fresh.skills.cluster_proficiencies = []
        fresh.skills.profile_requirements = []
        svc.save_state(
            fresh,
            action="infer-skills",
            lineage_payload={"profiles": len(payload), "skills": len(flat), "audit": audit.summary()},
        )

        summary = {
            "skills": len(flat),
            "profiles": len(payload),
            "mean_per_profile": round(len(flat) / max(1, len(payload)), 1),
            "technical": sum(1 for s in flat if s.kind == "technical"),
            "non_technical": sum(1 for s in flat if s.kind == "non-technical"),
            **audit.summary(),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "skills", work)


@router.get("")
def list_skills(client_slug: str, project_slug: str) -> dict:
    _, state = _load(client_slug, project_slug)
    return {
        "skills": [s.model_dump() for s in state.skills.inferred],
        "audit": state.skills.audit,
    }


# ===========================================================================
# Clustering into a taxonomy is handled by the shared per-tier routes —
# /cluster/{entity}/tier/{tier}/... in routes/tiers.py — with entity "skill".
#
# There used to be a single-shot path here: one build, one preview cutting all
# three tiers at once, one confirm that named every level in the same call. It has
# been removed rather than kept alongside, because two code paths writing the same
# `clustering` state is how the two drift apart, and the per-tier flow supersedes it
# outright: a cluster count and a stability gate chosen per tier, the routing cost
# shown before it is paid, and each level's names confirmed on their own.
# ===========================================================================


@router.get("/taxonomy")
def skills_taxonomy(client_slug: str, project_slug: str) -> dict:
    """Browsable skills taxonomy with the headcount analytics instructions.txt asks
    for ("Browsable Skils Taxonomy with intelligence re number of jobs requiring
    (add headcount analytics where we have headcount)")."""
    _, state = _load(client_slug, project_slug)
    c = state.skills.clustering
    if c is None:
        raise HTTPException(409, "skills not clustered yet")

    skill_by_id = {s.id: s for s in state.skills.inferred}
    # headcount per job profile, rolled up from the raw records via dedupe groups
    hc_by_record = {r.id: r.headcount for r in state.raw_records}
    group_members = {g.group_id: g.member_ids for g in state.dedupe_groups}
    profile_headcount: dict[str, int] = {}
    if state.clustering:
        item_to_profile = {a.item_id: a.final_profile_id for a in state.clustering.assignments}
        cluster_to_key = {d.profile_cluster_id: d.profile_key for d in state.job_profiles}
        for item_id, pid in item_to_profile.items():
            key = cluster_to_key.get(pid)
            if not key:
                continue
            total = sum(h for h in (hc_by_record.get(m) for m in group_members.get(item_id, [item_id])) if h)
            if total:
                profile_headcount[key] = profile_headcount.get(key, 0) + total

    reqs_by_cluster: dict[int, list] = {}
    for r in state.skills.profile_requirements:
        reqs_by_cluster.setdefault(r.cluster_id, []).append(r)
    prof_by_cluster = {p.cluster_id: p for p in state.skills.cluster_proficiencies}

    tree: dict[int, dict] = {}
    for a in c.assignments:
        fam = tree.setdefault(
            a.final_family_id,
            {"id": a.final_family_id, "name": c.family_names.get(a.final_family_id, "?"), "categories": {}},
        )
        cat = fam["categories"].setdefault(
            a.final_category_id,
            {"id": a.final_category_id, "name": c.category_names.get(a.final_category_id, "?"), "clusters": {}},
        )
        cl = cat["clusters"].setdefault(
            a.final_profile_id,
            {
                "id": a.final_profile_id,
                "name": c.profile_names.get(a.final_profile_id, "?"),
                "skills": [],
                "proficiency_definitions": (
                    prof_by_cluster[a.final_profile_id].definitions
                    if a.final_profile_id in prof_by_cluster
                    else {}
                ),
            },
        )
        s = skill_by_id.get(a.item_id)
        if s:
            cl["skills"].append(
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "kind": s.kind,
                    "source_profile_key": s.source_profile_key,
                    "stability_score": a.stability_score,
                    "routed_by_llm": a.routed_by_llm,
                }
            )

    def roll_up(skill_count: int, jobs: set[str]) -> dict:
        """Aggregate for a family or category.

        Jobs are unioned rather than summed: one job profile requiring three
        skills in the same family counts once toward that family's reach. Summing
        the child counts would inflate a broad family into looking universally
        required.
        """
        return {
            "skill_count": skill_count,
            "jobs_requiring_count": len(jobs),
            "headcount_requiring": sum(profile_headcount.get(k, 0) for k in jobs) or None,
        }

    families = []
    for fam in tree.values():
        cats = []
        fam_skills, fam_jobs = 0, set()
        for cat in fam["categories"].values():
            clusters = []
            cat_skills, cat_jobs = 0, set()
            for cl in cat["clusters"].values():
                reqs = reqs_by_cluster.get(cl["id"], [])
                jobs_requiring = sorted({r.profile_key for r in reqs})
                clusters.append(
                    {
                        **cl,
                        "skill_count": len(cl["skills"]),
                        "jobs_requiring_count": len(jobs_requiring),
                        "jobs_requiring": jobs_requiring,
                        "headcount_requiring": sum(profile_headcount.get(k, 0) for k in jobs_requiring) or None,
                        "level_distribution": _level_distribution(reqs),
                    }
                )
                cat_skills += len(cl["skills"])
                cat_jobs.update(jobs_requiring)
            clusters.sort(key=lambda x: -x["jobs_requiring_count"])
            cats.append(
                {
                    **{k: v for k, v in cat.items() if k != "clusters"},
                    "clusters": clusters,
                    **roll_up(cat_skills, cat_jobs),
                }
            )
            fam_skills += cat_skills
            fam_jobs.update(cat_jobs)
        cats.sort(key=lambda x: -x["jobs_requiring_count"])
        families.append(
            {
                **{k: v for k, v in fam.items() if k != "categories"},
                "categories": cats,
                **roll_up(fam_skills, fam_jobs),
            }
        )
    families.sort(key=lambda x: -x["jobs_requiring_count"])

    return {"families": families, "has_headcount": bool(profile_headcount)}


def _level_distribution(reqs) -> dict[str, int]:
    dist: dict[str, int] = {}
    for r in reqs:
        if r.assigned_level:
            dist[r.assigned_level] = dist.get(r.assigned_level, 0) + 1
    return dist


# ===========================================================================
# Step 9b — proficiency template, definitions, rollup, level assignment
# ===========================================================================
@router.get("/proficiency/template")
def get_template(
    client_slug: str, project_slug: str, defaults: bool = False
) -> ProficiencyTemplateConfig:
    """`defaults=true` forces the shipped default, so the editor's "Load
    defaults" control can get back to it after the project has saved its own."""
    _, state = _load(client_slug, project_slug)
    if state.skills.proficiency_template.levels and not defaults:
        return state.skills.proficiency_template
    default = proficiency.load_default_template()
    return ProficiencyTemplateConfig(
        levels=[
            {
                "name": level.name,
                "ordinal": level.ordinal,
                "criteria": level.criteria,
                "typical_autonomy": level.typical_autonomy,
            }
            for level in default.levels
        ]
    )


@router.put("/proficiency/template")
def put_template(
    client_slug: str, project_slug: str, template: ProficiencyTemplateConfig
) -> dict:
    svc, state = _load(client_slug, project_slug)
    parsed = proficiency.ProficiencyTemplate(
        levels=[
            proficiency.ProficiencyLevel(
                name=level.name,
                ordinal=level.ordinal,
                criteria=level.criteria,
                typical_autonomy=level.typical_autonomy,
            )
            for level in template.levels
        ]
    )
    problems = proficiency.validate_template(parsed)
    if problems:
        raise HTTPException(422, {"message": "proficiency template is not valid", "problems": problems})

    state.skills.proficiency_template = template
    # definitions and assigned levels were written against the old scale
    state.skills.cluster_proficiencies = []
    for r in state.skills.profile_requirements:
        r.assigned_level = None
        r.rationale = None
    svc.save_state(
        state,
        action="update-proficiency-template",
        lineage_payload={"levels": [level.name for level in template.levels]},
    )
    return {"status": "saved", "levels": len(template.levels), "invalidated_definitions": True}


def _template_from_state(state: ProjectState) -> proficiency.ProficiencyTemplate:
    cfg = state.skills.proficiency_template
    if not cfg.levels:
        return proficiency.load_default_template()
    return proficiency.ProficiencyTemplate(
        levels=[
            proficiency.ProficiencyLevel(
                name=level.name,
                ordinal=level.ordinal,
                criteria=level.criteria,
                typical_autonomy=level.typical_autonomy,
            )
            for level in cfg.levels
        ]
    )


@router.post("/proficiency/generate")
async def generate_proficiency(
    client_slug: str, project_slug: str, workers: int | None = None
) -> dict:
    """Generate per-cluster proficiency definitions, roll up deterministically to
    job profiles, then assign each job's required level."""
    svc, state = _load(client_slug, project_slug)
    _workers = llm.resolve_workers(workers)
    c = state.skills.clustering
    if c is None or not c.profile_names:
        raise HTTPException(400, "cluster and name the skills taxonomy first")

    template = _template_from_state(state)
    skill_by_id = {s.id: s for s in state.skills.inferred}

    members_by_cluster: dict[int, list[tuple[str, str]]] = {}
    assignments: list[tuple[str, str, str, int]] = []
    for a in c.assignments:
        s = skill_by_id.get(a.item_id)
        if not s:
            continue
        members_by_cluster.setdefault(a.final_profile_id, []).append((s.name, s.description))
        assignments.append((s.source_profile_key, s.name, s.description, a.final_profile_id))

    clusters = [
        (cid, c.profile_names.get(cid, f"Cluster {cid}"), members)
        for cid, members in sorted(members_by_cluster.items())
    ]
    profile_lookup = {p.profile_key: (p.title, p.content) for p in state.job_profiles}

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(len(clusters), f"Writing proficiency definitions for {len(clusters)} clusters")
        defs = proficiency.generate_definitions_many(
            clusters, template, workers=_workers, progress=reporter.pmap_callback()
        )

        # deterministic rollup — no LLM, per the spec
        reqs = proficiency.rollup_clusters_to_profiles(assignments, c.profile_names)
        reporter.stage_start(len(reqs), f"Assigning required levels across {len(reqs)} job/skill pairs")
        reqs = proficiency.assign_levels_many(
            reqs,
            profile_lookup,
            {d.cluster_id: d for d in defs},
            template,
            workers=_workers,
            progress=reporter.pmap_callback(),
        )

        fresh = svc.load_state(client_slug, project_slug)
        fresh.skills.cluster_proficiencies = [
            ClusterProficiencyRecord(
                cluster_id=d.cluster_id, cluster_name=d.cluster_name, definitions=d.definitions
            )
            for d in defs
        ]
        fresh.skills.profile_requirements = [
            ProfileSkillRequirementRecord(
                profile_key=r.profile_key,
                cluster_id=r.cluster_id,
                cluster_name=r.cluster_name,
                contributing_skills=[n for n, _ in r.contributing_skills],
                assigned_level=r.assigned_level,
                rationale=r.rationale,
            )
            for r in reqs
        ]
        svc.save_state(
            fresh,
            action="generate-proficiency",
            lineage_payload={"clusters": len(defs), "requirements": len(reqs)},
        )

        dist: dict[str, int] = {}
        for r in reqs:
            if r.assigned_level:
                dist[r.assigned_level] = dist.get(r.assigned_level, 0) + 1
        summary = {
            "clusters_defined": len(defs),
            "profile_requirements": len(reqs),
            "levels_assigned": sum(1 for r in reqs if r.assigned_level),
            **{f"level_{k}": v for k, v in dist.items()},
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "skills", work)


@router.get("/profiles/{profile_key}/requirements")
def profile_requirements(client_slug: str, project_slug: str, profile_key: str) -> dict:
    """The skills a given job profile requires, with assigned proficiency levels."""
    _, state = _load(client_slug, project_slug)
    reqs = [r for r in state.skills.profile_requirements if r.profile_key == profile_key]
    if not reqs:
        raise HTTPException(404, "no skill requirements for this profile")
    prof_by_cluster = {p.cluster_id: p for p in state.skills.cluster_proficiencies}
    return {
        "profile_key": profile_key,
        "requirements": [
            {
                **r.model_dump(),
                "level_definition": (
                    prof_by_cluster[r.cluster_id].definitions.get(r.assigned_level or "", "")
                    if r.cluster_id in prof_by_cluster
                    else ""
                ),
            }
            for r in sorted(reqs, key=lambda x: x.cluster_name)
        ],
    }
