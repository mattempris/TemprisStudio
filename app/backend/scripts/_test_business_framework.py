"""The business-framework roll-up, and why it is a cross-tab rather than one value.

The organisation's own reporting cascade and the job architecture the app derives do not
nest. A job profile cluster is built from role *content*, so "Credit Analyst" can legitimately
exist in Retail Banking and in Commercial Banking at once. That makes a departmental filter a
question about a *share* of a job profile, not a yes/no about the whole thing — and collapsing
the cross-tab to a single dominant path would make the filter quietly wrong about which
people it included.

Every assertion here is a way that could go wrong without raising: a partial answer reported
as total, a headcount counted twice, or an unmapped record bucketed under a business unit
named nothing.

The mapping schema is checked too. It used to name each field in five places — `properties`,
`confidence`, `reasoning`, and three separate `required` arrays — so adding a field meant
five edits, and forgetting one would silently stop the field being asked for while everything
still validated.

No blob, no network, no model.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.project_state import (  # noqa: E402
    ClusteringState,
    DedupeGroup,
    ItemAssignmentRecord,
    JobProfileDoc,
    JobRecordRaw,
    ProjectMeta,
    ProjectState,
)
from app.services.ingestion import column_mapping as cm  # noqa: E402
from app.services.workforce import graph as wf  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")


def rec(rid: str, title: str, heads: int | None, l1: str = "", l2: str = "", l3: str = "") -> JobRecordRaw:
    return JobRecordRaw(
        id=rid,
        source_file_id="f",
        job_title=title,
        raw_text=title,
        headcount=heads,
        business_level_1=l1 or None,
        business_level_2=l2 or None,
        business_level_3=l3 or None,
    )


def build_state(records: list[JobRecordRaw], *, groups: dict[str, list[str]]) -> ProjectState:
    """Records -> dedupe groups -> one profile cluster, the chain the roll-up walks."""
    now = datetime.now(timezone.utc)
    st = ProjectState(
        meta=ProjectMeta(
            client_slug="c", project_slug="p", display_name="C", created_at=now, updated_at=now
        )
    )
    st.raw_records = records
    st.dedupe_groups = [
        DedupeGroup(group_id=g, member_ids=m, representative_id=m[0], avg_similarity=1.0)
        for g, m in groups.items()
    ]
    st.clustering = ClusteringState(
        linkage_blob_path="p/artifacts/x.npy",
        embedding_index_blob_path="p/artifacts/x_index.json",
        k_profiles=1,
        k_categories=1,
        k_families=1,
        assignments=[
            ItemAssignmentRecord(
                item_id=g,
                backbone_profile_id=1,
                backbone_category_id=1,
                backbone_family_id=1,
                final_profile_id=1,
                final_category_id=1,
                final_family_id=1,
                stability_score=1.0,
            )
            for g in groups
        ],
        profile_names={1: "Credit Analyst"},
        category_names={1: "Credit Risk"},
        family_names={1: "Risk"},
    )
    st.job_profiles = [
        JobProfileDoc(
            profile_key="credit-analyst-abc",
            profile_cluster_id=1,
            clustering_version=1,
            title="Credit Analyst",
            content={},
            html="",
            generated_at=now,
        )
    ]
    return st


def main() -> int:
    print("A profile cluster spanning two business units")
    # One job profile, four source records, two departments — 12 people in Retail and 8 in
    # Commercial. This is the shape the whole design exists for.
    st = build_state(
        [
            rec("r1", "Credit Analyst", 7, "Retail Banking", "Distribution", "Branch Credit"),
            rec("r2", "Credit Analyst", 5, "Retail Banking", "Distribution", "Branch Credit"),
            rec("r3", "Credit Analyst", 8, "Commercial Banking", "Lending", "Commercial Credit"),
            rec("r4", "Credit Analyst", 3, "Commercial Banking", "Lending", "Corporate Credit"),
        ],
        groups={"grp-1": ["r1", "r2"], "grp-2": ["r3"], "grp-3": ["r4"]},
    )
    units = wf.profile_business_units(st)
    key = "credit-analyst-abc"
    check("the profile resolves", key in units, str(list(units)))
    paths = units[key]
    check("three distinct framework paths", len(paths) == 3, str(len(paths)))
    check(
        "records in the same path and the same dedupe group are summed, not listed twice",
        paths[("Retail Banking", "Distribution", "Branch Credit")] == 12,
        str(paths.get(("Retail Banking", "Distribution", "Branch Credit"))),
    )
    check(
        "the total across paths equals the profile's headcount",
        sum(paths.values()) == 23,
        str(sum(paths.values())),
    )

    print("\nWhat a departmental filter would return — the point of keeping the split")
    retail = sum(h for p, h in paths.items() if p[0] == "Retail Banking")
    commercial = sum(h for p, h in paths.items() if p[0] == "Commercial Banking")
    check("Retail Banking sees 12 of the 23", retail == 12, str(retail))
    check("Commercial Banking sees 11 of the 23", commercial == 11, str(commercial))
    check(
        "so the filter reports a partial profile rather than all-or-nothing",
        retail < sum(paths.values()) and commercial < sum(paths.values()),
    )

    print("\nRecords with no framework value")
    st2 = build_state(
        [rec("r1", "Credit Analyst", 4), rec("r2", "Credit Analyst", 6, "Risk", "Credit", "")],
        groups={"grp-1": ["r1"], "grp-2": ["r2"]},
    )
    paths2 = wf.profile_business_units(st2)["credit-analyst-abc"]
    check(
        "an unmapped record is not bucketed under a unit named nothing",
        ("", "", "") not in paths2,
        str(list(paths2)),
    )
    check("only the mapped record contributes", sum(paths2.values()) == 6, str(sum(paths2.values())))
    check(
        "a partly-mapped path keeps its blank tail rather than being dropped",
        ("Risk", "Credit", "") in paths2,
        str(list(paths2)),
    )

    print("\nNo headcount column at all")
    st3 = build_state(
        [
            rec("r1", "Credit Analyst", None, "Risk", "Credit", "Wholesale"),
            rec("r2", "Credit Analyst", None, "Risk", "Credit", "Wholesale"),
        ],
        groups={"grp-1": ["r1", "r2"]},
    )
    paths3 = wf.profile_business_units(st3)["credit-analyst-abc"]
    check(
        "each record counts as one notional holder, matching how headcount's absence degrades",
        paths3[("Risk", "Credit", "Wholesale")] == 2,
        str(paths3),
    )

    print("\nThe has_business_framework flag")
    check("true when any record carries a value", wf.has_business_framework(st) is True)
    check("false when none do", wf.has_business_framework(st3.model_copy(update={
        "raw_records": [rec("r1", "Credit Analyst", 4)]
    })) is False)
    check(
        "an unclustered project returns empty rather than raising",
        wf.profile_business_units(
            ProjectState(meta=ProjectMeta(
                client_slug="c", project_slug="p", display_name="C",
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            ))
        ) == {},
    )

    print("\nThe mapping schema is derived from one list, not restated five times")
    props = set(cm.MAPPING_SCHEMA["properties"])
    cols = {f"{f}_column" for f in cm.TARGET_FIELDS}
    check("every target field is a property", cols <= props, str(sorted(cols - props)))
    check(
        "and every one is required — the API needs a value present, and null is that value",
        cols <= set(cm.MAPPING_SCHEMA["required"]),
    )
    for sub in ("confidence", "reasoning"):
        node = cm.MAPPING_SCHEMA["properties"][sub]
        check(
            f"{sub} covers exactly the same fields",
            set(node["properties"]) == cols and set(node["required"]) == cols,
        )
        check(f"{sub} forbids extra keys", node["additionalProperties"] is False)
    check(
        "the dataclass has a field per target, so construction cannot miss one",
        all(hasattr(cm.ColumnMappingSuggestion(**{f"{f}_col": None for f in cm.TARGET_FIELDS}),
                    f"{f}_col") for f in cm.TARGET_FIELDS),
    )
    check(
        "the three framework levels are among them",
        {"business_level_1", "business_level_2", "business_level_3"} <= set(cm.TARGET_FIELDS),
    )

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
