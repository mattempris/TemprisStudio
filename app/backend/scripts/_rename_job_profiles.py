"""Regenerate the job profile cluster names with the current naming prompt.

The 565 names on `banking-demo/full-ja` were produced on 2026-08-02, before the profile
tier's prompt was retargeted to ask for job titles rather than activity phrases. It shows:
485 of the 565 have no role noun in them — "Fraud Detection Data Science" where the prompt
now asks for "Fraud Detection Data Scientist".

**Names only. Nothing is re-clustered and nothing downstream is invalidated.** Cluster
membership, ids, and every artefact keyed to them are untouched; only the labels change.
That follows the precedent already set by the per-cluster rename endpoint, whose docstring
puts it well: it is a label, not a placement. Running the tier's confirm flow instead would
re-cluster, drop the two tiers above, and cascade through twenty downstream steps including
the 750-cluster opportunity assessment — which is emphatically not what is wanted here.

The old names are written to blob first, so this is reversible.

Run with --apply to write. Without it, names are generated and shown but nothing is saved,
which is the cheap way to judge whether the new prompt is actually better before committing
to it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.clustering import naming, tier_state  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402

CLIENT, PROJECT = "banking-demo", "full-ja"
ENTITY, TIER = "job", "profile"
BACKUP = "workforce/backups/job-profile-names-before-rename"

# A crude "does this read as a job title" probe. Only used to report the shape of the
# change, never to accept or reject a name — the prompt owns quality, and a regex that
# gatekept it would quietly reject correct titles like "Head of Risk".
ROLE_NOUN = re.compile(
    r"(er|or|ist|ant|ent|ian|ive|smith)\b|\b(Head|Lead|Chief|Manager|Director|Officer|"
    r"Analyst|Engineer|Architect|Adviser|Advisor|Specialist|Administrator|Partner|"
    r"Counsel|Controller|Secretary|Actuary|Auditor)\b",
    re.I,
)


def main() -> int:
    apply = "--apply" in sys.argv
    limit = None
    for i, a in enumerate(sys.argv):
        if a == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])

    svc = ProjectService()
    state = svc.load_state(CLIENT, PROJECT)
    rec = tier_state.tiers_of(state, ENTITY).get(TIER)
    if rec is None or not rec.names:
        print("the job profile tier is not confirmed")
        return 1

    ids = sorted(rec.names)
    if limit:
        ids = ids[:limit]
        print(f"--limit {limit}: naming only the first {limit} clusters, for a sample\n")

    missing = [c for c in ids if not rec.exemplars.get(c)]
    if missing:
        print(f"warning: {len(missing)} clusters have no stored exemplars and will be "
              f"named from their id alone: {missing[:10]}")

    blocks = [naming.build_cluster_block(c, rec.exemplars.get(c, [])[:4]) for c in ids]
    old = {c: rec.names[c] for c in ids}

    print(f"naming {len(blocks)} job profile clusters in batches of {naming.NAME_BATCH}…")
    named = naming.name_level(
        ENTITY,
        TIER,
        blocks,
        n_expected=len(blocks),
        progress=lambda done, total: print(f"  {done}/{total}"),
    )

    changed = {c: named[c] for c in ids if c in named and named[c] != old[c]}
    print(f"\n{len(named)} named, {len(changed)} changed\n")

    before_titles = sum(1 for c in ids if ROLE_NOUN.search(old[c]))
    after_titles = sum(1 for c in ids if c in named and ROLE_NOUN.search(named[c]))
    print(f"reads as a job title: {before_titles}/{len(ids)} before -> "
          f"{after_titles}/{len(ids)} after")

    dupes = [n for n in set(named.values()) if list(named.values()).count(n) > 1]
    print(f"duplicate names: {len(dupes)}{' ' + str(dupes[:5]) if dupes else ''}")
    print(f"unnamed: {sorted(set(ids) - set(named))}\n")

    print("sample of what changes:")
    for c in list(changed)[:20]:
        print(f"  [{c:>3}] {old[c]}")
        print(f"        -> {named[c]}")

    if not apply:
        print("\nDRY RUN — pass --apply to write these to project state")
        return 0
    if limit:
        print("\nrefusing to write a partial rename: run without --limit to apply")
        return 1

    svc.save_json(CLIENT, PROJECT, BACKUP, {str(k): v for k, v in old.items()})
    print(f"\nold names backed up to {BACKUP}.json")

    fresh = svc.load_state(CLIENT, PROJECT)
    target = tier_state.tiers_of(fresh, ENTITY).get(TIER)
    if target is None:
        print("the tier disappeared while naming")
        return 1
    target.names.update(named)
    # The denormalised view carries a copy of the names for everything downstream to read,
    # so it has to be rebuilt or half the app keeps showing the old labels.
    tier_state.rebuild_denormalised(fresh, ENTITY)

    svc.save_state(
        fresh,
        action="rename-job-profile-clusters",
        lineage_payload={
            "clusters": len(named),
            "changed": len(changed),
            "reason": "regenerated with the retargeted job-title naming prompt",
            "backup": f"{BACKUP}.json",
        },
    )
    after = fresh.clustering.profile_names if fresh.clustering else {}
    print(f"saved. denormalised view carries {len(after)} profile names; "
          f"sample: {after.get(ids[0])!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
