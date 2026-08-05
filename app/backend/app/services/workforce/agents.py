"""Agent definitions — Work Architecture Studio step 6.

One API call per agent to a strong model, returning the domain content; the technical
scaffolding is templated. That split is taken from
`Insurance Demo/pipeline/gen_agentdefs.py` and it is the right one: nobody needs a
model to invent that the retry policy has three attempts, and asking it to produce
sixty nested technical fields is how a spec ends up internally inconsistent. The model
writes what only it can — the problem, the capabilities, the risks, the personas — and
code assembles the eight sections around them.

**Where this differs from the reference.** That version is hardwired to one insurance
broker: `cleargroup.example` addresses, FCA and ICOBS in the compliance list, "UK
commercial insurance broking" in the system prompt. This runs against any client's
taxonomy, so the client name is a parameter and the regulatory frame is asked of the
model rather than assumed — a payroll agent and a trading agent answer to different
rulebooks, and putting ICOBS on both would be worse than leaving it blank.

**Placeholder hosts are deliberate.** Every URL and address uses the reserved
`.example` TLD, so a spec can be read as a spec and nothing in it resolves to a real
system. Inventing plausible internal hostnames would be the more dangerous kind of
helpful.

**Ranking.** Clusters are offered in order of FTE-equivalent time released —
automation × total time across every role that does the work — not by automation
alone. A rare, highly automatable task ranks below a common, moderately automatable
one, which is the honest priority for something you have to build and maintain.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services import llm

# Deliberately flat: two levels of nesting at most, and the innermost arrays hold
# strings rather than objects. Acceptance criteria as "Given… when… then…" strings lose
# nothing — they are rendered as text either way.
#
# It is still too large to compile as one grammar. The API rejects it with "the
# compiled grammar is too large … reduce the number of parameters in your tool schemas
# (limit: 24)", but that number does not map onto any obvious property count here — the
# ops half below compiles with more properties in total than the message would allow,
# so the real rule is something about the compiled grammar rather than the schema as
# written. What is established empirically, by scripts/_probe_agent_grammar.py, is
# narrower and enough to build on: the whole schema is rejected, each half is accepted,
# and `additionalProperties: false` is mandatory rather than optional, so trimming it
# to buy budget is not available. Re-run that probe before changing these shapes.
#
# So this whole schema is never sent. It is the union of the two halves below, kept as
# one object because it is the honest description of what an agent spec's domain
# content is, and because both halves are derived from it — which is what stops them
# drifting apart.
AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "purpose": {"type": "string"},
        "problem_statement": {"type": "string"},
        "goals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "metric": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": ["description", "metric", "target"],
                "additionalProperties": False,
            },
        },
        "non_goals": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "success_criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "measurement_method": {"type": "string"},
                },
                "required": ["description", "measurement_method"],
                "additionalProperties": False,
            },
        },
        "capabilities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": "string"},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                    "edge_cases": {"type": "array", "items": {"type": "string"}},
                    "inputs": {"type": "array", "items": {"type": "string"}},
                    "outputs": {"type": "array", "items": {"type": "string"}},
                    "data_sources": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "name",
                    "description",
                    "priority",
                    "acceptance_criteria",
                    "edge_cases",
                    "inputs",
                    "outputs",
                    "data_sources",
                ],
                "additionalProperties": False,
            },
        },
        "workflow_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["type", "description"],
                "additionalProperties": False,
            },
        },
        "trigger": {"type": "string"},
        "completion_definition": {"type": "string"},
        "retained_by_people": {"type": "array", "items": {"type": "string"}},
        "user_personas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "skill_level": {"type": "string"},
                    "jobs_to_be_done": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "description", "skill_level", "jobs_to_be_done"],
                "additionalProperties": False,
            },
        },
        "knowledge_sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "access_method": {"type": "string"},
                    "ownership": {"type": "string"},
                },
                "required": ["name", "type", "access_method", "ownership"],
                "additionalProperties": False,
            },
        },
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "type", "description"],
                "additionalProperties": False,
            },
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "likelihood": {"type": "string"},
                    "impact": {"type": "string"},
                    "mitigation": {"type": "string"},
                },
                "required": ["description", "likelihood", "impact", "mitigation"],
                "additionalProperties": False,
            },
        },
        "kpis": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "target": {"type": "string"}},
                "required": ["name", "target"],
                "additionalProperties": False,
            },
        },
        "system_instructions_summary": {"type": "string"},
        "pii_types": {"type": "array", "items": {"type": "string"}},
        "compliance_frameworks": {"type": "array", "items": {"type": "string"}},
        "escalation_path": {"type": "string"},
        "human_in_the_loop": {"type": "boolean"},
    },
    "required": [
        "purpose",
        "problem_statement",
        "goals",
        "non_goals",
        "assumptions",
        "constraints",
        "success_criteria",
        "capabilities",
        "workflow_steps",
        "trigger",
        "completion_definition",
        "retained_by_people",
        "user_personas",
        "knowledge_sources",
        "tools",
        "risks",
        "kpis",
        "system_instructions_summary",
        "pii_types",
        "compliance_frameworks",
        "escalation_path",
        "human_in_the_loop",
    ],
    "additionalProperties": False,
}

def _half(keys: tuple[str, ...]) -> dict:
    """One compilable half of AGENT_SCHEMA, derived rather than duplicated."""
    return {
        "type": "object",
        "properties": {k: AGENT_SCHEMA["properties"][k] for k in keys},
        "required": list(keys),
        "additionalProperties": False,
    }


# What the agent is for and what it must be able to do — the part a reviewer argues
# about — and separately how it runs. Split on that seam rather than arbitrarily, so
# each call has a coherent job and the model is not asked to hold both frames at once.
BUSINESS_KEYS = (
    "purpose",
    "problem_statement",
    "goals",
    "non_goals",
    "assumptions",
    "constraints",
    "success_criteria",
    "capabilities",
    "retained_by_people",
)
OPS_KEYS = (
    "workflow_steps",
    "trigger",
    "completion_definition",
    "user_personas",
    "knowledge_sources",
    "tools",
    "risks",
    "kpis",
    "system_instructions_summary",
    "pii_types",
    "compliance_frameworks",
    "escalation_path",
    "human_in_the_loop",
)

BUSINESS_SCHEMA = _half(BUSINESS_KEYS)
OPS_SCHEMA = _half(OPS_KEYS)

SYSTEM = (
    "You write engineering specifications for AI agents that absorb a defined slice "
    "of real work inside an organisation. You use UK English. You are concrete and "
    "specific to the work described, never generic.\n\n"
    "You are given one cluster of tasks, the actions inside it, how automatable each "
    "action is, and how much of the organisation's time the cluster consumes. Specify "
    "an agent that takes on the automatable actions and hands the rest to a person.\n\n"
    "name the agent implicitly through `purpose`: one sentence saying what it does and "
    "for whom.\n\n"
    "Scope it honestly. An action scored low for automation is work the agent must NOT "
    "attempt — put it in `retained_by_people` and reflect it in the handoff. An agent "
    "specified as doing the judgement is an agent that will not pass review.\n\n"
    "goals: 2-3, with metrics in TIME or QUALITY terms only. Never money — a cost "
    "saving is a consequence someone else will calculate, and inventing one here is "
    "the fastest way to lose the reader's trust.\n\n"
    "constraints: 2-4, including data protection and whatever sector regulation "
    "genuinely applies to THIS work. If you are not confident which rulebook applies, "
    "say so in general terms rather than naming a regime that may not.\n\n"
    "compliance_frameworks: only frameworks that plausibly bind this work, judged from "
    "the tasks themselves. An empty list is better than a wrong one.\n\n"
    "capabilities: 3-4. Each needs acceptance criteria written as 'Given …, when …, "
    "then …' and edge cases that are specific failure modes of this work, not generic "
    "ones. `priority` is 'must_have' or 'should_have'.\n\n"
    "workflow_steps: 5-8, each `type` one of trigger, retrieval, generation, "
    "validation, human_review, action, notification. Include at least one "
    "human_review step unless the work is genuinely unsupervised.\n\n"
    "tools: 3-4, `type` one of api, rpa, mcp_tool. Name the kind of system, and where "
    "a software catalogue has been supplied, name the systems it actually lists.\n\n"
    "risks: 2-3, with likelihood and impact each low, medium or high.\n\n"
    "human_in_the_loop: false only if no output of this agent needs a person to check "
    "or approve it before it has an effect.\n\n"
    "Do not invent internal system names, hostnames, team names or people. Where a "
    "specific system would be named and you have not been told one, describe the kind "
    "of system instead."
)


def slugify(text: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-") or "agent"


@dataclass
class AgentInput:
    """One task cluster to specify an agent for."""

    task_cluster_id: int
    cluster_name: str
    category: str
    domain: str
    automation_pct: float
    augmentation_pct: float
    # (name, definition, automation_pct, augmentation_pct)
    actions: list[tuple[str, str, float, float]] = field(default_factory=list)
    # Roles that do this work, with how much of their week it takes.
    roles: list[tuple[str, float]] = field(default_factory=list)
    task_names: list[str] = field(default_factory=list)
    absorbable: float = 0.0
    unit: str = "role-weeks"
    client_name: str = ""

    def prompt(self, *, threshold: float = 40.0) -> str:
        automatable = [a for a in self.actions if a[2] >= threshold]
        manual = [a for a in self.actions if a[2] < threshold]
        lines = [
            f"ORGANISATION: {self.client_name or 'the client'}",
            f"TASK CLUSTER: {self.cluster_name}  ({self.domain} › {self.category})",
            f"Overall {self.automation_pct:.0f}% automatable, {self.augmentation_pct:.0f}% augmentable.",
            f"Time released if automated: {self.absorbable:.2f} {self.unit}.",
            "",
            "The work, as the roles that do it describe it:",
        ]
        for n in self.task_names[:12]:
            lines.append(f"- {n}")
        if self.roles:
            lines += ["", "Roles that do this work (share of their week):"]
            for title, prop in sorted(self.roles, key=lambda r: -r[1])[:10]:
                lines.append(f"- {title}: {prop:.0f}%")
        if automatable:
            lines += ["", "Actions the agent should absorb:"]
            for n, d, auto, _aug in sorted(automatable, key=lambda a: -a[2]):
                lines.append(f"- {n} ({auto:.0f}% automatable): {d}")
        if manual:
            lines += ["", "Actions that must stay with a person:"]
            for n, d, auto, _aug in sorted(manual, key=lambda a: -a[2]):
                lines.append(f"- {n} (only {auto:.0f}% automatable): {d}")
        lines.append("\nSpecify the agent.")
        return "\n".join(lines)


@dataclass
class AgentSpec:
    task_cluster_id: int
    name: str
    slug: str
    purpose: str
    spec: dict
    human_in_the_loop: bool
    n_capabilities: int


class AgentError(RuntimeError):
    pass


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [ln for ln in (l.strip("- ").strip() for l in value.splitlines()) if ln]
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def _ids(items: list[dict], prefix: str) -> list[dict]:
    """Stamp sequential ids onto model output.

    The reference asked the model for "G1", "A1", "CAP1" and so on. It is not worth a
    schema field or a retry: ids exist so the sections can cross-reference each other,
    and code can number a list correctly every time.
    """
    return [{"id": f"{prefix}{i + 1}", **item} for i, item in enumerate(items)]


def build_spec(inp: AgentInput, d: dict, *, client_slug: str) -> AgentSpec:
    """Assemble the eight-section spec from the model's domain content.

    Everything here that is not `d[...]` is scaffolding: defensible defaults for a
    managed-container agent behind an enterprise identity provider. It is templated
    rather than generated so that every spec in a run is mutually consistent and so
    that a reviewer can tell at a glance which parts were a judgement and which were a
    house standard.
    """
    slug = slugify(inp.cluster_name)
    host = f"{slugify(client_slug)}.example"
    name = f"{inp.cluster_name} Agent"
    hitl = bool(d.get("human_in_the_loop", True))
    now = datetime.now(timezone.utc).isoformat()
    frameworks = _strings(d.get("compliance_frameworks")) or ["UK GDPR"]

    capabilities = [
        {
            "capability_id": f"CAP{i + 1}",
            "name": str(c.get("name", "")),
            "description": str(c.get("description", "")),
            "priority": str(c.get("priority", "should_have")),
            "acceptance_criteria": _strings(c.get("acceptance_criteria")),
            "edge_cases": _strings(c.get("edge_cases")),
            "inputs": _strings(c.get("inputs")),
            "outputs": _strings(c.get("outputs")),
            "dependencies": {"data_sources": _strings(c.get("data_sources"))},
        }
        for i, c in enumerate(d.get("capabilities") or [])
    ]

    steps = [
        {
            "step_id": f"S{i + 1}",
            "type": str(s.get("type", "action")),
            "description": str(s.get("description", "")),
            "failure_handling": {
                "fallback": "Route to the exception queue and notify the accountable role",
                "retry": {"enabled": True, "max_attempts": 3, "backoff_ms": 2000},
            },
        }
        for i, s in enumerate(d.get("workflow_steps") or [])
    ]

    tools = [
        {
            "tool_id": f"T{i + 1}",
            "name": str(t.get("name", "")),
            "type": str(t.get("type", "api")),
            "description": str(t.get("description", "")),
            "auth": {"method": "oauth2_client_credentials", "scopes": ["read", "write"]},
            "constraints": {"rate_limits": {"rpm": 60, "burst": 10}, "allowlist_domains": [f"*.{host}"]},
            "safety_controls": {
                # Follows the model's own judgement on supervision rather than a
                # constant: an agent that needs a person to approve its output needs
                # that enforced at the tool boundary, and one that does not should not
                # be saddled with a confirmation step that will be clicked through.
                "human_approval_required": hitl,
                "argument_validation": True,
                "output_sanitization": True,
            },
        }
        for i, t in enumerate(d.get("tools") or [])
    ]

    return AgentSpec(
        task_cluster_id=inp.task_cluster_id,
        name=name,
        slug=slug,
        purpose=str(d.get("purpose", "")),
        human_in_the_loop=hitl,
        n_capabilities=len(capabilities),
        spec={
            "meta": {
                "spec_version": "1.0.0",
                "agent_name": name,
                "agent_id": f"agent-{slug}",
                "task_cluster_id": inp.task_cluster_id,
                "task_cluster": inp.cluster_name,
                "taxonomy_path": [inp.domain, inp.category, inp.cluster_name],
                "owner_team": "Workforce Transformation",
                "created_at": now,
                "updated_at": now,
                "status": "draft",
                # Reserved TLD throughout: a spec should read as a spec, and inventing
                # plausible internal hostnames is the dangerous kind of helpful.
                "links": {
                    k: f"https://{host}/docs/{slug}/{k}" for k in ("prd", "tech_spec", "runbook")
                },
                "opportunity": {
                    "automation_pct": inp.automation_pct,
                    "augmentation_pct": inp.augmentation_pct,
                    "time_released": inp.absorbable,
                    "time_released_unit": inp.unit,
                    "basis": "Model estimate from the step 3 assessment, not a measurement",
                },
            },
            "business_context": {
                "problem_statement": str(d.get("problem_statement", "")),
                "goals": _ids(
                    [
                        {
                            "description": str(g.get("description", "")),
                            "metric": str(g.get("metric", "")),
                            "target": str(g.get("target", "")),
                        }
                        for g in (d.get("goals") or [])
                    ],
                    "G",
                ),
                "non_goals": _ids([{"description": s} for s in _strings(d.get("non_goals"))], "NG"),
                "assumptions": _ids([{"description": s} for s in _strings(d.get("assumptions"))], "A"),
                "constraints": _ids([{"description": s} for s in _strings(d.get("constraints"))], "C"),
                "success_criteria": _ids(
                    [
                        {
                            "description": str(s.get("description", "")),
                            "measurement_method": str(s.get("measurement_method", "")),
                        }
                        for s in (d.get("success_criteria") or [])
                    ],
                    "SC",
                ),
                "scope": {
                    "absorbed_actions": [a[0] for a in inp.actions if a[2] >= 40.0],
                    "retained_by_people": _strings(d.get("retained_by_people")),
                    "roles_affected": [r[0] for r in sorted(inp.roles, key=lambda r: -r[1])],
                },
            },
            "functional_requirements": {
                "capabilities": capabilities,
                "conversation_design": {
                    "handoff": {
                        "human_handoff_enabled": True,
                        "handoff_triggers": [
                            "low confidence in an extracted or generated field",
                            "a decision that requires a named person's authority",
                            "an exception outside the specified capabilities",
                        ],
                        "handoff_payload_schema_ref": f"schemas/{slug}_handoff.json",
                    },
                    "localisation": {"supported_locales": ["en-GB"], "time_zone_strategy": "Europe/London"},
                    "memory_policy": {
                        "enabled": True,
                        "retention_days": 90,
                        "what_to_store": ["case state", "document references", "decision rationale"],
                        "what_not_to_store": ["payment card data", "special category personal data"],
                        "deletion_process": "Automated purge at retention expiry; deletion on request",
                    },
                    "prompting": {
                        "system_instructions_summary": str(d.get("system_instructions_summary", "")),
                        "tool_use_guidelines": (
                            "Prefer authoritative system data over documents; cite the source "
                            "for every extracted field; never fabricate a value."
                        ),
                        "refusal_style": "State the boundary and route to the accountable person",
                    },
                    "tone_style": {"voice": "professional", "verbosity": "concise"},
                },
                "workflows": [
                    {
                        "workflow_id": f"wf-{slug}",
                        "name": name,
                        "trigger": str(d.get("trigger", "")),
                        "completion_definition": str(d.get("completion_definition", "")),
                        "sla": {"p95_seconds": 300, "availability_target": "99.5%"},
                        "steps": steps,
                    }
                ],
            },
            "governance": {
                "ownership": {
                    "product_owner": "Workforce Transformation lead",
                    "tech_lead": "Agent engineering lead",
                    "data_owner": "Data protection owner",
                    "security_owner": "Information security owner",
                },
                "risk_register": _ids(
                    [
                        {
                            "description": str(r.get("description", "")),
                            "likelihood": str(r.get("likelihood", "medium")),
                            "impact": str(r.get("impact", "medium")),
                            "mitigation": str(r.get("mitigation", "")),
                            "status": "open",
                        }
                        for r in (d.get("risks") or [])
                    ],
                    "R",
                ),
                "change_management": {
                    "versioning": {"schema_semver": "1.0.0", "change_notes": ["Initial draft"]},
                    "decision_log": [
                        {
                            "date": now[:10],
                            "decision": "Specification drafted for design review",
                            "rationale": (
                                f"{inp.cluster_name} identified in the AI opportunity assessment "
                                f"as releasing {inp.absorbable:.2f} {inp.unit}"
                            ),
                        }
                    ],
                },
            },
            "interfaces_and_schemas": {
                "api_contracts": [
                    {
                        "name": f"{slug}-api",
                        "version": "v1",
                        "base_path": f"/agents/{slug}",
                        "endpoints": [
                            {
                                "path": "/run",
                                "method": "POST",
                                "auth_required": True,
                                "request_schema": {
                                    "type": "object",
                                    "properties": {"case_reference": {"type": "string"}, "user_message": {"type": "string"}},
                                    "required": ["user_message"],
                                },
                                "response_schema": {
                                    "type": "object",
                                    "properties": {"agent_response": {"type": "string"}, "citations": {"type": "array"}},
                                },
                            }
                        ],
                    }
                ],
                "event_contracts": [
                    {
                        "name": f"{slug}-events",
                        "version": "v1",
                        "events": [
                            {
                                "event_name": f"{slug}.completed",
                                "delivery": "at_least_once",
                                "dlq": {"enabled": True},
                            }
                        ],
                    }
                ],
                "ui_contracts": {
                    "message_rendering": {"supports_markdown": True, "supports_streaming": True, "supports_citations": True},
                    "attachments": {"allowed_types": ["pdf", "docx", "xlsx", "csv", "eml"], "max_size_mb": 25},
                },
            },
            "non_functional_requirements": {
                "performance": {
                    "latency_targets": {"p50_seconds": 5.0, "p95_seconds": 20.0, "p99_seconds": 60.0},
                    "throughput": {"rps_target": 5, "concurrency_target": 20},
                    "streaming_responses": {"enabled": True},
                },
                "reliability": {
                    "availability_target": "99.5%",
                    "retries_timeouts": {"tool_timeout_seconds": 60, "global_timeout_seconds": 600},
                    "graceful_degradation": [
                        {"trigger": "a source system is unavailable", "mode": "queue work and notify the owning team"}
                    ],
                    "disaster_recovery": {"rpo_minutes": 60, "rto_minutes": 240},
                },
                "security_privacy": {
                    "data_classification": "confidential",
                    "pii_handling": {
                        "pii_expected": bool(_strings(d.get("pii_types"))),
                        "pii_types": _strings(d.get("pii_types")),
                        "masking_redaction": {"enabled": True},
                    },
                    "secrets_management": {"vault": "managed secret store", "rotation_policy_days": 90},
                    "compliance": {"frameworks": frameworks, "dpia_required": True},
                    "threat_model": {
                        "prompt_injection": {
                            "mitigations": ["input sanitisation", "tool allow-listing", "instruction hierarchy"]
                        },
                        "data_exfiltration": {"mitigations": ["egress allow-list", "output PII scanning"]},
                    },
                },
                "observability": {
                    "logging": {
                        "enabled": True,
                        "fields": ["request_id", "agent_id", "step_id", "latency_ms", "outcome"],
                        "log_redaction": {"enabled": True},
                    },
                    "metrics": {
                        "kpis": [
                            {"name": str(k.get("name", "")), "target": str(k.get("target", ""))}
                            for k in (d.get("kpis") or [])
                        ]
                    },
                    "tracing": {"enabled": True, "trace_provider": "OpenTelemetry"},
                    "alerting": {
                        "enabled": True,
                        "alerts": [
                            {"name": "error_rate_high", "severity": "P2", "threshold": ">2% over 15m"},
                            {"name": "queue_backlog", "severity": "P3", "threshold": ">50 items"},
                        ],
                    },
                },
                "safety_alignment": {
                    "policy_scope": _strings(d.get("retained_by_people")),
                    "refusal_and_redirection": {
                        "style": "explain and route",
                        "escalation_path": str(d.get("escalation_path", "")),
                    },
                },
            },
            "technical_architecture": {
                "model_stack": {
                    "primary_model": {"provider": "Anthropic", "model_name": "claude-sonnet-5", "mode": "agentic"},
                    "fallback_models": [
                        {"provider": "Anthropic", "model_name": "claude-haiku-4-5", "trigger": "low-complexity step"}
                    ],
                    "context_management": {
                        "summarisation": {"enabled": True, "strategy": "rolling case summary"},
                        "retrieval": {"enabled": True, "top_k": 8},
                    },
                },
                "runtime": {
                    "deployment_model": "managed_container",
                    "autoscaling": {"enabled": True, "min": 1, "max": 6},
                    "feature_flags": {"enabled": True},
                },
                "tools_and_integrations": {
                    "tool_registry": tools,
                    "action_policies": {
                        "side_effecting_actions": {"enabled": True, "require_confirmations": hitl}
                    },
                },
                "data": {
                    "knowledge_sources": [
                        {
                            "source_id": f"KS{i + 1}",
                            "name": str(k.get("name", "")),
                            "type": str(k.get("type", "unstructured")),
                            "access_method": str(k.get("access_method", "")),
                            "ownership": str(k.get("ownership", "")),
                            "freshness_sla_hours": 24,
                            "grounding": {"retrieval_enabled": True, "citations_required": True},
                        }
                        for i, k in enumerate(d.get("knowledge_sources") or [])
                    ],
                    "rag": {
                        "enabled": True,
                        "chunking": {"chunk_size_tokens": 512, "overlap_tokens": 64},
                        "ranking": {"method": "cosine_similarity", "top_k": 8},
                    },
                    "state": {
                        "session_store": {"ttl_minutes": 240},
                        "long_term_store": {"retention_days": 90},
                    },
                },
                "deployment_and_ops": {
                    "release_process": {
                        "environments": ["dev", "uat", "prod"],
                        "approvals": ["product_owner", "security_owner"],
                        "rollback_strategy": "blue_green",
                    },
                    "incident_management": {
                        "severity_levels": ["P1", "P2", "P3"],
                        "runbooks": [f"runbooks/{slug}.md"],
                    },
                },
                "evaluation_and_testing": {
                    "offline_eval": {
                        "datasets": [{"name": f"{slug}-golden", "size": 100, "labelling": "reviewed by a practitioner"}],
                        "metrics": ["extraction_accuracy", "task_success_rate", "hallucination_rate"],
                        "gating_thresholds": [{"metric": "task_success_rate", "min": 0.9}],
                    },
                    "online_eval": {"canary": {"enabled": True, "percent": 10}, "guardrail_monitoring": {"enabled": True}},
                    "test_plan": {
                        "unit_tests": {"enabled": True, "coverage_target": 0.8},
                        "integration_tests": {"enabled": True},
                        "red_teaming": {"enabled": True, "cadence": "quarterly"},
                    },
                },
            },
            "users_and_access": {
                "user_personas": [
                    {
                        "id": f"P{i + 1}",
                        "name": str(p.get("name", "")),
                        "description": str(p.get("description", "")),
                        "skill_level": str(p.get("skill_level", "intermediate")),
                        "primary_jobs_to_be_done": _strings(p.get("jobs_to_be_done")),
                    }
                    for i, p in enumerate(d.get("user_personas") or [])
                ],
                "access_channels": {
                    "web_app": {"enabled": True, "url": f"https://agents.{host}/{slug}"},
                    "api": {"enabled": True, "base_url": f"https://api.{host}/agents/{slug}"},
                    "email": {"enabled": False},
                    "voice": {"enabled": False},
                },
                "authentication_authorization": {
                    "authn": {"method": "oidc", "mfa_required": True},
                    "authz": {
                        "rbac_enabled": True,
                        "roles": [
                            {"name": "operator", "permissions": ["run", "review"]},
                            {"name": "approver", "permissions": ["run", "review", "approve", "release"]},
                        ],
                        "row_level_security": {"enabled": True},
                    },
                },
            },
        },
    )


SECTIONS = (
    "meta",
    "business_context",
    "functional_requirements",
    "governance",
    "interfaces_and_schemas",
    "non_functional_requirements",
    "technical_architecture",
    "users_and_access",
)


BUSINESS_TASK = (
    "\n\nFor this call, produce only the business content: what the agent is for, its "
    "goals and constraints, what it must be able to do, and what stays with people."
)
OPS_TASK = (
    "\n\nFor this call, produce only the operational content: how the agent runs — its "
    "workflow, who uses it, what it reads, what tools it calls, what could go wrong, "
    "and how it is measured."
)


def generate_agent(
    inp: AgentInput, *, client_slug: str, catalogue: str | None = None
) -> AgentSpec:
    """One agent, from two concurrent schema-constrained calls.

    Two rather than one because the full schema exceeds the API's 24-parameter grammar
    limit, and the alternative — sending no grammar and restating the schema in the
    prompt — trades a hard guarantee for a soft one on the most expensive artefact
    either studio produces. Splitting keeps the guarantee at almost no extra cost:
    output tokens dominate the bill and the two halves each produce about half the
    content, so only the prompt is paid for twice. With a catalogue supplied that
    second copy is a cache read anyway.

    Run concurrently, because the halves are independent and a person clicking
    "specify this agent" should wait for one call's latency, not two.

    `catalogue` is the optional software inventory, passed as a cache prefix: identical
    across every agent in a run, so it is written once and read back at a tenth of the
    input price for the rest of the fan-out.
    """
    def call(task: str, schema: dict) -> dict:
        return llm.complete_json(
            inp.prompt() + task,
            system=SYSTEM,
            cache_prefix=catalogue,
            json_schema=schema,
            effort="high",
            max_tokens=12_000,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        business = pool.submit(call, BUSINESS_TASK, BUSINESS_SCHEMA)
        ops = pool.submit(call, OPS_TASK, OPS_SCHEMA)
        d = {**business.result(), **ops.result()}

    if not d.get("capabilities"):
        raise AgentError(f"no capabilities returned for {inp.cluster_name}")
    spec = build_spec(inp, d, client_slug=client_slug)
    missing = [s for s in SECTIONS if s not in spec.spec]
    if missing:
        raise AgentError(f"spec for {inp.cluster_name} is missing sections: {missing}")
    return spec


def generate_many(
    inputs: list[AgentInput],
    *,
    client_slug: str,
    catalogue: str | None = None,
    workers: int = 6,
    progress=None,
) -> list[AgentSpec | None]:
    # Each agent holds two requests in flight, so the fan-out width is halved against
    # whatever was asked for — otherwise a setting of 16 quietly becomes 32 concurrent
    # requests and the account starts returning 429s.
    workers = max(1, workers // 2)
    return llm.pmap(
        lambda i: generate_agent(i, client_slug=client_slug, catalogue=catalogue),
        inputs,
        workers=workers,
        label="agents",
        progress=progress,
        tolerate_errors=True,
    )


# The most expensive artefact in either studio: a full spec is thousands of output
# tokens at high effort. Two calls per agent, so the prompt is counted twice and the
# output once — stated per agent so the fan-out cost is obvious before it is paid.
EST_INPUT_TOKENS = 3_200
EST_OUTPUT_TOKENS = 6_500
PRICE_INPUT = 3.00
PRICE_OUTPUT = 15.00


def cost_estimate(n: int) -> dict:
    dollars = n * (EST_INPUT_TOKENS * PRICE_INPUT + EST_OUTPUT_TOKENS * PRICE_OUTPUT) / 1_000_000
    return {
        "agents": n,
        "calls": n,
        "est_usd": round(dollars, 2),
        "basis": (
            f"~{EST_INPUT_TOKENS} input + ~{EST_OUTPUT_TOKENS} output tokens per agent "
            f"at ${PRICE_INPUT:.2f}/${PRICE_OUTPUT:.2f} per 1M"
        ),
    }
