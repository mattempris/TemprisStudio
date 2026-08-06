"""Skipping an optional step. Offline — no network, no blob, no model.

The claim under test is the one the whole design rests on: **a skip is the identity operation,
so nothing downstream can tell.** If that holds, no consumer needs a branch. If it does not,
every one of the ten places that read `dedupe_groups` is a latent bug.

Run:  python scripts/_test_skip_steps.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from app.models.project_state import (
    JobRecordRaw,
    JobRecordStripped,
    ProjectMeta,
    ProjectState,
)
from app.services import skip_steps

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))


def blank_state(n: int = 5) -> ProjectState:
    now = datetime.now(timezone.utc)
    st = ProjectState(
        meta=ProjectMeta(
            client_slug="t", project_slug="p", display_name="T", created_at=now, updated_at=now
        )
    )
    st.raw_records = [
        JobRecordRaw(
            id=f"rec-{i}",
            source_file_id="f",
            job_title=f"Title {i}",
            raw_text=f"Raw text for job {i}. Includes benefits blurb.",
        )
        for i in range(n)
    ]
    return st


print("\nThe marker is a record, and it round-trips")
{
    check("a fresh project has skipped nothing", blank_state().skipped_steps == []),
}
st = blank_state()
skip_steps.mark(st, "dedupe")
check("marking records the step", skip_steps.is_skipped(st, "dedupe"))
skip_steps.mark(st, "dedupe")
check("marking twice does not duplicate", st.skipped_steps == ["dedupe"], f"{st.skipped_steps}")
skip_steps.unmark(st, "dedupe")
check("unmarking clears it", not skip_steps.is_skipped(st, "dedupe"))
skip_steps.unmark(st, "dedupe")
check("unmarking something not skipped is a no-op", st.skipped_steps == [])

print("\nSkipping the strip step carries the text through, and says so")
st = blank_state(4)
n = skip_steps.skip_strip(st)
check("one stripped record per raw record", n == 4 and len(st.stripped_records) == 4)
check("ids are preserved", [r.id for r in st.stripped_records] == [r.id for r in st.raw_records])
check(
    "the text is the raw text, unchanged",
    all(s.stripped_text == r.raw_text for s, r in zip(st.stripped_records, st.raw_records)),
)
check("nothing is claimed as removed", all(not s.removed_sections for s in st.stripped_records))
# A passthrough and a strip that found nothing are different facts. Only one is a judgement
# about the source text, and an export has to be able to tell them apart.
check(
    'the model reads "skipped", not a real model name',
    all(s.model == "skipped" for s in st.stripped_records),
)

print("\nSkipping dedupe is the identity grouping — one group per record")
st = blank_state(6)
skip_steps.skip_strip(st)
n = skip_steps.skip_dedupe(st)
check("one group per stripped record", n == 6 and len(st.dedupe_groups) == 6)
check("every group holds exactly one member", all(len(g.member_ids) == 1 for g in st.dedupe_groups))
check(
    "and represents itself",
    all(g.representative_id == g.member_ids[0] for g in st.dedupe_groups),
)
check(
    "group ids match the form the real step writes",
    [g.group_id for g in st.dedupe_groups] == [f"grp-{i:04d}" for i in range(6)],
    st.dedupe_groups[0].group_id,
)
# Every consumer builds this exact map. If it is well-formed, none of them need a branch.
members = {g.group_id: g.member_ids for g in st.dedupe_groups}
check("the {group: members} map every consumer builds is total", len(members) == 6)
check(
    "and covers every record exactly once",
    sorted(m for ms in members.values() for m in ms) == sorted(r.id for r in st.raw_records),
)
check("a group of one is perfectly self-similar", all(g.avg_similarity == 1.0 for g in st.dedupe_groups))
# Skipping IS the decision. Leaving this False would show the step as still awaiting one.
check("the groups are confirmed", all(g.user_confirmed for g in st.dedupe_groups))
# Inventing a threshold would put a number in the summary line that nobody chose.
check("no threshold is invented", st.dedupe_threshold is None)

print("\nThe route back to the upload, which is where a 1:1 anchor role gets its name")
st = blank_state(4)
skip_steps.skip_strip(st)
skip_steps.skip_dedupe(st)
titles = skip_steps.source_titles(st)
check("one title per group", len(titles) == 4)
check(
    "and it is the title from the upload",
    titles["grp-0000"] == "Title 0" and titles["grp-0003"] == "Title 3",
    titles["grp-0000"],
)
# A real dedupe group holds several records; the representative is the one already chosen to
# stand for the group, so this stays consistent with what the rest of the app shows.
st.dedupe_groups[0].member_ids = ["rec-0", "rec-1"]
st.dedupe_groups[0].representative_id = "rec-1"
check(
    "a multi-member group uses its representative's title",
    skip_steps.source_titles(st)["grp-0000"] == "Title 1",
)
st.dedupe_groups[0].representative_id = "gone"
check(
    "a missing representative falls back to a surviving member rather than dropping the title",
    skip_steps.source_titles(st)["grp-0000"] == "Title 0",
)

print("\nA one-to-one tier is a real confirmed tier")
ids = ["grp-0000", "grp-0001", "grp-0002"]
texts = ["purpose one", "purpose two", "purpose three"]
emb = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]], dtype=np.float32)
res = skip_steps.identity_tier_result(ids, texts, emb, ["Analyst", "Engineer", "Manager"], gate=0.0)
check("k is the item count", res.k == 3)
check("one member per item", len(res.members) == 3)
check(
    "each item is its own cluster",
    [m.final_cluster_id for m in res.members] == [0, 1, 2],
)
check(
    "backbone and final agree, because nothing was routed",
    all(m.backbone_cluster_id == m.final_cluster_id for m in res.members),
)
check("nothing was routed by the model", res.n_routed == 0 and res.n_moved == 0)
check("and nothing moved", all(not m.routed_by_llm for m in res.members))
# A cluster of one cannot be perturbed into disagreeing with itself, so a score of 1.0 would
# be a measurement nobody took.
check(
    "stability is None, not a fabricated 1.0",
    all(m.stability_score is None for m in res.members),
)
check("names come from the data", res.names == {0: "Analyst", 1: "Engineer", 2: "Manager"})
# This is what makes a skipped tier composable: the tier above clusters these centroids, so it
# receives exactly the vectors it would have had.
check("centroids are the item embeddings", np.allclose(res.centroids, emb))
check("centroids are a copy, not a view", res.centroids.base is None)
check("every cluster has an exemplar", len(res.exemplar_texts) == 3)

print("\nDegenerate names do not produce a blank cluster label")
res = skip_steps.identity_tier_result(["a", "b"], ["t1", "t2"], np.zeros((2, 2), np.float32),
                                      ["", "  "], gate=0.0)
check("an empty title falls back to a positional label", res.names == {0: "Item 1", 1: "Item 2"},
      str(res.names))
res = skip_steps.identity_tier_result(["a", "b"], ["t1", "t2"], np.zeros((2, 2), np.float32),
                                      ["Only one"], gate=0.0)
check("a short names list does not raise", res.names[1] == "Item 2")

print("\nThe catalogue of optional steps is coherent")
check("every step has a consequence", all(s.consequence for s in skip_steps.SKIPPABLE))
check(
    "every kind is one of the two",
    all(s.kind in ("identity", "omission") for s in skip_steps.SKIPPABLE),
)
check("ids are unique", len({s.id for s in skip_steps.SKIPPABLE}) == len(skip_steps.SKIPPABLE))
# These two carry the whole pipeline: everything is clustered from what normalise writes, and
# skills, tasks, evaluation and matching all read the documents.
check("normalise is not optional", "normalize" not in skip_steps.BY_ID)
check("the role documents are not optional", "profiles" not in skip_steps.BY_ID)
check("the four grouping steps are identity steps",
      all(skip_steps.BY_ID[i].kind == "identity"
          for i in ("strip", "dedupe", "cluster", "categories", "families")))
check("the four analyses are omissions",
      all(skip_steps.BY_ID[i].kind == "omission"
          for i in ("evaluation", "skills", "tasks", "matching")))

print("\nSkipping the whole front of the pipeline leaves a usable project")
st = blank_state(5)
skip_steps.skip_strip(st)
skip_steps.skip_dedupe(st)
for step in ("strip", "dedupe"):
    skip_steps.mark(st, step)
check("the records survived", len(st.raw_records) == 5)
check("and are ready for the normalise step", len(st.dedupe_groups) == 5)
check("with both decisions on record", st.skipped_steps == ["strip", "dedupe"])
# Round-trip through the serialiser the state blob uses, since that is how it is persisted.
again = ProjectState.model_validate(st.model_dump(mode="json"))
check("skipped_steps survives serialisation", again.skipped_steps == ["strip", "dedupe"])

print("\nPASS\n" if ok else "\nFAIL\n")
sys.exit(0 if ok else 1)
