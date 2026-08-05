"""Level band naming: what happens when the model's answer is imperfect.

The suggestion is a naming call, and a naming call over a ladder has three failure modes
that all produce a plausible-looking result:

  - a band omitted, which would blank a rung or shift the ladder up by one
  - two bands given the same name, which makes the levelling output ambiguous
  - the score boundaries coming back changed, which silently re-levels every profile
    under the guise of relabelling them

All three are handled by keeping the existing name rather than accepting the answer, and
all three are checked here. The model is stubbed — no network, no spend.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.project_state import (  # noqa: E402
    JEFrameworkConfig,
    LevelBand,
    ProjectMeta,
    ProjectState,
)
from app.services.evaluation import level_titles  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")


def state_with(**meta) -> ProjectState:
    now = datetime.now(timezone.utc)
    return ProjectState(
        meta=ProjectMeta(
            client_slug="c", project_slug="p", display_name="Northbank",
            created_at=now, updated_at=now, **meta,
        )
    )


FRAMEWORK = JEFrameworkConfig(
    level_bands=[
        LevelBand(name="Operative", min_score=0, max_score=25),
        LevelBand(name="Technician", min_score=25, max_score=50),
        LevelBand(name="Principal", min_score=50, max_score=75),
        LevelBand(name="Director", min_score=75, max_score=100),
    ]
)


def stub(reply: dict):
    """Replace the model call with a fixed reply."""
    level_titles.llm.complete_json = lambda *a, **k: reply  # type: ignore[assignment]


def main() -> int:
    st = state_with()

    print("The happy path")
    stub(
        {
            "levels": [
                {"index": 0, "name": "Assistant", "rationale": "entry"},
                {"index": 1, "name": "Analyst", "rationale": "IC"},
                {"index": 2, "name": "Vice President", "rationale": "senior"},
                {"index": 3, "name": "Managing Director", "rationale": "exec"},
            ],
            "ladder_note": "",
        }
    )
    bands, why, note = level_titles.suggest_level_titles(st, FRAMEWORK)
    check(
        "all four bands renamed",
        [b.name for b in bands] == ["Assistant", "Analyst", "Vice President", "Managing Director"],
        str([b.name for b in bands]),
    )
    check(
        "the boundaries are untouched",
        [(b.min_score, b.max_score) for b in bands] == [(0, 25), (25, 50), (50, 75), (75, 100)],
    )
    check("a rationale per band", len(why) == 4 and why[0] == "entry")
    check("no note when the ladder shape is fine", note == "")

    print("\nA band the model skipped keeps its own name rather than going blank")
    stub(
        {
            "levels": [
                {"index": 0, "name": "Assistant", "rationale": "entry"},
                {"index": 3, "name": "Managing Director", "rationale": "exec"},
            ],
            "ladder_note": "",
        }
    )
    bands, why, _ = level_titles.suggest_level_titles(st, FRAMEWORK)
    check(
        "the ladder does not shift up — band 1 stays Technician",
        [b.name for b in bands] == ["Assistant", "Technician", "Principal", "Managing Director"],
        str([b.name for b in bands]),
    )
    check("and it is still four bands", len(bands) == 4)
    check("the skip is stated rather than hidden", "no suggestion" in why[1], why[1])

    print("\nA duplicate name is refused — two rungs called the same thing is ambiguous")
    stub(
        {
            "levels": [
                {"index": 0, "name": "Analyst", "rationale": "a"},
                {"index": 1, "name": "Analyst", "rationale": "b"},
                {"index": 2, "name": "analyst", "rationale": "c"},
                {"index": 3, "name": "Director", "rationale": "d"},
            ],
            "ladder_note": "",
        }
    )
    bands, why, _ = level_titles.suggest_level_titles(st, FRAMEWORK)
    check(
        "the first wins, later duplicates keep their existing names",
        [b.name for b in bands] == ["Analyst", "Technician", "Principal", "Director"],
        str([b.name for b in bands]),
    )
    check("case-insensitively, so 'analyst' is caught too", bands[2].name == "Principal")
    check("and each rejection says why", "duplicate" in why[1] and "duplicate" in why[2])

    print("\nThe model cannot move a boundary even if it tries")
    stub(
        {
            "levels": [
                {"index": i, "name": n, "rationale": "x", "min_score": 999, "max_score": 999}
                for i, n in enumerate(["A", "B", "C", "D"])
            ],
            "ladder_note": "",
        }
    )
    bands, _, _ = level_titles.suggest_level_titles(st, FRAMEWORK)
    check(
        "boundaries still come from the framework, not the reply",
        [(b.min_score, b.max_score) for b in bands] == [(0, 25), (25, 50), (50, 75), (75, 100)],
    )

    print("\nA ladder-shape concern is passed through for the user to decide")
    stub({"levels": [], "ladder_note": "Four bands is coarse for a bank; six is typical."})
    bands, _, note = level_titles.suggest_level_titles(st, FRAMEWORK)
    check("the note survives", note.startswith("Four bands is coarse"))
    check("but the band count is unchanged — it cannot act on it", len(bands) == 4)
    check("and every name is left alone", [b.name for b in bands] == [b.name for b in FRAMEWORK.level_bands])

    print("\nBands are read lowest-first regardless of stored order")
    shuffled = JEFrameworkConfig(level_bands=list(reversed(FRAMEWORK.level_bands)))
    stub(
        {
            "levels": [{"index": i, "name": f"L{i}", "rationale": "x"} for i in range(4)],
            "ladder_note": "",
        }
    )
    bands, _, _ = level_titles.suggest_level_titles(st, shuffled)
    check(
        "L0 lands on the 0-25 band, not on the Director band",
        bands[0].name == "L0" and bands[0].min_score == 0,
        f"{bands[0].name} @ {bands[0].min_score}",
    )

    print("\nAn empty framework is refused rather than returning an empty ladder")
    try:
        level_titles.suggest_level_titles(st, JEFrameworkConfig())
        check("empty framework raises", False, "it returned instead")
    except ValueError as e:
        check("empty framework raises", "no level bands" in str(e))

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
