"""Install the fine-tuned Qwen3-0.6B embedding models into app/backend/data/models/.

Each of models/{jobQWEN,skillQWEN,taskQWEN}/ ships a zip. Two shapes exist and
both are handled, because the zip name and shape change whenever a model is
retrained:

  MERGED — a complete sentence-transformers save directory (config.json,
    tokenizer, model.safetensors, 1_Pooling/, 2_Normalize/). ~1.9GB. Extracted
    as-is.

  LORA ADAPTER — adapter_config.json + adapter_model.safetensors and the
    sentence-transformers scaffolding, but no base weights. ~40MB. The adapter is
    merged into its base model (named in adapter_config.json) and the result
    saved as a merged directory, so app/services/embeddings.py can keep loading
    every model with a plain `SentenceTransformer(path)` and pays no adapter
    overhead at inference time.

The zip is located by glob rather than a fixed filename — hardcoding one meant a
replaced model silently failed to install.

Usage:
    python -m scripts.prepare_embedding_models                    # all three, skip installed
    python -m scripts.prepare_embedding_models taskQWEN           # just one
    python -m scripts.prepare_embedding_models taskQWEN --force   # reinstall over existing
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from app.core.config import MODELS_DIR, SOURCE_MODELS_DIR

ENTITIES = ["jobQWEN", "skillQWEN", "taskQWEN"]

# Written next to the installed model so you can tell WHICH source produced it.
# Without this, a replaced zip and a stale install look identical on disk.
STAMP_NAME = "installed_from.json"


def _find_zip(entity: str) -> Path:
    src_dir = SOURCE_MODELS_DIR / entity
    if not src_dir.is_dir():
        raise FileNotFoundError(f"[{entity}] source dir not found: {src_dir}")
    zips = sorted(src_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        raise FileNotFoundError(f"[{entity}] no .zip in {src_dir}")
    if len(zips) > 1:
        print(f"[{entity}] {len(zips)} zips present; using the newest: {zips[0].name}")
    return zips[0]


def _stamp(src_zip: Path) -> dict:
    st = src_zip.stat()
    return {"source_zip": src_zip.name, "size_bytes": st.st_size, "mtime": int(st.st_mtime)}


def _read_stamp(dest_dir: Path) -> dict | None:
    p = dest_dir / STAMP_NAME
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _extract(src_zip: Path, into: Path) -> Path:
    """Extract, stripping the single top-level folder. Returns the content root."""
    with zipfile.ZipFile(src_zip) as zf:
        names = zf.namelist()
        top = names[0].split("/")[0] + "/"
        flat = not all(n.startswith(top) for n in names if n.strip())
        for name in names:
            rel = name if flat else name[len(top):] if name.startswith(top) else None
            if not rel:
                continue
            target = into / rel
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as s, open(target, "wb") as d:
                shutil.copyfileobj(s, d)
    return into


def _merge_adapter(staged: Path, dest_dir: Path, entity: str) -> None:
    """Merge a LoRA adapter into its base model and save a full ST directory."""
    try:
        from peft import PeftModel
    except ImportError as e:
        raise SystemExit(
            f"[{entity}] this zip is a LoRA adapter, which needs `peft` to merge.\n"
            f"    pip install peft"
        ) from e
    from sentence_transformers import SentenceTransformer

    cfg = json.loads((staged / "adapter_config.json").read_text(encoding="utf-8"))
    base = cfg.get("base_model_name_or_path")
    if not base:
        raise SystemExit(f"[{entity}] adapter_config.json has no base_model_name_or_path")
    print(f"[{entity}] LoRA adapter (r={cfg.get('r')}, alpha={cfg.get('lora_alpha')})")
    print(f"[{entity}] loading base model {base} ...")

    # Load on CPU: merging is a weight operation, and this runs on machines whose
    # GPU may be busy with training.
    st = SentenceTransformer(base, device="cpu")
    transformer = st[0]

    print(f"[{entity}] applying and merging adapter ...")
    peft_model = PeftModel.from_pretrained(transformer.auto_model, str(staged))
    transformer.auto_model = peft_model.merge_and_unload()

    print(f"[{entity}] saving merged model to {dest_dir} ...")
    st.save(str(dest_dir))

    # save() writes the base model's ST scaffolding; overlay the fine-tune's own
    # pooling and prompt config so query/document prompts match how it was trained.
    for rel in ["1_Pooling/config.json", "config_sentence_transformers.json"]:
        src = staged / rel
        if src.exists():
            target = dest_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)


def prepare_one(entity: str, *, force: bool = False) -> None:
    src_zip = _find_zip(entity)
    dest_dir = MODELS_DIR / entity
    installed = _read_stamp(dest_dir)
    wanted = _stamp(src_zip)

    if (dest_dir / "config.json").exists() and not force:
        if installed == wanted:
            print(f"[{entity}] up to date ({src_zip.name}) — skipping")
            return
        print(
            f"[{entity}] INSTALLED MODEL IS STALE\n"
            f"    on disk: {installed.get('source_zip') if installed else 'unknown (pre-stamp install)'}\n"
            f"    source:  {src_zip.name}\n"
            f"    Re-run with --force to replace it. Note that any cached\n"
            f"    embeddings for this entity were produced by the old model and\n"
            f"    must be rebuilt — vectors from different models are not comparable."
        )
        return

    with tempfile.TemporaryDirectory(prefix=f"{entity}-") as tmp:
        staged = _extract(src_zip, Path(tmp) / "staged")
        is_adapter = (staged / "adapter_config.json").exists()

        # Replace rather than overlay: leftovers from a differently-sharded
        # previous model would be picked up alongside the new weights.
        #
        # Move the old install aside FIRST rather than deleting in place. A
        # running backend holds the model's files open, and an in-place rmtree
        # then deletes some files before hitting the lock — leaving a model that
        # is neither the old one nor the new one. Rename either succeeds whole or
        # fails having changed nothing.
        retired = None
        if dest_dir.exists():
            retired = dest_dir.with_name(f"{entity}.old-{wanted['mtime']}")
            try:
                dest_dir.rename(retired)
            except OSError as e:
                raise SystemExit(
                    f"[{entity}] cannot replace {dest_dir}: {e}\n"
                    f"    Something has the current model open. Stop the backend\n"
                    f"    (stopApp.bat) and re-run. Nothing has been changed."
                ) from e
        dest_dir.mkdir(parents=True, exist_ok=True)

        if is_adapter:
            _merge_adapter(staged, dest_dir, entity)
        else:
            print(f"[{entity}] merged model — extracting {src_zip.name} -> {dest_dir}")
            for item in staged.iterdir():
                dst = dest_dir / item.name
                shutil.move(str(item), str(dst))

    marker = dest_dir / "config.json"
    if not marker.exists():
        raise SystemExit(f"[{entity}] install finished but config.json is missing at {marker}")
    pooling = dest_dir / "1_Pooling" / "config.json"
    if not pooling.exists():
        raise SystemExit(
            f"[{entity}] install finished but {pooling} is missing — the model would "
            f"load with default mean pooling instead of the last-token pooling it "
            f"was trained with, producing quietly wrong embeddings."
        )
    (dest_dir / STAMP_NAME).write_text(json.dumps(wanted, indent=1), encoding="utf-8")

    if retired is not None and retired.exists():
        # Best effort: if the OS still holds handles this stays behind as
        # <entity>.old-<mtime> for manual cleanup rather than failing the install.
        shutil.rmtree(retired, ignore_errors=True)
        if retired.exists():
            print(f"[{entity}] previous install left at {retired} — delete it when convenient")

    files = sum(1 for p in dest_dir.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in dest_dir.rglob("*") if p.is_file())
    print(f"[{entity}] done — {files} files, {size / 1e9:.2f} GB")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    force = "--force" in args
    targets = [a for a in args if not a.startswith("--")] or ENTITIES
    for entity in targets:
        if entity not in ENTITIES:
            raise SystemExit(f"unknown entity '{entity}', expected one of {ENTITIES}")
        prepare_one(entity, force=force)


if __name__ == "__main__":
    main()
