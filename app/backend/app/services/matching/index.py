"""Taxonomy embedding index — built once with jobQWEN, cached to disk.

The 5,659 specializations don't change between projects or clients, so the index
is a process-level artifact under `data/cache/`, not per-project blob state. It's
keyed by the industry filter, since a filtered index is a different (smaller)
matrix. Rebuilding all 5,659 on GPU takes well under a minute; the cache means
that happens once, not once per project.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.config import DATA_DIR
from app.services.embeddings import get_embedding_service
from app.services.matching import taxonomy

CACHE_DIR = DATA_DIR / "cache" / "taxonomy_index"

# Bump when variant_texts() or the embedding model changes, so stale vectors are
# rebuilt rather than silently reused against a different text formulation.
INDEX_VERSION = "v2-jobqwen-variants"


@dataclass
class TaxonomyIndex:
    specs: list[taxonomy.Specialization]
    vectors: np.ndarray        # (n_variants, 1024) L2-normalized, grouped by spec
    offsets: np.ndarray        # (n_specs,) start index of each spec's variants
    industries: list[str] | None

    def __len__(self) -> int:
        return len(self.specs)

    @property
    def n_variants(self) -> int:
        return int(self.vectors.shape[0])


def _cache_key(industries: list[str] | None) -> str:
    scope = "ALL" if not industries else "|".join(sorted(i.strip().lower() for i in industries))
    digest = hashlib.sha256(f"{INDEX_VERSION}::{scope}".encode()).hexdigest()[:16]
    return f"{INDEX_VERSION}_{digest}"


def _cache_paths(key: str) -> tuple[Path, Path]:
    return CACHE_DIR / f"{key}.npy", CACHE_DIR / f"{key}.json"


def build_index(
    industries: list[str] | None = None,
    *,
    use_cache: bool = True,
    progress=None,
) -> TaxonomyIndex:
    def emit(msg: str) -> None:
        if progress:
            progress(msg)

    specs = taxonomy.load_specializations(industries)
    if not specs:
        raise taxonomy.TaxonomyUnavailable(
            f"no specializations matched industries={industries}. "
            f"Valid values come from GET /api/matching/industries."
        )

    key = _cache_key(industries)
    vec_path, meta_path = _cache_paths(key)

    # Variants are laid out grouped by spec so the shortlist can max-pool them
    # with a single reduceat rather than a per-spec Python loop.
    texts: list[str] = []
    offsets: list[int] = []
    for spec in specs:
        offsets.append(len(texts))
        texts.extend(spec.variant_texts())
    offsets_arr = np.asarray(offsets, dtype=np.int64)

    if use_cache and vec_path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        # The catalogue could be swapped underneath us; the code list and variant
        # count are the cheap integrity check that the cached rows still line up.
        if meta.get("codes") == [s.code for s in specs] and meta.get("n_variants") == len(texts):
            emit(f"taxonomy index loaded from cache ({len(specs)} specializations, {len(texts)} title variants)")
            return TaxonomyIndex(specs, np.load(vec_path), offsets_arr, industries)
        emit("cached taxonomy index is stale — rebuilding")

    emit(f"embedding {len(texts)} taxonomy title variants across {len(specs)} specializations with jobQWEN...")
    vectors = get_embedding_service().embed_documents("job", texts, batch_size=64)
    vectors = np.asarray(vectors, dtype=np.float32)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(vec_path, vectors)
    meta_path.write_text(
        json.dumps({"version": INDEX_VERSION, "industries": industries,
                    "n_variants": len(texts), "codes": [s.code for s in specs]}),
        encoding="utf-8",
    )
    emit(f"taxonomy index built and cached ({len(specs)} specializations, {len(texts)} title variants)")
    return TaxonomyIndex(specs, vectors, offsets_arr, industries)
