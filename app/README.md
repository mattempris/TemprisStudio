# Tempris JAStudio

A job-architecture studio: ingest a client's job descriptions or HRIS extract and
work through to a browsable Job Family › Category › Profile hierarchy with job
profile documents, job evaluation, skills and task taxonomies, and a match into
a 3rd-party market taxonomy.

Implements the 11-step process in `../instructions.txt`.

---

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

Also tunable there: `STABILITY_GATE` (default 0.58), `STABILITY_N_PERTURB` (50),
`SELF_CONSISTENCY_VOTES` (3), `CATCH_ALL_REVIEWERS` (5).

**One-time setup**: the three fine-tuned Qwen embedding models ship zipped in
`../models/`. Unzip them into place with:

```bash
cd app/backend
python -m scripts.prepare_embedding_models jobQWEN skillQWEN taskQWEN
```

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
- No REST job-status endpoint — the WebSocket is the only way to observe a
  running job. The page re-attaches via `active_job_id` after a refresh, but a
  silently dropped socket has no polling fallback.
- The critique and catch-all review passes exist in the clustering engine but
  have no UI trigger yet.
