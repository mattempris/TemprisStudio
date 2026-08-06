"""Step 7b — Job Evaluation ensemble voting.

instructions.txt: profiles are "passed to the Job Evaluation API voting ensemble
(check ./Insurance Demo showcase for Job Evaluation ensemble voting process, but
implement improvements to the way JE results are browsed)".

Recipe ported from `Insurance Demo/pipeline/gen_joblevel.py`, with one structural
change: that implementation hardcodes a 4-domain x 5-subfactor `DOMAINS` dict,
whereas instructions.txt requires the framework itself to be user-configurable
("User defines job profile template, Job Evaluation Framework and level names /
JE score mapping"). So the prompt, the JSON schema, and the aggregation are all
built dynamically from whatever JEFrameworkConfig is live.

Retained from the reference because they're load-bearing:
  - Three personas (Balanced, Generous, Harsh) derived in ONE call, with
    Generous/Harsh defined as nudges off Balanced rather than scored
    independently — that's what keeps them coherent.
  - A deterministic `clamp()` pass enforcing Generous >= Balanced >= Harsh per
    subfactor AFTER the call. The prompt asks for it; the code guarantees it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import DEFAULTS_DIR
from app.models.project_state import JEFrameworkConfig
from app.services import llm

PERSONAS = ("Balanced", "Generous", "Harsh")
SCORE_MIN, SCORE_MAX = 1, 5


# ---------------------------------------------------------------------------
# Framework loading
# ---------------------------------------------------------------------------
def load_default_framework() -> JEFrameworkConfig:
    """Default framework, converted from the Legacy jaStudio design CSVs
    (Job_Evaluation_Table.csv + Job_Evaluation_Domain_Weightings.csv) and
    levelling_table.json."""
    path = Path(DEFAULTS_DIR) / "je_framework_default.json"
    return JEFrameworkConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))


def validate_framework(framework: JEFrameworkConfig) -> list[str]:
    """Return a list of human-readable problems; empty means valid.

    Surfaced to the framework editor UI rather than raising, so a user
    mid-edit sees what's wrong instead of hitting a 500.
    """
    problems: list[str] = []
    if not framework.domains:
        problems.append("framework has no domains")

    domain_total = sum(d.weight for d in framework.domains)
    if abs(domain_total - 100) > 0.01:
        problems.append(f"domain weights sum to {domain_total:g}, expected 100")

    for d in framework.domains:
        if not d.subdomains:
            problems.append(f"domain '{d.name}' has no subdomains")
            continue
        sub_total = sum(s.weight for s in d.subdomains)
        if abs(sub_total - d.weight) > 0.01:
            problems.append(
                f"domain '{d.name}': subdomain weights sum to {sub_total:g}, "
                f"expected {d.weight:g} (the domain's own weight)"
            )
        for s in d.subdomains:
            if len(s.rubric) != 5:
                problems.append(
                    f"subdomain '{d.name} / {s.name}' has {len(s.rubric)} rubric "
                    "descriptors, expected 5 (one per 1-5 score point)"
                )

    if not framework.level_bands:
        problems.append("framework has no level bands")
    else:
        bands = sorted(framework.level_bands, key=lambda b: b.min_score)
        for a, b in zip(bands, bands[1:]):
            if b.min_score < a.max_score - 0.01:
                problems.append(
                    f"level bands '{a.name}' and '{b.name}' overlap "
                    f"({a.min_score:g}-{a.max_score:g} vs {b.min_score:g}-{b.max_score:g})"
                )
    return problems


# ---------------------------------------------------------------------------
# Dynamic prompt + schema
# ---------------------------------------------------------------------------
def build_schema(framework: JEFrameworkConfig) -> dict:
    """A FLAT schema: two arrays of uniform rows, not nested per-name objects.

    This shape was arrived at empirically, and the schema size turned out to be
    the binding constraint rather than max_tokens. Against the live API:
      - `minimum`/`maximum` on scores: rejected outright ("For 'integer' type,
        properties maximum, minimum are not supported").
      - `enum: [1..5]` per subfactor: "The compiled grammar is too large".
      - Nested objects keyed by domain/subfactor name, personas inlined: also
        "compiled grammar is too large".
      - Same, with the persona shape in `$defs`/`$ref`: passed validation, but
        every request then hung ~260s and failed with `overloaded_error` — at
        max_tokens of 32000, 16000, 8000 AND 4000 alike, while an identical
        32000-token request with no schema was served in 1.4s. The grammar was
        evidently still near the limit and too slow to compile.

    A nested schema needs one grammar rule per literal property name: 4 domains x
    (5 subfactors + Rationale) = 24 names, x3 personas. This flat form needs one
    rule for a score row and one for a rationale row, whatever the framework's
    size — so it also stops the grammar growing as a user adds domains.

    The cost is that the schema no longer guarantees every subfactor appears
    exactly once. That's fine: `validate_raw_evaluations()` already had to check
    completeness and ranges anyway, and now also catches missing or duplicate
    rows, with a retry behind it.
    """
    return {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string"},
                        "subfactor": {"type": "string"},
                        "balanced": {"type": "integer"},
                        "generous": {"type": "integer"},
                        "harsh": {"type": "integer"},
                    },
                    "required": ["domain", "subfactor", "balanced", "generous", "harsh"],
                    "additionalProperties": False,
                },
            },
            "rationales": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string"},
                        "persona": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["domain", "persona", "text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["scores", "rationales"],
        "additionalProperties": False,
    }


def _expand_flat_response(raw: dict, framework: JEFrameworkConfig) -> dict:
    """Turn the flat score/rationale rows into the nested
    persona -> domain -> {subfactor: score, Rationale: str} structure that
    clamp(), weighted_score() and domain_subtotals() all consume, so the flat
    wire format stays contained to this module's boundary.

    Names are matched case- and whitespace-insensitively, since the model echoes
    them back as free strings rather than picking from an enum.
    """
    canon_domain = {d.name.strip().lower(): d.name for d in framework.domains}
    canon_sub = {
        (d.name.strip().lower(), s.name.strip().lower()): s.name
        for d in framework.domains
        for s in d.subdomains
    }
    canon_persona = {p.lower(): p for p in PERSONAS}

    out: dict[str, dict] = {
        p: {d.name: {} for d in framework.domains} for p in PERSONAS
    }

    for row in raw.get("scores", []) or []:
        dname = canon_domain.get(str(row.get("domain", "")).strip().lower())
        if dname is None:
            continue
        sname = canon_sub.get((dname.strip().lower(), str(row.get("subfactor", "")).strip().lower()))
        if sname is None:
            continue
        for persona, field in (("Balanced", "balanced"), ("Generous", "generous"), ("Harsh", "harsh")):
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            out[persona][dname][sname] = value

    for row in raw.get("rationales", []) or []:
        dname = canon_domain.get(str(row.get("domain", "")).strip().lower())
        persona = canon_persona.get(str(row.get("persona", "")).strip().lower())
        if dname is None or persona is None:
            continue
        out[persona][dname]["Rationale"] = str(row.get("text", "")).strip()

    # ensure the Rationale key exists everywhere so validation reports "empty"
    # rather than raising a KeyError
    for persona in PERSONAS:
        for d in framework.domains:
            out[persona][d.name].setdefault("Rationale", "")

    return out


def build_system_prompt(framework: JEFrameworkConfig) -> str:
    lines = [
        "You are a job evaluation panel scoring one job profile against a "
        "structured job evaluation framework.\n",
        "Every subfactor is scored 1-5 using the rubric below. The rubric text "
        "for each score point tells you what that score means — score against "
        "the rubric, not against your general impression of seniority.\n",
        "FRAMEWORK:",
    ]
    for d in framework.domains:
        lines.append(f"\n{d.name} (domain weight {d.weight:g}%)")
        for s in d.subdomains:
            lines.append(f"  - {s.name} (weight {s.weight:g}%)")
            for level, descriptor in enumerate(s.rubric, start=1):
                lines.append(f"      {level}: {descriptor}")

    n_subfactors = sum(len(d.subdomains) for d in framework.domains)
    # How many subfactors the optimistic and pessimistic readings are allowed to move.
    #
    # This is the only lever on the width of the uncertainty band, because the model scores
    # subfactors and never sees the weighted total. Measured over all 565 profiles on the FS
    # demo, the previous window of n/4..n/2 (5..10 here) had the model moving 8.2 subfactors
    # up and 8.3 down, which is a Generous-minus-Harsh gap of 24.2 points on a 0-100 scale —
    # a band so wide it stopped being an uncertainty range and started covering three grades.
    # Each nudged subfactor is worth ~2.9 points of gap. Replaying the stored evaluations with
    # the nudge count capped measures it directly: 3 nudges gives a gap of 10.5, 4 gives 14.0,
    # 5 gives 17.4. So a 3..4 window puts the whole range inside 10-15 rather than only its
    # midpoint — the model previously landed about two thirds of the way up its window, and a
    # target band is not much use if the top of the window overshoots it.
    #
    # Balanced is untouched by this. It is the headline `aggregate_score`, derived
    # independently, and narrowing the band deliberately does not move it.
    spread_lo = max(2, n_subfactors // 6)
    spread_hi = max(spread_lo + 1, n_subfactors // 5)

    all_subfactors = [(d.name, s.name) for d in framework.domains for s in d.subdomains]

    lines.append(
        "\n\nScore each subfactor from THREE perspectives, derived in this order:\n"
        "1. BALANCED: your honest, defensible reading of the role.\n"
        f"2. GENEROUS: identical to Balanced EXCEPT nudge UP by exactly 1 the "
        f"{spread_lo}-{spread_hi} subfactors where the profile is genuinely "
        "ambiguous and an optimistic reading is still defensible. Never +2 on "
        "any subfactor.\n"
        f"3. HARSH: identical to Balanced EXCEPT nudge DOWN by exactly 1 the "
        f"{spread_lo}-{spread_hi} subfactors where the profile is genuinely "
        "ambiguous and a conservative reading is still defensible. Never -2 on "
        "any subfactor.\n\n"
        f"Move AT MOST {spread_hi} subfactors in either direction, and only where "
        "the evidence in the profile genuinely supports more than one rubric "
        "level. Where the profile is clear, all three perspectives agree — that "
        "is the expected case, not a failure to differentiate. These two "
        "perspectives exist to show where a panel would argue, so a wide gap on "
        "an unambiguous role is worse than no gap at all.\n\n"
        "HARD CONSTRAINTS: for every subfactor, generous >= balanced >= harsh, "
        "and every score is an integer 1-5. Generous and Harsh differ from "
        "Balanced only by these single-point nudges — they are not independent "
        "re-scorings.\n\n"
        "OUTPUT FORMAT:\n"
        "`scores`: one row per subfactor, carrying all three perspectives —\n"
        '  {"domain": ..., "subfactor": ..., "balanced": n, "generous": n, "harsh": n}\n'
        f"  Return exactly {len(all_subfactors)} rows, one for each subfactor "
        "listed above, using the domain and subfactor names verbatim. No "
        "duplicates and none omitted.\n"
        "`rationales`: one row per domain per perspective —\n"
        '  {"domain": ..., "persona": "Balanced"|"Generous"|"Harsh", "text": ...}\n'
        f"  Return exactly {len(framework.domains) * len(PERSONAS)} rows. Each "
        "text is 1-2 sentences in that perspective's voice, citing specifics from "
        "the profile that justify its scores. Do not restate the rubric."
    )
    return "\n".join(lines)


def build_user_prompt(profile_title: str, profile_content: dict) -> str:
    parts = [f"Job profile: {profile_title}\n"]

    def _add(label: str, value: Any) -> None:
        if not value:
            return
        if isinstance(value, list):
            if value and isinstance(value[0], dict):
                items = "; ".join(f"{i.get('label')}: {i.get('value')}" for i in value)
            else:
                items = "; ".join(str(v) for v in value)
            parts.append(f"{label}: {items}")
        else:
            parts.append(f"{label}: {value}")

    _add("About the role", profile_content.get("about_role"))
    _add("Key responsibilities", profile_content.get("responsibilities"))
    _add("Minimum requirements", profile_content.get("requirements"))
    _add("Essential skills", profile_content.get("essential_skills"))
    _add("Desirable skills", profile_content.get("desirable_skills"))
    _add("Effort and focus", profile_content.get("contribution"))
    _add("Working conditions", profile_content.get("required_of_you"))
    _add("Reporting line", profile_content.get("reporting_line"))
    _add("Budget responsibility", profile_content.get("budget_responsibility"))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Deterministic post-processing
# ---------------------------------------------------------------------------
class InvalidEvaluation(ValueError):
    """The model's response is structurally valid JSON but not a usable
    evaluation (out-of-range scores, missing rationales)."""


def validate_raw_evaluations(evaluations: dict, framework: JEFrameworkConfig) -> None:
    """Reject a hollow or nonsense response instead of clamping it into
    plausibility.

    This matters because the schema cannot enforce the 1-5 range (structured
    outputs reject numeric bounds, and per-subfactor enums blow the grammar
    budget). Observed failure modes when the model isn't reasoning properly
    include every score returned as 0, and wild values like -5 / 35550 / 11455
    alongside empty rationale strings. `_clip()` would quietly turn those into
    1s and 5s and emit a JE result that looks credible and means nothing — so
    they have to be caught here and retried instead.
    """
    problems: list[str] = []
    for persona in PERSONAS:
        pdata = evaluations.get(persona)
        if not isinstance(pdata, dict):
            problems.append(f"{persona}: missing")
            continue
        for d in framework.domains:
            ddata = pdata.get(d.name)
            if not isinstance(ddata, dict):
                problems.append(f"{persona}/{d.name}: missing")
                continue
            if not str(ddata.get("Rationale", "")).strip():
                problems.append(f"{persona}/{d.name}: empty Rationale")
            for s in d.subdomains:
                raw = ddata.get(s.name)
                if not isinstance(raw, int) or isinstance(raw, bool):
                    problems.append(f"{persona}/{d.name}/{s.name}: not an integer ({raw!r})")
                elif not (SCORE_MIN <= raw <= SCORE_MAX):
                    problems.append(f"{persona}/{d.name}/{s.name}: {raw} outside {SCORE_MIN}-{SCORE_MAX}")

    if problems:
        raise InvalidEvaluation(
            f"{len(problems)} problem(s) in the evaluation response: " + "; ".join(problems[:6])
        )


def clamp(evaluations: dict, framework: JEFrameworkConfig) -> dict:
    """Enforce Generous >= Balanced >= Harsh per subfactor.

    This is the reference implementation's key insight: the prompt asks for the
    ordering, and this guarantees it. Run only AFTER
    `validate_raw_evaluations()` — the `_clip()` calls here exist to keep a
    legitimately-nudged score inside the scale, not to rescue out-of-range
    garbage, which validation has already rejected.
    """
    for d in framework.domains:
        for s in d.subdomains:
            balanced = _clip(evaluations["Balanced"][d.name][s.name])
            evaluations["Balanced"][d.name][s.name] = balanced
            evaluations["Generous"][d.name][s.name] = _clip(
                max(evaluations["Generous"][d.name][s.name], balanced)
            )
            evaluations["Harsh"][d.name][s.name] = _clip(
                min(evaluations["Harsh"][d.name][s.name], balanced)
            )
    return evaluations


def _clip(v: int) -> int:
    return max(SCORE_MIN, min(SCORE_MAX, int(v)))


def weighted_score(persona_eval: dict, framework: JEFrameworkConfig) -> float:
    """Weighted 0-100 score for one persona.

    Each subdomain's 1-5 score is rescaled to 0-100 and multiplied by its weight
    (weights across all subdomains sum to 100).

    NOTE this differs deliberately from the legacy reference, which summed the
    20 subfactors with EQUAL weight for a 20-100 total and never applied the
    domain weightings at all. Honouring the weights is the whole reason
    instructions.txt supplies Job_Evaluation_Domain_Weightings.csv, but it means
    the scale's floor is 0 rather than 20 — so level bands written for the raw
    sum must be converted (pct = (raw_sum - 20) / 0.8) before use. The shipped
    default bands already are; see the note in je_framework_default.json.
    """
    total = 0.0
    for d in framework.domains:
        for s in d.subdomains:
            raw = _clip(persona_eval[d.name][s.name])
            pct = (raw - SCORE_MIN) / (SCORE_MAX - SCORE_MIN) * 100.0
            total += pct * (s.weight / 100.0)
    return round(total, 2)


def map_to_level(score: float, framework: JEFrameworkConfig) -> str:
    """Map an aggregate score to a level name via the user-editable bands."""
    bands = sorted(framework.level_bands, key=lambda b: b.min_score)
    for band in bands:
        if band.min_score <= score < band.max_score:
            return band.name
    if bands:
        # at/above the top band's ceiling, or below the lowest floor
        if score >= bands[-1].max_score:
            return bands[-1].name
        if score < bands[0].min_score:
            return bands[0].name
    return "Unbanded"


@dataclass
class JEResult:
    profile_key: str
    personas: dict  # persona -> {domain -> {subdomain: score, ..., "Rationale": str}}
    persona_scores: dict[str, float]
    aggregate_score: float  # the Balanced score — the headline number
    level_name: str
    spread: float  # Generous - Harsh, the visible uncertainty band

    def domain_subtotals(self, framework: JEFrameworkConfig, persona: str = "Balanced") -> dict[str, float]:
        """Per-domain weighted contribution, for the drill-down UI's
        domain-rollup level (so the drawer can show domain scores without
        exposing every subfactor)."""
        out: dict[str, float] = {}
        for d in framework.domains:
            subtotal = 0.0
            for s in d.subdomains:
                raw = _clip(self.personas[persona][d.name][s.name])
                pct = (raw - SCORE_MIN) / (SCORE_MAX - SCORE_MIN) * 100.0
                subtotal += pct * (s.weight / 100.0)
            out[d.name] = round(subtotal, 2)
        return out


def evaluate_one(
    profile_key: str,
    profile_title: str,
    profile_content: dict,
    framework: JEFrameworkConfig,
    *,
    attempts: int = 3,
) -> JEResult:
    prompt = build_user_prompt(profile_title, profile_content)
    system = build_system_prompt(framework)
    schema = build_schema(framework)

    last_err: InvalidEvaluation | None = None
    raw: dict | None = None
    for attempt in range(attempts):
        candidate = llm.complete_json(
            prompt,
            # The framework — every domain, sub-factor weight and rubric descriptor —
            # is identical for every profile in the run and is the bulk of the
            # request. Sent as a cache prefix it is written once and read back at a
            # tenth of the price for the remaining profiles.
            cache_prefix=system,
            json_schema=schema,
            effort="medium",
            # Keep the transient budget small: this loop already retries, and the
            # two multiply. At the default 4 that's up to 12 calls of a large,
            # slow request per profile — long enough to look like a hang.
            retries=2,
            # Generous budget: adaptive thinking shares max_tokens with the
            # visible JSON, and this response is ~60 scores + 12 rationales.
            # At 16000 the JSON was getting truncated mid-string.
            max_tokens=32000,
            # Adaptive thinking is essential here, not optional: this is a
            # 3-persona x N-subfactor reasoning task, and with thinking disabled
            # the constrained grammar gets filled with noise (see llm.complete's
            # note and validate_raw_evaluations).
            thinking="adaptive",
        )
        try:
            expanded = _expand_flat_response(candidate, framework)
            validate_raw_evaluations(expanded, framework)
            raw = expanded
            break
        except InvalidEvaluation as e:
            last_err = e
            print(f"  [job_evaluation] {profile_key}: invalid evaluation (attempt {attempt + 1}/{attempts}) — {e}")

    if raw is None:
        raise InvalidEvaluation(f"{profile_key}: no valid evaluation after {attempts} attempts: {last_err}")

    evaluations = clamp(raw, framework)

    scores = {p: weighted_score(evaluations[p], framework) for p in PERSONAS}
    balanced = scores["Balanced"]
    return JEResult(
        profile_key=profile_key,
        personas=evaluations,
        persona_scores=scores,
        aggregate_score=balanced,
        level_name=map_to_level(balanced, framework),
        spread=round(scores["Generous"] - scores["Harsh"], 2),
    )


def evaluate_many(
    profiles: list[tuple[str, str, dict]],  # (profile_key, title, content)
    framework: JEFrameworkConfig,
    *,
    workers: int = 6,
    progress=None,
) -> list[JEResult | None]:
    """One entry per input, in order; None where that profile could not be evaluated.

    Failures are tolerated rather than fatal. Each evaluation is an independent,
    expensive call, and a single profile that will not produce a valid response
    (or hits a server-side grammar timeout) used to abort the whole stage and
    discard every evaluation already paid for.
    """
    return llm.pmap(
        lambda p: evaluate_one(p[0], p[1], p[2], framework),
        profiles,
        workers=workers,
        label="job-evaluation",
        progress=progress,
        tolerate_errors=True,
    )
