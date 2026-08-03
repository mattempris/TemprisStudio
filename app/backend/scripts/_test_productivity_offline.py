"""Step 5's file format and ranking, offline.

The generated `.md` is the deliverable — someone uploads it into Claude — so the
things worth asserting are that it parses, that its name is a safe unique filename,
and that the ranking is by augmentation rather than automation. The prose quality is
what the live test is for.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from app.services.workforce import productivity as prod  # noqa: E402

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")


def skill(name: str, description: str = "d", body: str = "Ask for:\n- a") -> prod.GeneratedSkill:
    return prod.GeneratedSkill(
        profile_key="pk",
        task_cluster_id=1,
        name=name,
        description=description,
        hook="You get a brief.",
        when_to_use=['- "start the renewal for X"', "help me scope this"],
        when_not_to_use=["not for final pricing — that judgement stays with the handler"],
        body=body,
    )


def main() -> int:
    # ---- filenames ----
    check("kebab-cases a title", prod.kebab("Frame Renewal Strategy Brief") == "frame-renewal-strategy-brief")
    check("strips punctuation and collapses runs", prod.kebab("  Reconcile -- Daily/Cash!! ") == "reconcile-daily-cash")
    check("never returns an empty stem", prod.kebab("!!!") == "skill")
    check("filename is the name plus .md", skill("x-y").filename == "x-y.md")

    # ---- the file parses, even with hostile content ----
    hostile = skill(
        "frame-brief",
        description='Gathers "expiring" details: produces a brief — 100% of it.',
        body="Ask the user for:\n\n- **Client name**\n- Loss runs\n\nThen produce a brief.",
    )
    md = prod.to_markdown(hostile)
    parts = md.split("---")
    fm = yaml.safe_load(parts[1])
    check("frontmatter parses with quotes and colons in the description", isinstance(fm, dict))
    check("name survives", fm.get("name") == "frame-brief")
    check("description survives verbatim", fm.get("description") == hostile.description)
    check("body is present below the frontmatter", "Then produce a brief." in md)
    check("both boundary sections are written", "## When to use this" in md and "## When not to use this" in md)
    check(
        "bullets are normalised, not doubled",
        '- "start the renewal for X"' in md and '- - "start' not in md,
    )

    # ---- heading levels ----
    nested = prod.to_markdown(
        skill("x", body="## Inputs to ask for\n\n- a\n\n# Output\n\ntext\n\n### Deep\n\nkeep")
    )
    check("a body H2 is demoted below the file's own sections", "### Inputs to ask for" in nested)
    check("a body H1 is demoted too", "## Output" in nested and "\n# Output" not in nested)
    check("deeper headings are left alone", "### Deep" in nested)
    check(
        "the file's own sections stay at H2",
        nested.count("## When to use this") == 1 and "### When to use this" not in nested,
    )

    # ---- name collisions within a role ----
    a, b, c = skill("draft-response"), skill("draft-response"), skill("draft-response")
    prod.dedupe_names([a, b, c], set())
    check("colliding names are suffixed, not overwritten", [a.name, b.name, c.name] == ["draft-response", "draft-response-2", "draft-response-3"])
    d = skill("draft-response")
    prod.dedupe_names([d], {"draft-response", "draft-response-2"})
    check("suffixing skips names already taken by an earlier run", d.name == "draft-response-3", d.name)

    # ---- ranking is by augmentation, not automation ----
    # Contract review: automates badly, augments well, and takes a fifth of the week.
    # Chasing documents: automates well, augments little, and takes a tenth.
    review = prod.SkillInput(
        profile_key="p", role_title="Underwriter", task_cluster_id=1,
        cluster_name="Contract Review", domain="Risk", category="Review",
        proportion=20.0, augmentation_pct=65.0,
    )
    chasing = prod.SkillInput(
        profile_key="p", role_title="Underwriter", task_cluster_id=2,
        cluster_name="Chasing Documents", domain="Admin", category="Admin",
        proportion=10.0, augmentation_pct=20.0,
    )
    check("rank score is augmentation x share of the week", abs(review.rank_score - 13.0) < 0.01, str(review.rank_score))
    check("the augmentable task outranks the automatable one", review.rank_score > chasing.rank_score)

    # ---- the prompt carries what the model needs ----
    review.task_names = ["Policy Wording Review"]
    review.task_descriptions = ["Checks wordings against the standard form."]
    review.actions = [
        ("Reading Wordings", "Reads the submitted form.", 20.0, 70.0),
        ("Deciding Cover", "Signs off on cover.", 5.0, 15.0),
    ]
    p = review.prompt()
    check("prompt names the role and the task", "Underwriter" in p and "Contract Review" in p)
    check("prompt carries the role's own task wording", "Policy Wording Review" in p)
    check("prompt orders actions by augmentation", p.index("Reading Wordings") < p.index("Deciding Cover"))
    check("prompt states each action's augmentation", "70% augmentable" in p)
    check("prompt points low-augmentation actions at the boundaries", "when_not_to_use" in p)

    # ---- a list-shaped field arriving as a string is tolerated ----
    prod.llm.complete_json = lambda *a, **k: {  # type: ignore[assignment]
        "name": "Reconcile Daily Cash",
        "description": "desc",
        "hook": "You get a summary.",
        "when_to_use": '- "reconcile today"\n- "what broke overnight?"',
        "when_not_to_use": "- not for signing off breaks",
        "body": "Ask for the ledger.",
    }
    g = prod.generate_skill(review)
    check("a markdown-string list is split rather than rejected", len(g.when_to_use) == 2, str(g.when_to_use))
    check("the returned name is kebab-cased", g.name == "reconcile-daily-cash", g.name)

    prod.llm.complete_json = lambda *a, **k: {"name": "x", "description": "d", "hook": "h",  # type: ignore[assignment]
                                             "when_to_use": [], "when_not_to_use": [], "body": "  "}
    try:
        prod.generate_skill(review)
        check("an empty body is rejected", False)
    except prod.SkillError:
        check("an empty body is rejected", True)

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
