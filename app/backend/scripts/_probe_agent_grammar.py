"""Which version of the agent schema the API will actually compile.

Step 6's schema is large enough that the server rejects it with "the compiled grammar
is too large". `llm.complete_json` recovers by restating the schema in the prompt, but
it burns two doomed calls per agent to get there — on a bulk run of hundreds of agents
that is most of the bill spent discovering the same thing every time.

A failed compile is a 400 with no tokens billed, so the compile limit can be probed
almost for free. `max_tokens` is tiny deliberately: a variant that compiles will hit
the truncation path, and truncation is a *pass* here — it means the grammar was
accepted, which is the only question being asked.
"""
from __future__ import annotations

import copy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anthropic  # noqa: E402

from app.services import llm  # noqa: E402
from app.services.workforce.agents import AGENT_SCHEMA  # noqa: E402

PROMPT = "Specify an agent for handling customer complaints."


def strip_additional(node):
    """Drop every `additionalProperties: false`."""
    if isinstance(node, dict):
        node.pop("additionalProperties", None)
        for v in node.values():
            strip_additional(v)
    elif isinstance(node, list):
        for v in node:
            strip_additional(v)
    return node


def strip_required(node, *, top_only: bool):
    if isinstance(node, dict):
        node.pop("required", None)
        if not top_only:
            for v in node.values():
                strip_required(v, top_only=False)
    return node


def subset(keys: list[str]) -> dict:
    s = copy.deepcopy(AGENT_SCHEMA)
    s["properties"] = {k: v for k, v in s["properties"].items() if k in keys}
    s["required"] = [k for k in s["required"] if k in keys]
    return s


BUSINESS = [
    "purpose", "problem_statement", "goals", "non_goals", "assumptions",
    "constraints", "success_criteria", "capabilities", "retained_by_people",
]
# Read from the module rather than restated, so the probe cannot drift from what is
# actually sent — the whole point of _half() deriving the halves from one union.
from app.services.workforce.agents import BUSINESS_KEYS, OPS_KEYS  # noqa: E402

# The grammar-cheapest fallback shape for oversight tasks: three parallel arrays of
# scalars, zipped in code. Ugly, and only reached if the array-of-objects will not fit.
FLAT_OVERSIGHT = {
    "oversight_task_names": {"type": "array", "items": {"type": "string"}},
    "oversight_task_definitions": {"type": "array", "items": {"type": "string"}},
    "oversight_task_pcts": {"type": "array", "items": {"type": "integer"}},
}


def with_flat_oversight(keys) -> dict:
    """A half with the object-shaped oversight field swapped for three flat arrays."""
    s = subset([k for k in keys if k != "oversight_tasks"])
    s["properties"].update(FLAT_OVERSIGHT)
    s["required"] = list(s["properties"])
    return s
OPS = [
    "workflow_steps", "trigger", "completion_definition", "user_personas",
    "knowledge_sources", "tools", "risks", "kpis",
    "system_instructions_summary", "pii_types", "compliance_frameworks",
    "escalation_path", "human_in_the_loop",
]


def compiles(label: str, schema: dict, *, retries_on_529: int = 2) -> bool:
    """True if the API accepted the grammar. Truncation counts as acceptance.

    A 529 is retried rather than reported, because this project already knows that a
    grammar that will not compile can come back as `overloaded_error` instead of a
    grammar error. A schema that 529s every time while a strictly smaller sibling passed
    in the same run is almost certainly too large, and reporting that as a transient
    server problem would send someone down the wrong path for an afternoon.
    """
    size = len(str(schema))
    for attempt in range(retries_on_529 + 1):
        try:
            return _one(label, schema, size)
        except anthropic.InternalServerError as e:
            if attempt < retries_on_529:
                time.sleep(3)
                continue
            print(f"  529 x{retries_on_529 + 1}  {label}  ({size:,} chars) — treat as TOO LARGE "
                  f"if a smaller variant passed above: {str(e)[:70]}")
            return False
    return False


def _one(label: str, schema: dict, size: int) -> bool:
    try:
        llm.complete(
            PROMPT,
            system="You specify AI agents.",
            json_schema=schema,
            max_tokens=48,
            retries=1,
            effort="low",
        )
        print(f"  COMPILES   {label}  ({size:,} chars of schema)")
        return True
    except llm.LLMTruncatedError:
        print(f"  COMPILES   {label}  ({size:,} chars) — truncated at 48 tokens, as expected")
        return True
    except llm.LLMGrammarError as e:
        print(f"  TOO LARGE  {label}  ({size:,} chars) — {str(e)[-90:]}")
        return False
    except (llm.LLMRequestError, anthropic.APIStatusError) as e:
        print(f"  ERROR      {label}: {type(e).__name__}: {str(e)[:120]}")
        return False


def main() -> int:
    print("Probing the agent schema against the API's grammar compiler.\n")
    results = {
        "full schema": compiles("full schema", copy.deepcopy(AGENT_SCHEMA)),
        "no additionalProperties": compiles(
            "no additionalProperties", strip_additional(copy.deepcopy(AGENT_SCHEMA))
        ),
        "no required (top level)": compiles(
            "no required (top level)", strip_required(copy.deepcopy(AGENT_SCHEMA), top_only=True)
        ),
        "no required anywhere": compiles(
            "no required anywhere", strip_required(copy.deepcopy(AGENT_SCHEMA), top_only=False)
        ),
        "neither": compiles(
            "neither", strip_required(strip_additional(copy.deepcopy(AGENT_SCHEMA)), top_only=False)
        ),
        # Controls first, deliberately: the halves as they were before oversight_tasks
        # existed. Without a baseline in the same run, a failure below cannot be told
        # apart from the API simply being unhappy today.
        "business half (control)": compiles("business half (control)", subset(BUSINESS)),
        "ops half (control)": compiles("ops half (control)", subset(OPS)),
        # The candidate, and its fallbacks in order of preference.
        "business + oversight": compiles("business + oversight", subset(list(BUSINESS_KEYS))),
        "ops + oversight": compiles("ops + oversight", subset(list(OPS_KEYS) + ["oversight_tasks"])),
        "business + flat oversight": compiles(
            "business + flat oversight", with_flat_oversight(BUSINESS_KEYS)
        ),
        "oversight alone (third call)": compiles(
            "oversight alone (third call)", subset(["oversight_tasks"])
        ),
    }
    print()
    winners = [k for k, v in results.items() if v]
    if winners:
        print("Compiles:", ", ".join(winners))
    else:
        print("Nothing compiles — state the schema in the prompt and validate in code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
