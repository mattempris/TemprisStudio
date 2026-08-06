"""One sentence per cluster, written at naming time. Offline — no network.

A description is optional by design and a name is not, and that asymmetry is the whole of the
risk here. Every guarantee the naming step already made about names — every cluster has one, no
two collide — must keep holding now that a second field shares the call, and none of them may
start applying to descriptions, where they would do harm rather than good.

Run:  python scripts/_test_cluster_descriptions.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from app.models.project_state import ProjectMeta, ProjectState, TierState
from app.services import skip_steps
from app.services.clustering import naming, tier as tier_engine, tier_state

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))


print("\nThe naming call asks for a description, and cannot silently omit it")
item = naming.NAME_SCHEMA["properties"]["clusters"]["items"]
check("description is in the schema", "description" in item["properties"])
check("and required alongside the name", set(item["required"]) == {"id", "name", "description"})
check("it is a plain string", item["properties"]["description"]["type"] == "string")
# One call, not two: the model is already looking at the exemplars and at every sibling name it
# is producing, which is exactly what a distinguishing sentence needs.
check("the rules tell it what to write", "15-30 words" in naming._DESCRIPTION_RULES)
check("and forbid restating the name", "restating the name" in naming._DESCRIPTION_RULES)
# "Covers ..." and "This cluster ..." say only that a group contains its contents, and eat two
# of a thirty-word budget.
for filler in ("This cluster", "Covers", "Focuses on"):
    check(f"and the {filler!r} opener", filler in naming._DESCRIPTION_RULES)
check("every entity/tier prompt carries them",
      all(naming._DESCRIPTION_RULES in naming._build_system_prompt(e, t, has_parent_context=False)
          for e in ("job", "skill", "task") for t in ("profile", "category", "family")))

print("\nThe token budget grew, because a sentence is output too")
# Under-budgeting truncates the JSON mid-array; the short ids are then re-asked for, so the
# retry masks the shortfall while paying for the level twice.
check("30 clusters fits comfortably", naming._token_budget(30) >= 20_000, str(naming._token_budget(30)))
check("and it is still capped", naming._token_budget(10_000) == 32_000)
check("bigger than it was before descriptions", naming._token_budget(30) > 3_000 + 400 * 30)

print("\nEvery cluster is named; a description is optional and never invented")
blocks = ["[0] alpha exemplar; second", "[1] beta exemplar", "[2] gamma exemplar"]
names, descs = naming._ensure_complete_and_unique(
    {0: "Alpha", 1: "Beta"}, {0: "Does alpha things.", 9: "orphan"}, blocks, "skill", "profile"
)
check("the unnamed cluster still gets a name", 2 in names, names.get(2))
# A name is what the hierarchy depends on — the tier above iterates names, so an unnamed cluster
# orphans its members. A missing sentence costs nothing structural.
check("but no description is fabricated for it", 2 not in descs)
check("a description for a cluster that has no name is dropped", 9 not in descs)
check("the ones the model did write survive", descs[0] == "Does alpha things.")
check("a cluster named without a sentence is fine", 1 in names and 1 not in descs)

print("\nNames are deduplicated; descriptions deliberately are not")
names, descs = naming._ensure_complete_and_unique(
    {0: "Support", 1: "Support"},
    {0: "Helping people with things.", 1: "Helping people with things."},
    ["[0] a", "[1] b"], "skill", "profile",
)
check("a duplicate name is suffixed so the two rows differ", names[0] != names[1], str(names))
# Two clusters may legitimately be described in similar terms, and a "(2)" on a sentence would
# read as a defect rather than as the disambiguation it is on a name.
check("identical descriptions are left alone", descs[0] == descs[1])

print("\nA tier carries its descriptions, and one confirmed before they existed still loads")
now = datetime.now(timezone.utc)
rec = TierState(tier="family", k=2, gate=0.5, embedding_model="m", names={0: "A", 1: "B"},
                descriptions={0: "First."}, members=[], exemplars={},
                centroids_blob_path="", n_routed=0, n_moved=0, computed_at=now)
again = TierState.model_validate(rec.model_dump(mode="json"))
check("descriptions round-trip", again.descriptions == {0: "First."})
check("and are sparse rather than padded", 1 not in again.descriptions)
raw = rec.model_dump(mode="json")
raw.pop("descriptions")
check("a record written before the field loads with it empty",
      TierState.model_validate(raw).descriptions == {})

print("\nThe engine result defaults them, so the one-to-one path still builds")
res = skip_steps.identity_tier_result(
    ["a", "b"], ["t1", "t2"], np.zeros((2, 2), np.float32), ["One", "Two"], gate=0.0
)
# That path constructs a TierResult directly and has nothing to describe — it makes no model
# call at all. A non-defaulted field here would have been a TypeError at the point of skipping.
check("a skipped tier has no descriptions", res.descriptions == {})
check("and still has its names", res.names == {0: "One", 1: "Two"})
check("TierResult defaults the field", "descriptions" in tier_engine.TierResult.__dataclass_fields__)

print("\nReading them back per tier")
st = ProjectState(meta=ProjectMeta(client_slug="c", project_slug="p", display_name="P",
                                   created_at=now, updated_at=now))
st.skills.clustering_tiers["family"] = rec
out = tier_state.descriptions_of(st, "skill")
check("every tier is present so callers need no guard", set(out) == set(tier_state.ORDER))
check("the confirmed one carries its sentences", out["family"] == {0: "First."})
check("the unconfirmed ones are empty, not missing", out["profile"] == {} and out["category"] == {})
check("an entity with nothing confirmed is all empty",
      all(v == {} for v in tier_state.descriptions_of(st, "task").values()))
# Mutating the returned dict must not reach into state — this is read by response builders.
out["family"][0] = "mutated"
check("the return is a copy", st.skills.clustering_tiers["family"].descriptions[0] == "First.")

print("\nPASS\n" if ok else "\nFAIL\n")
sys.exit(0 if ok else 1)
