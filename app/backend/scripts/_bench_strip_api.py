"""Time the strip stage over the real HTTP API at two fan-out widths.

Confirms the `workers` query parameter actually reaches the thread pool, rather
than the setting existing but being ignored somewhere in the chain.
"""
from __future__ import annotations

import json
import sys
import time

import requests
import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CLIENT = "mercer-demo"
BASE = "http://localhost:9400/api/projects"


def run(project: str, workers: int) -> float:
    p = f"{BASE}/{CLIENT}/{project}"
    n = requests.get(f"{p}/summary", timeout=30).json()["raw_records"]
    job = requests.post(f"{p}/strip?workers={workers}", timeout=60).json()
    if "job_id" not in job:
        raise SystemExit(f"start failed: {job}")

    t0 = time.perf_counter()
    ws = websocket.create_connection(f"ws://localhost:9400/ws/pipeline/{job['job_id']}", timeout=1800)
    ws.settimeout(1800)
    try:
        while True:
            msg = json.loads(ws.recv())
            if msg.get("type") == "error":
                raise SystemExit(f"job failed: {msg.get('message')}")
            if msg.get("type") == "complete":
                break
    finally:
        ws.close()
    elapsed = time.perf_counter() - t0
    print(f"  workers={workers:<3} {n} records in {elapsed:6.1f}s  ({elapsed / n:.2f}s/record)")
    return elapsed


project = sys.argv[1] if len(sys.argv) > 1 else "headcount-test"
print(f"strip via HTTP on {CLIENT}/{project}")
slow = run(project, 2)
fast = run(project, 16)
print(f"\n2 -> 16 workers: {slow / fast:.1f}x faster")
print("PASS: the workers parameter reaches the pool" if fast < slow * 0.75
      else "INCONCLUSIVE: no clear speedup — check the plumbing")
