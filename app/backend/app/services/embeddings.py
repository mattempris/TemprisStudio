"""GPU-enabled, lazy-loaded wrapper around the three fine-tuned Qwen3-0.6B
sentence-transformers models (job/skill/task), unzipped by
scripts/prepare_embedding_models.py into data/models/<entity>/.

Each model is an asymmetric query/document embedding model (1024-dim, last-token
pooling, cosine similarity) — same interface `jobMatching`'s EmbeddingService uses
for JobBERT-v3, just with a different underlying checkpoint per entity type.

Note on imports: torch and sentence-transformers are imported inside the
functions that need them, not at module scope. Importing them costs ~7 seconds,
and this module is reachable from the API routes — so at module scope it delayed
uvicorn binding its port by that much, during which the frontend (ready in under
a second) got connection-refused on every request. The models were always loaded
lazily, so the eager import bought nothing.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

import numpy as np

from app.core.config import MODELS_DIR, get_settings

if TYPE_CHECKING:  # torch/sentence-transformers are imported lazily — see below
    from sentence_transformers import SentenceTransformer

# How many texts to encode between progress reports. A multiple of the usual
# encode batch_size so GPU batching stays efficient, but small enough that the
# bar moves every few seconds on a realistic corpus.
_PROGRESS_CHUNK = 128

EntityType = Literal["job", "skill", "task"]


@dataclass(frozen=True)
class ModelSpec:
    """A selectable embedding model.

    `has_query_prompt` matters because the two families differ in how they handle
    asymmetric query/document use: the Juggernaught models take an instruction prefix
    via `prompt_name="query"`, while Lightning has no prompts at all and passing one
    raises. `dim` is recorded because both happen to be 1024 — so a dimension check
    cannot tell their vectors apart, and only the fingerprint can.

    `name` and `dir_name` are internal: the fingerprint leads with `dir_name`, so they
    are frozen for the life of a project's cached vectors. `label` is what a user
    sees, and is free to change.
    """

    name: str
    dir_name: str
    entity: EntityType
    dim: int
    has_query_prompt: bool
    note: str
    label: str = ""


MODELS: dict[str, ModelSpec] = {
    "jobQWEN": ModelSpec(
        "jobQWEN", "jobQWEN", "job", 1024, True,
        "Fine-tuned for job data, with last-token pooling and instruction-prefixed "
        "queries. Handles full job descriptions.",
        label="Juggernaught",
    ),
    "JobBERT-v2": ModelSpec(
        "JobBERT-v2", "JobBERT-v2", "job", 1024, False,
        "A bi-encoder specialised for job TITLE normalisation, trained on 5.5M "
        "title-skill pairs. 512-token limit, so long descriptions are truncated — "
        "strongest on titles and short text, and ~6x smaller and faster than "
        "Juggernaught.",
        label="Lightning",
    ),
    "skillQWEN": ModelSpec(
        "skillQWEN", "skillQWEN", "skill", 1024, True,
        "Fine-tuned for skills.", label="Juggernaught",
    ),
    "taskQWEN": ModelSpec(
        "taskQWEN", "taskQWEN", "task", 1024, True,
        "Fine-tuned for tasks.", label="Juggernaught",
    ),
}


def display_name(model_key: str | None) -> str:
    """What to show a user for a stored model key.

    State written before the rename holds raw keys like "taskQWEN", and exports and
    summaries render them, so the mapping has to work on any historic value rather
    than only on the live registry.
    """
    if not model_key:
        return "unknown"
    spec = MODELS.get(model_key)
    if spec and spec.label:
        return spec.label
    return "Juggernaught" if model_key.upper().endswith("QWEN") else model_key

# Only the job slot is selectable today; skills and tasks have one model each.
_DEFAULT_BY_ENTITY: dict[str, str] = {"job": "jobQWEN", "skill": "skillQWEN", "task": "taskQWEN"}


def models_for(entity: EntityType) -> list[ModelSpec]:
    return [m for m in MODELS.values() if m.entity == entity]


def resolve_model(entity: EntityType, override: str | None = None) -> ModelSpec:
    """Which model to use for an entity: an explicit choice, else the configured
    default for the job slot, else the entity's only model."""
    if override:
        spec = MODELS.get(override)
        if spec is None:
            raise ValueError(
                f"unknown embedding model {override!r}; available for '{entity}': "
                f"{[m.name for m in models_for(entity)]}"
            )
        if spec.entity != entity:
            raise ValueError(
                f"model {override!r} is a '{spec.entity}' model, not '{entity}'"
            )
        return spec

    if entity == "job":
        configured = get_settings().job_embedding_model
        spec = MODELS.get(configured)
        if spec is None or spec.entity != "job":
            print(
                f"[embeddings] JOB_EMBEDDING_MODEL={configured!r} is not a known job "
                f"model — using jobQWEN. Valid: {[m.name for m in models_for('job')]}"
            )
            return MODELS["jobQWEN"]
        return spec

    return MODELS[_DEFAULT_BY_ENTITY[entity]]


def _resolve_device(override: str | None = None) -> str:
    """Pick the device, refusing CUDA when there is not enough free VRAM.

    `torch.cuda.is_available()` is True whenever a usable driver and card exist —
    it says nothing about whether there is memory left. On a 6GB laptop GPU shared
    with a training run that distinction is the whole game: the old check would
    happily select CUDA with 21MiB free, then fail on the first allocation, and a
    CUDA OOM can take neighbouring work down with it rather than just this job.

    Falling back to CPU is slower but finishes. An explicit `override` (per-run,
    from the API) wins over the configured default so a user can keep the GPU
    clear without restarting the server.
    """
    import torch

    settings = get_settings()
    want = (override or settings.embedding_device or "cpu").lower()
    if want != "cuda":
        return "cpu"

    if not torch.cuda.is_available():
        print("[embeddings] CUDA requested but not available — falling back to CPU")
        return "cpu"

    free_bytes, total_bytes = torch.cuda.mem_get_info()
    free_mb, total_mb = free_bytes // 1024**2, total_bytes // 1024**2
    need_mb = settings.embedding_min_free_vram_mb
    if free_mb < need_mb:
        print(
            f"[embeddings] CUDA has only {free_mb}MiB of {total_mb}MiB free, "
            f"need ~{need_mb}MiB — falling back to CPU. Free the GPU or lower "
            f"EMBEDDING_MIN_FREE_VRAM_MB to override."
        )
        return "cpu"

    print(f"[embeddings] using CUDA ({free_mb}MiB of {total_mb}MiB free)")
    return "cuda"


_LOADED: set[str] = set()


@lru_cache(maxsize=8)
def _load_model(model_name: str, device: str | None = None) -> SentenceTransformer:
    """Cached per (model, device). The device is part of the key because a model
    already resident on CUDA is not usable as a CPU model — without it, asking for
    CPU after a GPU load would silently hand back the GPU copy."""
    spec = MODELS[model_name]
    model_dir = MODELS_DIR / spec.dir_name
    if not model_dir.exists():
        raise FileNotFoundError(
            f"Embedding model {model_name!r} not found at {model_dir}. "
            f"Run: python -m scripts.prepare_embedding_models {spec.dir_name}"
        )
    from sentence_transformers import SentenceTransformer

    resolved = _resolve_device(device)
    print(f"[embeddings] loading {model_name} from {model_dir} on {resolved}...")
    model = SentenceTransformer(str(model_dir), device=resolved)
    _LOADED.add(model_name)
    return model


def is_loaded(entity: EntityType, model: str | None = None) -> bool:
    """Whether the model for this entity is already resident.

    The first load of a session moves the weights onto the device and can take
    tens of seconds during which nothing else reports — callers use this to say
    "loading the model" rather than leaving the user staring at a stalled bar.
    """
    return resolve_model(entity, model).name in _LOADED


class EmbeddingService:
    """One instance covers all three entity types; each model loads lazily on first use."""

    def embed_documents(
        self,
        entity: EntityType,
        texts: list[str],
        *,
        batch_size: int = 32,
        progress: Callable[[int, int], None] | None = None,
        device: str | None = None,
        model: str | None = None,
    ) -> np.ndarray:
        """Embed texts in 'document' mode (no query instruction prefix) — used for
        dedup/clustering, where we're comparing item-to-item, not query-to-document.

        With a `progress` callback the work is chunked so the caller can report
        partway through. `model.encode` already batches internally but only
        returns once, so a single call over a few thousand texts is a long silent
        block — which is indistinguishable from a hang at the UI.

        Chunking does not change the result: each text is encoded independently
        and normalisation is per-vector, so the output is identical either way.
        """
        spec = resolve_model(entity, model)
        st = _load_model(spec.name, device)
        encode = lambda batch: st.encode(  # noqa: E731
            batch, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False
        )

        if progress is None or len(texts) <= _PROGRESS_CHUNK:
            out = encode(texts)
            if progress:
                progress(len(texts), len(texts))
            return out

        chunks: list[np.ndarray] = []
        for start in range(0, len(texts), _PROGRESS_CHUNK):
            chunks.append(encode(texts[start : start + _PROGRESS_CHUNK]))
            progress(min(start + _PROGRESS_CHUNK, len(texts)), len(texts))
        return np.vstack(chunks)

    def warm(self, entity: EntityType, device: str | None = None, model: str | None = None) -> None:
        """Load the model now rather than lazily inside the next encode.

        Callers announce "loading the model" first, so the load has to happen
        while that message is on screen. Left to happen inside embed_documents it
        would occur *after* the caller had already switched the message to
        "embedding", which is the silent stretch this exists to explain.
        """
        _load_model(resolve_model(entity, model).name, device)

    def embed_query(
        self,
        entity: EntityType,
        text: str,
        *,
        model: str | None = None,
        device: str | None = None,
    ) -> np.ndarray:
        """Embed a single query string, using the model's query instruction prompt
        where it has one.

        JobBERT-v2 has no prompts — passing prompt_name to it raises. It is
        asymmetric in a different way, via Router heads, but its documented usage
        pins encoding to the `anchor` head, which is what plain-string encoding
        already selects. So there is nothing to switch for a query.
        """
        spec = resolve_model(entity, model)
        st = _load_model(spec.name, device)
        kwargs = {"prompt_name": "query"} if spec.has_query_prompt else {}
        return st.encode(
            [text], normalize_embeddings=True, show_progress_bar=False, **kwargs
        )[0]

    def is_ready(self, entity: EntityType, model: str | None = None) -> bool:
        spec = resolve_model(entity, model)
        return (MODELS_DIR / spec.dir_name / "config.json").exists()

    def fingerprint(self, entity: EntityType, model: str | None = None) -> str:
        """Identifies WHICH build of a model is installed, e.g.
        'taskQWEN@ft_epoch3.zip:1785570701'.

        Cached embeddings are only reusable if the model that produced them is
        still installed — cosine similarity between vectors from two different
        models is meaningless, and the failure is silent: clustering still runs
        and still returns plausible-looking groups. Stamping caches with this and
        comparing on load turns that into an explicit error.

        The model NAME leads the fingerprint, which is what makes switching the
        job model safe: jobQWEN and JobBERT-v2 both produce 1024-dim vectors, so
        no shape or dimension check can tell their output apart. The name is the
        only thing that can.

        Falls back to the bare name when the stamp is absent (a model installed
        before stamping existed, or one downloaded rather than unzipped), which
        compares unequal to any stamped fingerprint and so errs toward rebuilding.
        """
        name = resolve_model(entity, model).dir_name
        stamp = MODELS_DIR / name / "installed_from.json"
        if stamp.exists():
            try:
                meta = json.loads(stamp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return name
            # Two provenance shapes: models unzipped from a local zip carry
            # source_zip/mtime, models pulled from the Hub carry a repo and a
            # commit sha. Either uniquely identifies the build.
            if meta.get("source_zip"):
                return f"{name}@{meta['source_zip']}:{meta.get('mtime')}"
            if meta.get("revision"):
                return f"{name}@{meta.get('source_repo', 'hub')}:{meta['revision'][:12]}"
        return name


class StaleEmbeddingCache(RuntimeError):
    """Cached vectors were produced by a different build of the embedding model."""


def assert_cache_current(entity: EntityType, stored_fingerprint: str | None) -> None:
    """Refuse to reuse embeddings from a model that is no longer installed.

    Silent reuse is the dangerous case: clustering would run to completion over a
    mix of old and new vectors and return groupings that look entirely
    reasonable. Only a rebuild fixes it, so this fails loudly and says so.

    The four cases, and why an unstamped index is not automatically fatal:

      model stamped, index stamped, equal    -> reuse
      model stamped, index stamped, differ   -> refuse; the model was replaced
      model stamped, index NOT stamped       -> refuse; the model has been
          (re)installed since stamping began, so an unstamped index necessarily
          predates that install
      neither stamped                        -> reuse; a model installed before
          stamping existed, with a cache from the same era. Nothing indicates a
          change, and refusing here would invalidate every existing project's
          tree on upgrade for no reason.
    """
    current = get_embedding_service().fingerprint(entity)
    if stored_fingerprint == current:
        return

    model_is_stamped = "@" in current
    if stored_fingerprint is None:
        if not model_is_stamped:
            return  # legacy on both sides — no evidence the model changed
        raise StaleEmbeddingCache(
            f"cached {entity} embeddings carry no model version, but {current} "
            f"has been installed since version tracking began — so the cache "
            f"predates it. Rebuild the {entity} tree."
        )
    raise StaleEmbeddingCache(
        f"cached {entity} embeddings were produced by {stored_fingerprint}, but "
        f"{current} is installed now. Vectors from different models are not "
        f"comparable — rebuild the {entity} tree."
    )


_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service
