# Tempris JAStudio

A job-architecture studio: ingest a client's job descriptions or HRIS extract and
work through to a browsable Job Family › Category › Profile hierarchy with job
profile documents, job evaluation, skills and task taxonomies, and a match into
a 3rd-party market taxonomy.

Implements the 11-step process in `../instructions.txt`.

---

## Quick start

From the repo root, double-click **`startApp.bat`** (or run it from a terminal).
It checks the conda env and `.env` are present, warns if a stale server is
already holding a port, starts backend and frontend in their own windows, waits
for both to answer, and opens a browser tab. **`stopApp.bat`** shuts them down —
strictly by listening port, never by process name, so unrelated Python work is
left alone.

Everything below is the manual equivalent.

## Running locally (recommended for development)

GPU passthrough into Docker on native Windows needs WSL2 plus
nvidia-container-toolkit and is fiddly. Embedding inference is the only
GPU-dependent part, so for day-to-day work run the backend natively.

**Backend** — needs the `jastudio-backend` conda env (Python 3.12, CUDA torch):

```bash
cd app/backend
"C:/Users/matt_/.conda/envs/jastudio-backend/python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 9400
```

Call the env's `python.exe` directly rather than `conda run`, which buffers
output until the process exits — you want to see the pipeline's progress lines.

`--reload` is unreliable here because the repo sits on a OneDrive-synced path and
the file watcher misses changes; restart the process after editing backend code.

**Frontend**:

```bash
cd app/frontend
npm install     # first time only
npm run dev
```

Then open <http://localhost:5173>. Vite proxies `/api` to port 9400, so both must
be running.

## Running with Docker

```bash
cd app
docker compose up --build                                        # CPU
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up # GPU host
```

Frontend on <http://localhost:8080>, backend on 9400. On CPU, embedding is slower
but functional — everything else is unaffected.

## Configuration

`app/backend/.env` (gitignored — it holds live credentials):

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | required |
| `ANTHROPIC_MODEL` | defaults to `claude-sonnet-5` |
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | service principal for blob storage |
| `AZURE_BLOB_ACCOUNT` | `temprisdev` |
| `EMBEDDING_DEVICE` | `cuda` or `cpu`; falls back to CPU automatically |
| `APP_PORT`, `CORS_ORIGINS`, `MAX_FILE_SIZE_MB` | server basics |
| `LLM_WORKERS` | how many LLM calls run concurrently in the per-item stages (default 8) |
| `LLM_MAX_WORKERS` | ceiling the API will accept for a per-run override (default 64) |

Also tunable there: `STABILITY_GATE` (default 0.58), `STABILITY_N_PERTURB` (50),
`SELF_CONSISTENCY_VOTES` (3), `CATCH_ALL_REVIEWERS` (5).

## Embedding models

The three fine-tuned Qwen3-0.6B models ship zipped in `../models/`. Install them
with:

```bash
cd app/backend
python -m scripts.prepare_embedding_models                    # all three
python -m scripts.prepare_embedding_models taskQWEN --force   # replace one
```

Two zip shapes are handled: a fully merged sentence-transformers directory
(~1.9GB), and a **LoRA adapter** (~40MB, `adapter_config.json` +
`adapter_model.safetensors`), which is merged into its base model at install time
so inference pays no adapter overhead. Merging needs `peft` and the base model
named in `adapter_config.json` — currently `Qwen/Qwen3-Embedding-0.6B`, pulled
from the HuggingFace cache or downloaded once.

**Replacing a model**: stop the backend first (`stopApp.bat`). A running server
holds the model's files open, and Windows will not let the directory be replaced
while it does. The installer moves the old copy aside before writing rather than
deleting in place, so a failed attempt changes nothing.

**Cached vectors are model-specific.** Each project stores its embeddings and Ward
tree in blob storage, stamped with the model build that produced them. Replacing
a model makes those stale — cosine similarity between vectors from two different
models is meaningless, and the failure would otherwise be silent, since
clustering still completes and still returns plausible groupings. Loading a stale
cache returns a 409 naming both versions; rebuild that entity's tree to clear it.
Only the affected entity is invalidated.

## Storage layout

Persistence is Azure Blob Storage. `temprisdev` is a **shared, live** account:
each client is its own container `client-<slug>`, and JAStudio writes only inside
a `job-architecture/` subtree per container. It must never touch the existing
`inputs/`, `runs/` or `taxonomy/` trees, nor the `state` / `global` containers.

`client-mercer-demo` is the safe sandbox for smoke tests.

Within the subtree: `state/current.json` is the materialised project state
(overwritten after each confirmed step); `lineage/` is an append-only log written
only for user-confirmed decisions; `artifacts/` caches embeddings, the Ward
linkage tree and raw LLM outputs so an interrupted run can recover without
re-paying for completed work.

## Using it

Pick a client and project, then work down the nine numbered stages. Each is
locked until its input exists, and collapses to a one-line summary once done.
Stages that cost real LLM spend are always an explicit button press.

**Input** accepts either individual job description files (PDF/DOC/DOCX/TXT/HTML,
one role per file) or a spreadsheet (XLSX/XLS/CSV) holding many roles, or both.
For a spreadsheet the app profiles the columns and proposes a mapping for job
title, description, level and headcount, showing its confidence and reasoning for
each; you confirm or change it. Headcount is optional and is the only source of
the headcount analytics later. Descriptions stored as HTML fragments (common in
job-board and ATS exports) are stripped to prose on import.

**Three things are user-defined** and all live in the stage that consumes them:

- *Job profile template* (step 7) — which sections a profile contains, their
  headings, their order, and the guidance the model gets per section. Sections
  come from a catalogue rather than being free-form, because the PDF and DocX
  renderers build from the same structured content as the HTML; each catalogue
  entry carries the shape all three know how to lay out. Disabling a section
  removes it from the generation schema entirely.
- *Job evaluation framework* (step 7) — domains, sub-factor weights, the 1-5
  rubric per sub-factor, and the level names each score band maps to. Weights
  validate live: domains sum to 100, and each domain's sub-factors sum to that
  domain's weight.
- *Proficiency template* (step 9) — the level scale and criteria that per-cluster
  proficiency wording is generated against. Edit before generating.

Saving any of these invalidates work already produced under the old version;
each editor says so before you commit, and affected artifacts are marked stale
rather than deleted.

### Speed of the per-item stages

Strip, normalise, skill and task inference, profile generation, evaluation and
taxonomy matching are all one LLM call per item, run through a thread pool. They
are latency-bound, not compute-bound: a strip call on a 4KB description takes
about 9 seconds almost entirely waiting on the API, so wall time is roughly
`ceil(items / workers) x 9s`.

The **Parallel requests** slider in the left sidebar sets that width for every
stage (persisted locally; `LLM_WORKERS` is the server-side default and applies
when a request omits it). Measured on 8 real descriptions:

| workers | 8 records | vs serial |
|---|---|---|
| 1 | 76s | 0.9× |
| 4 | 22s | 3.2× |
| 8 | 13s | 5.6× |

Scaling stays close to linear until the account's requests- or tokens-per-minute
limit is reached; past that the API returns 429s, which are retried with backoff,
so too high a value degrades into waiting rather than failing — but throughput
stops improving. Start at 8-16 and raise it if the account allows.

A wave is only as fast as its slowest item, which is why 8 workers on 8 records
gives 5.6× rather than 8× — one 6KB description gates the batch.

Two panels carry most of the method:

- **Cluster and name** — sliders re-cut a cached Ward tree, so previewing
  different cluster counts costs nothing. The stability gate decides how much
  gets sent to the model: items the geometry places confidently are free, and
  only the uncertain tail is routed. 0.55–0.60 is a good range; above 0.70 tends
  to pay for assignments the model just confirms.
- **3rd-party taxonomy match** — the structure view is the deliverable; the
  "Needs review" tab lists only uncertain matches, worst first, each with the
  shortlist it chose from and an override control. A profile with no defensible
  match is reported as a coverage finding rather than forced into the nearest
  bucket. Scoping to the client's industries sharpens matches.

The **Job architecture** section at the bottom is the combined output, and the
export bar there produces one multi-sheet workbook or per-dataset CSVs. The
`Input jobs` sheet carries the audit trail — backbone vs final cluster,
stability score, whether a model moved an item — which is what makes a contested
placement answerable.

## Verification scripts

`app/backend/scripts/` holds the checks used while building. The useful one:

```bash
cd app/backend
python -m scripts._e2e_full     # all 11 steps over real HTTP against a live backend
```

It creates a throwaway project under `mercer-demo`, runs the 7 sample JDs in
`../Legacy jaStudio/2. Job Profile/a.Before/` through every stage, and asserts on
the browsable outputs. Takes about 4-5 minutes and spends real LLM budget. The
`_test_*_offline.py` scripts need no API key or GPU.

## Known gaps

- Skills inference drifts on the 1-3 word name rule (roughly 20% of names run
  long). The audit line under the stage reports it; the prompt hasn't been tuned.
- The profile-template catalogue is fixed. You can rename, reorder, disable and
  re-guide the eleven sections but cannot invent a twelfth — a new section needs
  a shape the HTML, PDF and DocX renderers all understand.
- No REST job-status endpoint — the WebSocket is the only way to observe a
  running job. The page re-attaches via `active_job_id` after a refresh, but a
  silently dropped socket has no polling fallback.
- The critique and catch-all review passes exist in the clustering engine but
  have no UI trigger yet.
