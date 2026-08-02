"""Phase 1 pipeline routes — instructions.txt steps 1-7.

Route-shape convention, following the plan's design:
  - Long-running compute (LLM fan-outs, bootstrap stability) returns a job_id
    immediately and streams progress over /ws/pipeline/{job_id}.
  - Interactive decisions (dedupe threshold, cluster k) are plain synchronous
    endpoints, because they recompute from cached artifacts in milliseconds and
    are called on every slider drag.
  - Every user-confirmed decision writes state + a lineage entry; intermediate
    compute does not.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.models.project_state import (
    ClusteringState,
    ColumnMapping,
    DedupeGroup,
    ItemAssignmentRecord,
    JEEvaluationResult,
    JEFrameworkConfig,
    JobProfileDoc,
    JobProfileTemplateConfig,
    JobRecordRaw,
    JobRecordStripped,
    NormalizedProfile,
    ProjectState,
    RawInputFile,
    StageName,
)
from app.services import dedupe as dedupe_svc
from app.services import llm, normalization, stripping
from app.services.clustering import backbone as bb
from app.services.clustering import engine as cluster_engine
from app.services.clustering import naming, rollup, routing
from app.services import embeddings
from app.services.embeddings import get_embedding_service
from app.services.evaluation import job_evaluation as je
from app.services.ingestion.column_mapping import suggest_mapping
from app.services.ingestion.hris import load_spreadsheet
from app.services.ingestion import parsers
from app.services.ingestion.parsers import ParseFailed, UnsupportedFileType, extract_text
from app.services.job_profile import exporters, generator
from app.services.job_profile import template_config as tpl
from app.services.orchestrator import (
    JobAlreadyRunning,
    ProgressReporter,
    get_registry,
    run_job,
)
from app.services.project_service import ProjectService, framework_hash

router = APIRouter(prefix="/api/projects/{client_slug}/{project_slug}", tags=["pipeline"])


def _svc() -> ProjectService:
    return ProjectService()


def _load(client_slug: str, project_slug: str) -> tuple[ProjectService, ProjectState]:
    svc = _svc()
    try:
        return svc, svc.load_state(client_slug, project_slug)
    except LookupError as e:
        raise HTTPException(404, f"project not found: {client_slug}/{project_slug}") from e


def _start_job(client_slug: str, project_slug: str, stage: str, work) -> dict:
    registry = get_registry()
    try:
        job = registry.create(client_slug, project_slug, stage)
    except JobAlreadyRunning as e:
        raise HTTPException(
            409,
            {"message": str(e), "existing_job_id": e.job.job_id, "existing_stage": e.job.stage},
        ) from e

    asyncio.create_task(run_job(job, work))
    return {"job_id": job.job_id, "stage": stage, "websocket_url": f"/ws/pipeline/{job.job_id}"}


# ===========================================================================
# State inspection
# ===========================================================================
@router.get("/state")
def get_state(client_slug: str, project_slug: str) -> ProjectState:
    _, state = _load(client_slug, project_slug)
    return state


class StageSummary(BaseModel):
    """Compact per-stage status for the wizard's collapsed section summaries —
    avoids shipping the whole ProjectState just to render the step list."""

    raw_records: int = 0
    stripped_records: int = 0
    dedupe_threshold: float | None = None
    dedupe_groups: int = 0
    # Whether the dedupe embeddings exist. The threshold preview needs them, and
    # without this the UI had no way to know a build had finished — it kept
    # showing the "not built yet" error it got before the build ran.
    dedupe_embeddings_ready: bool = False
    normalized_profiles: int = 0
    clustered: bool = False
    k_families: int | None = None
    k_categories: int | None = None
    k_profiles: int | None = None
    named: bool = False
    job_profiles: int = 0
    je_results: int = 0
    current_stage: str = "ingest"
    active_job_id: str | None = None
    active_job_stage: str | None = None


def _dedupe_ready(
    svc: ProjectService, client_slug: str, project_slug: str, state: ProjectState
) -> bool:
    """Whether the dedupe embeddings exist, answering locally where possible.

    The summary is polled, so a blob HEAD on every call is worth avoiding — it
    roughly doubled the endpoint's latency. Two cheap answers cover almost every
    real case:

      - the graph is in this process's cache, so it was built this session
      - dedupe has been confirmed, which cannot have happened without them

    Only a cold process with unconfirmed dedupe pays for the round-trip, which is
    exactly the state where the answer is genuinely unknown. If the blob were
    deleted after confirmation this would say True wrongly, and the preview would
    then 409 — an acceptable trade for not charging every poll.
    """
    if (client_slug, project_slug) in _GRAPH_CACHE:
        return True
    if state.dedupe_groups:
        return True
    if not state.stripped_records:
        return False  # nothing could have been embedded yet
    return svc.array_exists(client_slug, project_slug, "dedupe_embeddings")


@router.get("/summary")
def get_summary(client_slug: str, project_slug: str) -> StageSummary:
    svc, state = _load(client_slug, project_slug)
    active = get_registry().active_for_project(client_slug, project_slug)
    c = state.clustering
    return StageSummary(
        raw_records=len(state.raw_records),
        stripped_records=len(state.stripped_records),
        dedupe_threshold=state.dedupe_threshold,
        dedupe_groups=len(state.dedupe_groups),
        dedupe_embeddings_ready=_dedupe_ready(svc, client_slug, project_slug, state),
        normalized_profiles=len(state.normalized_profiles),
        clustered=c is not None,
        k_families=c.k_families if c else None,
        k_categories=c.k_categories if c else None,
        k_profiles=c.k_profiles if c else None,
        named=bool(c and c.profile_names),
        job_profiles=len([p for p in state.job_profiles if not p.stale]),
        je_results=len([r for r in state.je_results if not r.stale]),
        current_stage=state.meta.current_stage.value,
        active_job_id=active.job_id if active else None,
        active_job_stage=active.stage if active else None,
    )


@router.get("/embedding-models")
def list_embedding_models(client_slug: str, project_slug: str) -> dict:
    """Selectable embedding models per entity, with what is installed.

    Only the job slot has a choice today. Both job models emit 1024 dims, so the
    UI cannot infer incompatibility from shape — `current` is what a run will use
    unless overridden, and switching invalidates cached job embeddings.
    """
    svc_emb = get_embedding_service()
    out: dict[str, dict] = {}
    for entity in ("job", "skill", "task"):
        current = embeddings.resolve_model(entity)  # type: ignore[arg-type]
        out[entity] = {
            "current": current.name,
            "selectable": len(embeddings.models_for(entity)) > 1,  # type: ignore[arg-type]
            "models": [
                {
                    "name": m.name,
                    "dim": m.dim,
                    "note": m.note,
                    "installed": svc_emb.is_ready(entity, m.name),  # type: ignore[arg-type]
                    "loaded": embeddings.is_loaded(entity, m.name),  # type: ignore[arg-type]
                }
                for m in embeddings.models_for(entity)  # type: ignore[arg-type]
            ],
        }
    return out


# ===========================================================================
# Step 0 — ingestion
# ===========================================================================
@router.post("/ingest/files")
async def ingest_files(client_slug: str, project_slug: str, files: list[UploadFile]) -> dict:
    svc, state = _load(client_slug, project_slug)
    settings = get_settings()
    max_bytes = settings.max_file_size_mb * 1024 * 1024

    added, errors = [], []
    for upload in files:
        data = await upload.read()
        if len(data) > max_bytes:
            errors.append({"filename": upload.filename, "error": f"exceeds {settings.max_file_size_mb}MB"})
            continue
        try:
            text = extract_text(upload.filename or "unnamed", data)
        except (UnsupportedFileType, ParseFailed) as e:
            errors.append({"filename": upload.filename, "error": str(e)})
            continue

        blob_path, digest = svc.save_input_file(client_slug, project_slug, upload.filename or "unnamed", data)
        file_id = f"file-{uuid.uuid4().hex[:8]}"
        state.inputs.append(
            RawInputFile(
                id=file_id,
                filename=upload.filename or "unnamed",
                blob_path=blob_path,
                kind="jd_file",
                uploaded_at=datetime.now(timezone.utc),
                content_hash=digest,
            )
        )
        record_id = f"rec-{uuid.uuid4().hex[:8]}"
        state.raw_records.append(
            JobRecordRaw(
                id=record_id,
                source_file_id=file_id,
                job_title=_title_from_filename(upload.filename or "unnamed"),
                raw_text=text,
            )
        )
        added.append({"file_id": file_id, "record_id": record_id, "filename": upload.filename, "chars": len(text)})

    if added:
        svc.save_state(state, action="ingest-files", lineage_payload={"added": added, "errors": errors})
    return {"added": added, "errors": errors, "total_records": len(state.raw_records)}


def _title_from_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ").strip() or filename


@router.post("/ingest/hris/preview")
async def ingest_hris_preview(
    client_slug: str, project_slug: str, file: UploadFile, header_row: int = 0
) -> dict:
    """Upload a spreadsheet and get the AI-suggested column mapping back for the
    user to confirm (instructions.txt: 'AI estimate / user confirm')."""
    svc, state = _load(client_slug, project_slug)
    data = await file.read()
    try:
        loaded = load_spreadsheet(file.filename or "sheet.xlsx", data, header_row=header_row)
    except (ValueError, Exception) as e:
        raise HTTPException(400, f"could not read spreadsheet: {e}") from e

    blob_path, digest = svc.save_input_file(client_slug, project_slug, file.filename or "sheet.xlsx", data)
    file_id = f"file-{uuid.uuid4().hex[:8]}"
    state.inputs.append(
        RawInputFile(
            id=file_id,
            filename=file.filename or "sheet.xlsx",
            blob_path=blob_path,
            kind="hris",
            uploaded_at=datetime.now(timezone.utc),
            content_hash=digest,
        )
    )
    svc.save_state(state, action="ingest-hris", lineage_payload={"file_id": file_id, "rows": loaded.row_count})

    suggestion = suggest_mapping(loaded.profiles, loaded.row_count)
    return {
        "file_id": file_id,
        "row_count": loaded.row_count,
        "columns": loaded.columns,
        "preview": loaded.preview,
        "suggested_mapping": {
            "job_title_col": suggestion.job_title_col,
            "job_description_col": suggestion.job_description_col,
            "job_level_col": suggestion.job_level_col,
            "headcount_col": suggestion.headcount_col,
            "confidence": suggestion.confidence,
            "reasoning": suggestion.reasoning,
        },
    }


class ConfirmMappingRequest(BaseModel):
    file_id: str
    job_title_col: str
    job_description_col: str | None = None
    job_level_col: str | None = None
    headcount_col: str | None = None
    header_row: int = 0
    # Every ingested row costs a strip call and (post-dedupe) a normalize call, so
    # a large export commits real spend. This lets a user trial a subset first
    # rather than finding out afterwards.
    limit: int | None = Field(default=None, ge=1)


def _cell(row, col: str | None) -> str | None:
    """Read a cell as clean text, or None.

    `str(row[col]).strip()` is wrong here: a pandas NaN stringifies to "nan",
    which is truthy, so empty cells would be ingested as the literal text "nan" —
    blank titles becoming real records and blank descriptions becoming the word
    "nan" for the LLM to strip and normalise.
    """
    if not col:
        return None
    value = row[col]
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


@router.post("/ingest/hris/confirm")
def ingest_hris_confirm(client_slug: str, project_slug: str, req: ConfirmMappingRequest) -> dict:
    svc, state = _load(client_slug, project_slug)
    source = next((f for f in state.inputs if f.id == req.file_id), None)
    if source is None:
        raise HTTPException(404, f"unknown file_id {req.file_id}")

    data = svc.read_input_file(client_slug, source.blob_path)
    if data is None:
        raise HTTPException(404, "uploaded file no longer present in storage")

    loaded = load_spreadsheet(source.filename, data, header_row=req.header_row)
    df = loaded.df
    for col in filter(None, [req.job_title_col, req.job_description_col, req.job_level_col, req.headcount_col]):
        if col not in df.columns:
            raise HTTPException(400, f"column '{col}' not present in the sheet")

    added = 0
    skipped_no_title = 0
    for idx, row in df.iterrows():
        title = _cell(row, req.job_title_col)
        if not title:
            skipped_no_title += 1
            continue
        description = _cell(row, req.job_description_col)
        # Job-board and ATS exports routinely store the description as an HTML
        # fragment in the cell. Strip it here so downstream stages see prose,
        # matching what the .html file parser already does.
        if description and parsers.looks_like_html(description):
            description = parsers.clean_html_text(description) or description
        state.raw_records.append(
            JobRecordRaw(
                id=f"rec-{uuid.uuid4().hex[:8]}",
                source_file_id=req.file_id,
                source_row_index=int(idx),
                job_title=title,
                # With no description column the title is all we have. That's the
                # titles-only case the methodology package addresses by generating
                # a synthetic profile — normalization handles it downstream.
                raw_text=description or title,
                level_raw=_cell(row, req.job_level_col),
                headcount=_safe_int(row[req.headcount_col]) if req.headcount_col else None,
            )
        )
        added += 1
        if req.limit and added >= req.limit:
            break

    state.column_mapping = ColumnMapping(
        job_title_col=req.job_title_col,
        job_description_col=req.job_description_col,
        job_level_col=req.job_level_col,
        headcount_col=req.headcount_col,
        user_confirmed=True,
    )
    svc.save_state(
        state,
        action="confirm-column-mapping",
        lineage_payload={
            "file_id": req.file_id,
            "records_added": added,
            "rows_in_sheet": len(df),
            "limit": req.limit,
            "mapping": state.column_mapping.model_dump(),
        },
    )
    return {
        "records_added": added,
        "total_records": len(state.raw_records),
        "rows_in_sheet": len(df),
        "skipped_no_title": skipped_no_title,
        "limited": bool(req.limit and added >= req.limit and len(df) > added),
    }


class BoilerplateRequest(BaseModel):
    """Project-level fixed text for the profile documents (step 7's "default
    boiler plate documents"). Null leaves the field unset; empty string clears it."""

    client_company_description: str | None = None
    diversity_statement: str | None = None
    accent_color: str | None = None


@router.get("/boilerplate")
def get_boilerplate(client_slug: str, project_slug: str) -> dict:
    _, state = _load(client_slug, project_slug)
    return {
        "client_company_description": state.meta.client_company_description,
        "diversity_statement": state.meta.diversity_statement,
        "accent_color": state.meta.accent_color,
    }


@router.put("/boilerplate")
def put_boilerplate(client_slug: str, project_slug: str, req: BoilerplateRequest) -> dict:
    """Editable after creation, not only at it.

    Step 1 deliberately strips company blurb and equality statements out of the
    source job descriptions, so the generated documents have nowhere else to get
    them from. Existing profiles are re-rendered rather than marked stale: this is
    fixed text around the content, so no LLM call is needed to apply it.
    """
    svc, state = _load(client_slug, project_slug)
    if req.client_company_description is not None:
        state.meta.client_company_description = req.client_company_description or None
    if req.diversity_statement is not None:
        state.meta.diversity_statement = req.diversity_statement or None
    if req.accent_color is not None:
        state.meta.accent_color = req.accent_color

    sections = _resolve_sections(state)
    section_headings = tpl.headings(sections)
    je_by_key = {r.profile_key: r for r in state.je_results}
    for doc in state.job_profiles:
        doc.html = generator.render_html(
            doc.content,
            accent_color=state.meta.accent_color,
            company_name=state.meta.display_name,
            about_company=state.meta.client_company_description,
            diversity_statement=state.meta.diversity_statement,
            job_level=je_by_key[doc.profile_key].level_name if doc.profile_key in je_by_key else None,
            headings=section_headings,
            sections=sections,
        )

    svc.save_state(
        state,
        action="update-boilerplate",
        lineage_payload={
            "has_company_description": bool(state.meta.client_company_description),
            "has_diversity_statement": bool(state.meta.diversity_statement),
            "accent_color": state.meta.accent_color,
            "profiles_rerendered": len(state.job_profiles),
        },
    )
    return {"saved": True, "profiles_rerendered": len(state.job_profiles)}


@router.get("/profile-template")
def get_profile_template(
    client_slug: str, project_slug: str, defaults: bool = False
) -> dict:
    """Step 7's job profile template: which sections a profile has, what each is
    called, and the guidance the model gets for it.

    Returns the catalogue alongside the config so the editor can show what each
    section is for and which ones cannot be removed, without hardcoding it.
    """
    _, state = _load(client_slug, project_slug)
    sections = tpl.default_sections() if defaults else _resolve_sections(state)
    return {
        "sections": [
            {"key": s.key, "heading": s.heading, "include": s.include, "guidance": s.guidance}
            for s in sections
        ],
        "catalogue": [
            {
                "key": spec.key,
                "default_heading": spec.default_heading,
                "shape": spec.shape,
                "description": spec.description,
                "default_guidance": spec.default_guidance,
                "removable": spec.removable,
            }
            for spec in tpl.CATALOGUE
        ],
    }


@router.put("/profile-template")
def put_profile_template(
    client_slug: str, project_slug: str, config: JobProfileTemplateConfig
) -> dict:
    svc, state = _load(client_slug, project_slug)
    sections = [
        tpl.SectionConfig(key=s.key, heading=s.heading, include=s.include, guidance=s.guidance)
        for s in config.sections
    ]
    problems = tpl.validate(sections)
    if problems:
        raise HTTPException(
            422, {"message": "job profile template is not valid", "problems": problems}
        )

    state.profile_template = config
    # Existing profiles were generated against the previous section set, so they
    # no longer match this template. Marked stale rather than deleted — they keep
    # their content until regenerated.
    stale = 0
    for doc in state.job_profiles:
        if not doc.stale:
            doc.stale = True
            stale += 1

    svc.save_state(
        state,
        action="update-profile-template",
        lineage_payload={
            "sections": [s.key for s in sections if s.include],
            "profiles_marked_stale": stale,
        },
    )
    return {"saved": True, "profiles_marked_stale": stale}


def _resolve_sections(state: ProjectState) -> list[tpl.SectionConfig]:
    """The project's profile-template sections, or the shipped default set.

    A project only stores `profile_template` once the user edits it, so an empty
    list means "never customised" rather than "no sections".
    """
    saved = state.profile_template.sections
    if not saved:
        return tpl.default_sections()
    return [
        tpl.SectionConfig(key=s.key, heading=s.heading, include=s.include, guidance=s.guidance)
        for s in saved
    ]


def _safe_int(value) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


# ===========================================================================
# Step 1 — strip
# ===========================================================================
@router.post("/strip")
async def start_strip(
    client_slug: str, project_slug: str, workers: int | None = None
) -> dict:
    svc, state = _load(client_slug, project_slug)
    _workers = llm.resolve_workers(workers)
    if not state.raw_records:
        raise HTTPException(400, "no records to strip — ingest files or a spreadsheet first")

    records = [(r.job_title, r.raw_text) for r in state.raw_records]
    record_ids = [r.id for r in state.raw_records]

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(len(records), f"Stripping boilerplate from {len(records)} job descriptions")
        results = stripping.strip_many(records, workers=_workers, progress=reporter.pmap_callback())

        fresh = svc.load_state(client_slug, project_slug)
        fresh.stripped_records = [
            JobRecordStripped(
                id=rid,
                stripped_text=res.stripped_text,
                removed_sections=res.removed_sections,
                model=get_settings().anthropic_model,
                generated_at=datetime.now(timezone.utc),
            )
            for rid, res in zip(record_ids, results)
        ]
        fresh.meta.current_stage = StageName.strip
        svc.save_state(fresh, action="strip", lineage_payload={"records": len(results)})

        low_fidelity = [
            {"record_id": rid, "fidelity": round(res.extractive_fidelity, 3)}
            for rid, res in zip(record_ids, results)
            if res.fidelity_warning
        ]
        summary = {
            "records": len(results),
            "mean_retained_pct": round(
                100 * sum(len(r.stripped_text) for r in results) / max(1, sum(len(t) for _, t in records)), 1
            ),
            "mean_extractive_fidelity": round(sum(r.extractive_fidelity for r in results) / len(results), 3),
            "low_fidelity_records": low_fidelity,
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "strip", work)


# ===========================================================================
# Step 2 — dedupe
# ===========================================================================
_GRAPH_CACHE: dict[tuple[str, str], tuple[dedupe_svc.SimilarityGraph, np.ndarray]] = {}


def _get_dedupe_graph(
    svc: ProjectService, state: ProjectState
) -> tuple[dedupe_svc.SimilarityGraph, np.ndarray]:
    """Similarity graph, from the in-process cache or rebuilt from persisted
    embeddings. Rebuilding is a matrix multiply, not GPU inference, so a cold
    cache costs milliseconds rather than re-embedding."""
    client, project = state.meta.client_slug, state.meta.project_slug
    key = (client, project)
    if key in _GRAPH_CACHE:
        return _GRAPH_CACHE[key]

    ids = [r.id for r in state.stripped_records]
    emb = svc.load_array(client, f"{project}/artifacts/dedupe_embeddings.npy")
    if emb is None:
        raise HTTPException(409, "embeddings not built yet — run the dedupe build step first")
    graph = dedupe_svc.build_similarity_graph(ids, emb)
    _GRAPH_CACHE[key] = (graph, emb)
    return graph, emb


@router.post("/dedupe/build")
async def start_dedupe_build(
    client_slug: str,
    project_slug: str,
    device: str | None = None,
    embedding_model: str | None = None,
) -> dict:
    """Embed stripped records once; the threshold slider then works off the cache."""
    svc, state = _load(client_slug, project_slug)
    if not state.stripped_records:
        raise HTTPException(400, "no stripped records — run the strip stage first")

    try:
        spec = embeddings.resolve_model("job", embedding_model)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    texts = [r.stripped_text for r in state.stripped_records]
    ids = [r.id for r in state.stripped_records]

    def work(reporter: ProgressReporter) -> dict:
        svc_emb = get_embedding_service()
        if not embeddings.is_loaded("job", embedding_model):
            reporter.message(f"Loading the {spec.name} model (first use this session)")
            svc_emb.warm("job", device, embedding_model)
        reporter.stage_start(len(texts), f"Embedding {len(texts)} records with {spec.name}")
        emb = svc_emb.embed_documents(
            "job", texts, device=device, model=embedding_model,
            progress=lambda done, total: reporter.progress(done, total, "embedded")
        )
        reporter.message("Computing the similarity graph")

        svc.save_array(client_slug, project_slug, "dedupe_embeddings", emb)
        svc.save_index(client_slug, project_slug, "dedupe_embeddings", ids)
        graph = dedupe_svc.build_similarity_graph(ids, emb)
        _GRAPH_CACHE[(client_slug, project_slug)] = (graph, emb)

        summary = {"records": len(texts), "candidate_pairs": graph.pair_count()}
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "dedupe", work)


@router.get("/dedupe/preview")
def dedupe_preview(client_slug: str, project_slug: str, threshold: float = 0.9) -> dict:
    """Cheap enough to call on every slider drag — no embedding, no matmul."""
    svc, state = _load(client_slug, project_slug)
    graph, emb = _get_dedupe_graph(svc, state)
    summary = dedupe_svc.summarize(graph, threshold, embeddings=emb)

    titles = {r.id: r.job_title for r in state.raw_records}
    return {
        "threshold": threshold,
        "total_items": summary.total_items,
        "group_count": summary.group_count,
        "duplicate_group_count": summary.duplicate_group_count,
        "items_merged_away": summary.items_merged_away,
        "groups": [
            {
                "group_id": g.group_id,
                "member_ids": g.member_ids,
                "member_titles": [titles.get(m, m) for m in g.member_ids],
                "representative_id": g.representative_id,
                "avg_similarity": round(g.avg_similarity, 4),
                "member_similarities": {k: round(v, 4) for k, v in g.member_similarities.items()},
            }
            # only groups with duplicates are interesting in the review UI;
            # singletons are implied by the counts
            for g in summary.groups
            if len(g.member_ids) > 1
        ],
    }


class ConfirmDedupeRequest(BaseModel):
    threshold: float = Field(ge=0.0, le=1.0)
    # optional manual overrides: explicit final groups, if the user split/merged
    groups: list[list[str]] | None = None


@router.post("/dedupe/confirm")
def confirm_dedupe(client_slug: str, project_slug: str, req: ConfirmDedupeRequest) -> dict:
    svc, state = _load(client_slug, project_slug)
    graph, emb = _get_dedupe_graph(svc, state)

    if req.groups is not None:
        known = {r.id for r in state.stripped_records}
        flat = [m for g in req.groups for m in g]
        if len(flat) != len(set(flat)):
            raise HTTPException(400, "a record appears in more than one group")
        unknown = set(flat) - known
        if unknown:
            raise HTTPException(400, f"unknown record ids: {sorted(unknown)}")
        missing = known - set(flat)
        if missing:
            raise HTTPException(400, f"these records are in no group: {sorted(missing)}")
        final = [
            DedupeGroup(
                group_id=f"grp-{i:04d}",
                member_ids=list(members),
                representative_id=members[0],
                avg_similarity=1.0,
                user_confirmed=True,
            )
            for i, members in enumerate(req.groups)
        ]
    else:
        computed = dedupe_svc.group_at_threshold(graph, req.threshold, embeddings=emb)
        final = [
            DedupeGroup(
                group_id=g.group_id,
                member_ids=g.member_ids,
                representative_id=g.representative_id,
                avg_similarity=g.avg_similarity,
                user_confirmed=True,
            )
            for g in computed
        ]

    state.dedupe_threshold = req.threshold
    state.dedupe_groups = final
    state.meta.current_stage = StageName.dedupe
    # changing dedupe invalidates everything downstream of it
    _invalidate_from(state, "dedupe")
    svc.save_state(
        state,
        action="confirm-dedupe",
        lineage_payload={"threshold": req.threshold, "groups": len(final), "manual": req.groups is not None},
    )
    return {"groups": len(final), "threshold": req.threshold}


def _invalidate_from(state: ProjectState, stage: str) -> None:
    """Cascade staleness when an upstream stage is re-run.

    Downstream artifacts are marked stale rather than deleted — lineage keeps
    everything, and the user gets told what was invalidated instead of silently
    losing work.
    """
    order = ["dedupe", "normalize", "cluster", "profiles"]
    idx = order.index(stage)
    if idx < order.index("normalize"):
        state.normalized_profiles = []
    if idx < order.index("cluster"):
        state.clustering = None
    for p in state.job_profiles:
        p.stale = True
    for r in state.je_results:
        r.stale = True


# ===========================================================================
# Step 3 — normalize
# ===========================================================================
@router.post("/normalize")
async def start_normalize(
    client_slug: str, project_slug: str, workers: int | None = None
) -> dict:
    svc, state = _load(client_slug, project_slug)
    _workers = llm.resolve_workers(workers)
    if not state.dedupe_groups:
        raise HTTPException(400, "no confirmed dedupe groups — confirm dedupe first")

    stripped_by_id = {s.id: s.stripped_text for s in state.stripped_records}
    titles = {r.id: r.job_title for r in state.raw_records}
    group_inputs = [
        [(titles.get(m, m), stripped_by_id[m]) for m in g.member_ids if m in stripped_by_id]
        for g in state.dedupe_groups
    ]
    group_ids = [g.group_id for g in state.dedupe_groups]
    source_ids = [g.member_ids for g in state.dedupe_groups]

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(len(group_inputs), f"Normalising {len(group_inputs)} job groups")
        results = normalization.normalize_many(group_inputs, workers=_workers, progress=reporter.pmap_callback())

        fresh = svc.load_state(client_slug, project_slug)
        fresh.normalized_profiles = [
            NormalizedProfile(
                id=gid,
                source_record_ids=srcs,
                purpose_statement=res.purpose_statement,
                key_tasks=res.key_tasks,
                management_line=res.management_line,
                budget_responsibility=res.budget_responsibility,
                generated_at=datetime.now(timezone.utc),
            )
            for gid, srcs, res in zip(group_ids, source_ids, results)
        ]
        fresh.meta.current_stage = StageName.normalize
        svc.save_state(fresh, action="accept-normalization", lineage_payload={"profiles": len(results)})

        summary = {
            "profiles": len(results),
            "mean_key_tasks": round(sum(len(r.key_tasks) for r in results) / len(results), 1),
            "with_management_line": sum(1 for r in results if r.management_line),
            "with_budget": sum(1 for r in results if r.budget_responsibility),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "normalize", work)


# ===========================================================================
# Steps 4-6 — cluster, choose k, name
# ===========================================================================
_TREE_CACHE: dict[tuple[str, str], tuple[np.ndarray, np.ndarray, list[str]]] = {}


@router.post("/cluster/build")
async def start_cluster_build(
    client_slug: str,
    project_slug: str,
    device: str | None = None,
    embedding_model: str | None = None,
) -> dict:
    """Embed normalised profiles and build ONE Ward tree.

    Stability is deliberately NOT computed here — it depends on the chosen
    profile-level k, and it costs 50 bootstrap re-clusterings. Computing it per
    slider position would be unusable, so it runs once at cluster/confirm.
    """
    svc, state = _load(client_slug, project_slug)
    if len(state.normalized_profiles) < 3:
        raise HTTPException(400, "need at least 3 normalised profiles to cluster")

    profiles = state.normalized_profiles
    texts = [
        normalization.NormalizedResult(
            purpose_statement=p.purpose_statement,
            key_tasks=p.key_tasks,
            management_line=p.management_line,
            budget_responsibility=p.budget_responsibility,
        ).embedding_text()
        for p in profiles
    ]
    ids = [p.id for p in profiles]

    try:
        spec2 = embeddings.resolve_model("job", embedding_model)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    def work(reporter: ProgressReporter) -> dict:
        if not embeddings.is_loaded("job", embedding_model):
            reporter.message(f"Loading the {spec2.name} model (first use this session)")
            get_embedding_service().warm("job", device, embedding_model)
        reporter.stage_start(len(texts), f"Embedding {len(texts)} profiles with {spec2.name}")
        emb = get_embedding_service().embed_documents(
            "job", texts, device=device, model=embedding_model,
            progress=lambda done, total: reporter.progress(done, total, "embedded")
        )
        reporter.message("Building the Ward tree")
        tree = bb.build_linkage_tree(emb)

        svc.save_array(client_slug, project_slug, "cluster_embeddings", emb)
        svc.save_array(client_slug, project_slug, "cluster_linkage", tree)
        svc.save_index(
            client_slug, project_slug, "cluster_embeddings", ids,
            model_fingerprint=get_embedding_service().fingerprint("job", embedding_model),
        )
        _TREE_CACHE[(client_slug, project_slug)] = (tree, emb, ids)

        n = len(ids)
        summary = {
            "items": n,
            # sensible starting points, scaled to dataset size
            "suggested_k_families": max(2, min(8, n // 12 or 2)),
            "suggested_k_categories": max(3, min(24, n // 5 or 3)),
            "suggested_k_profiles": max(4, min(64, n // 2 or 4)),
            "max_k": n - 1,
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "cluster", work)


def _get_tree(svc: ProjectService, state: ProjectState) -> tuple[np.ndarray, np.ndarray, list[str]]:
    client, project = state.meta.client_slug, state.meta.project_slug
    key = (client, project)
    if key in _TREE_CACHE:
        return _TREE_CACHE[key]
    index_path = f"{project}/artifacts/cluster_embeddings_index.json"
    tree = svc.load_array(client, f"{project}/artifacts/cluster_linkage.npy")
    emb = svc.load_array(client, f"{project}/artifacts/cluster_embeddings.npy")
    ids = svc.load_index(client, index_path)
    if tree is None or emb is None or ids is None:
        raise HTTPException(409, "cluster tree not built yet — run cluster/build first")
    try:
        embeddings.assert_cache_current("job", svc.load_index_fingerprint(client, index_path))
    except embeddings.StaleEmbeddingCache as e:
        raise HTTPException(409, str(e)) from e
    _TREE_CACHE[key] = (tree, emb, ids)
    return tree, emb, ids


@router.get("/cluster/preview-cut")
def cluster_preview_cut(
    client_slug: str,
    project_slug: str,
    k_families: int,
    k_categories: int,
    k_profiles: int,
) -> dict:
    """Cut the cached tree at three heights. Cheap — safe to call per slider drag."""
    svc, state = _load(client_slug, project_slug)
    tree, emb, ids = _get_tree(svc, state)

    try:
        cuts = cluster_engine.cut_three_tiers(
            tree, k_family=k_families, k_category=k_categories, k_profile=k_profiles
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    p2c = rollup.majority_vote_parent(cuts["profile"], cuts["category"])
    c2f = rollup.majority_vote_parent(cuts["category"], cuts["family"])

    def sizes(labels: np.ndarray) -> list[int]:
        return np.bincount(labels, minlength=int(labels.max()) + 1).tolist()

    titles = {p.id: p.purpose_statement[:80] for p in state.normalized_profiles}
    return {
        "k_families": k_families,
        "k_categories": k_categories,
        "k_profiles": k_profiles,
        "family_sizes": sizes(cuts["family"]),
        "category_sizes": sizes(cuts["category"]),
        "profile_sizes": sizes(cuts["profile"]),
        "profile_to_category": {str(k): v for k, v in p2c.items()},
        "category_to_family": {str(k): v for k, v in c2f.items()},
        "singleton_profiles": int(sum(1 for s in sizes(cuts["profile"]) if s == 1)),
        "largest_profile_size": int(max(sizes(cuts["profile"]))),
        "sample": [
            {"item_id": ids[i], "summary": titles.get(ids[i], ""), "profile": int(cuts["profile"][i])}
            for i in range(min(10, len(ids)))
        ],
    }


class ConfirmClusterRequest(BaseModel):
    k_families: int = Field(ge=2)
    k_categories: int = Field(ge=2)
    k_profiles: int = Field(ge=2)
    gate: float = Field(default=0.58, ge=0.0, le=1.0)
    n_perturb: int = Field(default=50, ge=5, le=200)


@router.post("/cluster/confirm")
async def confirm_cluster(client_slug: str, project_slug: str, req: ConfirmClusterRequest) -> dict:
    """The expensive stage: bootstrap stability, LLM routing of the unstable
    slice with self-consistency, then batched naming of all three tiers."""
    svc, state = _load(client_slug, project_slug)
    tree, emb, ids = _get_tree(svc, state)
    if req.k_profiles >= len(ids):
        raise HTTPException(422, f"k_profiles must be < number of items ({len(ids)})")

    summaries = {p.id: p.purpose_statement for p in state.normalized_profiles}
    item_texts = [summaries.get(i, i) for i in ids]

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(4, "Clustering: stability, routing, naming")

        result = asyncio.run(
            cluster_engine.run_clustering_pipeline(
                "job",
                item_texts,
                emb,
                k_family=req.k_families,
                k_category=req.k_categories,
                k_profile=req.k_profiles,
                gate=req.gate,
                n_perturb=req.n_perturb,
                route_concurrency=8,
                progress=reporter.pmap_callback(),
            )
        )

        fresh = svc.load_state(client_slug, project_slug)
        fresh.clustering = ClusteringState(
            embedding_model="jobQWEN",
            linkage_blob_path=f"{project_slug}/artifacts/cluster_linkage.npy",
            embedding_index_blob_path=f"{project_slug}/artifacts/cluster_embeddings_index.json",
            k_profiles=req.k_profiles,
            k_categories=req.k_categories,
            k_families=req.k_families,
            gate=req.gate,
            computed_at=datetime.now(timezone.utc),
            version=fresh.meta.clustering_version,
            profile_names=result.profile_names,
            category_names=result.category_names,
            family_names=result.family_names,
            assignments=[
                ItemAssignmentRecord(
                    item_id=ids[a.item_index],
                    backbone_profile_id=a.backbone_profile_id,
                    backbone_category_id=a.backbone_category_id,
                    backbone_family_id=a.backbone_family_id,
                    final_profile_id=a.final_profile_id,
                    final_category_id=a.final_category_id,
                    final_family_id=a.final_family_id,
                    stability_score=a.stability_score,
                    routed_by_llm=a.routed_by_llm,
                    route_confidence=a.route_confidence,
                    secondary_profile_id=a.secondary_profile_id,
                    secondary_confidence=a.secondary_confidence,
                    self_consistency=a.self_consistency,
                )
                for a in result.assignments
            ],
        )
        fresh.meta.current_stage = StageName.name
        for p in fresh.job_profiles:
            p.stale = True
        for r in fresh.je_results:
            r.stale = True
        svc.save_state(
            fresh,
            action="confirm-cluster-k",
            lineage_payload={
                "k_families": req.k_families,
                "k_categories": req.k_categories,
                "k_profiles": req.k_profiles,
                "gate": req.gate,
                "n_unstable": result.n_unstable,
            },
        )

        routed = [a for a in result.assignments if a.routed_by_llm]
        low_conf = [a for a in routed if (a.route_confidence or 1.0) < 0.5]
        summary = {
            "items": len(result.assignments),
            "n_unstable_routed": result.n_unstable,
            "pct_routed": round(100 * result.n_unstable / max(1, len(result.assignments)), 1),
            "families": len(result.family_names),
            "categories": len(result.category_names),
            "profiles": len(result.profile_names),
            # Per the methodology: a cluster of low-confidence routes means the
            # taxonomy is missing a bucket, not that the model guessed badly.
            "low_confidence_routes": len(low_conf),
            "multi_home_items": sum(1 for a in result.assignments if a.secondary_profile_id is not None),
        }
        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "cluster", work)


@router.get("/cluster/hierarchy")
def get_hierarchy(client_slug: str, project_slug: str) -> dict:
    """Browsable Family > Category > Profile tree with the full audit trail."""
    _, state = _load(client_slug, project_slug)
    c = state.clustering
    if c is None:
        raise HTTPException(409, "clustering not run yet")

    titles = {r.id: r.job_title for r in state.raw_records}
    group_members = {g.group_id: g.member_ids for g in state.dedupe_groups}
    headcount = {r.id: r.headcount for r in state.raw_records}

    tree: dict[int, dict] = {}
    for a in c.assignments:
        fam = tree.setdefault(
            a.final_family_id,
            {"id": a.final_family_id, "name": c.family_names.get(a.final_family_id, "?"), "categories": {}},
        )
        cat = fam["categories"].setdefault(
            a.final_category_id,
            {"id": a.final_category_id, "name": c.category_names.get(a.final_category_id, "?"), "profiles": {}},
        )
        prof = cat["profiles"].setdefault(
            a.final_profile_id,
            {"id": a.final_profile_id, "name": c.profile_names.get(a.final_profile_id, "?"), "items": []},
        )
        members = group_members.get(a.item_id, [a.item_id])
        prof["items"].append(
            {
                "item_id": a.item_id,
                "source_titles": [titles.get(m, m) for m in members],
                "headcount": sum(h for h in (headcount.get(m) for m in members) if h) or None,
                "stability_score": a.stability_score,
                "routed_by_llm": a.routed_by_llm,
                "route_confidence": a.route_confidence,
                "backbone_profile_id": a.backbone_profile_id,
                "moved_by_llm": a.routed_by_llm and a.backbone_profile_id != a.final_profile_id,
                "secondary_profile_id": a.secondary_profile_id,
                "secondary_profile_name": (
                    c.profile_names.get(a.secondary_profile_id) if a.secondary_profile_id is not None else None
                ),
            }
        )

    return {
        "families": [
            {
                **fam,
                "categories": [
                    {**cat, "profiles": list(cat["profiles"].values())} for cat in fam["categories"].values()
                ],
            }
            for fam in tree.values()
        ]
    }


class RenameRequest(BaseModel):
    level: str  # "family" | "category" | "profile"
    cluster_id: int
    name: str


@router.post("/cluster/rename")
def rename_cluster(client_slug: str, project_slug: str, req: RenameRequest) -> dict:
    """Let the user override a generated name. The name is stored separately from
    profile_key, so renaming never invalidates downstream profiles."""
    svc, state = _load(client_slug, project_slug)
    if state.clustering is None:
        raise HTTPException(409, "clustering not run yet")
    target = {
        "family": state.clustering.family_names,
        "category": state.clustering.category_names,
        "profile": state.clustering.profile_names,
    }.get(req.level)
    if target is None:
        raise HTTPException(400, "level must be one of: family, category, profile")
    if req.cluster_id not in target:
        raise HTTPException(404, f"no {req.level} cluster with id {req.cluster_id}")

    old = target[req.cluster_id]
    target[req.cluster_id] = req.name.strip()
    svc.save_state(
        state,
        action="rename-cluster",
        lineage_payload={"level": req.level, "cluster_id": req.cluster_id, "from": old, "to": req.name},
    )
    return {"level": req.level, "cluster_id": req.cluster_id, "name": req.name}


# ===========================================================================
# Step 7 — JE framework, profile generation, evaluation
# ===========================================================================
@router.get("/je-framework")
def get_je_framework(
    client_slug: str, project_slug: str, defaults: bool = False
) -> JEFrameworkConfig:
    """The project's framework, or the shipped default if it has none.

    `defaults=true` forces the shipped default even when the project has saved
    its own — that is what the editor's "Load defaults" control needs, and
    without it there is no way back to the original once you have saved.
    """
    _, state = _load(client_slug, project_slug)
    if defaults or not state.je_framework.domains:
        return je.load_default_framework()
    return state.je_framework


@router.put("/je-framework")
def put_je_framework(client_slug: str, project_slug: str, framework: JEFrameworkConfig) -> dict:
    svc, state = _load(client_slug, project_slug)
    problems = je.validate_framework(framework)
    if problems:
        raise HTTPException(422, {"message": "framework is not valid", "problems": problems})

    state.je_framework = framework
    for r in state.je_results:
        r.stale = True  # results computed under the old framework no longer apply
    svc.save_state(
        state,
        action="update-je-framework",
        lineage_payload={"framework_hash": framework_hash(framework.model_dump(mode="json"))},
    )
    return {"status": "saved", "invalidated_je_results": len(state.je_results)}


@router.post("/profiles/generate")
async def start_profile_generation(
    client_slug: str, project_slug: str, run_je: bool = True, workers: int | None = None
) -> dict:
    """Generate a Job Profile document per profile cluster, then (optionally) run
    the JE ensemble over them."""
    svc, state = _load(client_slug, project_slug)
    _workers = llm.resolve_workers(workers)
    c = state.clustering
    if c is None or not c.profile_names:
        raise HTTPException(400, "clustering and naming must be complete first")

    framework = state.je_framework if state.je_framework.domains else je.load_default_framework()
    fw_hash = framework_hash(framework.model_dump(mode="json"))

    norm_by_id = {p.id: p for p in state.normalized_profiles}
    headcount_by_item: dict[str, int] = {}
    hc = {r.id: r.headcount for r in state.raw_records}
    for g in state.dedupe_groups:
        total = sum(h for h in (hc.get(m) for m in g.member_ids) if h)
        if total:
            headcount_by_item[g.group_id] = total

    by_profile: dict[int, list[str]] = {}
    for a in c.assignments:
        by_profile.setdefault(a.final_profile_id, []).append(a.item_id)

    specs: list[generator.ProfileGenerationInput] = []
    for pid, item_ids in sorted(by_profile.items()):
        members = []
        for iid in item_ids:
            n = norm_by_id.get(iid)
            if n:
                members.append((n.purpose_statement, n.key_tasks, n.management_line, n.budget_responsibility))
        if not members:
            continue
        cat_id = next((a.final_category_id for a in c.assignments if a.final_profile_id == pid), None)
        fam_id = next((a.final_family_id for a in c.assignments if a.final_profile_id == pid), None)
        name = c.profile_names.get(pid, f"Profile {pid}")
        specs.append(
            generator.ProfileGenerationInput(
                profile_key=_profile_key(name, item_ids),
                cluster_name=name,
                family_name=c.family_names.get(fam_id) if fam_id is not None else None,
                category_name=c.category_names.get(cat_id) if cat_id is not None else None,
                members=members,
                headcount=sum(headcount_by_item.get(i, 0) for i in item_ids) or None,
            )
        )
    profile_cluster_ids = sorted(by_profile.keys())
    # Step 7's user-defined template. Resolved once here rather than per profile
    # so every document in a run uses the same section set.
    sections = _resolve_sections(state)
    section_headings = tpl.headings(sections)
    for spec in specs:
        spec.sections = sections

    def work(reporter: ProgressReporter) -> dict:
        reporter.stage_start(len(specs), f"Generating {len(specs)} job profile documents")
        contents = generator.generate_many(specs, workers=_workers, progress=reporter.pmap_callback())

        accent = state.meta.accent_color
        company = state.meta.display_name
        about_company = state.meta.client_company_description
        diversity = state.meta.diversity_statement

        docs: list[JobProfileDoc] = []
        for spec, pid, content in zip(specs, profile_cluster_ids, contents):
            html = generator.render_html(
                content,
                accent_color=accent,
                company_name=company,
                about_company=about_company,
                diversity_statement=diversity,
                headings=section_headings,
                sections=sections,
            )
            svc.save_profile_content(client_slug, project_slug, spec.profile_key, content)
            svc.save_profile_html(client_slug, project_slug, spec.profile_key, html)
            docs.append(
                JobProfileDoc(
                    profile_key=spec.profile_key,
                    profile_cluster_id=pid,
                    clustering_version=state.meta.clustering_version,
                    title=content.get("title", spec.cluster_name),
                    content=content,
                    html=html,
                    generated_at=datetime.now(timezone.utc),
                )
            )

        fresh = svc.load_state(client_slug, project_slug)
        fresh.job_profiles = docs
        fresh.meta.current_stage = StageName.profile_je
        svc.save_state(fresh, action="generate-profiles", lineage_payload={"profiles": len(docs)})
        summary: dict = {"profiles_generated": len(docs)}

        if run_je:
            reporter.stage_start(len(docs), f"Job evaluation ensemble across {len(docs)} profiles")
            je_inputs = [(d.profile_key, d.title, d.content) for d in docs]
            results = je.evaluate_many(je_inputs, framework, workers=_workers, progress=reporter.pmap_callback())

            # None entries are profiles whose evaluation could not be produced —
            # the rest are kept rather than the whole stage being discarded.
            evaluated = [r for r in results if r is not None]
            failed = [d.profile_key for d, r in zip(docs, results) if r is None]

            fresh2 = svc.load_state(client_slug, project_slug)
            fresh2.je_results = [
                JEEvaluationResult(
                    profile_key=r.profile_key,
                    clustering_version=fresh2.meta.clustering_version,
                    framework_version_hash=fw_hash,
                    personas=r.personas,
                    aggregate_score=r.aggregate_score,
                    level_name=r.level_name,
                    computed_at=datetime.now(timezone.utc),
                )
                for r in evaluated
            ]
            # re-render each profile HTML now that its evaluated level is known
            for doc, res in zip(fresh2.job_profiles, results):
                doc.html = generator.render_html(
                    doc.content,
                    accent_color=accent,
                    company_name=company,
                    about_company=about_company,
                    diversity_statement=diversity,
                    job_level=res.level_name if res else None,
                    headings=section_headings,
                    sections=sections,
                )
                svc.save_profile_html(client_slug, project_slug, doc.profile_key, doc.html)
            svc.save_state(
                fresh2,
                action="run-je-evaluation",
                lineage_payload={"evaluated": len(evaluated), "failed": failed},
            )

            summary["je_evaluated"] = len(evaluated)
            summary["levels"] = sorted({r.level_name for r in evaluated})
            summary["mean_score"] = round(
                sum(r.aggregate_score for r in evaluated) / max(1, len(evaluated)), 2
            )
            if failed:
                summary["je_failed"] = len(failed)
                summary["je_failed_profiles"] = failed[:10]

        reporter.stage_complete(summary)
        return summary

    return _start_job(client_slug, project_slug, "profiles", work)


def _profile_key(name: str, item_ids: list[str]) -> str:
    """Stable slug + membership hash. Survives a rename (name is stored
    separately) but changes if cluster membership changes — the anchor later
    phases join on."""
    import hashlib
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "profile"
    digest = hashlib.sha256("|".join(sorted(item_ids)).encode()).hexdigest()[:8]
    return f"{slug}-{digest}"


@router.get("/profiles")
def list_profiles(client_slug: str, project_slug: str, include_stale: bool = False) -> dict:
    """Aggregate-first JE browsing list.

    Deliberately returns only headline numbers per profile — no persona or
    sub-factor detail. That detail is a separate, explicit request
    (/profiles/{key}/je), which is the UX fix over the legacy report where one
    click exposed all 60 sub-factor rows at once.
    """
    _, state = _load(client_slug, project_slug)
    je_by_key = {r.profile_key: r for r in state.je_results}
    framework = state.je_framework if state.je_framework.domains else je.load_default_framework()

    rows = []
    for doc in state.job_profiles:
        if doc.stale and not include_stale:
            continue
        result = je_by_key.get(doc.profile_key)
        breadcrumb = [doc.content.get("family"), doc.content.get("category")]
        row = {
            "profile_key": doc.profile_key,
            "title": doc.title,
            "breadcrumb": [b for b in breadcrumb if b],
            "stale": doc.stale,
            "has_je": result is not None,
        }
        if result:
            scores = {p: je.weighted_score(result.personas[p], framework) for p in je.PERSONAS}
            row.update(
                {
                    "aggregate_score": result.aggregate_score,
                    "level_name": result.level_name,
                    "spread_low": scores["Harsh"],
                    "spread_high": scores["Generous"],
                    "je_stale": result.stale,
                }
            )
        rows.append(row)

    rows.sort(key=lambda r: r.get("aggregate_score") or -1, reverse=True)
    return {"profiles": rows, "count": len(rows)}


@router.get("/profiles/{profile_key}")
def get_profile(client_slug: str, project_slug: str, profile_key: str) -> dict:
    _, state = _load(client_slug, project_slug)
    doc = next((d for d in state.job_profiles if d.profile_key == profile_key), None)
    if doc is None:
        raise HTTPException(404, "profile not found")
    return {
        "profile_key": doc.profile_key,
        "title": doc.title,
        "content": doc.content,
        "html": doc.html,
        "stale": doc.stale,
        "generated_at": doc.generated_at.isoformat(),
    }


@router.get("/profiles/{profile_key}/je")
def get_profile_je(client_slug: str, project_slug: str, profile_key: str) -> dict:
    """Full JE detail — the deliberate second step behind the aggregate list.

    Returns domain rollups alongside the raw persona/sub-factor scores so the UI
    can show domain-level first and keep sub-factors behind a further toggle.
    """
    _, state = _load(client_slug, project_slug)
    stored = next((r for r in state.je_results if r.profile_key == profile_key), None)
    if stored is None:
        raise HTTPException(404, "no job evaluation for this profile")

    framework = state.je_framework if state.je_framework.domains else je.load_default_framework()
    result = je.JEResult(
        profile_key=stored.profile_key,
        personas=stored.personas,
        persona_scores={p: je.weighted_score(stored.personas[p], framework) for p in je.PERSONAS},
        aggregate_score=stored.aggregate_score,
        level_name=stored.level_name,
        spread=0.0,
    )
    return {
        "profile_key": profile_key,
        "aggregate_score": stored.aggregate_score,
        "level_name": stored.level_name,
        "stale": stored.stale,
        "framework_version_hash": stored.framework_version_hash,
        "persona_scores": result.persona_scores,
        "domain_rollups": {p: result.domain_subtotals(framework, p) for p in je.PERSONAS},
        "personas": stored.personas,
        "framework": framework.model_dump(mode="json"),
    }


@router.get("/profiles/{profile_key}/export/{fmt}")
def export_profile(client_slug: str, project_slug: str, profile_key: str, fmt: str) -> dict:
    from fastapi.responses import Response

    svc, state = _load(client_slug, project_slug)
    doc = next((d for d in state.job_profiles if d.profile_key == profile_key), None)
    if doc is None:
        raise HTTPException(404, "profile not found")

    level = next((r.level_name for r in state.je_results if r.profile_key == profile_key), None)
    company = state.meta.display_name
    about = state.meta.client_company_description

    if fmt == "html":
        return Response(content=doc.html, media_type="text/html")

    _sections = _resolve_sections(state)
    if fmt == "docx":
        data = exporters.render_docx(
            doc.content,
            company_name=company,
            job_level=level,
            about_company=about,
            diversity_statement=state.meta.diversity_statement,
            accent_color=state.meta.accent_color,
            # Same headings the HTML used, or the two formats of one profile
            # would disagree on what its sections are called.
            headings=tpl.headings(_sections),
            sections=_sections,
        )
        svc.save_export(
            client_slug, project_slug, profile_key, "job-profile.docx", data,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{profile_key}.docx"'},
        )

    if fmt == "pdf":
        try:
            data = exporters.render_pdf(doc.html)
        except exporters.PdfExportUnavailable as e:
            raise HTTPException(503, str(e)) from e
        svc.save_export(client_slug, project_slug, profile_key, "job-profile.pdf", data, "application/pdf")
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{profile_key}.pdf"'},
        )

    raise HTTPException(400, "fmt must be one of: html, docx, pdf")


@router.get("/export/capabilities")
def export_capabilities() -> dict:
    """Lets the UI disable a PDF button rather than offering an export that 503s."""
    return {"html": True, "docx": True, "pdf": exporters.pdf_available()}
