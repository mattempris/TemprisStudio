"""The final combined deliverable — one browsable tree carrying every artifact.

instructions.txt, final requirement: "Ability to browse full hierarchy of job
family, job category, job profile, and job profile detail including skills and
tasks", with headcount analytics and the 3rd-party taxonomy match.

Everything downstream of clustering keys off `profile_key`, so this is a join
rather than a recomputation: job profile + JE result + required skills + tasks +
external taxonomy match, rolled up through Family › Category › Profile with
headcount at every tier.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.project_state import ProjectState
from app.services.project_service import ProjectService

router = APIRouter(
    prefix="/api/projects/{client_slug}/{project_slug}/overview", tags=["overview"]
)


def _load(client_slug: str, project_slug: str) -> ProjectState:
    svc = ProjectService()
    try:
        return svc.load_state(client_slug, project_slug)
    except LookupError as e:
        raise HTTPException(404, f"project not found: {client_slug}/{project_slug}") from e


def _profile_headcount(state: ProjectState) -> dict[str, int]:
    """Headcount per profile_key, summed over the raw records behind each cluster."""
    if not state.clustering:
        return {}
    hc = {r.id: r.headcount for r in state.raw_records}
    members = {g.group_id: g.member_ids for g in state.dedupe_groups}
    cluster_to_key = {d.profile_cluster_id: d.profile_key for d in state.job_profiles}
    out: dict[str, int] = {}
    for a in state.clustering.assignments:
        key = cluster_to_key.get(a.final_profile_id)
        if not key:
            continue
        total = sum(h for h in (hc.get(m) for m in members.get(a.item_id, [a.item_id])) if h)
        if total:
            out[key] = out.get(key, 0) + total
    return out


def _source_titles(state: ProjectState) -> dict[str, list[str]]:
    """The original job titles that fed each profile — the audit trail a client
    asks for first ("which of our jobs ended up here?")."""
    if not state.clustering:
        return {}
    titles = {r.id: r.job_title for r in state.raw_records}
    members = {g.group_id: g.member_ids for g in state.dedupe_groups}
    cluster_to_key = {d.profile_cluster_id: d.profile_key for d in state.job_profiles}
    out: dict[str, list[str]] = {}
    for a in state.clustering.assignments:
        key = cluster_to_key.get(a.final_profile_id)
        if not key:
            continue
        out.setdefault(key, []).extend(
            titles.get(m, m) for m in members.get(a.item_id, [a.item_id])
        )
    return out


@router.get("")
def overview(client_slug: str, project_slug: str) -> dict:
    state = _load(client_slug, project_slug)
    c = state.clustering
    if c is None or not c.profile_names:
        raise HTTPException(409, "cluster and name the job hierarchy first")

    headcount = _profile_headcount(state)
    sources = _source_titles(state)
    je_by_key = {r.profile_key: r for r in state.je_results}
    match_by_key = {m.profile_key: m for m in state.matching.matches}

    # skills required per profile, with the cluster they belong to
    skill_cluster_names = (
        state.skills.clustering.profile_names if state.skills.clustering else {}
    )
    skills_by_key: dict[str, list[dict]] = {}
    for r in state.skills.profile_requirements:
        skills_by_key.setdefault(r.profile_key, []).append(
            {
                "cluster_id": r.cluster_id,
                "cluster_name": skill_cluster_names.get(r.cluster_id, r.cluster_name),
                "assigned_level": r.assigned_level,
            }
        )

    # tasks per profile, with time proportion
    task_cluster_of: dict[str, int] = {}
    if state.tasks.clustering:
        task_cluster_of = {a.item_id: a.final_profile_id for a in state.tasks.clustering.assignments}
    task_cluster_names = state.tasks.clustering.profile_names if state.tasks.clustering else {}
    tasks_by_key: dict[str, list[dict]] = {}
    for t in state.tasks.inferred:
        cid = task_cluster_of.get(t.id)
        tasks_by_key.setdefault(t.source_profile_key, []).append(
            {
                "name": t.name,
                "description": t.description,
                "proportion": t.proportion,
                "cluster_id": cid,
                "cluster_name": task_cluster_names.get(cid) if cid is not None else None,
            }
        )
    for rows in tasks_by_key.values():
        rows.sort(key=lambda x: -x["proportion"])

    # Build Family › Category › Profile from the job profiles themselves rather
    # than from assignments, so a profile with no input jobs still appears.
    fam_of_profile: dict[int, tuple[int, int]] = {}
    for a in c.assignments:
        fam_of_profile[a.final_profile_id] = (a.final_family_id, a.final_category_id)

    tree: dict[int, dict] = {}
    for doc in state.job_profiles:
        if doc.stale:
            continue
        fam_id, cat_id = fam_of_profile.get(doc.profile_cluster_id, (-1, -1))
        fam = tree.setdefault(
            fam_id,
            {"id": fam_id, "name": c.family_names.get(fam_id, "Unassigned"), "categories": {}},
        )
        cat = fam["categories"].setdefault(
            cat_id,
            {"id": cat_id, "name": c.category_names.get(cat_id, "Unassigned"), "profiles": []},
        )

        je = je_by_key.get(doc.profile_key)
        m = match_by_key.get(doc.profile_key)
        skills = skills_by_key.get(doc.profile_key, [])
        tasks = tasks_by_key.get(doc.profile_key, [])
        cat["profiles"].append(
            {
                "profile_key": doc.profile_key,
                "title": doc.title,
                "headcount": headcount.get(doc.profile_key),
                "source_titles": sorted(set(sources.get(doc.profile_key, []))),
                "source_job_count": len(sources.get(doc.profile_key, [])),
                "evaluation": (
                    {
                        "aggregate_score": je.aggregate_score,
                        "level_name": je.level_name,
                        "stale": je.stale,
                    }
                    if je
                    else None
                ),
                "skills": skills,
                "skill_count": len(skills),
                "tasks": tasks,
                "task_count": len(tasks),
                "taxonomy_match": (
                    {
                        "spec_code": m.spec_code,
                        "spec_title": m.spec_title,
                        "family_title": m.family_title,
                        "level_code": m.level_code,
                        "level_title": m.level_title,
                        "confidence": m.confidence,
                        "needs_review": m.needs_review,
                        "overridden_by_user": m.overridden_by_user,
                    }
                    if m and m.matched
                    else None
                ),
            }
        )

    def roll(profiles: list[dict]) -> dict:
        hc = [p["headcount"] for p in profiles if p["headcount"]]
        scored = [p["evaluation"]["aggregate_score"] for p in profiles if p["evaluation"]]
        return {
            "profile_count": len(profiles),
            "headcount": sum(hc) if hc else None,
            "source_job_count": sum(p["source_job_count"] for p in profiles),
            "mean_je_score": round(sum(scored) / len(scored), 1) if scored else None,
            "matched_count": sum(1 for p in profiles if p["taxonomy_match"]),
        }

    families = []
    for fam in tree.values():
        cats, fam_profiles = [], []
        for cat in fam["categories"].values():
            cat["profiles"].sort(key=lambda p: -(p["headcount"] or 0))
            cats.append({**cat, **roll(cat["profiles"])})
            fam_profiles.extend(cat["profiles"])
        cats.sort(key=lambda x: -(x["headcount"] or 0))
        families.append(
            {
                **{k: v for k, v in fam.items() if k != "categories"},
                "categories": cats,
                **roll(fam_profiles),
            }
        )
    families.sort(key=lambda x: -(x["headcount"] or 0))

    all_profiles = [p for f in families for c2 in f["categories"] for p in c2["profiles"]]
    return {
        "families": families,
        "totals": {
            **roll(all_profiles),
            "families": len(families),
            "categories": sum(len(f["categories"]) for f in families),
            "skills": len(state.skills.inferred),
            "tasks": len(state.tasks.inferred),
        },
        "has_headcount": bool(headcount),
        # Which stages have actually run — lets the UI grey out columns rather
        # than rendering empty ones as if the data were missing.
        "available": {
            "evaluation": bool(state.je_results),
            "skills": bool(state.skills.profile_requirements),
            "tasks": bool(state.tasks.inferred),
            "taxonomy_match": bool(state.matching.matches),
        },
    }
