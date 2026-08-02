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
_ENTITY_TO_DIR = {"job": "jobQWEN", "skill": "skillQWEN", "task": "taskQWEN"}


def _resolve_device() -> str:
    import torch

    settings = get_settings()
    if settings.embedding_device == "cuda" and torch.cuda.is_available():
        return "cuda"
    if settings.embedding_device == "cuda":
        print("[embeddings] CUDA requested but not available — falling back to CPU")
    return "cpu"


_LOADED: set[str] = set()


@lru_cache(maxsize=3)
def _load_model(entity: EntityType) -> SentenceTransformer:
    model_dir = MODELS_DIR / _ENTITY_TO_DIR[entity]
    if not model_dir.exists():
        raise FileNotFoundError(
            f"Embedding model for '{entity}' not found at {model_dir}. "
            f"Run: python -m scripts.prepare_embedding_models {_ENTITY_TO_DIR[entity]}"
        )
    from sentence_transformers import SentenceTransformer

    device = _resolve_device()
    print(f"[embeddings] loading {entity} model from {model_dir} on {device}...")
    model = SentenceTransformer(str(model_dir), device=device)
    _LOADED.add(entity)
    return model


def is_loaded(entity: EntityType) -> bool:
    """Whether the model is already resident.

    The first call of a session pulls ~1.2GB onto the GPU and can take tens of
    seconds during which nothing else reports — callers use this to say "loading
    the model" rather than leaving the user staring at a stalled bar.
    """
    return entity in _LOADED


class EmbeddingService:
    """One instance covers all three entity types; each model loads lazily on first use."""

    def embed_documents(
        self,
        entity: EntityType,
        texts: list[str],
        *,
        batch_size: int = 32,
        progress: Callable[[int, int], None] | None = None,
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
        model = _load_model(entity)
        encode = lambda batch: model.encode(  # noqa: E731
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

    def warm(self, entity: EntityType) -> None:
        """Load the model now rather than lazily inside the next encode.

        Callers announce "loading the model" first, so the load has to happen
        while that message is on screen. Left to happen inside embed_documents it
        would occur *after* the caller had already switched the message to
        "embedding", which is the silent stretch this exists to explain.
        """
        _load_model(entity)

    def embed_query(self, entity: EntityType, text: str) -> np.ndarray:
        """Embed a single query string using the model's query instruction prompt."""
        model = _load_model(entity)
        return model.encode([text], prompt_name="query", normalize_embeddings=True, show_progress_bar=False)[0]

    def is_ready(self, entity: EntityType) -> bool:
        model_dir = MODELS_DIR / _ENTITY_TO_DIR[entity]
        return (model_dir / "config.json").exists()

    def fingerprint(self, entity: EntityType) -> str:
        """Identifies WHICH build of a model is installed, e.g.
        'taskQWEN@ft_epoch3.zip:1785570701'.

        Cached embeddings are only reusable if the model that produced them is
        still installed — cosine similarity between vectors from two different
        models is meaningless, and the failure is silent: clustering still runs
        and still returns plausible-looking groups. Stamping caches with this and
        comparing on load turns that into an explicit error.

        Falls back to the bare name when the stamp is absent (a model installed
        before stamping existed), which compares unequal to any stamped
        fingerprint and so errs toward rebuilding.
        """
        name = _ENTITY_TO_DIR[entity]
        stamp = MODELS_DIR / name / "installed_from.json"
        if stamp.exists():
            try:
                meta = json.loads(stamp.read_text(encoding="utf-8"))
                return f"{name}@{meta.get('source_zip')}:{meta.get('mtime')}"
            except (json.JSONDecodeError, OSError):
                pass
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
