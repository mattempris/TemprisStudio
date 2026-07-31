"""Unzip the fine-tuned Qwen3-0.6B embedding models into app/backend/data/models/.

Each of models/{jobQWEN,skillQWEN,taskQWEN}/qwen3-0.6b-family-ft-epoch5-merged.zip
contains a complete sentence-transformers SentenceTransformer save directory
(config.json, tokenizer files, model.safetensors, 1_Pooling/, 2_Normalize/) under
a single top-level folder. This script extracts each one into
data/models/<entity>QWEN/ so app/services/embeddings.py can load it directly with
`SentenceTransformer(path)`.

Usage:
    python -m scripts.prepare_embedding_models              # all three
    python -m scripts.prepare_embedding_models jobQWEN       # just one
"""
from __future__ import annotations

import sys
import zipfile

from app.core.config import MODELS_DIR, SOURCE_MODELS_DIR

ENTITIES = ["jobQWEN", "skillQWEN", "taskQWEN"]
ZIP_NAME = "qwen3-0.6b-family-ft-epoch5-merged.zip"


def prepare_one(entity: str, *, force: bool = False) -> None:
    src_zip = SOURCE_MODELS_DIR / entity / ZIP_NAME
    dest_dir = MODELS_DIR / entity

    marker = dest_dir / "config.json"
    if marker.exists() and not force:
        print(f"[{entity}] already prepared at {dest_dir} (config.json present) — skipping")
        return

    if not src_zip.exists():
        raise FileNotFoundError(f"[{entity}] source zip not found: {src_zip}")

    print(f"[{entity}] extracting {src_zip} -> {dest_dir} ...")
    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(src_zip) as zf:
        names = zf.namelist()
        # the zip has one top-level folder; strip it so files land directly in dest_dir
        top_level = names[0].split("/")[0] + "/"
        for name in names:
            if not name.startswith(top_level):
                continue
            rel = name[len(top_level) :]
            if not rel:
                continue
            target = dest_dir / rel
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target, "wb") as dst:
                dst.write(src.read())

    assert marker.exists(), f"[{entity}] extraction finished but config.json missing at {marker}"
    print(f"[{entity}] done — {sum(1 for _ in dest_dir.rglob('*') if _.is_file())} files extracted")


def main() -> None:
    targets = sys.argv[1:] or ENTITIES
    for entity in targets:
        if entity not in ENTITIES:
            raise SystemExit(f"unknown entity '{entity}', expected one of {ENTITIES}")
        prepare_one(entity)


if __name__ == "__main__":
    main()
