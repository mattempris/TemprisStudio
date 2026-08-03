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
OPS = [
    "workflow_steps", "trigger", "completion_definition", "user_personas",
    "knowledge_sources", "tools", "risks", "kpis",
    "system_instructions_summary", "pii_types", "compliance_frameworks",
    "escalation_path", "human_in_the_loop",
]


def compiles(label: str, schema: dict) -> bool:
    """True if the API accepted the grammar. Truncation counts as acceptance."""
    size = len(str(schema))
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
        "business half": compiles("business half", subset(BUSINESS)),
        "ops half": compiles("ops half", subset(OPS)),
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
