"""The job architecture as a standalone HTML report.

A client deliverable, not a view of the app. One file that opens anywhere, with the data
embedded — no server, no network, nothing to install.

**It reports the result, not the method.** Nothing about uploads, similarity thresholds,
cluster counts, embedding models, stability gates or model calls appears. Those are how the
architecture was reached and they belong in the app, where they can be changed; a reader of this
document wants to know what the architecture *is*. The one exception is that skipped steps are
named in the footnote, because a section that is absent needs to say whether it was declined or
simply not reached — silence there would be the reader's problem to solve.

Five sections, each one an existing result rather than new analysis:

  Overview   the shape of it in numbers
  Structure  families and categories, sized by people
  Levelling  the job evaluation, as a distribution across the bands
  Capability the skills taxonomy, and how widely each family is needed
  Work       the task taxonomy, by share of the workforce's week
  Explore    the tree, down to a role and its detail

Sections whose data does not exist are omitted entirely — including from the nav — rather than
rendered empty. A report with a "Levelling" heading over nothing reads as broken; one without
that heading reads as a project that has not been evaluated, which is what it is.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.models.project_state import ProjectState
from app.services import skip_steps
from app.services.exports import architecture

_TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "data" / "defaults"
_env: Environment | None = None


def _get_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
        )
    return _env


class NotReady(RuntimeError):
    """The architecture has not been built far enough to report on."""


def _taxonomy(clustering) -> list[dict]:
    """A skill or task taxonomy as Family > Category > Cluster, with leaf counts.

    Both hierarchies are the same three-tier shape as the job one, so one function reads both.
    Sorted by size at every level, because "the biggest thing first" is how anyone reads a
    structure they have not seen before.
    """
    if clustering is None or not clustering.profile_names:
        return []
    leaves: dict[int, int] = {}
    fam_of: dict[int, tuple[int, int]] = {}
    for a in clustering.assignments:
        leaves[a.final_profile_id] = leaves.get(a.final_profile_id, 0) + 1
        fam_of[a.final_profile_id] = (a.final_family_id, a.final_category_id)

    tree: dict[int, dict] = {}
    for cid, name in clustering.profile_names.items():
        fid, catid = fam_of.get(cid, (-1, -1))
        fam = tree.setdefault(
            fid,
            {"id": fid, "name": clustering.family_names.get(fid, "Unassigned"), "categories": {}},
        )
        cat = fam["categories"].setdefault(
            catid,
            {
                "id": catid,
                "name": clustering.category_names.get(catid, "Unassigned"),
                "clusters": [],
            },
        )
        cat["clusters"].append({"id": cid, "name": name, "leaves": leaves.get(cid, 0)})

    out = []
    for fam in tree.values():
        cats = []
        for cat in fam["categories"].values():
            cat["clusters"].sort(key=lambda x: -x["leaves"])
            cats.append({**cat, "leaves": sum(c["leaves"] for c in cat["clusters"])})
        cats.sort(key=lambda x: -x["leaves"])
        out.append(
            {
                "id": fam["id"],
                "name": fam["name"],
                "categories": cats,
                "clusters": sum(len(c["clusters"]) for c in cats),
                "leaves": sum(c["leaves"] for c in cats),
            }
        )
    out.sort(key=lambda x: -x["leaves"])
    return out


# How many of a role's tasks travel with it. The detail panel shows ten and says how many more
# there are, so twelve leaves headroom without carrying a tail nothing displays.
TASKS_PER_ROLE = 12
# Enough source titles to recognise what a role absorbed. The panel shows fourteen.
TITLES_PER_ROLE = 20


def _trim(arch: dict) -> None:
    """Drop what the report does not render, in place.

    Not premature: the untrimmed payload is 1.93 MB for 565 roles, of which 1.44 MB is task
    *descriptions* — and the report only ever shows a task's name and its share of the week. A
    client deliverable that has to survive being emailed cannot carry a megabyte of prose no
    reader will see.
    """
    for f in arch["families"]:
        for c in f["categories"]:
            for p in c["profiles"]:
                p["tasks"] = [
                    {"name": t["name"], "proportion": t["proportion"]}
                    for t in (p.get("tasks") or [])[:TASKS_PER_ROLE]
                ]
                p["skills"] = [
                    {"cluster_name": sk["cluster_name"], "assigned_level": sk["assigned_level"]}
                    for sk in (p.get("skills") or [])
                ]
                # `source_job_count` already carries the true total, so truncating the list
                # loses nothing the report states.
                p["source_titles"] = p["source_titles"][:TITLES_PER_ROLE]


def _levelling(state: ProjectState, arch: dict) -> dict | None:
    """The job evaluation as a distribution across the framework's own bands.

    Bands come from the framework rather than from the scores, so a band nobody landed in is
    still drawn — an empty grade is a finding about the structure, and inferring the bands from
    the results would hide exactly that.

    Roles carry headcount as well as a count, because "three roles at this level" and "three
    hundred people at this level" are different sentences and the second is usually the point.
    """
    fw = state.je_framework
    if not state.je_results or not fw.level_bands:
        return None
    by_key = {r.profile_key: r for r in state.je_results if not r.stale}
    # One pass to pair every role with its family, rather than scanning the whole tree per
    # member — that inner scan was quadratic in roles and used list identity to find the
    # parent, which is fragile as well as slow.
    roles: list[tuple[dict, str]] = [
        (p, f["name"])
        for f in arch["families"] for c in f["categories"] for p in c["profiles"]
    ]

    bands = []
    for b in sorted(fw.level_bands, key=lambda x: x.min_score):
        members = [
            {
                "title": p["title"],
                "family": fam,
                "score": by_key[p["profile_key"]].aggregate_score,
                "headcount": p["headcount"],
            }
            for p, fam in roles
            if p["profile_key"] in by_key
            and b.min_score <= by_key[p["profile_key"]].aggregate_score <= b.max_score
        ]
        members.sort(key=lambda m: -m["score"])
        bands.append(
            {
                "name": b.name,
                "min_score": b.min_score,
                "max_score": b.max_score,
                "roles": len(members),
                "headcount": sum(m["headcount"] or 0 for m in members) or None,
                "members": members,
            }
        )

    scored = [r.aggregate_score for r in by_key.values()]
    # Recomputed from the stored personas rather than read off the record: `spread` lives on the
    # service's own result object and is never persisted, so `r.spread` was silently absent and
    # the report quietly dropped the sentence. The personas are all there, so the number is
    # recoverable — and it is the one figure that stops a levelling deck implying a precision
    # the method does not have.
    from app.services.evaluation.job_evaluation import weighted_score

    spreads = []
    for r in by_key.values():
        try:
            spreads.append(
                weighted_score(r.personas["Generous"], fw)
                - weighted_score(r.personas["Harsh"], fw)
            )
        except (KeyError, TypeError):
            # An older record, or one written before the three-persona shape. Skipped rather
            # than defaulted to zero, which would drag the mean towards a false confidence.
            continue
    return {
        "bands": bands,
        "evaluated": len(scored),
        "of": len(roles),
        "mean_score": round(sum(scored) / len(scored), 1) if scored else None,
        "min_score": round(min(scored), 1) if scored else None,
        "max_score": round(max(scored), 1) if scored else None,
        # The panel's own disagreement, averaged. Reported because a levelling deck that shows
        # only the midpoint invites a precision the method does not have.
        "mean_spread": round(sum(spreads) / len(spreads), 1) if spreads else None,
        "domains": [{"name": d.name, "weight": d.weight} for d in fw.domains],
    }


def _work(state: ProjectState, arch: dict) -> dict | None:
    """Where the workforce's week goes, by task cluster and by domain.

    Weighted by headcount when it exists, so a cluster taking 60% of two people's week does not
    outrank one taking 5% of four hundred people's. Without headcount the weight is one per
    role and the unit says so, which is the same degradation the rest of the app uses — never a
    synthesised headcount, and never both units at once.
    """
    t = state.tasks
    if not t.inferred or t.clustering is None or not t.clustering.profile_names:
        return None

    heads = {
        p["profile_key"]: (p["headcount"] or 1)
        for f in arch["families"] for c in f["categories"] for p in c["profiles"]
    }
    has_hc = arch["has_headcount"]
    cluster_of = {a.item_id: a.final_profile_id for a in t.clustering.assignments}
    fam_of = {a.final_profile_id: (a.final_family_id, a.final_category_id)
              for a in t.clustering.assignments}

    weight: dict[int, float] = {}
    for rec in t.inferred:
        cid = cluster_of.get(rec.id)
        if cid is None:
            continue
        w = (rec.proportion / 100.0) * (heads.get(rec.source_profile_key, 1) if has_hc else 1)
        weight[cid] = weight.get(cid, 0.0) + w

    total = sum(weight.values()) or 1.0
    clusters = [
        {
            "id": cid,
            "name": t.clustering.profile_names.get(cid, str(cid)),
            "domain": t.clustering.family_names.get(fam_of.get(cid, (-1, -1))[0], "Unassigned"),
            "share_pct": round(100 * w / total, 2),
            "fte": round(w, 1),
        }
        for cid, w in weight.items()
    ]
    clusters.sort(key=lambda x: -x["share_pct"])

    domains: dict[str, float] = {}
    for c in clusters:
        domains[c["domain"]] = domains.get(c["domain"], 0.0) + c["share_pct"]

    return {
        "unit": "FTE" if has_hc else "roles",
        "clusters": clusters,
        "domains": sorted(
            ({"name": k, "share_pct": round(v, 2)} for k, v in domains.items()),
            key=lambda x: -x["share_pct"],
        ),
        "n_clusters": len(clusters),
        "n_tasks": len(t.inferred),
    }


def build_data(state: ProjectState) -> dict:
    """Everything the report renders, as one JSON-serialisable structure."""
    arch = architecture.build(state)
    if arch is None:
        raise NotReady(
            "the job hierarchy has not been clustered and named yet, so there is no "
            "architecture to report on"
        )
    _trim(arch)

    skills_tax = _taxonomy(state.skills.clustering)

    # How many roles need each skill family — the number that makes a capability section worth
    # reading. A taxonomy alone says what exists; this says what the organisation runs on.
    cluster_family: dict[int, str] = {}
    if state.skills.clustering:
        for a in state.skills.clustering.assignments:
            cluster_family[a.final_profile_id] = state.skills.clustering.family_names.get(
                a.final_family_id, "Unassigned"
            )
    roles_per_family: dict[str, set[str]] = {}
    for r in state.skills.profile_requirements:
        fam = cluster_family.get(r.cluster_id)
        if fam:
            roles_per_family.setdefault(fam, set()).add(r.profile_key)
    for fam in skills_tax:
        fam["roles"] = len(roles_per_family.get(fam["name"], ()))
    # A taxonomy can exist without the per-role requirements that step 9 writes — the clusters
    # were confirmed but the roles were never mapped onto them. Reporting that as "0 roles need
    # this" would be a false finding about the organisation rather than a missing step, so the
    # section says which measure it is showing and falls back to the taxonomy's own size.
    mapped = bool(state.skills.profile_requirements)

    return {
        "meta": {
            "client": state.meta.display_name,
            "generated": datetime.now(timezone.utc).strftime("%d %B %Y"),
            "accent": state.meta.accent_color,
        },
        "architecture": arch,
        "levelling": _levelling(state, arch),
        "skills": {
            "families": skills_tax,
            "n_skills": len(state.skills.inferred),
            "n_clusters": sum(f["clusters"] for f in skills_tax),
            "n_categories": sum(len(f["categories"]) for f in skills_tax),
            "role_mapped": mapped,
        }
        if skills_tax
        else None,
        "work": _work(state, arch),
        # Named so an absent section reads as a decision rather than a gap. See the module
        # docstring on why this is the one piece of method the report carries.
        "declined": [
            skip_steps.BY_ID[s].label for s in state.skipped_steps if s in skip_steps.BY_ID
        ],
    }


def render(state: ProjectState) -> str:
    data = build_data(state)
    return _get_env().get_template("architecture_report.html.j2").render(
        d=data,
        # Embedded rather than fetched: the report has to work from a file:// URL in an email
        # attachment, which is the only distribution channel that reliably exists.
        #
        # `| safe` in the template means the escaping happens here instead. A role titled
        # "</script> foo" would otherwise end the script block and put the rest of the data
        # into the document as markup — so `<` is escaped to its JSON unicode form, which is
        # identical to the parser and inert to the HTML tokeniser. Same for the two sequences
        # that can end a comment or start one.
        data_json=json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\u003c")
        .replace(">", "\u003e")
        .replace("&", "\u0026"),
    )
