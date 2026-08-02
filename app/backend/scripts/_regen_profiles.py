"""Regenerate job profiles + JE for a project, following the job over the WebSocket.

Used to verify the step-7 template path end to end: generation now builds its
structured-output schema and its per-section prompt guidance from the project's
profile template, so this confirms a real run still produces complete profiles.
"""
from __future__ import annotations

import json
import sys

import requests
import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CLIENT, PROJECT = "mercer-demo", sys.argv[1] if len(sys.argv) > 1 else "e2e-1785572258"
P = f"http://localhost:9400/api/projects/{CLIENT}/{PROJECT}"

handle = requests.post(f"{P}/profiles/generate", timeout=60)
handle.raise_for_status()
job = handle.json()
print(f"job {job['job_id']}")

ws = websocket.create_connection(f"ws://localhost:9400/ws/pipeline/{job['job_id']}", timeout=3600)
ws.settimeout(3600)
last = ""
try:
    while True:
        msg = json.loads(ws.recv())
        t = msg.get("type")
        if t == "progress":
            line = f"  {msg.get('current')}/{msg.get('total')} {msg.get('message','')}"
            if line != last:
                print(line[:110])
                last = line
        elif t == "stage_start":
            print(f"  -> {msg.get('message','')}")
        elif t == "error":
            print(f"FAILED: {msg.get('message')}")
            sys.exit(1)
        elif t == "complete":
            print(f"\nsummary: {json.dumps(msg.get('summary', {}))}")
            break
finally:
    ws.close()

profiles = requests.get(f"{P}/profiles", timeout=60).json()["profiles"]
print(f"\n{len(profiles)} profiles:")
for p in profiles:
    print(f"  {p['title']}: {p.get('level_name')} ({p.get('aggregate_score')}) stale={p['stale']}")

# Every default section should be present in the regenerated content.
from app.services.job_profile import template_config as tpl  # noqa: E402

expected = set(tpl.order(tpl.default_sections()))
missing_any = False
for p in profiles:
    full = requests.get(f"{P}/profiles/{p['profile_key']}", timeout=60).json()
    present = {k for k, v in full["content"].items() if v}
    missing = expected - present
    if missing:
        missing_any = True
        print(f"  {p['title']}: MISSING {sorted(missing)}")
print(
    "\nall default sections present in every profile: "
    + ("NO" if missing_any else "OK")
)
