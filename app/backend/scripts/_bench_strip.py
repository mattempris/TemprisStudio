"""Measure strip throughput: is it actually parallel, and where is the time going?

Times one call alone, then the same 8 records at increasing concurrency. If the
pool works, wall time for 8 records at 8 workers should be close to one call, not
eight.
"""
from __future__ import annotations

import sys
import time

from app.services import stripping
from app.services.project_service import ProjectService

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

state = ProjectService().load_state("mercer-demo", sys.argv[1] if len(sys.argv) > 1 else "hris-test")
records = [(r.job_title, r.raw_text) for r in state.raw_records][:8]
print(f"{len(records)} records, input sizes: {[len(t) for _, t in records]}")
print(f"mean input {sum(len(t) for _, t in records) // len(records)} chars\n")

t0 = time.perf_counter()
one = stripping.strip_one(records[0][1], job_title=records[0][0])
single = time.perf_counter() - t0
print(f"single call: {single:.1f}s  ({len(records[0][1])} -> {len(one.stripped_text)} chars, "
      f"fidelity {one.extractive_fidelity:.2f})\n")

for workers in (1, 4, 8):
    t0 = time.perf_counter()
    stripping.strip_many(records, workers=workers)
    elapsed = time.perf_counter() - t0
    per = elapsed / len(records)
    speedup = (single * len(records)) / elapsed
    print(f"workers={workers:<3} {len(records)} records in {elapsed:6.1f}s "
          f"({per:5.1f}s/record, {speedup:4.1f}x vs serial estimate)")

print(f"\nExtrapolated for {len(state.raw_records)} records:")
for workers in (8, 16, 32):
    waves = -(-len(state.raw_records) // workers)
    print(f"  workers={workers:<3} ~{waves} waves x {single:.0f}s = ~{waves * single / 60:.1f} min")
