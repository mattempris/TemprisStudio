"""JobBERT-v2 as a selectable alternative to jobQWEN for the job slot.

Runs on CPU. It does load JobBERT-v2 (425MB), which is small and quick, but never
loads jobQWEN — so nothing here competes for the GPU.

The property that matters most is that switching models invalidates cached job
embeddings. Both models emit 1024 dims, so their vectors are shape-compatible and
would mix silently: clustering would complete and return plausible nonsense. Only
the fingerprint can catch it, so that is what is asserted hardest here.
"""
from __future__ import annotations

import sys

import numpy as np

from app.services import embeddings
from app.services.embeddings import EmbeddingService, get_embedding_service

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("=== registry ===")
for entity in ("job", "skill", "task"):
    names = [m.name for m in embeddings.models_for(entity)]
    print(f"  {entity:6} default={embeddings.resolve_model(entity).name:12} available={names}")

print("\n=== both job models are installed ===")
svc: EmbeddingService = get_embedding_service()
for name in ("jobQWEN", "JobBERT-v2"):
    print(f"  {name:12} installed={svc.is_ready('job', name)}")
    assert svc.is_ready("job", name), f"{name} is not installed"

print("\n=== fingerprints differ, which is the only guard against mixing ===")
fp_qwen = svc.fingerprint("job", "jobQWEN")
fp_bert = svc.fingerprint("job", "JobBERT-v2")
print(f"  jobQWEN    -> {fp_qwen}")
print(f"  JobBERT-v2 -> {fp_bert}")
assert fp_qwen != fp_bert, "fingerprints must differ or a model swap goes unnoticed"
assert embeddings.MODELS["jobQWEN"].dim == embeddings.MODELS["JobBERT-v2"].dim == 1024, (
    "both are 1024-dim; if that ever changes, update the comments claiming a shape "
    "check cannot distinguish them"
)

print("\n=== a cache written by one model is refused by the other ===")
embeddings.assert_cache_current("job", fp_bert)  # matches when JobBERT is selected
print("  same model -> allowed")
try:
    embeddings.assert_cache_current("job", fp_qwen)
except embeddings.StaleEmbeddingCache as e:
    print(f"  cross-model -> REFUSED: {str(e)[:100]}...")
else:
    raise AssertionError("a jobQWEN cache was accepted while JobBERT-v2 is selected")

print("\n=== JobBERT-v2 encodes on CPU ===")
titles = [
    "Water Network Technician",
    "Network Maintenance Operative",
    "Head of Financial Planning",
    "Wastewater Process Scientist",
]
v = svc.embed_documents("job", titles, device="cpu", model="JobBERT-v2")
print(f"  shape {v.shape} | norms {np.round(np.linalg.norm(v, axis=1), 4)}")
assert v.shape == (len(titles), 1024)
sims = v @ v.T
print("  similarity matrix:")
for row in sims:
    print("    " + " ".join(f"{x:5.3f}" for x in row))
# The two network-maintenance titles are near-synonyms; finance is unrelated.
assert sims[0, 1] > sims[0, 2], (sims[0, 1], sims[0, 2])
print(f"  near-synonym titles {sims[0,1]:.3f} > unrelated {sims[0,2]:.3f}: OK")

print("\n=== chunked progress still matches unchunked, on this model too ===")
many = [f"{t} {i}" for i in range(70) for t in titles][:300]
plain = svc.embed_documents("job", many, device="cpu", model="JobBERT-v2")
seen: list[tuple[int, int]] = []
chunked = svc.embed_documents(
    "job", many, device="cpu", model="JobBERT-v2", progress=lambda d, t: seen.append((d, t))
)
assert np.allclose(plain, chunked), "chunking changed JobBERT-v2 output"
assert seen[-1] == (len(many), len(many))
print(f"  {len(many)} texts, {len(seen)} progress reports, identical output: OK")

print("\n=== a query does not pass prompt_name to a model without prompts ===")
q = svc.embed_query("job", "water network maintenance", model="JobBERT-v2", device="cpu")
print(f"  embed_query shape {q.shape}")
assert q.shape == (1024,)
nearest = titles[int(np.argmax(v @ q))]
print(f"  nearest title: {nearest!r}")

print("\n=== unknown / wrong-entity selections are rejected ===")
for bad, why in [("nope", "unknown"), ("skillQWEN", "wrong entity")]:
    try:
        embeddings.resolve_model("job", bad)
    except ValueError as e:
        print(f"  {bad!r} ({why}) -> {str(e)[:80]}...")
    else:
        raise AssertionError(f"{bad!r} should have been rejected")

print("\nJOBBERT-V2 TESTS PASSED (CPU only, jobQWEN never loaded)")
