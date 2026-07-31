"""GPU-enabled, lazy-loaded wrapper around the three fine-tuned Qwen3-0.6B
sentence-transformers models (job/skill/task), unzipped by
scripts/prepare_embedding_models.py into data/models/<entity>/.

Each model is an asymmetric query/document embedding model (1024-dim, last-token
pooling, cosine similarity) — same interface `jobMatching`'s EmbeddingService uses
for JobBERT-v3, just with a different underlying checkpoint per entity type.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from app.core.config import MODELS_DIR, get_settings

EntityType = Literal["job", "skill", "task"]
_ENTITY_TO_DIR = {"job": "jobQWEN", "skill": "skillQWEN", "task": "taskQWEN"}


def _resolve_device() -> str:
    settings = get_settings()
    if settings.embedding_device == "cuda" and torch.cuda.is_available():
        return "cuda"
    if settings.embedding_device == "cuda":
        print("[embeddings] CUDA requested but not available — falling back to CPU")
    return "cpu"


@lru_cache(maxsize=3)
def _load_model(entity: EntityType) -> SentenceTransformer:
    model_dir = MODELS_DIR / _ENTITY_TO_DIR[entity]
    if not model_dir.exists():
        raise FileNotFoundError(
            f"Embedding model for '{entity}' not found at {model_dir}. "
            f"Run: python -m scripts.prepare_embedding_models {_ENTITY_TO_DIR[entity]}"
        )
    device = _resolve_device()
    print(f"[embeddings] loading {entity} model from {model_dir} on {device}...")
    model = SentenceTransformer(str(model_dir), device=device)
    return model


class EmbeddingService:
    """One instance covers all three entity types; each model loads lazily on first use."""

    def embed_documents(self, entity: EntityType, texts: list[str], *, batch_size: int = 32) -> np.ndarray:
        """Embed texts in 'document' mode (no query instruction prefix) — used for
        dedup/clustering, where we're comparing item-to-item, not query-to-document."""
        model = _load_model(entity)
        return model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)

    def embed_query(self, entity: EntityType, text: str) -> np.ndarray:
        """Embed a single query string using the model's query instruction prompt."""
        model = _load_model(entity)
        return model.encode([text], prompt_name="query", normalize_embeddings=True, show_progress_bar=False)[0]

    def is_ready(self, entity: EntityType) -> bool:
        model_dir = MODELS_DIR / _ENTITY_TO_DIR[entity]
        return (model_dir / "config.json").exists()


_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service
