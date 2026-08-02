"""Verify embed_documents' progress chunking without loading a real model.

Substitutes a stub encoder, so this runs on CPU in milliseconds and never touches
the GPU. What it checks is the part that could be wrong: that chunking reports
monotonically to the true total, and that the chunked result is identical to the
unchunked one — the whole change is only safe because each text is encoded
independently.
"""
from __future__ import annotations

import sys

import numpy as np

from app.services import embeddings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class StubModel:
    """Deterministic per-text vector, so chunk boundaries would be visible as a
    mismatch if encoding were order- or batch-dependent."""

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts, **kwargs):
        self.calls += 1
        # +1 so no text can produce the zero vector, which would normalise to NaN
        # and then compare unequal to itself, failing the identity check for a
        # reason that has nothing to do with chunking.
        rows = [np.full(4, float(hash(t) % 97 + 1), dtype=np.float32) for t in texts]
        arr = np.vstack(rows)
        return arr / np.linalg.norm(arr, axis=1, keepdims=True)


stub = StubModel()
embeddings._load_model.cache_clear()
# Signature is (model_name, device) — accept anything so this stub does not have
# to be revisited every time the real loader gains a parameter.
embeddings._load_model = lambda *a, **k: stub  # type: ignore[assignment]
svc = embeddings.EmbeddingService()

CHUNK = embeddings._PROGRESS_CHUNK
print(f"progress chunk = {CHUNK}\n")

for n in (5, CHUNK, CHUNK + 1, CHUNK * 3 + 7):
    texts = [f"role number {i}" for i in range(n)]

    stub.calls = 0
    plain = svc.embed_documents("job", texts)
    plain_calls = stub.calls

    seen: list[tuple[int, int]] = []
    stub.calls = 0
    chunked = svc.embed_documents("job", texts, progress=lambda d, t: seen.append((d, t)))
    chunked_calls = stub.calls

    identical = np.array_equal(plain, chunked)
    monotonic = all(b[0] > a[0] for a, b in zip(seen, seen[1:]))
    reaches_total = bool(seen) and seen[-1] == (n, n)
    totals_consistent = all(t == n for _, t in seen)

    print(f"n={n:<5} encode calls {plain_calls} -> {chunked_calls} | "
          f"reports {len(seen):>2} | last={seen[-1] if seen else None}")
    print(f"        identical output: {identical} | monotonic: {monotonic} | "
          f"reaches total: {reaches_total} | totals consistent: {totals_consistent}")
    assert identical, f"n={n}: chunking changed the vectors"
    assert monotonic, f"n={n}: progress went backwards"
    assert reaches_total, f"n={n}: progress never reached {n}"
    assert totals_consistent, f"n={n}: reported an inconsistent total"

# No callback must mean no behaviour change at all.
stub.calls = 0
svc.embed_documents("job", [f"r{i}" for i in range(CHUNK * 2)])
assert stub.calls == 1, f"without a progress callback it should stay one call, got {stub.calls}"
print(f"\nno callback -> single encode call: OK")

print("\nEMBED PROGRESS TESTS PASSED (no GPU used)")
