# Repeatable steps, and a fast demo project — plan

Two requirements, one shared root cause. Everything below is measured on
`banking-demo/full-ja` on 2026-08-04, not estimated.

---

## 1. What is actually slow

The state blob is the whole story.

| Blob | Size |
|---|---|
| `state/current.json` | **42.5 MB** |
| `artifacts/skill_embeddings.npy` | 23.0 MB |
| `artifacts/task_embeddings.npy` | 21.3 MB |
| `inputs/raw/banking jobs.csv` | 6.9 MB |
| everything else (1,203 blobs) | ~49 MB |

**Downloading and parsing `state/current.json` takes 14.7 seconds.** Almost every
endpoint in the app calls `_load`, which does exactly that. That is the unreliability:
not the model calls, the state read.

What is inside it:

| Section | Size | Note |
|---|---|---|
| `job_profiles` | 8.16 MB | of which `.html` is **5.52 MB** — and it is *already* saved separately by `save_profile_html` |
| `skills` | 5.99 MB | 5,605 inferred skills |
| `tasks` | 5.84 MB | 5,193 inferred tasks |
| `raw_records` | 5.62 MB | full source text of 1,170 job descriptions |
| `stripped_records` | 3.77 MB | full stripped text |
| `je_results` | 2.62 MB | 565 × 3 personas × 20 subfactors |
| `workforce` | 1.37 MB | 3,805 actions dominate |
| `clustering_tiers` | 1.27 MB | |

Two things follow. First, roughly **20 MB of that is duplicated or only needed by the
stage that produced it** — rendered profile HTML, raw and stripped source text. Second,
this is a *product* problem, not only a demo problem: it grows with every project and it
is already at 15 seconds.

The second cost is tier rebuilds. `_items_and_tree` recomputes the Ward linkage every
time and caches it **in process memory only**, so after any restart re-clustering means
downloading 21–23 MB of vectors and recomputing the tree. `TierState.linkage_blob_path`
exists in the model and is never written.

---

## 2. Requirement 1 — repeat any step, invalidate what follows

### What already works

- **Within a hierarchy**, re-confirming a tier drops the coarser tiers above it.
  `save_tier` does this deliberately and documents why.
- **The tier UI already supports repeating**: a confirmed tier shows "Rebuild to
  re-cluster", then the full tile/slider/gate UI, then "Re-cluster and rename".
- Per-artifact `stale` flags exist on `job_profiles` and `je_results` and are honoured
  by the profile list, the overview, matching and the pipeline summary.

So "users can't update the clustering again" is not quite the problem. The problem is
that doing so needs a rebuild, and a rebuild is a 23 MB download plus a Ward recompute
that is only cached in memory — slow enough that in a demo it reads as broken.

### What is missing

1. **The cross-stage cascade covers one step out of twenty-six.** `_invalidate_from` in
   `pipeline.py` is called from exactly one place — dedupe confirmation — and its stage
   list is `dedupe → normalize → cluster → profiles`, which predates per-tier clustering,
   skills, tasks, matching and all seven Workforce Studio steps. So re-running dedupe
   cleared the normalised profiles and the old flat clustering, and left the per-tier
   hierarchies, both taxonomies, the matches, the opportunity assessment, four agent specs
   and the graph all describing records that no longer existed. Re-running anything else
   invalidated nothing at all.

   *(An earlier draft of this plan said the function was never called from anywhere. That
   was wrong — I had grepped for the wrong name. The consequence is the same but the
   statement was not accurate.)*
2. **No dependency graph.** Each route knows its own preconditions; nothing knows what
   *depends on it*.
3. **No notification.** Nothing tells the user what a repeat invalidated.
4. **`stale` is not modelled for most artifacts.** Skills, tasks, tiers, matching,
   actions, agents, processes and future roles have no staleness concept at all.

### Design

**One declared dependency graph**, in a new `app/services/lineage.py`, listing every
stage and what it consumes:

```
ingest → strip → dedupe → normalize
normalize → job:profile → job:category → job:family → profiles → evaluation
profiles → skills:infer → skill:profile → skill:category → skill:family → proficiency
profiles → tasks:infer → task:profile → task:category → task:family
profiles → matching
task:family + profiles → workforce:graph → opportunity → {augmentation, automation, future-roles}
processes → process-opportunity
```

Declared once as data, so adding a stage means adding one row rather than remembering
nine call sites.

**Two invalidation verbs, chosen per artifact:**

- **Clear** — the artifact is meaningless without its input and keeping it would let
  something read it by accident. Clustering when its embeddings changed; the graph fact
  table, which is derived and cheap to rebuild.
- **Mark stale** — the artifact is expensive and worth keeping for lineage and
  comparison. Profiles, evaluations, agents, skill files, future roles. Already the
  pattern for `job_profiles`.

The distinction is not cosmetic: a stale profile is still readable and still exportable
with a warning, while a cleared clustering must not be readable at all.

**Confirm before, notify after.** Repeating a step that has descendants opens a dialog
naming exactly what will be invalidated and how much of it — *"Re-running dedupe will
clear the job hierarchy and mark 565 profiles, 565 evaluations, 2 taxonomies and 4
agents stale."* After the run, a persistent banner lists what went stale, with a link
per affected step. The counts come from the same graph, so the warning cannot drift from
the behaviour.

**Persist the linkage tree.** Write it to the `linkage_blob_path` the model already
declares, so a rebuild is a download rather than a recompute, and survives a restart.

### Decisions to settle

- **Confirm-then-invalidate, or invalidate-then-notify?** I would confirm first for
  anything that clears, and notify only for anything that marks stale. Wrong guess here
  is either an unskippable dialog on every click or silent data loss.
- **Can a stale step be re-run out of order?** Strictly in order is simpler to reason
  about; out of order is what a demo actually wants ("just regenerate the profiles").
- **Do stale artifacts stay exportable?** I would say yes, badged, because a client
  deliverable that silently omits 565 profiles is worse than one that labels them.

---

## 3. Requirement 2 — an FS-Demo project that loads fast

### The constraint to check first

`BlobProjectStore`'s own notes record that the service principal **cannot create
containers** — `client-<slug>` creation fails with `AuthorizationFailure` and needs a
Storage Blob Data Contributor grant at account scope. So a genuinely new
`client-fs-demo` container needs an admin action first. Three ways round it:

1. **`banking-demo/fs-demo`** — a new *project* in the container that already exists.
   Works today, no admin needed. Loses the "different client" framing.
2. **`client-fs-demo`** — needs the RBAC grant. Cleanest naming.
3. **Virtual project, never in blob at all** — served entirely from a local cache. Works
   today, cannot touch production data, and makes reset trivial.

### Three architectures

**A. Snapshot replay.** Capture the full state after each step; a "run" swaps in the
next snapshot after a delay. Fastest to build, most robust, and completely on rails —
the cluster-count slider, the dedupe threshold and the stability gate become decorations,
because nothing recomputes. Given requirement 1 is *about* interacting with those
controls, this defeats the point.

**B. LLM cache only.** Keep every code path; intercept `llm.complete*` and serve from a
cache seeded from the real run. Controls stay live. But embeddings and Ward still run, so
tier rebuilds stay slow, and the 14.7-second state read is untouched.

**C. Local mirror plus LLM cache — recommended.** Three separate caches, each fixing a
measured cost:

| Cache | Fixes | Mechanism |
|---|---|---|
| State + artifacts on local disk | the 14.7s read | `BlobProjectStore` gains a read-through local mirror for this project; writes go to disk only |
| LLM responses | model latency and spend | keyed on `(stage, item id)`, not prompt hash, so prompt edits still hit |
| Ward linkage on disk | tier rebuild | persist what is currently memory-only; benefits the real product too |

Everything still executes. Moving the cluster slider re-cuts a cached tree in
milliseconds, which is already how preview works. Re-running dedupe genuinely recomputes
union-find over cached vectors. The stability gate genuinely re-routes, against cached
routing decisions. So requirement 1 is demonstrable rather than mimed.

Keying the LLM cache on `(stage, item id)` rather than a prompt hash is the important
detail: it means the demo still works after a prompt is edited, which a hash-keyed cache
would silently break at the worst moment.

### Deliberate latency

Real work becomes fast enough to look fake, so each stage gets a demo duration and the
progress bar is driven by it: a per-stage table of seconds, ticked in steps so the
counter and the item count both move. Sized from what the real run took, divided by
about ten — long enough to read the phase message, short enough not to stall a meeting.
Configurable in one place, not sprinkled through the stages.

### Reset

A demo that can only be run once is not a demo. `POST /demo/reset` restores the local
mirror to its seeded starting point, so the whole journey can be re-run from ingestion.
This is also the cheapest possible test of requirement 1's cascade.

### Seeding

One script, `scripts/seed_demo_project.py`, reads `banking-demo/full-ja` once and writes
the local mirror plus the LLM cache. Run per machine, gitignored, ~165 MB. It is a copy
of real output, so nothing is fabricated — the demo shows results the pipeline genuinely
produced.

### Labelling

The demo project carries a visible badge saying results are replayed from a previous run.
Not for the sales narrative but because the app otherwise says "Assessing 750 task
clusters" while reading a file, and someone will eventually believe a client's own data
was processed.

### Decisions to settle

- **Which of the three homes** — new project in the existing container, new container
  with an admin grant, or virtual and local-only. I lean virtual: no admin dependency,
  no possibility of writing to production, trivial reset.
- **Does the demo need to work on a machine that has never seen the real project?** If
  yes, the seed has to be committed or downloadable, and 165 MB does not belong in git.
- **Should the state-blob slimming happen for the real product too?** It is the same
  work as the local mirror and would take the real app from 14.7s to an estimated 2–3s.

---

## 4. Should the state blob be fixed properly?

Separate from both requirements, and the highest-value item on this page.

Move out of `state/current.json`, each to its own blob, loaded on demand:

- `job_profiles[].html` — 5.5 MB, already written separately, pure duplication
- `raw_records[].raw_text` and `stripped_records[].stripped_text` — 9.4 MB, needed by
  the stages that produce them and by nothing downstream
- `skills.inferred`, `tasks.inferred`, `workforce.actions` — 4.5 MB, read by specific
  screens rather than by every request
- `je_results[].personas` — 2.6 MB, read only by the JE drawer

That is roughly **22 MB of 42.5 MB**, and it is the part that grows fastest with project
size. Estimated state read afterwards: 2–3 seconds, without any caching at all.

The cost is that every consumer of those fields needs an explicit load, and
`ProjectState` stops being one object that contains everything — which is a real loss of
simplicity and the reason it was not done this way originally.

---

## 5. Phasing

**Phase 1 — the dependency graph and the cascade.** `lineage.py`, wire it into every
re-runnable route, per-artifact staleness, the confirm dialog and the banner. No demo
work. Delivers requirement 1 on the real project.

**Phase 2 — persist the linkage tree and slim the state blob.** Both are product fixes
that the demo then inherits. Biggest single win for perceived speed.

**Phase 3 — the demo mirror.** Local read-through cache, LLM cache keyed on stage and
item, seeding script, reset endpoint.

**Phase 4 — pacing and polish.** Per-stage demo durations, the replay badge, a full
end-to-end rehearsal of the journey including a mid-journey repeat.

---

## 6. Verification

- Every stage in the graph has a test asserting what it invalidates, so the dialog's
  counts and the actual behaviour come from one source.
- Re-running dedupe on a seeded demo leaves no readable artifact that describes deleted
  data — asserted, not eyeballed.
- The demo journey runs twice from a single reset without a restart.
- State read time measured before and after phase 2, against the 14.7s baseline.
- A prompt edited after seeding still hits the LLM cache.
- No demo path writes to `client-banking-demo`.

---

## 7. Also worth knowing

`workforce/backups/state-before-orphan-repair.json` is 42.45 MB — the safety copy I took
before the task-cluster repair. It has served its purpose now that the repair is verified
and can be deleted, and it should not become a habit at this blob size.
