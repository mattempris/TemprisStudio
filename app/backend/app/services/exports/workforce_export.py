"""Everything Work Architecture Studio holds, as one JSON document.

A data export rather than a report: nothing is summarised, ranked or rounded for reading. The
audience is a system or an analyst who wants the assessment out of the app and into something
else, so every record comes out as it is held.

Three things make it self-contained rather than a dump of foreign keys:

**The taxonomies come with it.** Almost every record here is keyed by a task cluster id, and an
id without its name is unusable outside the app. All three hierarchies are included flat, one row
per cluster with its parent, so a consumer can resolve any id and rebuild the tree without
guessing at nesting.

**The agent specifications are inlined.** They live in one blob each, and an export that hands
back a blob path the reader cannot fetch is not an export. It costs a read per agent, which is
the slowest part of this endpoint and worth it.

**The graph edges are included.** The studio's picture is job-to-task and job-to-skill weights,
and without them the assessment is a list of scores with nothing to attach them to.

Deliberately NOT included: embeddings and linkage trees, which are large, opaque and
reproducible; and the job architecture itself beyond the cluster names, which has its own export
in `exports/architecture.py` and its own XLSX.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.project_state import ProjectState
from app.services.clustering import tier_state
from app.services.project_service import ProjectService
from app.services.workforce import graph as wf


class NotReady(RuntimeError):
    """The work architecture has not been built, so there is nothing to export."""


def _taxonomy(state: ProjectState, entity: str) -> list[dict]:
    """One row per cluster across all three tiers, flat, each naming its parent.

    Flat rather than nested because a consumer joining on a cluster id wants a lookup, and a
    tree forces them to walk it. `parent_id` is enough to rebuild the nesting if they want it.

    Read from the tier records rather than the denormalised view, so descriptions come along —
    that view carries names only.
    """
    tiers = tier_state.tiers_of(state, entity)
    spec = tier_state.spec(entity)
    out: list[dict] = []
    for tier in tier_state.ORDER:  # finest first
        rec = tiers.get(tier)
        if rec is None or not rec.names:
            continue
        parent = tier_state.PARENT_OF.get(tier)
        prec = tiers.get(parent) if parent else None
        # A cluster's parent is the cluster its own id was placed into one tier up, where it is
        # known as "<tier>:<id>".
        parent_of: dict[int, int] = {}
        if prec is not None:
            for m in prec.members:
                if m.item_id.startswith(f"{tier}:"):
                    try:
                        parent_of[int(m.item_id.split(":", 1)[1])] = m.final_cluster_id
                    except ValueError:
                        continue
        sizes: dict[int, int] = {}
        for m in rec.members:
            sizes[m.final_cluster_id] = sizes.get(m.final_cluster_id, 0) + 1
        for cid in sorted(rec.names):
            out.append(
                {
                    "tier": tier,
                    "tier_title": tier_state.tier_title(entity, tier),
                    "id": cid,
                    "name": rec.names[cid],
                    "description": rec.descriptions.get(cid, ""),
                    "parent_tier": parent,
                    "parent_id": parent_of.get(cid),
                    "members": sizes.get(cid, 0),
                    "member_noun": spec.nouns[tier_state.ORDER.index(tier)],
                }
            )
    return out


def build(
    svc: ProjectService, state: ProjectState, facts: wf.Facts, *, include_specs: bool = True
) -> dict:
    """The whole export. `facts` is the built graph — this never builds one."""
    w = state.workforce
    client = state.meta.client_slug

    agents = []
    for a in w.agents:
        row = a.model_dump(mode="json")
        if include_specs:
            # One blob read per agent. The record carries the headline numbers; the
            # specification is the deliverable, and a path the reader cannot resolve is not an
            # export. `None` where the blob has gone rather than omitting the key, so a
            # consumer can tell "no spec stored" from "field not in this export".
            row["specification"] = svc.load_json(client, a.blob_path) if a.blob_path else None
        agents.append(row)

    return {
        "meta": {
            "client_slug": client,
            "project_slug": state.meta.project_slug,
            "display_name": state.meta.display_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "graph_built_at": facts.built_at,
            "graph_version": facts.version,
            # Every "hours" figure downstream divides by this, so it travels with the data.
            "hours_per_fte_week": w.hours_per_fte_week,
            "has_headcount": facts.has_headcount,
            "specifications_included": include_specs,
        },
        "counts": {
            "task_clusters_assessed": len(w.opportunity),
            "actions": len(w.actions),
            "augmentations": len(w.skills_guidance),
            "agents": len(w.agents),
            "processes": len(w.processes),
            "process_assessments": len(w.process_assessments),
            "future_roles": len(w.future_roles),
            "context_documents": len(w.context_uploads),
            "job_task_edges": len(facts.job_task),
            "job_skill_edges": len(facts.job_skill),
        },
        # Ids resolve against these. Included for all three hierarchies because the graph
        # connects all three.
        "taxonomies": {e: _taxonomy(state, e) for e in tier_state.ENTITIES},
        # What the studio's picture is made of: which roles do which work, and how much.
        "architecture": {
            "job_task": [
                {"job_cluster": j, "task_cluster": t, "proportion_pct": round(p, 4)}
                for j, t, p in facts.job_task
            ],
            "job_skill": [
                {"job_cluster": j, "skill_cluster": s, "weight": round(v, 4)}
                for j, s, v in facts.job_skill
            ],
            # cluster -> (automation, augmentation). Kept as the rolled-up pair the graph
            # colours by, so a consumer reproducing the picture does not have to re-derive it.
            "task_opportunity": [
                {"task_cluster": cid, "automation_pct": a, "augmentation_pct": g}
                for cid, (a, g) in sorted(facts.task_opportunity.items())
            ],
            "job_opportunity": [
                {
                    "job_cluster": cid,
                    "automation_pct": a,
                    "augmentation_pct": g,
                    "coverage_pct": c,
                }
                for cid, (a, g, c) in sorted(facts.job_opportunity.items())
            ],
        },
        # Step 3. Actions are the level the scores are real at — a cluster's score is their
        # effort-weighted mean, so both are here rather than only the roll-up.
        "assessment": {
            "task_opportunity": [r.model_dump(mode="json") for r in w.opportunity],
            "actions": [r.model_dump(mode="json") for r in w.actions],
        },
        # Named for the axis each acts on, as the studio's own steps are: augmentation keeps the
        # person and makes them faster, automation takes the work away.
        "augmentation": [r.model_dump(mode="json") for r in w.skills_guidance],
        "automation": {"agents": agents},
        "processes": {
            "documents": [r.model_dump(mode="json") for r in w.processes],
            "assessments": [r.model_dump(mode="json") for r in w.process_assessments],
        },
        "future_roles": [r.model_dump(mode="json") for r in w.future_roles],
        "context_documents": [r.model_dump(mode="json") for r in w.context_uploads],
        "audit": w.audit,
    }
