"""Download TechWolf/JobBERT-v2 into data/models/JobBERT-v2.

Unlike the Qwen models, which ship as zips in ../models/, this one comes from the
HuggingFace Hub — so it needs fetching once per checkout rather than unzipping.
The stamp it writes records the commit sha, which is what the embedding
fingerprint uses to tell one build from another.

Usage:  python -m scripts.download_jobbert
"""
from __future__ import annotations

import json
import sys

from huggingface_hub import model_info, snapshot_download

from app.core.config import MODELS_DIR

REPO = "TechWolf/JobBERT-v2"
DEST = MODELS_DIR / "JobBERT-v2"


def main() -> int:
    force = "--force" in sys.argv
    if (DEST / "config.json").exists() and not force:
        print(f"[JobBERT-v2] already present at {DEST} — pass --force to re-download")
        return 0

    print(f"[JobBERT-v2] downloading {REPO} -> {DEST} (~425MB) ...")
    snapshot_download(REPO, local_dir=str(DEST))

    info = model_info(REPO)
    (DEST / "installed_from.json").write_text(
        json.dumps(
            {
                "source_repo": REPO,
                "revision": info.sha,
                "downloaded_via": "huggingface_hub.snapshot_download",
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"[JobBERT-v2] done — revision {info.sha[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
