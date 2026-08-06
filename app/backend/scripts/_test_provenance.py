"""Which input the skills and tasks inference reads. Offline — no network.

The rule has to be exact in both directions, and both failures are silent.

Passing a source description for an anchor role that actually merges several records would feed
one member's job description to a role built from five, describing a role nobody holds. Failing
to pass it where the mapping *is* one-to-one silently keeps the compression this exists to
remove, and the output looks identical either way.

Run:  python scripts/_test_provenance.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.project_state import (
    ClusteringState,
    DedupeGroup,
    ItemAssignmentRecord,
    JobProfileDoc,
    JobRecordRaw,
    JobRecordStripped,
    ProjectMeta,
    ProjectState,
)
from app.services import provenance
from app.services.skills.inference import _profile_prompt as skills_prompt
from app.services.tasks.inference import _profile_prompt as tasks_prompt

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))


NOW = datetime.now(timezone.utc)


def build() -> ProjectState:
    """A project with all four shapes of provenance in it at once.

    cluster 0  one normalised job, one record          -> 1:1, gets its source
    cluster 1  two normalised jobs                     -> merged at clustering
    cluster 2  one normalised job, but three records   -> merged at dedupe
    cluster 3  one of each, but the record has no text -> nothing to pass
    """
    st = ProjectState(
        meta=ProjectMeta(
            client_slug="t", project_slug="p", display_name="T", created_at=NOW, updated_at=NOW
        )
    )
    recs = ["r0", "r1a", "r1b", "r2a", "r2b", "r2c", "r3"]
    st.raw_records = [
        JobRecordRaw(id=r, source_file_id="f", job_title=f"Title {r}", raw_text=f"raw {r}")
        for r in recs
    ]
    st.stripped_records = [
        JobRecordStripped(
            id=r,
            # The one for cluster 3 is blank, which must read as "no source" rather than as an
            # empty prompt.
            stripped_text="" if r == "r3" else f"FULL DESCRIPTION FOR {r}",
            model="m",
            generated_at=NOW,
        )
        for r in recs
    ]
    st.dedupe_groups = [
        DedupeGroup(group_id="g0", member_ids=["r0"], representative_id="r0", avg_similarity=1.0),
        DedupeGroup(group_id="g1a", member_ids=["r1a"], representative_id="r1a", avg_similarity=1.0),
        DedupeGroup(group_id="g1b", member_ids=["r1b"], representative_id="r1b", avg_similarity=1.0),
        DedupeGroup(group_id="g2", member_ids=["r2a", "r2b", "r2c"],
                    representative_id="r2a", avg_similarity=0.9),
        DedupeGroup(group_id="g3", member_ids=["r3"], representative_id="r3", avg_similarity=1.0),
    ]
    pairs = [("g0", 0), ("g1a", 1), ("g1b", 1), ("g2", 2), ("g3", 3)]
    st.clustering = ClusteringState(
        linkage_blob_path="", embedding_index_blob_path="",
        k_profiles=4, k_categories=2, k_families=1,
        assignments=[
            ItemAssignmentRecord(
                item_id=gid, backbone_profile_id=pid, backbone_category_id=0,
                backbone_family_id=0, final_profile_id=pid, final_category_id=0, final_family_id=0,
            )
            for gid, pid in pairs
        ],
        profile_names={0: "Solo Role", 1: "Merged Role", 2: "Deduped Role", 3: "Textless Role"},
    )
    st.job_profiles = [
        JobProfileDoc(
            profile_key=f"key-{pid}", profile_cluster_id=pid, clustering_version=1,
            title=name, content={"about_role": f"GENERATED SUMMARY {pid}"}, html="",
            generated_at=NOW,
        )
        for pid, name in st.clustering.profile_names.items()
    ]
    return st


print("\nOnly a genuine one-to-one chain gets its source description")
st = build()
src = provenance.single_source_text(st)
check("the 1:1 role does", "key-0" in src, str(sorted(src)))
check("and it is that record's own text", src.get("key-0") == "FULL DESCRIPTION FOR r0")
# Feeding one member's description to a role built from several would describe a role nobody
# holds, so both kinds of merge are excluded.
check("a role merging two normalised jobs does NOT", "key-1" not in src)
check("a role whose single job merged three records does NOT", "key-2" not in src)
check("a role whose record has no stripped text does NOT", "key-3" not in src)
check("exactly one of the four qualifies", len(src) == 1, str(len(src)))

print("\nCoverage is reported, so the UI can say which inputs were read")
n, total = provenance.coverage(st)
check("count and total", (n, total) == (1, 4), f"{n} of {total}")
st.job_profiles[0].stale = True
n2, total2 = provenance.coverage(st)
check("a stale document is excluded from both", (n2, total2) == (0, 3), f"{n2} of {total2}")
check("and from the mapping", "key-0" not in provenance.single_source_text(st))

print("\nDegenerate states do not raise")
blank = ProjectState(
    meta=ProjectMeta(client_slug="t", project_slug="p", display_name="T",
                     created_at=NOW, updated_at=NOW)
)
check("an unclustered project yields nothing", provenance.single_source_text(blank) == {})
check("and reports zero of zero", provenance.coverage(blank) == (0, 0))
# A dedupe group absent from state entirely — the normalised profile id is used as its own
# member, which is what the skip path produces before dedupe groups are written.
st2 = build()
st2.dedupe_groups = []
check(
    "no dedupe groups: the normalised id stands in as its own record",
    provenance.single_source_text(st2) == {},
    "g0 is not a raw record id, so there is no text to find",
)

print("\nThe source REPLACES the document, and only when present")
doc = {"about_role": "GENERATED SUMMARY", "responsibilities": ["summarised a", "summarised b"]}
for name, prompt in (("skills", skills_prompt), ("tasks", tasks_prompt)):
    with_src = prompt("Analyst", doc, "FULL DESCRIPTION with twenty-five responsibilities")
    without = prompt("Analyst", doc)
    check(f"{name}: the source text is in the prompt", "FULL DESCRIPTION" in with_src)
    # Passing both would let one responsibility be counted twice — once from the summary and once
    # from the source. For tasks that also distorts every other task's share of the week.
    check(f"{name}: the generated summary is NOT", "GENERATED SUMMARY" not in with_src)
    check(f"{name}: nor are its responsibilities", "summarised a" not in with_src)
    check(f"{name}: the anchor role's title still leads", with_src.startswith("Job profile: Analyst"))
    check(f"{name}: the source is labelled as the organisation's", "as supplied by the organisation" in with_src)
    check(f"{name}: without a source, the document is used", "GENERATED SUMMARY" in without)
    check(f"{name}: and the source label is absent", "as supplied by the organisation" not in without)
    check(f"{name}: an empty source falls back to the document",
          "GENERATED SUMMARY" in prompt("Analyst", doc, ""))

print("\nPASS\n" if ok else "\nFAIL\n")
sys.exit(0 if ok else 1)
