"""What a cluster tile says it contains, at each tier.

The rule under test: above the finest tier a tile lists its **child clusters** with the count
beneath each, and at the finest tier it lists source titles. This replaced resolving every
tier down to source titles, which put 300 job titles behind a family tile and showed an
arbitrary twelve of them.

The cases worth pinning down are the ones that are wrong-but-plausible rather than crashy:

  - a family tile listing job titles instead of category names (the old behaviour)
  - the count being the child's *own* size rather than what sits beneath it
  - the cap dropping the biggest categories because the sample was sorted alphabetically
  - the heading noun still saying "source job titles" over a list of category names

No blob, no network, no model.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone  # noqa: E402

from app.api.routes import tiers  # noqa: E402
from app.models.project_state import (  # noqa: E402
    ProjectMeta,
    ProjectState,
    TierMemberRecord,
    TierState,
)

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")


def member(item_id: str, cid: int) -> TierMemberRecord:
    return TierMemberRecord(
        item_id=item_id, backbone_cluster_id=cid, final_cluster_id=cid, stability_score=1.0
    )


def build_state() -> ProjectState:
    """Two families over five categories over twelve profiles, deliberately unbalanced.

    Family 0 holds the two big categories, family 1 the three small ones — so a
    biggest-first sample and an alphabetical one give visibly different answers.
    """
    now = datetime.now(timezone.utc)
    state = ProjectState(
        meta=ProjectMeta(
            client_slug="c", project_slug="p", display_name="C", created_at=now, updated_at=now
        )
    )

    # profile tier: 12 normalised jobs into 5 profiles is not what we need here; the
    # profile tier's members are the base records, and its clusters are the profiles.
    prof_members = [member(f"job:{i}", i % 12) for i in range(12)]
    state.clustering_tiers["profile"] = TierState(
        tier="profile",
        k=12,
        gate=0.5,
        names={i: f"Profile {i}" for i in range(12)},
        members=prof_members,
    )

    # category tier: profiles -> 5 categories. Sizes 5, 4, 1, 1, 1.
    assign = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 1, 6: 1, 7: 1, 8: 1, 9: 2, 10: 3, 11: 4}
    state.clustering_tiers["category"] = TierState(
        tier="category",
        k=5,
        gate=0.5,
        names={
            0: "Zebra Operations",       # biggest, but last alphabetically
            1: "Yankee Servicing",       # second biggest, second-last alphabetically
            2: "Alpha Advisory",
            3: "Bravo Control",
            4: "Charlie Reporting",
        },
        members=[member(f"profile:{p}", c) for p, c in assign.items()],
    )

    # family tier: 5 categories -> 2 families.
    state.clustering_tiers["family"] = TierState(
        tier="family",
        k=2,
        gate=0.5,
        names={0: "Operations & Servicing", 1: "Advisory & Control"},
        members=[
            member("category:0", 0),
            member("category:1", 0),
            member("category:2", 1),
            member("category:3", 1),
            member("category:4", 1),
        ],
    )
    return state


def main() -> int:
    state = build_state()

    print("A family tile lists its categories, not job titles")
    view = tiers._child_view(state, "job", "family")
    check("family tier resolves a child view", view is not None)
    assert view is not None
    labels, counts, noun = view
    check(
        "items are keyed as category ids",
        set(labels) == {f"category:{i}" for i in range(5)},
        str(sorted(labels)),
    )
    check("the label is the category's name", labels["category:0"] == "Zebra Operations")
    check(
        "the count is the profiles beneath that category, not the category itself",
        counts["category:0"] == 5 and counts["category:2"] == 1,
        f"cat0={counts['category:0']} cat2={counts['category:2']}",
    )
    check("the count noun names what is beneath", noun == "job profiles", noun)

    sample = tiers._child_sample(
        [(labels[f"category:{i}"], counts[f"category:{i}"]) for i in (0, 1)], noun
    )
    check(
        "a family tile's lines read '<category> · N job profiles'",
        sample["titles"] == ["Zebra Operations · 5 job profiles", "Yankee Servicing · 4 job profiles"],
        str(sample["titles"]),
    )
    check(
        "the heading total is the number of categories, not of source records",
        sample["title_count"] == 2,
        str(sample["title_count"]),
    )

    print("\nA category tile lists its profiles")
    view = tiers._child_view(state, "job", "category")
    assert view is not None
    labels, counts, noun = view
    check("items are keyed as profile ids", labels["profile:3"] == "Profile 3")
    check("the noun is what a profile groups", noun == "normalised jobs", noun)

    print("\nThe finest tier keeps source titles — there is no child tier to read")
    check("profile tier has no child view", tiers._child_view(state, "job", "profile") is None)

    print("\nBiggest-first, so the cap cannot drop the categories that define the family")
    many = [(f"Cat {i:02d}", 100 - i) for i in range(20)]
    capped = tiers._child_sample(many, "job profiles")
    check(
        f"only {tiers.TOOLTIP_TITLES} lines are shown",
        len(capped["titles"]) == tiers.TOOLTIP_TITLES,
        str(len(capped["titles"])),
    )
    check("the biggest child survives the cap", capped["titles"][0].startswith("Cat 00 · 100"))
    check("the omitted count is reported", capped["titles_omitted"] == 20 - tiers.TOOLTIP_TITLES)
    # The old sampler sorted (-count, name) over source titles where every count was 1,
    # which degenerated to alphabetical. Alphabetical here would lead with "Cat 19".
    check(
        "ordering is by size, not alphabetical",
        "Cat 19" not in " ".join(capped["titles"]),
    )

    print("\nAn unconfirmed tier below cannot be described")
    bare = build_state()
    bare.clustering_tiers.pop("category")
    check(
        "family tier falls back rather than inventing names",
        tiers._child_view(bare, "job", "family") is None,
    )

    print("\nThe heading noun matches what is actually listed")
    check("family says job categories", tiers._label_noun("job", "family") == "job categories")
    check("category says job profiles", tiers._label_noun("job", "category") == "job profiles")
    check(
        "profile still says source job titles",
        tiers._label_noun("job", "profile") == "source job titles",
    )
    check("and it holds for tasks too", tiers._label_noun("task", "family") == "task categories")

    print("\nA child with nothing beneath it is named without a bogus '0'")
    check(
        "no count suffix when the count is zero",
        tiers._child_sample([("Empty Category", 0)], "job profiles")["titles"]
        == ["Empty Category"],
    )

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
