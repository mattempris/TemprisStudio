"""Level and qualifications in the normalised description. Offline — no network.

The claim: level and qualification reach the *geometry* and the *router*, and a project written
before they existed still works.

Worth testing because every way this fails is silent. A field written by the normalise step and
dropped before embedding would leave the whole change inert while looking implemented. And the
router reads its cluster list through a 120-character truncation, so a signal at the tail of the
text reaches the item being routed and not the clusters it chooses between — the model would be
asked to weigh a criterion visible on one side only.

Run:  python scripts/_test_normalize_level.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.project_state import NormalizedProfile
from app.services.clustering import routing
from app.services.normalization import LEVEL_LADDER, NORMALIZE_SCHEMA, NormalizedResult

# The truncation the router applies to each cluster exemplar. Mirrored from tier.finalise; if
# that number moves, this test is the thing that should notice.
ROUTE_EXEMPLAR_CHARS = 120

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))


PURPOSE = (
    "Leads the credit policy function for the commercial bank, owning underwriting standards "
    "and the risk appetite framework across all lending products and channels."
)
TASKS = [
    "Set underwriting standards",
    "Own risk appetite framework",
    "Chair the credit committee",
    "Report to the board risk committee",
    "Review large exposures",
]


def result(level=None, quals=None) -> NormalizedResult:
    return NormalizedResult(
        purpose_statement=PURPOSE,
        key_tasks=TASKS,
        management_line="Reports to the CRO; leads a team of nine",
        budget_responsibility="Owns a £3m functional budget",
        level_indicator=level,
        level_evidence="accountable for the whole credit function" if level else None,
        qualifications=quals,
    )


print("\nThe schema asks for the new fields, off a closed ladder")
props = NORMALIZE_SCHEMA["properties"]
for f in ("level_indicator", "level_evidence", "qualifications"):
    check(f"{f} is in the schema", f in props)
    check(f"and required, so it cannot be quietly omitted", f in NORMALIZE_SCHEMA["required"])
check("level is an enum, not free text", props["level_indicator"].get("enum") == list(LEVEL_LADDER))
# Free-text level would give two roles at the same rung almost no shared surface form, so the
# signal would average away instead of separating them.
check("the ladder has a usable number of rungs", 6 <= len(LEVEL_LADDER) <= 12, str(len(LEVEL_LADDER)))
check("qualifications may be null — most roles require none", "null" in props["qualifications"]["type"])
# The evidence is prose and never embedded, so it does not need a null case.
check("level_evidence is a plain string", props["level_evidence"]["type"] == "string")

print("\nBoth signals reach the embedding text")
t = result("director", "ACA or ACCA").embedding_text()
check("level appears", "Level: director." in t, t[:60])
check("qualifications appear", "Qualifications: ACA or ACCA." in t)
check("purpose is still there", PURPOSE in t)
check("and so are the tasks", "Chair the credit committee" in t)

print("\nThey LEAD, because the router truncates its cluster exemplars")
head = t[:ROUTE_EXEMPLAR_CHARS]
check(
    f"level survives the {ROUTE_EXEMPLAR_CHARS}-char cut",
    "Level: director" in head,
    repr(head[:64]),
)
check("so does the qualification", "ACA" in head)
# The failure this guards against: trailing them puts level in the item being routed and not in
# the clusters it chooses between.
trailing = f"{PURPOSE} {' '.join(TASKS)} Level: director."
check(
    "trailing them would NOT have survived — which is why they lead",
    "Level:" not in trailing[:ROUTE_EXEMPLAR_CHARS],
)
check("some function detail still fits alongside", len(head.split(".")) > 2)

print("\nAbsent fields degrade rather than emitting a label with nothing behind it")
t0 = result().embedding_text()
check("no Level: prefix when there is no rung", "Level:" not in t0)
check("no Qualifications: prefix when there are none", "Qualifications:" not in t0)
check("the legacy text is exactly purpose + tasks", t0 == f"{PURPOSE} {' '.join(TASKS)}")
check("level with no qualification still works", "Level: senior." in result("senior").embedding_text())
check(
    "a qualification with no rung still works",
    result(None, "qualified solicitor").embedding_text().startswith("Qualifications:"),
)

print("\nmanagement_line and budget stay out of the geometry")
# They are the diffuse version of what level_indicator now says precisely, and they are the
# free-form prose the original decision was really objecting to.
check("reporting line is not embedded", "Reports to the CRO" not in t)
check("budget is not embedded", "£3m" not in t)

print("\nA state blob written before these fields still deserialises")
raw = {
    "id": "grp-0001",
    "source_record_ids": ["rec-1"],
    "purpose_statement": PURPOSE,
    "key_tasks": TASKS,
    "management_line": None,
    "budget_responsibility": None,
    "generated_at": datetime.now(timezone.utc).isoformat(),
}
old = NormalizedProfile.model_validate(raw)
check("it loads", old.id == "grp-0001")
check("with the new fields empty rather than absent", old.level_indicator is None)
# The path that actually matters: tier_state.base_items rebuilds a NormalizedResult from this to
# get the embedding text. If the fields were not threaded, a fresh normalise run would write
# them and clustering would silently ignore them — the change would be inert.
rebuilt = NormalizedResult(
    purpose_statement=old.purpose_statement,
    key_tasks=old.key_tasks,
    management_line=old.management_line,
    budget_responsibility=old.budget_responsibility,
    level_indicator=old.level_indicator,
    level_evidence=old.level_evidence,
    qualifications=old.qualifications,
)
check("and clusters on purpose and tasks alone", rebuilt.embedding_text() == t0)

new = NormalizedProfile.model_validate({**raw, "level_indicator": "manager",
                                        "qualifications": "chartered engineer"})
check("a fresh record round-trips its rung", new.level_indicator == "manager")
check("and its qualification", new.qualifications == "chartered engineer")

print("\nThe router is told to weigh level and qualification — for jobs only")
job = routing._route_system_prompt("job")
check("jobs: level is a criterion", "LEVEL" in job)
check("jobs: qualification is a criterion", "QUALIFICATION" in job)
check("jobs: it points at the phrases actually in the text", "'Level:'" in job)
check(
    "jobs: and says similar work at different levels splits",
    "different levels belong in different" in job,
)
# The reversal must be complete. The old prompt told the model to ignore seniority, and leaving
# that in alongside the new instruction would be a prompt arguing with itself.
check("jobs: the old 'ignore seniority' instruction is gone", "not surface wording or seniority" not in job)

for entity in ("skill", "task"):
    p = routing._route_system_prompt(entity)
    check(f"{entity}s: still ignore seniority", "seniority" in p and "not surface wording" in p)
    check(f"{entity}s: level is NOT a criterion", "LEVEL" not in p)
check("every entity still gets a calibration instruction",
      all("calibrated" in routing._route_system_prompt(e) for e in ("job", "skill", "task")))

print("\nPASS\n" if ok else "\nFAIL\n")
sys.exit(0 if ok else 1)
