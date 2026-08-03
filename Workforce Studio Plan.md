# Workforce Studio — Implementation Plan

A second section of the JAStudio application. JAStudio answers *what the work is*; Workforce
Studio answers *what AI does to it*. It reads a completed job architecture — hierarchy, profiles,
skills taxonomy, tasks taxonomy — and produces a living work-architecture graph, an AI opportunity
assessment, downloadable Claude Skills, agent specifications, and redesigned roles.

Source: `Workforce Studio Instructions.txt`. Reference implementations: `./Insurance Demo` and
`./HR`, which contain finished versions of most of these outputs against hand-seeded data. The job
of this build is to produce the same artefacts from JAStudio's own pipeline output, for any project.

---

## 1. Decisions taken

Four were escalated and settled before planning.

**The graph aggregates, and zooms.** The whole graph is computed once and persisted to Azure. The
view is a *resolution cut* of it: by default the coarsest level of all three hierarchies (job
families, skill families, task domains), with a zoom control to move the whole view finer and
click-to-expand on any single node. This is both more readable and cheaper than shipping the
full graph — see §4 for why the compute is negligible.

**Two opportunity scores, not one.** Each action carries `automation_pct` (what AI can do
unattended) and `augmentation_pct` (how much faster a person is with AI help). Step 6 ranks agents
by automation; Step 5 ranks prompts by augmentation. The demos score only automation, which would
rank Step 5's list by how replaceable each task is — contract review would sink to the bottom
despite being one of the tasks a good prompt helps most.

**Agent generation is one API call per agent**, fanned out concurrently to a strong model. Not the
Anthropic Batch API: that would halve the cost but a submission can take hours, and the job
registry is in-memory only, so it would need durable job records and resume-across-restart first.
"Generate All Agents" is a wide fan-out of individual calls, reusing the existing progress
plumbing.

**Skills are downloadable files.** Each generated skill is written as `kebab-case-skill-name.md`
with YAML frontmatter, downloadable individually or as a per-role zip, for upload into Claude.

---

## 2. Where it lives

Same app, same backend, same project state, same blob container. Not a separate deployment:
every output here hangs off `profile_key`, `cluster_id` and the task/skill cluster ids that
JAStudio already mints, and duplicating that state would guarantee the two drift apart.

**Palettes.** Six selectable palettes — light, dark, vintage, cyberpunk, sepia and
upbeat — implemented as `data-palette` overrides of the existing design tokens in
`palettes.css`, so every component follows without change. Built in JAStudio ahead of
this work and inherited here for free; the one thing Workforce Studio must respect is
that the graph's own colours are tokens too, not literals, or the palette stops at the
canvas edge.

**Navigation.** `App.tsx` currently switches between `ProjectSelectPage` and `PipelinePage` on
store state. It gains a third axis: a `studio` mode of `"job-architecture" | "workforce"`, with a
toggle at the top of the nav panel. Workforce Studio renders `WorkforcePage` — the same sticky
step-sidebar and scrolling accordion of `StageSection`s, reusing `StageSection`, `ProgressBar`,
`JobPulse`, `Modal`, `Tooltip`, `Collapsible`, the heat scale and the Tempris tokens unchanged.

**Gating.** A "Proceed to Workforce Studio" button sits at the foot of the JAStudio page, and the
nav toggle is enabled, only when all of the following hold:

| Requirement | State check |
|---|---|
| Job hierarchy complete | `clustering_tiers` confirmed at profile, category and family |
| Profile documents written | `job_profiles` non-empty |
| Skills taxonomy named | `skills.clustering.profile_names` non-empty |
| Tasks taxonomy named | `tasks.clustering.profile_names` non-empty |

Job evaluation is deliberately **not** required: the instructions place levelling in JAStudio and
exclude it here, and nothing in Workforce Studio reads a JE score. Where the gate is unmet the
button states which asset is missing rather than being inertly disabled.

> **Prerequisite for testing.** `banking-demo/full-ja` currently has 565 profiles, 90 categories,
> 14 families, 565 profile documents, 565 evaluations and a 550-cluster skills taxonomy — but **no
> task taxonomy**. Tasks must be inferred and clustered before any of Steps 1–7 can be exercised on
> real data. Everything below is testable on that project the moment they are.

---

## 3. State and persistence

A new `WorkforceState` on `ProjectState`, following the existing conventions exactly — Pydantic
models, blob-persisted via `ProjectService.save_state`, one lineage entry per user-confirmed
decision, nothing written for intermediate compute.

```
WorkforceState
  actions:        list[TaskActionRecord]     # 3-5 per task cluster, Step 3
  opportunity:    TaskOpportunityIndex       # rolled-up scores per task cluster
  processes:      list[ProcessRecord]        # Step 2 uploads, parsed and mapped
  process_assessments: list[ProcessAssessment]  # Step 4, as-is/to-be per process
  skills_guidance: list[TaskSkillRecord]     # Step 5, one per (role, task) generated
  agents:         list[AgentDefinitionRecord]   # Step 6
  future_roles:   list[FutureRoleRecord]     # Step 7
  context_uploads: list[ContextDocRecord]    # software catalogue, strategic context
  graph_version:  int                        # bumped when the graph fact table changes
```

Large artefacts stay out of the state blob, which is already ~9MB on this project and read on
nearly every request. They go to their own blobs under the existing subtree:

```
client-<slug>/job-architecture/<project>/workforce/
  graph/facts.json            # leaf-level relationships — the whole graph, once
  graph/cuts/<hash>.json      # cached resolution cuts
  agents/<agent_id>.json      # full 8-section spec, ~50KB each
  skills/<role>/<skill>.md    # the downloadable bundles
  processes/<process_id>.json  # parsed steps + mapping
  reports/<step>.html          # embedded reports
```

---

## 4. The work architecture graph (Step 1)

This is the centrepiece and everything else feeds it, so it is specified first.

### The fact table

The whole graph is computed once and persisted as a **leaf-level fact table** — the finest-grained
relationships, with weights:

| Edge | Source | Weight |
|---|---|---|
| profile → skill cluster | `skills.clustering` assignments via `source_profile_key` | count of skills, proficiency level if mapped |
| profile → task cluster | `tasks.clustering` assignments via `source_profile_key` | task proportion of that role |
| task cluster → action | Step 3 | `pct_of_task` |
| profile → job category → job family | `clustering_tiers` | structural |
| skill cluster → skill category → skill family | `skills.clustering_tiers` | structural |
| task cluster → task category → task domain | `tasks.clustering_tiers` | structural |
| agent → task cluster | Step 6 | time saved |
| process → task cluster | Step 2 | step count |
| process → automated task | Step 2, where no job-derived task matches | new node type |

For the current project that is roughly 15,000 rows. It is derived deterministically from state, so
it is recomputed rather than migrated whenever `graph_version` changes.

### Resolution cuts

A cut is a request for *which level of each hierarchy to show*, plus any individually expanded
nodes. The server rolls the fact table up to that cut and returns only what the view needs:

```
GET /workforce/graph?jobs=family&skills=family&tasks=domain
    &expand=job:family:3,skill:category:11
```

Roll-up is a dictionary aggregation over 15k rows — single-digit milliseconds — and the response is
100–400 nodes rather than 1,100+. Aggregated edge weight is the sum of the leaf weights between the
two ancestor sets, which is what makes a thick edge between "Technology" and "Engineering Skills"
mean something. The three pure-resolution cuts are precomputed and cached in blob; mixed cuts from
expansion are computed on demand and cached by hash.

This is why aggregating is *less* compute than the alternative, not more: the expensive part (the
fact table) happens once, and the browser never receives more than a few hundred nodes.

### Rendering

D3 v7 force-directed, ported from `Insurance Demo/report/Reports/graph_report.html` — already
Tempris-styled and proven, and no new dependency. Its node-type palette carries over, extended:

```
role #1F7FB8   taxonomy #10B981   task #9ca3af   agent #c00000
process #7C3AED   skill (new)     action (new)   automated-task (new)
```

Node radius scales with headcount where available, else membership count. A zoom control moves all
three hierarchies coarser/finer together; clicking a node expands just that branch.

Hover shows name and description via the existing `Tooltip`. Clicking opens a `Modal` with the full
detail, and — per the instructions — **the modal grows as later steps run**: a profile modal gains
its tasks and AI opportunity after Step 3, its skills after Step 5, its future design after Step 7;
a task cluster modal gains actions, agent coverage and process membership.

---

## 5. The steps

### Step 2 — Process upload (optional; unlocks Step 4)

Upload `html, svg, pdf, xlsx, docx`. `parsers.py` already covers pdf/docx/doc/txt/html; this adds
**SVG** (labels are text nodes in the XML, so extraction is tractable) and **XLSX** (pandas is
already a dependency for HRIS ingestion). Extraction is text-only — a process *diagram* yields its
step labels and adjacency where the file carries them, not a reconstructed flowchart. That limit is
stated in the UI rather than hidden.

One LLM call per document infers ordered process steps with actor, system and manual/automated
flag. Steps are then matched to existing task clusters by embedding similarity plus an LLM
confirmation on the uncertain tail — the same stability-gated pattern the clustering engine already
uses, so the match is auditable. Steps with no plausible task match become **automated-task** nodes:
work that exists in the process but was invisible to job-description-derived inference.

**Fixtures.** The instructions ask for the two demo processes to be extracted into process
documents. Both are in `./HR`: `as-is-anon.html` / `to-be-anon.html` (Client Payroll) and
`offer-to-hire2.html` / `offer-to-hire_new.html` (Offer to Hire). These become
`fixtures/processes/*.html` and are the test corpus for this step. `./Insurance Demo` additionally
carries Commercial Renewal and Claims Operations, useful as a second corpus.

### Step 3 — AI Opportunity Assessment

One LLM call per task cluster returns 3–5 **actions**: name, definition, `pct_of_task` (integers
summing to 100), `automation_pct` (0–80), `augmentation_pct` (0–80). Calibration guidance is ported
from `Insurance Demo/pipeline/gen_tasks.py`, which is explicit and well-tuned — judgement,
negotiation and regulated advice score low; drafting, comparison, checking and summarising score
high — extended with the augmentation axis.

Roll-ups are deterministic, not asked of the model:

```
task_cluster.automation   = Σ (action.pct_of_task/100 × action.automation_pct)
task_cluster.augmentation = Σ (action.pct_of_task/100 × action.augmentation_pct)
role.automation           = Σ over its tasks (task.proportion/100 × task.automation)
fte_saved                 = role.automation/100 × headcount
```

Output: an embedded report matching **demo section 6, Opportunity Assessment (Role-level)** — every
role and task with automation potential and time saving, plus the role heat map. Actions become
graph nodes at the finest resolution.

Validation mirrors the JE precedent: percentages that do not sum, or scores outside range, are
rejected and retried rather than clamped into plausibility.

### Step 4 — Process Opportunity Assessment (unlocks when processes exist)

Per process, an as-is/to-be assessment producing exactly the metrics demo section 7 reports —
process steps, manual touchpoints, actors, required sign-offs, handler effort — for both states,
with the narrative of what changes. Embedded as a report alongside the parsed as-is map.

### Step 5 — Personal Productivity (unlocks with Step 3)

Filter to a role through job family → category → profile. Its tasks are listed ordered by
**augmentation × proportion of role** — the tasks where a prompt helps this person most. A button
per task generates guidance in the demo's proven shape (`name`, `description`, `hook`,
`when_to_use`, `when_not_to_use`, `body` — see `report/data/skills.json`), which is already
essentially an Anthropic Skill.

It is written out as `kebab-case-skill-name.md`:

```markdown
---
name: frame-renewal-strategy-brief
description: Gathers expiring policy details, loss history and client context to
  produce a structured renewal planning brief and open-decisions checklist.
---

<body>
```

Downloadable per skill, or as a zip per role. Generated skills are attached to the profile's graph
modal, per the instructions.

### Step 6 — Agent definitions (unlocks with Step 3)

Filter to a task cluster through task domain → category. Clusters are listed ordered by
**automation × total proportion across all roles**, weighted by headcount where available — i.e. by
FTE-equivalent time released, which is the honest priority for an agent.

A button generates a full specification: **one API call per agent to a strong model**, returning
the eight-section spec the demo produces (`meta`, `business_context`, `functional_requirements`,
`governance`, `interfaces_and_schemas`, `non_functional_requirements`, `technical_architecture`,
`users_and_access`). `Insurance Demo/pipeline/gen_agentdefs.py` is the reference, including its
useful split: domain content from the model, technical scaffolding templated for schema fidelity.
The prompt carries the cluster's actions, the automated versus retained-manual split, and the
projected saving, following **demo section 9, Build Options**.

Optional upload of a software catalogue / tech infrastructure document (`xlsx, docx, pdf, html,
txt`) is folded into the prompt as shared context so specs name systems the organisation actually
runs. Being identical across every agent in a run, it is passed as a cache prefix — the
prompt-caching path added this week — so a catalogue of any size is written once and read back
cheaply for the rest of the fan-out.

**Generate All Agents** fans out one call per cluster above a threshold, with live progress, then
produces an embedded report prioritised by total time saving, matching
`report/Reports/agent_impact_summary.html`. Per-item failures are tolerated so one bad spec does not
discard the rest; a credit or key failure still stops everything, per the existing `pmap` contract.
Each agent joins the graph as an `agent` node edged to the task clusters it absorbs.

### Step 7 — Future Role Design

Per role, generate the view demo section 12 produces: the evolution narrative (today → after
automation → future role), future responsibilities, which tasks are absorbed and which are retained
and deepened, and the skills to build — with deliberate practice to keep AI-assisted judgement
sharp. Optional upload of strategic context on how the organisation wants freed-up time used, again
passed as a cached prefix.

### Export

`workbook.py` gains datasets for actions and opportunity, agents, processes, skills guidance and
future roles, so they appear in the existing manifest, per-dataset CSV and multi-sheet XLSX with no
new export surface. Skill `.md` bundles and agent JSON specs download individually.

---

## 6. Reference mapping

The instructions cite demo step numbers. `Insurance Demo/index.html` numbers its sections
explicitly, and they resolve as:

| Instruction | Demo section | Anchor |
|---|---|---|
| Step 3 report "as per step 6" | 6 · Opportunity Assessment (Role-level) | `#role-reporting` |
| Step 4 output "as per step 7" | 7 · Opportunity Assessment (Process-level) | `#process-maps` |
| Step 6 "Build Options" | 9 · Build Options | `#agents` |
| Step 7 view "like step 12" | 12 · Future Role Design | `#future-role` |
| Step 5 "as per Step 13" | 13 · Personal Productivity | `#prompting` |
| Step 1 graph | 11 · Work Architecture | `#work-architecture` |

---

## 7. What is reused rather than rebuilt

- `llm.py` in full: prompt caching, the grammar-timeout fallback, budget escalation on truncation,
  `pmap` with per-item tolerance and systemic-failure propagation.
- `orchestrator.py` — job registry, WebSocket progress, heartbeats. Each Workforce Studio step
  becomes a `StageName`, so progress, spinners and the heartbeat all work unchanged.
- `ProjectService` / `BlobProjectStore` — state, arrays, indexes, lineage.
- The embedding service for process-step-to-task matching (`taskQWEN`).
- Frontend: `StageSection`, `ProgressBar`, `JobPulse`, `Modal`, `Tooltip`, `Collapsible`,
  `Dropzone`, `Badge`, `Button`, `lib/heat.ts`, the Tempris tokens.
- `exports/workbook.py` dataset pattern.
- `parsers.py`, extended with SVG and XLSX.

---

## 8. Phasing

Each phase is independently demoable and leaves the app working.

**Phase A — Shell and graph.** Mode toggle, gate, `WorkforcePage`, the fact table, resolution cuts,
the D3 renderer, tooltips and modals. Delivers Step 1 against existing JAStudio output with no new
LLM spend.

**Phase B — Opportunity. BUILT.** Step 3: actions, both scores, roll-ups, the role-level report,
action nodes in the graph. Unlocks Steps 5 and 6.

Deviations from this plan, found while building:

- **Percentage sums are normalised, not retried.** §5 said percentages that do not sum and scores
  out of range are both rejected and retried. Only the scores are: task inference already learned
  that a model asked for integers summing to 100 returns 97 or 103 often enough that retrying is
  paying twice for the same near-miss, and the sum is a guarantee code can just provide. Raw sums
  are recorded so the drift stays visible. Out-of-range scores *are* retried, with the violation
  quoted back; a second failure clamps and flags rather than losing the cluster.
- **Actions are an expansion, not a fourth level.** They appear only when a task cluster is opened
  at the finest resolution, edged to their parent with a rigid short link. Putting them in the
  resolution ladder would have added 2,000-3,000 nodes to a view that is deliberately a few hundred.
- **Nodes carry `null` opportunity, not zero,** where nothing beneath them is assessed, and every
  roll-up reports `coverage` — the share of a node's weight that has been assessed. Without it a
  half-finished run reads as a low-opportunity workforce.
- **The graph's opportunity ramp is stretched to the observed range,** not the absolute 0-80 used in
  the tables. Real cluster automation spans about 20-40%, which on 0-80 is twenty-five degrees of
  blue. The legend carries the actual endpoints, which is what makes the stretch honest.
- **Cuts above ~600 nodes now warn.** "Finest" on this project is 1,870 nodes and 10,300 links; the
  layout settles into a mass. The button stays, and says so. A real fix belongs in Phase E's pass
  over graph growth.
- **One BlobProjectStore per process.** Every request was building a fresh `ClientSecretCredential`,
  costing ~0.9s on any endpoint that touches blob. Unrelated to step 3, found by measuring it.

Verified on `banking-demo/full-ja`: 35 offline assertions on the arithmetic; a live calibration run
over 8 hand-picked contrasting clusters and 10 through the HTTP path (54 actions), with automation
spanning 21-38% against augmentation 32-55% — building client relationships at 5% automation
against matching transaction records at 65%. Both reports, both graph colour modes, action nodes,
and the action modal checked in the browser. The remaining 740 clusters (~$14) are left for the user
to run.

**Phase C — The two generators. BUILT.** Step 5 (filter, ranked tasks, skill generation, `.md`
download) and Step 6 (filter, FTE ranking, single and bulk agent generation, impact report,
software-catalogue context). The largest phase and the largest spend.

Deviations from this plan:

- **Agent generation is two calls, not one.** §1 settled on one API call per agent. It is not
  available: the domain schema exceeds the API's grammar compile limit, and the whole schema is
  rejected while each half compiles — established by probe (`scripts/_probe_agent_grammar.py`), which
  also found that `additionalProperties: false` is mandatory, so trimming it to buy budget is out.
  The fallback in `llm.py` does recover by restating the schema in the prompt, but that trades a hard
  guarantee for a soft one on the most expensive artefact either studio produces. Two
  grammar-constrained halves — business content and operational content — run concurrently per agent
  instead. Output tokens dominate the bill and each half produces about half the content, so only the
  prompt is paid twice, and with a catalogue supplied that copy is a cache read. The intent behind the
  original decision — individual on-demand requests rather than the Batch API — is unchanged.
- **The regulatory frame is asked of the model, not assumed.** The reference hardcodes FCA and ICOBS.
  Against the banking project the model returned UK GDPR and PCI DSS, which is right and which a
  hardcoded insurance frame would have got wrong. An empty list is accepted in preference to a
  confident wrong one, falling back only to data protection.
- **Every host in a spec is under the reserved `.example` TLD**, asserted in the offline test.
  Inventing plausible internal hostnames is the dangerous kind of helpful.
- **Skills are one per (role, task cluster), not per task.** A role with three tasks in one cluster
  wants one skill, not three near-identical ones.
- **Skill bodies get their headings demoted** so the model's own `##` sections sit below the file's.
  Fixed in code rather than in the prompt: heading level is the first instruction a model drops.
- **`when_not_to_use` is the field that earns its keep.** It is where the accountability and
  regulated boundaries get written down, and the prompt says so — on the trading-UI role it produced
  "not for signing off that a screen meets accessibility standards — that needs a formal audit".

Verified on `banking-demo/full-ja`: 3 skills for one role (valid frontmatter, unique kebab-case
filenames, working zip) and 4 agent specs (all eight sections, 27KB each, retained-vs-absorbed split
matching the action scores, human-in-the-loop on all four). 63 offline assertions across the two
steps. Both stages, the skill viewer, the spec accordion and the downloads checked in the browser.

**Phase D — Processes.** Fixture extraction from `./HR`, SVG and XLSX parsing, Step 2 upload and
mapping, automated-task nodes, Step 4 assessment.

**Phase E — Future roles, export, polish.** Step 7, the export datasets, and a full pass over the
graph's growth behaviour as every step contributes nodes.

---

## 9. Verification

Following this repo's existing discipline: offline tests with stubbed LLM calls for logic, small
live runs for prompt behaviour, and browser checks before any step is called done.

- **Graph**: fact-table row counts reconcile to state (every profile, skill cluster and task cluster
  appears exactly once); aggregated edge weights at family resolution sum to the leaf totals;
  cut latency measured at the current project's size; renderer checked in the browser at each
  resolution and with expansion.
- **Step 3**: percentages sum to 100 per task; scores within range; roll-up arithmetic asserted
  against hand-computed fixtures; one live run over ~10 task clusters to check calibration is
  discriminating rather than uniformly mid-range.
- **Step 5**: generated `.md` parses as valid frontmatter + body; filename is kebab-case and unique;
  zip opens; ordering differs from Step 6's ordering on the same data — the point of the second
  score.
- **Step 6**: spec validates against the eight-section schema; catalogue context appears as a cached
  prefix with a cache hit rate above 90% across a fan-out; one bad spec does not lose the run.
- **Step 2/4**: both HR fixtures parse; step-to-task matching audited on a sample; automated-task
  nodes appear only where no plausible match exists.
- **Throughout**: every step writes to blob and survives a backend restart; exports open.

---

## 10. Known limits, stated rather than hidden

- **Process diagrams are read as text.** A PDF or SVG process map yields labels and whatever
  adjacency the file encodes. Hand-drawn boxes in a PDF will produce steps without reliable
  ordering, and the UI says so at upload.
- **Both opportunity scores are model estimates.** They are calibrated by prompt and validated for
  range and sum, not measured. Anything client-facing needs the estimate labelled as one.
- **Headcount is optional.** FTE-equivalent ranking in Step 6 degrades to proportion-only ranking
  where the HRIS import carried no headcount column, which changes the ordering. Surfaced in the UI
  rather than silently assumed.
- **Agent generation is the most expensive step in either studio.** ~50KB of output per agent across
  hundreds of clusters. The threshold control and the cost preview pattern from the clustering gate
  should apply here too.
- **No Batch API.** Halving agent cost is available later; it needs durable job records first.
- **The graph's fact table is derived, not authored.** Re-running any JAStudio stage changes it, so
  it is versioned and recomputed rather than incrementally patched.

---

## 11. Decided without escalating

- Same app, same project state, same container — the alternative guarantees drift.
- JE is not part of the gate; the instructions exclude levelling from this studio.
- D3 rather than a new graph dependency, since the demo's renderer is proven and already styled.
- Actions are per task cluster, not per role — the instructions say "for every task cluster", and it
  keeps generation at ~N-clusters calls rather than N-roles × N-tasks.
- Roll-ups computed in code, never asked of the model, matching the JE and dedupe precedent.
- Each step is a `StageName` on the existing orchestrator rather than a new progress mechanism.
