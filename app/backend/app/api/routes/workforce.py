"""Work Architecture Studio routes.

Steps 1 (the work architecture graph) and 3 (the AI opportunity assessment) are here.
Later steps — process upload, personal productivity, agent definitions, future role
design — extend this module and the same fact table.

The endpoint split follows cost, as elsewhere in the app:

  status              what is ready and what is missing, from state alone. Cheap, polled.
  build               compute the whole graph at leaf resolution and persist it. Once, as a job.
  graph               roll the persisted table up to one view. Instant, so it drives the zoom.
  node                everything behind one node, for its modal.
  opportunity/assess  one LLM call per task cluster. The only paid path here, as a job.
  opportunity/*       read the assessment back, rolled up per cluster or per role.
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from fastapi import UploadFile

from app.models.project_state import (
    AgentDefinitionRecord,
    AgentOversightTask,
    ContextDocRecord,
    FutureRoleRecord,
    ProcessAssessmentRecord,
    ProcessRecord,
    ProcessStepRecord,
    ProjectState,
    TaskActionRecord,
    TaskOpportunityRecord,
    TaskSkillRecord,
)
from app.services import embeddings as emb
from app.services import llm
from app.services.clustering import tier_state
from app.services.ingestion import parsers
from app.services.orchestrator import JobAlreadyRunning, ProgressReporter, get_registry, run_job
from app.services.project_service import ProjectService
from app.services.workforce import agents as ag
from app.services.workforce import future_roles as fr
from app.services.workforce import graph as wf
from app.services.workforce import opportunity as opp
from app.services.workforce import processes as proc
from app.services.workforce import productivity as prod

router = APIRouter(prefix="/api/projects/{client_slug}/{project_slug}/workforce", tags=["workforce"])

GRAPH_BLOB = "workforce/graph_facts"

# Per (client, project): the parsed fact table. Rebuilding is deterministic and the
# blob read is the only real cost, so a cold cache costs a read rather than
# correctness.
_FACTS: dict[tuple[str, str], wf.Facts] = {}


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


def _facts(svc: ProjectService, client_slug: str, project_slug: str) -> wf.Facts:
    key = (client_slug, project_slug)
    if key in _FACTS:
        return _FACTS[key]
    raw = svc.load_json(client_slug, f"{project_slug}/{GRAPH_BLOB}.json")
    if raw is None:
        raise HTTPException(409, "the work architecture has not been built yet")
    facts = wf.Facts.from_json(raw)
    _FACTS[key] = facts
    return facts


@router.get("/status")
def workforce_status(client_slug: str, project_slug: str) -> dict:
    """Gate state plus whether the graph exists. Never builds anything."""
    svc, state = _load(client_slug, project_slug)
    built = (client_slug, project_slug) in _FACTS or svc.json_exists(
        client_slug, f"{project_slug}/{GRAPH_BLOB}.json"
    )
    r = wf.readiness(state)
    return {
        **r,
        "graph_built": bool(built),
        "levels": list(wf.LEVELS),
        "entities": list(wf.ENTITIES),
        "level_titles": wf.LEVEL_TITLES,
        # What later steps are gated on, from the same state read the gate already
        # does — so the page does not need a call per step to know what is unlocked.
        "clusters_assessed": len(state.workforce.opportunity),
        "skills_written": len(state.workforce.skills_guidance),
        # Work Design Studio needs at least one lever to pull, and either kind will do.
        # Here rather than on its own endpoint for the same reason as the two above: the
        # studio switcher should not need a call per studio to know what is reachable.
        "agents_defined": len(state.workforce.agents),
    }


@router.post("/graph/build")
async def build_graph(client_slug: str, project_slug: str) -> dict:
    """Compute and persist the whole graph at leaf resolution.

    A job rather than a synchronous call: it walks every inferred skill and task on
    the project (5,000-10,000 of each at real scale) and writes a blob, which is
    seconds rather than milliseconds. No model calls — it is derived from state.
    """
    svc, state = _load(client_slug, project_slug)
    r = wf.readiness(state)
    if not r["ready"]:
        raise HTTPException(409, {"message": "not ready", "missing": r["missing"]})

    def work(reporter: ProgressReporter) -> dict:
        reporter.message("Reading the job, skill and task hierarchies")
        facts = wf.build(state)
        reporter.message("Saving the work architecture")
        svc.save_json(client_slug, project_slug, GRAPH_BLOB, facts.to_json())
        _FACTS[(client_slug, project_slug)] = facts
        summary = {
            "job_profiles": facts.leaf_counts()["job"],
            "skill_clusters": facts.leaf_counts()["skill"],
            "task_clusters": facts.leaf_counts()["task"],
            "job_skill_edges": len(facts.job_skill),
            "job_task_edges": len(facts.job_task),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "workforce", work)


@router.get("/graph")
def get_graph(
    client_slug: str,
    project_slug: str,
    jobs: str = "family",
    skills: str = "family",
    tasks: str = "family",
    expand: str = "",
    show: str = "job,skill,task",
    job_filter: str = "",
    skill_filter: str = "",
    task_filter: str = "",
    filter_level: str = "family",
) -> dict:
    """One view of the graph: the fact table rolled up to the requested resolution.

    `expand` is a comma-separated list of node ids whose children should be shown, so
    one branch can be opened without dropping the whole view to a finer level.

    `show` picks which hierarchies to draw — skills and tasks answer different questions
    and drawing both at once is unreadable. The `*_filter` params are comma-separated
    cluster ids at `filter_level`, narrowing each hierarchy to a chosen branch.
    """
    # Deliberately no state read: a cut needs only the fact table, and the state blob
    # is ~9MB on a real project. Reading it here put ~2.8s on every zoom and every
    # expand, which is the whole latency budget for a control meant to feel live.
    facts = _facts(ProjectService(), client_slug, project_slug)
    expanded = {x for x in (e.strip() for e in expand.split(",")) if x}

    def ids(raw: str) -> set[int]:
        out = set()
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.add(int(part))
            except ValueError as e:
                raise HTTPException(422, f"filter ids must be integers, got {part!r}") from e
        return out

    if filter_level not in wf.LEVELS:
        raise HTTPException(422, f"unknown filter level {filter_level!r}")
    filters = {
        entity: (filter_level, chosen)
        for entity, chosen in (
            ("job", ids(job_filter)),
            ("skill", ids(skill_filter)),
            ("task", ids(task_filter)),
        )
        if chosen
    }
    shown = tuple(s.strip() for s in show.split(",") if s.strip())
    try:
        return wf.cut(
            facts,
            levels={"job": jobs, "skill": skills, "task": tasks},
            expanded=expanded,
            show=shown,
            filters=filters,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/graph/filters")
def graph_filters(client_slug: str, project_slug: str) -> dict:
    """Families and categories per hierarchy, for the filter controls."""
    return wf.filter_options(_facts(ProjectService(), client_slug, project_slug))


@router.get("/node/{node_id}")
def get_node(client_slug: str, project_slug: str, node_id: str) -> dict:
    """Everything behind one node, for its modal."""
    facts = _facts(ProjectService(), client_slug, project_slug)
    try:
        return wf.node_detail(facts, node_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


# ===========================================================================
# Step 3 — AI opportunity assessment
# ===========================================================================
def _profile_headcount(state: ProjectState) -> dict[str, int]:
    """Headcount per job profile_key.

    The graph builder's own rollup, reused rather than reimplemented: the FTE numbers
    in the opportunity report and the node sizes in the graph must be the same figure,
    and two copies of this walk through dedupe groups is how they stop being.
    """
    return wf.profile_headcount(state)


@router.get("/opportunity/status")
def opportunity_status(client_slug: str, project_slug: str) -> dict:
    """What is assessed, what is left, and what finishing it would cost.

    The estimate is shown before the button is pressed, following the clustering
    gate's precedent: this is the first Work Architecture Studio step that spends money, and
    a run over a real project is hundreds of calls.
    """
    _, state = _load(client_slug, project_slug)
    if state.tasks.clustering is None:
        raise HTTPException(409, "the task taxonomy has not been built yet")
    total = len(state.tasks.clustering.profile_names)
    assessed = {o.task_cluster_id for o in state.workforce.opportunity}
    remaining = total - len(assessed)
    return {
        "task_clusters": total,
        "assessed": len(assessed),
        "remaining": remaining,
        "actions": len(state.workforce.actions),
        "audit": state.workforce.audit,
        "hours_per_fte_week": state.workforce.hours_per_fte_week,
        "has_headcount": bool(_profile_headcount(state)),
        "estimate_remaining": opp.cost_estimate(remaining),
        "estimate_all": opp.cost_estimate(total),
    }


class SettingsRequest(BaseModel):
    """Project-level workforce settings. Only what a user can legitimately change."""

    hours_per_fte_week: float = Field(gt=0, le=168)


@router.get("/settings")
def get_workforce_settings(client_slug: str, project_slug: str) -> dict:
    _, state = _load(client_slug, project_slug)
    return {
        "hours_per_fte_week": state.workforce.hours_per_fte_week,
        "has_headcount": bool(_profile_headcount(state)),
        "has_business_framework": wf.has_business_framework(state),
    }


@router.put("/settings")
def put_workforce_settings(client_slug: str, project_slug: str, req: SettingsRequest) -> dict:
    """Set the length of a full-time week.

    This existed as a model field with a comment saying it was "configurable because a
    35-hour and a 40-hour week give materially different numbers and neither is a safe
    silent default" — and then no route ever wrote it, so it sat pinned at 37.5 on every
    project. Every capacity figure and every released-hours figure divides by it, so a
    client on a 40-hour week was reading numbers 6.7% wrong with no way to correct them.

    Deliberately not a job: it changes no stored result. Everything derived from it —
    released hours, capacity, required headcount — is computed on read, so the next
    request simply reports different numbers. Nothing to invalidate, which is why this
    does not touch lineage.
    """
    svc, state = _load(client_slug, project_slug)
    before = state.workforce.hours_per_fte_week
    state.workforce.hours_per_fte_week = req.hours_per_fte_week
    svc.save_state(
        state,
        action="set-workforce-settings",
        lineage_payload={"hours_per_fte_week": [before, req.hours_per_fte_week]},
    )
    return {"hours_per_fte_week": state.workforce.hours_per_fte_week, "was": before}


class AssessRequest(BaseModel):
    """`limit` assesses only the largest N clusters by time share — the shape of a
    live calibration check before committing to the whole taxonomy. `redo` re-assesses
    clusters that already have a result instead of skipping them."""

    cluster_ids: list[int] | None = None
    limit: int | None = None
    redo: bool = False


@router.post("/opportunity/assess")
async def assess_opportunity(
    client_slug: str, project_slug: str, req: AssessRequest, workers: int | None = None
) -> dict:
    svc, state = _load(client_slug, project_slug)
    if state.tasks.clustering is None:
        raise HTTPException(409, "the task taxonomy has not been built yet")
    _workers = llm.resolve_workers(workers)

    inputs = opp.cluster_inputs(state)
    if req.cluster_ids is not None:
        wanted = set(req.cluster_ids)
        inputs = [i for i in inputs if i.cluster_id in wanted]
    if not req.redo:
        done = {o.task_cluster_id for o in state.workforce.opportunity}
        inputs = [i for i in inputs if i.cluster_id not in done]
    if req.limit:
        inputs = inputs[: req.limit]
    if not inputs:
        raise HTTPException(400, "nothing to assess — every requested cluster already has a result")

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(
            len(inputs), f"Assessing AI opportunity across {len(inputs)} task clusters"
        )
        results = opp.assess_many(inputs, workers=_workers, progress=reporter.pmap_callback())
        audit = opp.audit(results, requested=len(inputs))

        reporter.message("Saving the assessment")
        now = datetime.now(timezone.utc)
        fresh = svc.load_state(client_slug, project_slug)
        # Replace per cluster rather than appending: a redo of one cluster must not
        # leave the old actions behind alongside the new ones, which would double its
        # weight in every roll-up.
        touched = {r.cluster_id for r in results if r is not None}
        fresh.workforce.actions = [
            a for a in fresh.workforce.actions if a.task_cluster_id not in touched
        ]
        fresh.workforce.opportunity = [
            o for o in fresh.workforce.opportunity if o.task_cluster_id not in touched
        ]
        for r in results:
            if r is None:
                continue
            fresh.workforce.actions.extend(
                TaskActionRecord(
                    id=f"act-{uuid.uuid4().hex[:8]}",
                    task_cluster_id=r.cluster_id,
                    name=a.name,
                    definition=a.definition,
                    pct_of_task=a.pct_of_task,
                    automation_pct=a.automation_pct,
                    augmentation_pct=a.augmentation_pct,
                )
                for a in r.actions
            )
            fresh.workforce.opportunity.append(
                TaskOpportunityRecord(
                    task_cluster_id=r.cluster_id,
                    cluster_name=r.cluster_name,
                    automation_pct=r.automation_pct,
                    augmentation_pct=r.augmentation_pct,
                    n_actions=len(r.actions),
                    raw_pct_sum=r.raw_pct_sum,
                    clamped=r.clamped,
                    computed_at=now,
                )
            )
        fresh.workforce.audit = {
            **audit.summary(),
            "total_assessed": len(fresh.workforce.opportunity),
        }
        svc.save_state(
            fresh,
            action="assess-ai-opportunity",
            lineage_payload={"requested": len(inputs), **audit.summary()},
        )

        # The graph is derived, so it is rebuilt rather than patched — otherwise the
        # architecture keeps showing no opportunity until someone happens to press
        # "rebuild", and the two views of the same project disagree.
        if svc.json_exists(client_slug, f"{project_slug}/{GRAPH_BLOB}.json"):
            reporter.message("Refreshing the work architecture")
            facts = wf.build(fresh)
            svc.save_json(client_slug, project_slug, GRAPH_BLOB, facts.to_json())
            _FACTS[(client_slug, project_slug)] = facts

        summary = {
            "requested": len(inputs),
            "actions": sum(len(r.actions) for r in results if r is not None),
            **audit.summary(),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "opportunity", work)


@router.get("/opportunity/clusters")
def opportunity_clusters(client_slug: str, project_slug: str) -> dict:
    """The assessment per task cluster, with its actions, ordered by absorbable time.

    `fte_weighted` is the honest priority for step 6: automation percentage alone
    ranks a rare, highly automatable task above a ubiquitous, moderately automatable
    one, and an agent should be built for the second.
    """
    _, state = _load(client_slug, project_slug)
    c = state.tasks.clustering
    if c is None:
        raise HTTPException(409, "the task taxonomy has not been built yet")

    headcount = _profile_headcount(state)
    task_by_id = {t.id: t for t in state.tasks.inferred}
    # Time on each cluster, as a share of one job holder's week and as FTE.
    proportion: dict[int, float] = {}
    fte: dict[int, float] = {}
    roles: dict[int, set[str]] = {}
    for a in c.assignments:
        t = task_by_id.get(a.item_id)
        if t is None:
            continue
        cid = a.final_profile_id
        proportion[cid] = proportion.get(cid, 0.0) + t.proportion
        heads = headcount.get(t.source_profile_key, 0)
        if heads:
            fte[cid] = fte.get(cid, 0.0) + t.proportion / 100.0 * heads
        roles.setdefault(cid, set()).add(t.source_profile_key)

    actions_by_cluster: dict[int, list[dict]] = {}
    for a in state.workforce.actions:
        actions_by_cluster.setdefault(a.task_cluster_id, []).append(
            {
                "name": a.name,
                "definition": a.definition,
                "pct_of_task": a.pct_of_task,
                "automation": a.automation_pct,
                "augmentation": a.augmentation_pct,
            }
        )
    for items in actions_by_cluster.values():
        items.sort(key=lambda x: -x["pct_of_task"])

    # A cluster's parents, for the domain/category filter in the UI.
    parents: dict[int, tuple[int, int]] = {}
    for a in c.assignments:
        parents[a.final_profile_id] = (a.final_category_id, a.final_family_id)

    rows = []
    for o in state.workforce.opportunity:
        cid = o.task_cluster_id
        cat, fam = parents.get(cid, (-1, -1))
        prop = round(proportion.get(cid, 0.0), 2)
        f = fte.get(cid)
        rows.append(
            {
                "cluster_id": cid,
                "name": c.profile_names.get(cid, o.cluster_name),
                "category": c.category_names.get(cat, "—"),
                "domain": c.family_names.get(fam, "—"),
                "automation": o.automation_pct,
                "augmentation": o.augmentation_pct,
                "n_actions": o.n_actions,
                "clamped": o.clamped,
                "roles": len(roles.get(cid, ())),
                "proportion_sum": prop,
                "fte": round(f, 2) if f is not None else None,
                # What the assessment says is absorbable, in whichever unit the
                # project actually has. Never both — one of them would be invented.
                "absorbable": round(
                    (f if f is not None else prop / 100.0) * o.automation_pct / 100.0, 2
                ),
                "actions": actions_by_cluster.get(cid, []),
            }
        )
    rows.sort(key=lambda r: -r["absorbable"])
    return {
        "clusters": rows,
        "has_headcount": bool(headcount),
        "unit": "FTE" if headcount else "role-weeks",
        "total_clusters": len(c.profile_names),
        "audit": state.workforce.audit,
    }


@router.get("/opportunity/roles")
def opportunity_roles(client_slug: str, project_slug: str) -> dict:
    """The role-level report — instructions step 3, demo section 6.

    Every role with its automation and augmentation potential, its released capacity
    where headcount is known, and the task breakdown behind each number.
    """
    _, state = _load(client_slug, project_slug)
    c = state.tasks.clustering
    if c is None:
        raise HTTPException(409, "the task taxonomy has not been built yet")

    scores = {
        o.task_cluster_id: (o.automation_pct, o.augmentation_pct)
        for o in state.workforce.opportunity
    }
    if not scores:
        raise HTTPException(409, "no AI opportunity assessment has been run yet")

    headcount = _profile_headcount(state)
    hours = state.workforce.hours_per_fte_week
    cluster_of = {a.item_id: a.final_profile_id for a in c.assignments}
    title_of = {p.profile_key: p.title for p in state.job_profiles}

    by_role: dict[str, list] = {}
    for t in state.tasks.inferred:
        cid = cluster_of.get(t.id)
        if cid is None:
            continue
        by_role.setdefault(t.source_profile_key, []).append((t, cid))

    rows = []
    for key, items in by_role.items():
        r = opp.role_opportunity(
            profile_key=key,
            title=title_of.get(key, key),
            headcount=headcount.get(key),
            tasks=[(t.proportion, cid) for t, cid in items],
            cluster_scores=scores,
            hours_per_fte_week=hours,
        )
        rows.append(
            {
                "profile_key": r.profile_key,
                "title": r.title,
                "headcount": r.headcount,
                "automation": r.automation_pct,
                "augmentation": r.augmentation_pct,
                "coverage": r.coverage_pct,
                "n_tasks": r.n_tasks,
                "fte_released": r.fte_released,
                "hours_per_week": r.hours_per_week,
                "tasks": sorted(
                    [
                        {
                            "name": t.name,
                            "description": t.description,
                            "proportion": t.proportion,
                            "cluster_id": cid,
                            "cluster": c.profile_names.get(cid, str(cid)),
                            "automation": scores[cid][0] if cid in scores else None,
                            "augmentation": scores[cid][1] if cid in scores else None,
                            # Ranking key for step 5: where a prompt helps this
                            # person most is augmentation weighted by how much of
                            # their week the task takes, not augmentation alone.
                            "augmentation_weighted": round(
                                t.proportion / 100.0 * scores[cid][1], 2
                            )
                            if cid in scores
                            else None,
                        }
                        for t, cid in items
                    ],
                    key=lambda x: -x["proportion"],
                ),
            }
        )
    rows.sort(key=lambda r: (-(r["fte_released"] or 0), -r["automation"]))

    assessed = [r for r in rows if r["coverage"] > 0]
    total_fte = sum(r["fte_released"] or 0 for r in rows)
    return {
        "roles": rows,
        "has_headcount": bool(headcount),
        "hours_per_fte_week": hours,
        "totals": {
            "roles": len(rows),
            "roles_assessed": len(assessed),
            "headcount": sum(r["headcount"] or 0 for r in rows) or None,
            "mean_automation": round(
                sum(r["automation"] for r in assessed) / len(assessed), 1
            )
            if assessed
            else 0.0,
            "mean_augmentation": round(
                sum(r["augmentation"] for r in assessed) / len(assessed), 1
            )
            if assessed
            else 0.0,
            "mean_coverage": round(sum(r["coverage"] for r in rows) / len(rows), 1) if rows else 0.0,
            "fte_released": round(total_fte, 1) if headcount else None,
            "hours_per_week": round(total_fte * hours, 0) if headcount else None,
        },
    }


# ===========================================================================
# Step 5 — Personal productivity
# ===========================================================================
# The skill files live in blob, not in state: bodies run to several KB and a full
# project could produce thousands of them.
SKILLS_PREFIX = "workforce/skills"


def _job_ancestry(state: ProjectState) -> dict[str, tuple[str, str]]:
    """profile_key -> (job family name, job category name), for the role picker.

    The instructions ask for the filter to run family › category › profile, so the
    names travel with each role rather than needing a lookup per click.
    """
    c = state.clustering
    if c is None:
        return {}
    by_cluster = {a.final_profile_id: (a.final_category_id, a.final_family_id) for a in c.assignments}
    out: dict[str, tuple[str, str]] = {}
    for d in state.job_profiles:
        cat, fam = by_cluster.get(d.profile_cluster_id, (-1, -1))
        out[d.profile_key] = (c.family_names.get(fam, "—"), c.category_names.get(cat, "—"))
    return out


def _skill_inputs(
    state: ProjectState, *, profile_keys: set[str] | None = None
) -> list[prod.SkillInput]:
    """Every (role, task cluster) pair that could have a skill, best first.

    Only assessed clusters are eligible. Without the augmentation score there is
    nothing to rank by and nothing to aim the skill at, and one written against an
    unscored cluster would be guesswork with a filename.

    Grouped per (role, cluster) rather than per task: a role with three tasks in the
    same cluster wants one skill, not three near-identical ones.
    """
    c = state.tasks.clustering
    if c is None:
        return []
    scores = {
        o.task_cluster_id: (o.automation_pct, o.augmentation_pct)
        for o in state.workforce.opportunity
    }
    if not scores:
        return []

    actions: dict[int, list[tuple[str, str, float, float]]] = {}
    for a in state.workforce.actions:
        actions.setdefault(a.task_cluster_id, []).append(
            (a.name, a.definition, a.automation_pct, a.augmentation_pct)
        )

    profile = {d.profile_key: d for d in state.job_profiles}
    parents = {a.final_profile_id: (a.final_category_id, a.final_family_id) for a in c.assignments}
    cluster_of = {a.item_id: a.final_profile_id for a in c.assignments}

    grouped: dict[tuple[str, int], list] = {}
    for t in state.tasks.inferred:
        cid = cluster_of.get(t.id)
        if cid is None or cid not in scores:
            continue
        if profile_keys and t.source_profile_key not in profile_keys:
            continue
        grouped.setdefault((t.source_profile_key, cid), []).append(t)

    out: list[prod.SkillInput] = []
    for (key, cid), tasks in grouped.items():
        doc = profile.get(key)
        cat, fam = parents.get(cid, (-1, -1))
        out.append(
            prod.SkillInput(
                profile_key=key,
                role_title=doc.title if doc else key,
                task_cluster_id=cid,
                cluster_name=c.profile_names.get(cid, str(cid)),
                domain=c.family_names.get(fam, "—"),
                category=c.category_names.get(cat, "—"),
                task_names=[t.name for t in tasks],
                task_descriptions=[t.description for t in tasks],
                actions=actions.get(cid, []),
                proportion=round(sum(t.proportion for t in tasks), 2),
                augmentation_pct=scores[cid][1],
                role_purpose=str((doc.content or {}).get("about_role", ""))[:600] if doc else "",
            )
        )
    out.sort(key=lambda i: -i.rank_score)
    return out


@router.get("/productivity/roles")
def productivity_roles(client_slug: str, project_slug: str) -> dict:
    """Every role with an assessed task, its tasks ranked by where a prompt helps most.

    Ranked by augmentation × share of the week — a different order from step 6's, which
    is the whole reason step 3 scores two axes.
    """
    _, state = _load(client_slug, project_slug)
    if not state.workforce.opportunity:
        raise HTTPException(409, "run the AI opportunity assessment first")

    ancestry = _job_ancestry(state)
    existing = {
        (s.profile_key, s.task_cluster_id): {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "hook": s.hook,
        }
        for s in state.workforce.skills_guidance
    }

    by_role: dict[str, dict] = {}
    for inp in _skill_inputs(state):
        fam, cat = ancestry.get(inp.profile_key, ("—", "—"))
        r = by_role.setdefault(
            inp.profile_key,
            {
                "profile_key": inp.profile_key,
                "title": inp.role_title,
                "family": fam,
                "category": cat,
                "tasks": [],
            },
        )
        r["tasks"].append(
            {
                "cluster_id": inp.task_cluster_id,
                "cluster": inp.cluster_name,
                "domain": inp.domain,
                "task_names": inp.task_names,
                "proportion": inp.proportion,
                "augmentation": inp.augmentation_pct,
                "rank_score": inp.rank_score,
                "skill": existing.get((inp.profile_key, inp.task_cluster_id)),
            }
        )

    roles = sorted(by_role.values(), key=lambda r: (r["family"], r["category"], r["title"]))
    for r in roles:
        r["tasks"].sort(key=lambda t: -t["rank_score"])
        r["skills"] = sum(1 for t in r["tasks"] if t["skill"])
        r["assessed_share"] = round(sum(t["proportion"] for t in r["tasks"]), 1)
    return {
        "roles": roles,
        "families": sorted({r["family"] for r in roles}),
        "total_skills": len(state.workforce.skills_guidance),
        "eligible_pairs": sum(len(r["tasks"]) for r in roles),
    }


class GenerateSkillsRequest(BaseModel):
    """Empty `cluster_ids` means every ranked task for the role. `limit` takes the top
    N by rank, which is the usual shape — the tail is where a prompt helps least."""

    profile_key: str
    cluster_ids: list[int] | None = None
    limit: int | None = None
    redo: bool = False


@router.get("/productivity/estimate")
def productivity_estimate(
    client_slug: str, project_slug: str, profile_key: str, limit: int | None = None
) -> dict:
    """What generating this role's skills would cost, before the button is pressed."""
    _, state = _load(client_slug, project_slug)
    inputs = _skill_inputs(state, profile_keys={profile_key})
    done = {(s.profile_key, s.task_cluster_id) for s in state.workforce.skills_guidance}
    pending = [i for i in inputs if (i.profile_key, i.task_cluster_id) not in done]
    if limit:
        pending = pending[:limit]
    return {"eligible": len(inputs), **prod.cost_estimate(len(pending))}


@router.post("/productivity/generate")
async def generate_skills(
    client_slug: str, project_slug: str, req: GenerateSkillsRequest, workers: int | None = None
) -> dict:
    svc, state = _load(client_slug, project_slug)
    _workers = llm.resolve_workers(workers)

    inputs = _skill_inputs(state, profile_keys={req.profile_key})
    if not inputs:
        raise HTTPException(
            400, "no assessed tasks for that role — run the AI opportunity assessment first"
        )
    if req.cluster_ids:
        wanted = set(req.cluster_ids)
        inputs = [i for i in inputs if i.task_cluster_id in wanted]
    if not req.redo:
        done = {(s.profile_key, s.task_cluster_id) for s in state.workforce.skills_guidance}
        inputs = [i for i in inputs if (i.profile_key, i.task_cluster_id) not in done]
    if req.limit:
        inputs = inputs[: req.limit]
    if not inputs:
        raise HTTPException(400, "nothing to generate — every requested task already has a skill")

    role_title = inputs[0].role_title

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(len(inputs), f"Writing {len(inputs)} skills for {role_title}")
        results = prod.generate_many(inputs, workers=_workers, progress=reporter.pmap_callback())
        made = [r for r in results if r is not None]

        reporter.message("Saving the skill files")
        fresh = svc.load_state(client_slug, project_slug)
        replacing = {(r.profile_key, r.task_cluster_id) for r in made}
        fresh.workforce.skills_guidance = [
            s
            for s in fresh.workforce.skills_guidance
            if (s.profile_key, s.task_cluster_id) not in replacing
        ]
        # Unique within the role, because the name *is* the filename — two tasks can
        # legitimately produce the same skill name and the second would overwrite it.
        taken = {s.name for s in fresh.workforce.skills_guidance if s.profile_key == req.profile_key}
        prod.dedupe_names(made, taken)

        now = datetime.now(timezone.utc)
        by_cluster = {i.task_cluster_id: i for i in inputs}
        for r in made:
            path = f"{project_slug}/{SKILLS_PREFIX}/{r.profile_key}/{r.filename}"
            svc.store.write_bytes(
                client_slug,
                path,
                prod.to_markdown(r).encode("utf-8"),
                content_type="text/markdown; charset=utf-8",
            )
            src = by_cluster.get(r.task_cluster_id)
            fresh.workforce.skills_guidance.append(
                TaskSkillRecord(
                    id=f"skill-{uuid.uuid4().hex[:8]}",
                    profile_key=r.profile_key,
                    role_title=src.role_title if src else "",
                    task_cluster_id=r.task_cluster_id,
                    cluster_name=src.cluster_name if src else "",
                    name=r.name,
                    description=r.description,
                    hook=r.hook,
                    blob_path=path,
                    rank_score=src.rank_score if src else 0.0,
                    generated_at=now,
                )
            )
        svc.save_state(
            fresh,
            action="generate-productivity-skills",
            lineage_payload={
                "profile_key": req.profile_key,
                "requested": len(inputs),
                "generated": len(made),
            },
        )
        summary = {
            "role": role_title,
            "requested": len(inputs),
            "generated": len(made),
            "failed": len(inputs) - len(made),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "productivity", work)


def _skill_record(state: ProjectState, skill_id: str) -> TaskSkillRecord:
    rec = next((s for s in state.workforce.skills_guidance if s.id == skill_id), None)
    if rec is None:
        raise HTTPException(404, f"no skill {skill_id}")
    return rec


@router.get("/productivity/skill/{skill_id}")
def get_skill(client_slug: str, project_slug: str, skill_id: str) -> dict:
    """The skill's markdown, for the in-app viewer."""
    svc, state = _load(client_slug, project_slug)
    rec = _skill_record(state, skill_id)
    data = svc.store.read_bytes(client_slug, rec.blob_path)
    if data is None:
        raise HTTPException(404, f"the file for {skill_id} is missing from storage")
    return {**rec.model_dump(mode="json"), "markdown": data.decode("utf-8")}


@router.get("/productivity/skill/{skill_id}/download")
def download_skill(client_slug: str, project_slug: str, skill_id: str) -> Response:
    svc, state = _load(client_slug, project_slug)
    rec = _skill_record(state, skill_id)
    data = svc.store.read_bytes(client_slug, rec.blob_path)
    if data is None:
        raise HTTPException(404, f"the file for {skill_id} is missing from storage")
    return Response(
        content=data,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{rec.name}.md"'},
    )


@router.get("/productivity/role/{profile_key}/zip")
def download_role_skills(client_slug: str, project_slug: str, profile_key: str) -> Response:
    """Every skill for one role, as a zip — the shape you hand to a person."""
    svc, state = _load(client_slug, project_slug)
    recs = [s for s in state.workforce.skills_guidance if s.profile_key == profile_key]
    if not recs:
        raise HTTPException(404, f"no skills generated for {profile_key}")
    buf = io.BytesIO()
    missing: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in recs:
            data = svc.store.read_bytes(client_slug, r.blob_path)
            if data is None:
                # Named in the zip rather than dropped, so a partial download cannot be
                # mistaken for a complete one.
                missing.append(r.name)
                continue
            z.writestr(f"{r.name}.md", data)
        if missing:
            z.writestr(
                "MISSING.txt",
                "These skills are recorded in the project but their files could not be "
                "read from storage, so they are not in this zip:\n\n"
                + "\n".join(f"- {m}.md" for m in missing)
                + "\n",
            )
    stem = re.sub(r"[^a-z0-9]+", "-", (recs[0].role_title or profile_key).lower()).strip("-")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{stem or profile_key}-skills.zip"'},
    )


# ===========================================================================
# Step 6 — Agent definitions
# ===========================================================================
AGENTS_PREFIX = "workforce/agents"
# A catalogue larger than this is not refused, only truncated: it rides on every call
# of the fan-out as a cache prefix, and a 400KB inventory would dominate the prompt
# without telling the model anything the first few pages did not.
MAX_CATALOGUE_CHARS = 60_000


def _agent_inputs(state: ProjectState) -> list[ag.AgentInput]:
    """Every assessed task cluster as a candidate agent, best first.

    Ordered by time released — automation weighted by how much of the organisation's
    week the cluster consumes — rather than by automation alone. A rare, highly
    automatable task ranks below a common, moderately automatable one, which is the
    honest order for something that has to be built and then maintained.
    """
    c = state.tasks.clustering
    if c is None:
        return []
    scores = {
        o.task_cluster_id: (o.automation_pct, o.augmentation_pct)
        for o in state.workforce.opportunity
    }
    if not scores:
        return []

    actions: dict[int, list[tuple[str, str, float, float]]] = {}
    for a in state.workforce.actions:
        actions.setdefault(a.task_cluster_id, []).append(
            (a.name, a.definition, a.automation_pct, a.augmentation_pct)
        )

    headcount = _profile_headcount(state)
    title_of = {p.profile_key: p.title for p in state.job_profiles}
    parents = {a.final_profile_id: (a.final_category_id, a.final_family_id) for a in c.assignments}
    cluster_of = {a.item_id: a.final_profile_id for a in c.assignments}

    names: dict[int, set[str]] = {}
    roles: dict[int, dict[str, float]] = {}
    proportion: dict[int, float] = {}
    fte: dict[int, float] = {}
    for t in state.tasks.inferred:
        cid = cluster_of.get(t.id)
        if cid is None or cid not in scores:
            continue
        names.setdefault(cid, set()).add(t.name)
        title = title_of.get(t.source_profile_key, t.source_profile_key)
        r = roles.setdefault(cid, {})
        r[title] = r.get(title, 0.0) + t.proportion
        proportion[cid] = proportion.get(cid, 0.0) + t.proportion
        heads = headcount.get(t.source_profile_key, 0)
        if heads:
            fte[cid] = fte.get(cid, 0.0) + t.proportion / 100.0 * heads

    unit = "FTE" if headcount else "role-weeks"
    out: list[ag.AgentInput] = []
    for cid, (auto, aug) in scores.items():
        cat, fam = parents.get(cid, (-1, -1))
        base = fte.get(cid) if headcount else proportion.get(cid, 0.0) / 100.0
        out.append(
            ag.AgentInput(
                task_cluster_id=cid,
                cluster_name=c.profile_names.get(cid, str(cid)),
                category=c.category_names.get(cat, "—"),
                domain=c.family_names.get(fam, "—"),
                automation_pct=auto,
                augmentation_pct=aug,
                actions=actions.get(cid, []),
                roles=sorted(roles.get(cid, {}).items(), key=lambda kv: -kv[1]),
                task_names=sorted(names.get(cid, set())),
                absorbable=round((base or 0.0) * auto / 100.0, 2),
                unit=unit,
                # The *client*, prettified from its slug — `display_name` is the
                # project's name, and specs generated with it read "for architects at
                # Full JA", naming the piece of work rather than the organisation.
                client_name=state.meta.client_slug.replace("-", " ").title(),
            )
        )
    out.sort(key=lambda i: -i.absorbable)
    return out


def _catalogue(state: ProjectState) -> str | None:
    """The software catalogue as a prompt prefix, if one has been uploaded."""
    docs = [d for d in state.workforce.context_uploads if d.kind == "software_catalogue"]
    if not docs:
        return None
    joined = "\n\n".join(f"### {d.filename}\n{d.text}" for d in docs)
    return (
        "SOFTWARE AND INFRASTRUCTURE THIS ORGANISATION ACTUALLY RUNS.\n"
        "Name these systems where a tool or knowledge source would otherwise be "
        "described generically. Do not invent systems that are not listed here.\n\n"
        + joined
    )


@router.post("/agents/catalogue")
async def upload_catalogue(
    client_slug: str, project_slug: str, file: UploadFile, kind: str = "software_catalogue"
) -> dict:
    """Upload a software catalogue or strategic-context document.

    Folded into every generation prompt in a run as a cache prefix, so a catalogue of
    any size is paid for once and read back cheaply for the rest of the fan-out.
    """
    if kind not in ("software_catalogue", "strategic_context"):
        raise HTTPException(422, f"unknown context kind {kind!r}")
    svc, _ = _load(client_slug, project_slug)
    data = await file.read()
    try:
        text = parsers.extract_text(file.filename or "upload.txt", data)
    except (parsers.UnsupportedFileType, parsers.ParseFailed) as e:
        raise HTTPException(422, str(e)) from e
    text = text.strip()
    if not text:
        raise HTTPException(422, "no text could be extracted from that file")
    truncated = len(text) > MAX_CATALOGUE_CHARS

    fresh = svc.load_state(client_slug, project_slug)
    fresh.workforce.context_uploads = [
        d for d in fresh.workforce.context_uploads if not (d.kind == kind and d.filename == file.filename)
    ]
    fresh.workforce.context_uploads.append(
        ContextDocRecord(
            id=f"ctx-{uuid.uuid4().hex[:8]}",
            kind=kind,
            filename=file.filename or "upload.txt",
            text=text[:MAX_CATALOGUE_CHARS],
            chars=len(text),
            uploaded_at=datetime.now(timezone.utc),
        )
    )
    svc.save_state(
        fresh,
        action="upload-workforce-context",
        lineage_payload={"kind": kind, "filename": file.filename, "chars": len(text)},
    )
    return {
        "filename": file.filename,
        "kind": kind,
        "chars": len(text),
        "truncated": truncated,
        "cacheable": llm.is_cacheable(text[:MAX_CATALOGUE_CHARS]),
        "documents": len(fresh.workforce.context_uploads),
    }


@router.delete("/agents/catalogue/{doc_id}")
def delete_catalogue(client_slug: str, project_slug: str, doc_id: str) -> dict:
    svc, state = _load(client_slug, project_slug)
    if not any(d.id == doc_id for d in state.workforce.context_uploads):
        raise HTTPException(404, f"no context document {doc_id}")
    state.workforce.context_uploads = [d for d in state.workforce.context_uploads if d.id != doc_id]
    svc.save_state(state, action="remove-workforce-context", lineage_payload={"id": doc_id})
    return {"documents": len(state.workforce.context_uploads)}


@router.get("/agents")
def list_agents(client_slug: str, project_slug: str, threshold: float = 0.0) -> dict:
    """Candidate clusters ranked by time released, with any spec already written.

    `threshold` filters to clusters releasing at least that much time — the control
    that keeps a bulk run from specifying an agent for work worth two hours a year.
    """
    _, state = _load(client_slug, project_slug)
    if not state.workforce.opportunity:
        raise HTTPException(409, "run the AI opportunity assessment first")

    existing = {a.task_cluster_id: a for a in state.workforce.agents}
    rows = []
    for inp in _agent_inputs(state):
        if inp.absorbable < threshold:
            continue
        a = existing.get(inp.task_cluster_id)
        ov_fraction, ov_source = (
            ag.oversight_fraction(
                pct_total=a.oversight_pct_total, human_in_the_loop=a.human_in_the_loop
            )
            if a
            else (0.0, "none")
        )
        rows.append(
            {
                "cluster_id": inp.task_cluster_id,
                "cluster": inp.cluster_name,
                "category": inp.category,
                "domain": inp.domain,
                "automation": inp.automation_pct,
                "augmentation": inp.augmentation_pct,
                "time_released": inp.absorbable,
                "roles": len(inp.roles),
                "top_roles": [r[0] for r in inp.roles[:3]],
                "n_actions": len(inp.actions),
                "agent": (
                    {
                        "id": a.id,
                        "name": a.name,
                        "purpose": a.purpose,
                        "n_capabilities": a.n_capabilities,
                        "human_in_the_loop": a.human_in_the_loop,
                        # On the list, not only on the detail. Work Design recomputes the
                        # task profile every time an agent is ticked, and reading these
                        # from a per-agent call would be one request per checkbox.
                        "oversight_tasks": [t.model_dump() for t in a.oversight_tasks],
                        "oversight_pct_total": a.oversight_pct_total,
                        "oversight_clamped": a.oversight_clamped,
                        # Resolved here so every reader agrees on the number and on whether
                        # it was judged for this agent or assumed for all of them.
                        "oversight_fraction": ov_fraction,
                        "oversight_source": ov_source,
                    }
                    if a
                    else None
                ),
            }
        )
    docs = [
        {"id": d.id, "kind": d.kind, "filename": d.filename, "chars": d.chars}
        for d in state.workforce.context_uploads
    ]
    return {
        "clusters": rows,
        "unit": rows[0]["time_released"] and ("FTE" if _profile_headcount(state) else "role-weeks") or "role-weeks",
        "total_agents": len(state.workforce.agents),
        "domains": sorted({r["domain"] for r in rows}),
        "context_documents": docs,
        "estimate_all": ag.cost_estimate(sum(1 for r in rows if not r["agent"])),
    }


class GenerateAgentsRequest(BaseModel):
    """`cluster_ids` names specific clusters; `threshold` and `limit` drive the bulk
    run. Both are here because "generate all" on a 750-cluster taxonomy at ~6,000
    output tokens each is the most expensive thing either studio can do."""

    cluster_ids: list[int] | None = None
    threshold: float = 0.0
    limit: int | None = None
    redo: bool = False


@router.post("/agents/generate")
async def generate_agents(
    client_slug: str, project_slug: str, req: GenerateAgentsRequest, workers: int | None = None
) -> dict:
    svc, state = _load(client_slug, project_slug)
    _workers = llm.resolve_workers(workers)

    inputs = _agent_inputs(state)
    if req.cluster_ids:
        wanted = set(req.cluster_ids)
        inputs = [i for i in inputs if i.task_cluster_id in wanted]
    else:
        inputs = [i for i in inputs if i.absorbable >= req.threshold]
    if not req.redo:
        done = {a.task_cluster_id for a in state.workforce.agents}
        inputs = [i for i in inputs if i.task_cluster_id not in done]
    if req.limit:
        inputs = inputs[: req.limit]
    if not inputs:
        raise HTTPException(400, "nothing to generate — every matching cluster already has a spec")

    catalogue = _catalogue(state)

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(len(inputs), f"Specifying {len(inputs)} agents")
        llm.reset_cache_stats()
        results = ag.generate_many(
            inputs,
            client_slug=client_slug,
            catalogue=catalogue,
            workers=_workers,
            progress=reporter.pmap_callback(),
        )
        made = [r for r in results if r is not None]
        cache = llm.cache_stats()

        reporter.message("Saving the specifications")
        fresh = svc.load_state(client_slug, project_slug)
        replacing = {r.task_cluster_id for r in made}
        fresh.workforce.agents = [
            a for a in fresh.workforce.agents if a.task_cluster_id not in replacing
        ]
        now = datetime.now(timezone.utc)
        by_cluster = {i.task_cluster_id: i for i in inputs}
        for r in made:
            agent_id = f"agent-{uuid.uuid4().hex[:8]}"
            path = svc.save_json(client_slug, project_slug, f"{AGENTS_PREFIX}/{agent_id}", r.spec)
            src = by_cluster.get(r.task_cluster_id)
            fresh.workforce.agents.append(
                AgentDefinitionRecord(
                    id=agent_id,
                    task_cluster_id=r.task_cluster_id,
                    cluster_name=src.cluster_name if src else "",
                    name=r.name,
                    slug=r.slug,
                    purpose=r.purpose,
                    blob_path=path,
                    time_released=src.absorbable if src else 0.0,
                    time_released_unit=src.unit if src else "role-weeks",
                    automation_pct=src.automation_pct if src else 0.0,
                    n_capabilities=r.n_capabilities,
                    human_in_the_loop=r.human_in_the_loop,
                    oversight_tasks=[AgentOversightTask(**t) for t in r.oversight_tasks],
                    oversight_pct_total=r.oversight_pct_total,
                    oversight_clamped=r.oversight_clamped,
                    generated_at=now,
                )
            )
        svc.save_state(
            fresh,
            action="generate-agent-definitions",
            lineage_payload={"requested": len(inputs), "generated": len(made)},
        )
        summary = {
            "requested": len(inputs),
            "generated": len(made),
            "failed": len(inputs) - len(made),
            "prompt_cache": cache.summary(),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "agents", work)


@router.get("/agents/{agent_id}")
def get_agent(client_slug: str, project_slug: str, agent_id: str) -> dict:
    """One agent's full eight-section specification."""
    svc, state = _load(client_slug, project_slug)
    rec = next((a for a in state.workforce.agents if a.id == agent_id), None)
    if rec is None:
        raise HTTPException(404, f"no agent {agent_id}")
    spec = svc.load_json(client_slug, rec.blob_path)
    if spec is None:
        raise HTTPException(404, f"the specification for {agent_id} is missing from storage")
    return {**rec.model_dump(mode="json"), "spec": spec, "sections": list(ag.SECTIONS)}


@router.get("/agents/{agent_id}/download")
def download_agent(client_slug: str, project_slug: str, agent_id: str) -> Response:
    svc, state = _load(client_slug, project_slug)
    rec = next((a for a in state.workforce.agents if a.id == agent_id), None)
    if rec is None:
        raise HTTPException(404, f"no agent {agent_id}")
    spec = svc.load_json(client_slug, rec.blob_path)
    if spec is None:
        raise HTTPException(404, f"the specification for {agent_id} is missing from storage")
    return Response(
        content=json.dumps(spec, indent=2, ensure_ascii=False).encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{rec.slug}-agent-spec.json"'},
    )


@router.get("/agents/report/impact")
def agent_impact(client_slug: str, project_slug: str) -> dict:
    """The impact summary: every specified agent, prioritised by time released."""
    _, state = _load(client_slug, project_slug)
    rows = sorted(state.workforce.agents, key=lambda a: -a.time_released)
    unit = rows[0].time_released_unit if rows else "role-weeks"
    supervised = sum(1 for a in rows if a.human_in_the_loop)
    return {
        "agents": [
            {
                "id": a.id,
                "name": a.name,
                "cluster": a.cluster_name,
                "purpose": a.purpose,
                "automation": a.automation_pct,
                "time_released": a.time_released,
                "n_capabilities": a.n_capabilities,
                "human_in_the_loop": a.human_in_the_loop,
            }
            for a in rows
        ],
        "unit": unit,
        "totals": {
            "agents": len(rows),
            "time_released": round(sum(a.time_released for a in rows), 2),
            "supervised": supervised,
            "unsupervised": len(rows) - supervised,
            "mean_automation": round(sum(a.automation_pct for a in rows) / len(rows), 1)
            if rows
            else 0.0,
        },
    }


# ===========================================================================
# Step 2 — Process upload and mapping
# ===========================================================================
PROCESS_PREFIX = "workforce/processes"


def _task_candidates(
    svc: ProjectService, client_slug: str, project_slug: str, state: ProjectState
) -> list[proc.ClusterCandidate]:
    """Task cluster centroids, from the cached embedding matrix.

    Recomputed from cache rather than persisted: the vectors and the assignments are
    both already stored, the mean is microseconds, and a fourth cached derivative is a
    fourth thing that can go stale against a re-clustering.
    """
    c = state.tasks.clustering
    if c is None:
        return []
    spec = tier_state.spec("task")
    matrix = svc.load_array(client_slug, f"{project_slug}/artifacts/{spec.array_name}.npy")
    ids = svc.load_index(client_slug, f"{project_slug}/artifacts/{spec.array_name}_index.json")
    if matrix is None or ids is None:
        raise HTTPException(
            409,
            "the task embeddings are not cached for this project, so process steps "
            "cannot be matched — re-run the task taxonomy to rebuild them",
        )
    return proc.cluster_centroids(
        matrix,
        ids,
        {a.item_id: a.final_profile_id for a in c.assignments},
        dict(c.profile_names),
    )


@router.get("/processes")
def list_processes(client_slug: str, project_slug: str) -> dict:
    """Uploaded processes, their steps, and any assessment."""
    _, state = _load(client_slug, project_slug)
    assessments = {a.process_id: a for a in state.workforce.process_assessments}
    return {
        "processes": [
            {
                **p.model_dump(mode="json"),
                "unmatched_steps": p.unmatched_steps,
                "manual_steps": sum(1 for s in p.steps if not s.automated),
                "handoffs": sum(1 for s in p.steps if s.handoff),
                "sign_offs": sum(1 for s in p.steps if s.sign_off),
                "assessment": (
                    assessments[p.id].model_dump(mode="json") if p.id in assessments else None
                ),
            }
            for p in state.workforce.processes
        ],
        "supported_extensions": sorted(parsers.SUPPORTED_EXTENSIONS),
        "assessed": len(state.workforce.process_assessments),
        "has_opportunity": bool(state.workforce.opportunity),
    }


@router.post("/processes/upload")
async def upload_process(client_slug: str, project_slug: str, file: UploadFile) -> dict:
    """Store a process document and infer its steps. Mapping is a separate call.

    Split that way because parsing plus one model call is seconds, and mapping needs the
    embedding model loaded — which on a cold GPU is a different order of wait and
    deserves its own progress bar.
    """
    svc, state = _load(client_slug, project_slug)
    data = await file.read()
    name = file.filename or "process.txt"
    try:
        text = parsers.extract_text(name, data)
    except parsers.UnsupportedFileType as e:
        raise HTTPException(422, str(e)) from e
    except parsers.ParseFailed as e:
        raise HTTPException(422, f"{name} could not be read: {e}") from e

    blob_path = f"{project_slug}/{PROCESS_PREFIX}/raw/{name}"
    svc.store.write_bytes(client_slug, blob_path, data)

    def work(reporter: ProgressReporter) -> dict:
        reporter.message(f"Reading the process in {name}")
        inferred = proc.infer_process(text, filename=name)

        fresh = svc.load_state(client_slug, project_slug)
        fresh.workforce.processes = [p for p in fresh.workforce.processes if p.filename != name]
        process_id = f"proc-{uuid.uuid4().hex[:8]}"
        fresh.workforce.processes.append(
            ProcessRecord(
                id=process_id,
                filename=name,
                blob_path=blob_path,
                process_name=inferred.process_name,
                summary=inferred.summary,
                ordering_confidence=inferred.ordering_confidence,
                steps=[
                    ProcessStepRecord(
                        sequence=s.sequence,
                        name=s.name,
                        description=s.description,
                        actor=s.actor,
                        system=s.system,
                        automated=s.automated,
                        handoff=s.handoff,
                        sign_off=s.sign_off,
                    )
                    for s in inferred.steps
                ],
                uploaded_at=datetime.now(timezone.utc),
            )
        )
        svc.save_state(
            fresh,
            action="upload-process",
            lineage_payload={"filename": name, "steps": len(inferred.steps)},
        )
        summary = {
            "process": inferred.process_name,
            "steps": len(inferred.steps),
            "manual_steps": inferred.manual_steps,
            "actors": len(inferred.actors),
            "ordering_confidence": inferred.ordering_confidence,
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "processes", work)


@router.post("/processes/{process_id}/map")
async def map_process(client_slug: str, project_slug: str, process_id: str) -> dict:
    """Match each step onto the task taxonomy: geometry first, model on the tail."""
    svc, state = _load(client_slug, project_slug)
    record = next((p for p in state.workforce.processes if p.id == process_id), None)
    if record is None:
        raise HTTPException(404, f"no process {process_id}")
    if state.tasks.clustering is None:
        raise HTTPException(409, "the task taxonomy has not been built yet")

    def work(reporter: ProgressReporter) -> dict:
        reporter.message("Building task cluster centroids")
        candidates = _task_candidates(svc, client_slug, project_slug, state)
        steps = [
            proc.InferredStep(
                name=s.name, description=s.description, actor=s.actor, system=s.system,
                automated=s.automated, handoff=s.handoff, sign_off=s.sign_off, sequence=s.sequence,
            )
            for s in record.steps
        ]
        reporter.message(f"Embedding {len(steps)} process steps")
        service = emb.get_embedding_service()
        vectors = service.embed_documents("task", [s.embedding_text() for s in steps])

        reporter.stage_start(len(steps), "Matching steps to the task taxonomy")
        done = {"n": 0}

        def confirm(step, shortlist):
            out = proc.confirm_match(step, shortlist)
            done["n"] += 1
            reporter.progress(done["n"], len(steps), "confirming uncertain matches")
            return out

        matches = proc.match_steps(steps, vectors, candidates, confirm=confirm)

        fresh = svc.load_state(client_slug, project_slug)
        target = next((p for p in fresh.workforce.processes if p.id == process_id), None)
        if target is None:
            raise RuntimeError(f"process {process_id} disappeared while mapping")
        by_seq = {m.sequence: m for m in matches}
        for s in target.steps:
            m = by_seq.get(s.sequence)
            if m is None:
                continue
            s.task_cluster_id = m.cluster_id
            s.task_cluster_name = m.cluster_name
            s.match_cosine = m.cosine
            s.routed_by_llm = m.routed_by_llm
            s.match_confidence = m.confidence
            s.match_reasoning = m.reasoning
        target.mapped_at = datetime.now(timezone.utc)
        svc.save_state(
            fresh,
            action="map-process-steps",
            lineage_payload={
                "process_id": process_id,
                "steps": len(matches),
                "unmatched": sum(1 for m in matches if not m.matched),
            },
        )
        summary = {
            "process": target.process_name,
            "steps": len(matches),
            "matched": sum(1 for m in matches if m.matched),
            "unmatched": sum(1 for m in matches if not m.matched),
            "confirmed_by_model": sum(1 for m in matches if m.routed_by_llm),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "processes", work)


@router.delete("/processes/{process_id}")
def delete_process(client_slug: str, project_slug: str, process_id: str) -> dict:
    svc, state = _load(client_slug, project_slug)
    if not any(p.id == process_id for p in state.workforce.processes):
        raise HTTPException(404, f"no process {process_id}")
    state.workforce.processes = [p for p in state.workforce.processes if p.id != process_id]
    state.workforce.process_assessments = [
        a for a in state.workforce.process_assessments if a.process_id != process_id
    ]
    svc.save_state(state, action="delete-process", lineage_payload={"process_id": process_id})
    return {"processes": len(state.workforce.processes)}


# ===========================================================================
# Step 4 — Process opportunity assessment
# ===========================================================================
@router.post("/processes/{process_id}/assess")
async def assess_process_route(client_slug: str, project_slug: str, process_id: str) -> dict:
    svc, state = _load(client_slug, project_slug)
    record = next((p for p in state.workforce.processes if p.id == process_id), None)
    if record is None:
        raise HTTPException(404, f"no process {process_id}")
    if not record.mapped_at:
        raise HTTPException(409, "map the process onto the task taxonomy first")

    scores = {
        o.task_cluster_id: (o.automation_pct, o.augmentation_pct)
        for o in state.workforce.opportunity
    }

    def work(reporter: ProgressReporter) -> dict:
        reporter.message(f"Assessing {record.process_name} as-is and to-be")
        inferred = proc.InferredProcess(
            process_name=record.process_name,
            summary=record.summary,
            ordering_confidence=record.ordering_confidence,
            steps=[
                proc.InferredStep(
                    name=s.name, description=s.description, actor=s.actor, system=s.system,
                    automated=s.automated, handoff=s.handoff, sign_off=s.sign_off,
                    sequence=s.sequence,
                )
                for s in record.steps
            ],
        )
        matches = [
            proc.StepMatch(s.sequence, s.task_cluster_id, s.task_cluster_name, s.match_cosine)
            for s in record.steps
        ]
        a = proc.assess_process(inferred, matches, scores)

        fresh = svc.load_state(client_slug, project_slug)
        fresh.workforce.process_assessments = [
            x for x in fresh.workforce.process_assessments if x.process_id != process_id
        ]
        fresh.workforce.process_assessments.append(
            ProcessAssessmentRecord(
                process_id=process_id,
                # Measured from the steps, not asked of the model.
                as_is_steps=len(inferred.steps),
                as_is_manual_touchpoints=inferred.manual_steps,
                as_is_actors=len(inferred.actors),
                as_is_sign_offs=sum(1 for s in inferred.steps if s.sign_off),
                as_is_handoffs=sum(1 for s in inferred.steps if s.handoff),
                to_be_steps=a.to_be_steps,
                to_be_manual_touchpoints=a.to_be_manual_touchpoints,
                to_be_actors=a.to_be_actors,
                to_be_sign_offs=a.to_be_sign_offs,
                effort_reduction_pct=a.effort_reduction_pct,
                elapsed_reduction_pct=a.elapsed_reduction_pct,
                as_is_narrative=a.as_is_narrative,
                to_be_narrative=a.to_be_narrative,
                what_changes=a.what_changes,
                risks=a.risks,
                prerequisites=a.prerequisites,
                computed_at=datetime.now(timezone.utc),
            )
        )
        svc.save_state(
            fresh,
            action="assess-process",
            lineage_payload={"process_id": process_id, "effort_reduction": a.effort_reduction_pct},
        )
        summary = {
            "process": record.process_name,
            "steps": f"{len(inferred.steps)} → {a.to_be_steps}",
            "manual_touchpoints": f"{inferred.manual_steps} → {a.to_be_manual_touchpoints}",
            "effort_reduction_pct": a.effort_reduction_pct,
            "elapsed_reduction_pct": a.elapsed_reduction_pct,
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "processes", work)


# ===========================================================================
# Step 7 — Future role design
# ===========================================================================
def _future_role_inputs(
    state: ProjectState, *, profile_keys: set[str] | None = None
) -> list[fr.FutureRoleInput]:
    """Roles eligible for a redesign, most-affected first."""
    c = state.tasks.clustering
    if c is None:
        return []
    scores = {
        o.task_cluster_id: (o.automation_pct, o.augmentation_pct)
        for o in state.workforce.opportunity
    }
    if not scores:
        return []

    context = "\n\n".join(
        f"### {d.filename}\n{d.text}"
        for d in state.workforce.context_uploads
        if d.kind == "strategic_context"
    )
    prefix = (
        "HOW THIS ORGANISATION WANTS FREED-UP TIME USED.\n"
        "Point the future role at these priorities where they are relevant.\n\n" + context
        if context
        else ""
    )

    agents_by_cluster: dict[int, list[str]] = {}
    for a in state.workforce.agents:
        agents_by_cluster.setdefault(a.task_cluster_id, []).append(a.name)

    profile = {d.profile_key: d for d in state.job_profiles}
    cluster_of = {a.item_id: a.final_profile_id for a in c.assignments}
    grouped: dict[str, dict[int, float]] = {}
    for t in state.tasks.inferred:
        cid = cluster_of.get(t.id)
        if cid is None or cid not in scores:
            continue
        if profile_keys and t.source_profile_key not in profile_keys:
            continue
        g = grouped.setdefault(t.source_profile_key, {})
        g[cid] = g.get(cid, 0.0) + t.proportion

    out: list[fr.FutureRoleInput] = []
    for key, by_cluster in grouped.items():
        doc = profile.get(key)
        tasks = [
            (c.profile_names.get(cid, str(cid)), prop, scores[cid][0], scores[cid][1])
            for cid, prop in by_cluster.items()
        ]
        covered = sum(prop for _n, prop, _a, _g in tasks)
        auto = round(sum(prop * a for _n, prop, a, _g in tasks) / covered, 1) if covered else 0.0
        aug = round(sum(prop * g for _n, prop, _a, g in tasks) / covered, 1) if covered else 0.0
        out.append(
            fr.FutureRoleInput(
                profile_key=key,
                title=doc.title if doc else key,
                purpose=str((doc.content or {}).get("about_role", ""))[:600] if doc else "",
                automation_pct=auto,
                augmentation_pct=aug,
                tasks=tasks,
                agents=sorted({n for cid in by_cluster for n in agents_by_cluster.get(cid, [])}),
                strategic_context=prefix,
            )
        )
    out.sort(key=lambda i: -i.time_released_pct)
    return out


@router.get("/future-roles")
def list_future_roles(client_slug: str, project_slug: str) -> dict:
    _, state = _load(client_slug, project_slug)
    if not state.workforce.opportunity:
        raise HTTPException(409, "run the AI opportunity assessment first")
    existing = {f.profile_key: f for f in state.workforce.future_roles}
    ancestry = _job_ancestry(state)
    rows = []
    for inp in _future_role_inputs(state):
        fam, cat = ancestry.get(inp.profile_key, ("—", "—"))
        rows.append(
            {
                "profile_key": inp.profile_key,
                "title": inp.title,
                "family": fam,
                "category": cat,
                "automation": inp.automation_pct,
                "augmentation": inp.augmentation_pct,
                "time_released_pct": inp.time_released_pct,
                "n_tasks": len(inp.tasks),
                "absorbed": inp.absorbed,
                "agents": inp.agents,
                "design": (
                    existing[inp.profile_key].model_dump(mode="json")
                    if inp.profile_key in existing
                    else None
                ),
            }
        )
    return {
        "roles": rows,
        "families": sorted({r["family"] for r in rows}),
        "designed": len(state.workforce.future_roles),
        "has_strategic_context": any(
            d.kind == "strategic_context" for d in state.workforce.context_uploads
        ),
        "estimate_all": fr.cost_estimate(sum(1 for r in rows if not r["design"])),
    }


class DesignRolesRequest(BaseModel):
    profile_keys: list[str] | None = None
    limit: int | None = None
    redo: bool = False


@router.post("/future-roles/design")
async def design_future_roles(
    client_slug: str, project_slug: str, req: DesignRolesRequest, workers: int | None = None
) -> dict:
    svc, state = _load(client_slug, project_slug)
    _workers = llm.resolve_workers(workers)
    inputs = _future_role_inputs(
        state, profile_keys=set(req.profile_keys) if req.profile_keys else None
    )
    if not req.redo:
        done = {f.profile_key for f in state.workforce.future_roles}
        inputs = [i for i in inputs if i.profile_key not in done]
    if req.limit:
        inputs = inputs[: req.limit]
    if not inputs:
        raise HTTPException(400, "nothing to design — every matching role already has a design")

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(len(inputs), f"Redesigning {len(inputs)} roles")
        llm.reset_cache_stats()
        results = fr.design_many(inputs, workers=_workers, progress=reporter.pmap_callback())
        made = [r for r in results if r is not None]
        cache = llm.cache_stats()

        reporter.message("Saving the designs")
        fresh = svc.load_state(client_slug, project_slug)
        replacing = {r.profile_key for r in made}
        fresh.workforce.future_roles = [
            f for f in fresh.workforce.future_roles if f.profile_key not in replacing
        ]
        now = datetime.now(timezone.utc)
        for r in made:
            fresh.workforce.future_roles.append(
                FutureRoleRecord(
                    profile_key=r.profile_key,
                    title=r.title,
                    evolution_today=r.evolution_today,
                    evolution_after_automation=r.evolution_after_automation,
                    evolution_future=r.evolution_future,
                    future_purpose=r.future_purpose,
                    future_responsibilities=r.future_responsibilities,
                    absorbed_tasks=r.absorbed_tasks,
                    deepened_tasks=r.deepened_tasks,
                    skills_to_build=r.skills_to_build,
                    deliberate_practice=r.deliberate_practice,
                    automation_pct=r.automation_pct,
                    time_released_pct=r.time_released_pct,
                    computed_at=now,
                )
            )
        svc.save_state(
            fresh,
            action="design-future-roles",
            lineage_payload={"requested": len(inputs), "designed": len(made)},
        )
        summary = {
            "requested": len(inputs),
            "designed": len(made),
            "failed": len(inputs) - len(made),
            "prompt_cache": cache.summary(),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "future-roles", work)
