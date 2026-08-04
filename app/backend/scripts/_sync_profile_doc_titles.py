"""Bring profile document titles into line with the renamed job profile clusters.

Regenerating the cluster names left 349 of 565 documents titled with the old
activity-phrase name — the document's title comes from its generated content, not from the
cluster, so renaming the cluster did not touch it. In the app that shows as a breadcrumb
saying "Audit Data Scientist" above a document headed "Internal Audit Data Science".

Only the 349 whose title *equalled the old cluster name* are touched. The other 216 had
titles the model wrote independently — "Credit Risk Technology Delivery Lead" where the
cluster was "Credit Risk Technology Delivery" — and those are already job titles, often
better than the cluster name. Overwriting them would be a downgrade dressed as a fix.

No model calls. The title is set in the two places that hold it, and the HTML is
re-rendered from the updated content using the same call the boilerplate-update route
already makes — so the surrounding document is byte-identical apart from the title.
Deliberately not a string replacement inside the existing HTML: the old name appears in
prose too, and "supports Internal Audit Data Science initiatives" would become
"supports Audit Data Scientist initiatives".

Run with --apply to write.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes.pipeline import _resolve_sections  # noqa: E402
from app.services.job_profile import generator  # noqa: E402
from app.services.job_profile import template_config as tpl  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402

CLIENT, PROJECT = "banking-demo", "full-ja"
OLD_NAMES = "full-ja/workforce/backups/job-profile-names-before-rename.json"


def main() -> int:
    apply = "--apply" in sys.argv
    svc = ProjectService()
    state = svc.load_state(CLIENT, PROJECT)

    raw = svc.load_json(CLIENT, OLD_NAMES)
    if not raw:
        print(f"no backup of the old names at {OLD_NAMES} — refusing to guess which "
              f"titles were derived from a cluster name")
        return 1
    old = {int(k): v for k, v in raw.items()}
    new = state.clustering_tiers["profile"].names

    targets = [
        d
        for d in state.job_profiles
        if d.title == old.get(d.profile_cluster_id)
        and new.get(d.profile_cluster_id)
        and new[d.profile_cluster_id] != d.title
    ]
    independent = [
        d for d in state.job_profiles if d.title != old.get(d.profile_cluster_id)
    ]
    print(f"{len(state.job_profiles)} documents")
    print(f"  {len(targets)} titled with the old cluster name — will be synced")
    print(f"  {len(independent)} titled independently by the model — left alone")

    print("\nsample:")
    for d in targets[:10]:
        print(f"  {d.title!r}")
        print(f"    -> {new[d.profile_cluster_id]!r}")
    print("\nleft alone, for contrast:")
    for d in independent[:4]:
        print(f"  {d.title!r}  (cluster is now {new.get(d.profile_cluster_id)!r})")

    if not apply:
        print("\nDRY RUN — pass --apply to write")
        return 0

    fresh = svc.load_state(CLIENT, PROJECT)
    names = fresh.clustering_tiers["profile"].names
    sections = _resolve_sections(fresh)
    headings = tpl.headings(sections)
    je_by_key = {r.profile_key: r for r in fresh.je_results}

    synced = 0
    for doc in fresh.job_profiles:
        want = names.get(doc.profile_cluster_id)
        if not want or doc.title != old.get(doc.profile_cluster_id) or doc.title == want:
            continue
        doc.title = want
        # The content is what the renderer reads, so the title has to change there too or
        # the next re-render puts the old one straight back.
        if isinstance(doc.content, dict):
            doc.content["title"] = want
        doc.html = generator.render_html(
            doc.content,
            accent_color=fresh.meta.accent_color,
            company_name=fresh.meta.display_name,
            about_company=fresh.meta.client_company_description,
            diversity_statement=fresh.meta.diversity_statement,
            job_level=(
                je_by_key[doc.profile_key].level_name if doc.profile_key in je_by_key else None
            ),
            headings=headings,
            sections=sections,
        )
        # The rendered document is also served from its own blob, so state alone is half
        # the job — the profile viewer and the exports read the blob.
        svc.save_profile_html(CLIENT, PROJECT, doc.profile_key, doc.html)
        svc.save_profile_content(CLIENT, PROJECT, doc.profile_key, doc.content)
        synced += 1
        if synced % 50 == 0:
            print(f"  {synced}/{len(targets)}")

    svc.save_state(
        fresh,
        action="sync-profile-doc-titles",
        lineage_payload={
            "synced": synced,
            "left_alone": len(independent),
            "reason": "job profile cluster names were regenerated with the job-title prompt",
        },
    )
    print(f"\n{synced} documents synced, state and per-profile blobs written")

    check = svc.load_state(CLIENT, PROJECT)
    bad = [
        d.title
        for d in check.job_profiles
        if d.title == old.get(d.profile_cluster_id)
        and check.clustering_tiers["profile"].names.get(d.profile_cluster_id) != d.title
    ]
    print(f"documents still carrying an old cluster name: {len(bad)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
