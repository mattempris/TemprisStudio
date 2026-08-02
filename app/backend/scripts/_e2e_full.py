"""Full end-to-end run through the real HTTP API: instructions.txt steps 1-11.

Uses the 7 real sample JDs. Every stage goes over HTTP exactly as the frontend
calls it, and long stages are followed over the WebSocket the UI uses, so this
exercises the actual wiring rather than the services in isolation.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests
import websocket  # from websocket-client

# The Windows console defaults to cp1252, which can't encode the arrows and
# curly quotes that appear in real job-description text.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:9400"
CLIENT = "mercer-demo"
PROJECT = f"e2e-{int(time.time())}"
JD_DIR = Path(r"C:\Users\matt_\OneDrive\Desktop\JAStudio\Legacy jaStudio\2. Job Profile\a.Before")

P = f"{BASE}/api/projects/{CLIENT}/{PROJECT}"


def check(r: requests.Response, what: str) -> dict:
    if not r.ok:
        print(f"\nFAILED {what}: HTTP {r.status_code}\n{r.text[:800]}")
        sys.exit(1)
    return r.json() if r.content else {}


def follow(handle: dict, what: str, timeout: int = 1800) -> dict:
    """Follow a job over the same WebSocket the UI uses."""
    url = f"ws://localhost:9400/ws/pipeline/{handle['job_id']}"
    ws = websocket.create_connection(url, timeout=timeout)
    ws.settimeout(timeout)
    summary, last = {}, ""
    try:
        while True:
            msg = json.loads(ws.recv())
            t = msg.get("type")
            if t == "progress":
                line = f"    {msg.get('current')}/{msg.get('total')} {msg.get('message','')}"
                if line != last:
                    print(line[:110])
                    last = line
            elif t == "stage_start":
                print(f"    → {msg.get('message','')}")
            elif t == "error":
                print(f"\nFAILED {what}: {msg.get('message')}")
                sys.exit(1)
            elif t == "complete":
                summary = msg.get("summary", {})
                break
    finally:
        ws.close()
    print(f"  {what}: {json.dumps(summary)[:300]}")
    return summary


def step(n: str, title: str) -> None:
    print(f"\n{'='*72}\n{n}  {title}\n{'='*72}")


t_start = time.perf_counter()

step("0", "Create project")
created = check(requests.post(f"{BASE}/api/projects", json={
    "client_slug": CLIENT, "project_name": PROJECT}), "create")
# The server derives the slug from the name; use what it actually created.
PROJECT = created.get("project_slug", PROJECT)
P = f"{BASE}/api/projects/{CLIENT}/{PROJECT}"
print(f"  {CLIENT}/{PROJECT}")

step("1", "Ingest 7 real job descriptions")
files = [("files", (p.name, p.read_bytes(), "text/plain")) for p in sorted(JD_DIR.glob("*.txt"))]
r = check(requests.post(f"{P}/ingest/files", files=files, timeout=120), "ingest")
print(f"  {r['total_records']} records, {len(r['errors'])} errors")

step("2", "Strip irrelevant content")
follow(check(requests.post(f"{P}/strip"), "strip"), "strip")

step("3", "Dedupe")
follow(check(requests.post(f"{P}/dedupe/build"), "dedupe build"), "dedupe build")
prev = check(requests.get(f"{P}/dedupe/preview?threshold=0.9"), "dedupe preview")
print(f"  at 0.90: {prev['group_count']} groups, {prev['items_merged_away']} merged away")
check(requests.post(f"{P}/dedupe/confirm", json={"threshold": 0.9}), "dedupe confirm")

step("4", "Normalise")
follow(check(requests.post(f"{P}/normalize"), "normalize"), "normalize")

step("5", "Cluster (interactive k) and name")
follow(check(requests.post(f"{P}/cluster/build"), "cluster build"), "cluster build")
pv = check(requests.get(f"{P}/cluster/preview-cut?k_families=2&k_categories=3&k_profiles=5"), "preview")
print(f"  preview 2/3/5 -> profile sizes {pv['profile_sizes']}")
follow(check(requests.post(f"{P}/cluster/confirm", json={
    "k_families": 2, "k_categories": 3, "k_profiles": 5, "gate": 0.58}), "cluster confirm"),
    "cluster+name", timeout=2400)

step("6-7", "Job profiles + job evaluation")
# Two steps now: documents, then evaluation against the framework.
follow(check(requests.post(f"{P}/profiles/generate"), "profiles"), "profiles", timeout=3600)
follow(check(requests.post(f"{P}/evaluation/run"), "evaluation"), "evaluation", timeout=3600)
profs = check(requests.get(f"{P}/profiles"), "list profiles")
for p in profs["profiles"]:
    print(f"    {p['title']}: {p.get('level_name')} ({p.get('aggregate_score')})")

step("8-9", "Skills taxonomy + proficiency")
follow(check(requests.post(f"{P}/skills/infer", json={"profile_keys": None}), "skills infer"),
       "skills infer", timeout=2400)
follow(check(requests.post(f"{P}/skills/cluster/build"), "skills tree"), "skills tree")
sk = check(requests.get(f"{P}/skills/summary"), "skills summary")
kf, kc, kk = 2, 4, max(4, min(12, sk["inferred_skills"] // 3))
print(f"  clustering {sk['inferred_skills']} skills into {kf}/{kc}/{kk}")
follow(check(requests.post(f"{P}/skills/cluster/confirm", json={
    "k_families": kf, "k_categories": kc, "k_clusters": kk, "gate": 0.58}), "skills confirm"),
    "skills cluster", timeout=2400)
follow(check(requests.post(f"{P}/skills/proficiency/generate"), "proficiency"),
       "proficiency", timeout=3600)

step("10", "Task taxonomy")
follow(check(requests.post(f"{P}/tasks/infer", json={"profile_keys": None}), "tasks infer"),
       "tasks infer", timeout=2400)
follow(check(requests.post(f"{P}/tasks/cluster/build"), "tasks tree"), "tasks tree")
tk = check(requests.get(f"{P}/tasks/summary"), "tasks summary")
tkk = max(4, min(12, tk["inferred_tasks"] // 3))
print(f"  clustering {tk['inferred_tasks']} tasks into 2/4/{tkk}")
follow(check(requests.post(f"{P}/tasks/cluster/confirm", json={
    "k_domains": 2, "k_categories": 4, "k_tasks": tkk, "gate": 0.58}), "tasks confirm"),
    "tasks cluster", timeout=2400)

step("11", "3rd-party taxonomy matching")
follow(check(requests.post(f"{P}/matching/run", json={
    "industries": None, "shortlist_size": 12, "assign_level": True}), "matching"),
    "matching", timeout=2400)

step("✓", "Verify the browsable outputs the UI renders")
st = check(requests.get(f"{P}/skills/taxonomy"), "skills taxonomy")
fam = st["families"][0]
print(f"  skills: {len(st['families'])} families; top = {fam['name']!r} "
      f"({fam['skill_count']} skills, {fam['jobs_requiring_count']} jobs)")
assert "skill_count" in fam and "jobs_requiring_count" in fam, "family aggregates missing"
cat = fam["categories"][0]
assert "skill_count" in cat, "category aggregates missing"
print(f"    category rollup present: {cat['name']!r} ({cat['skill_count']} skills)")

tt = check(requests.get(f"{P}/tasks/taxonomy"), "tasks taxonomy")
dom = tt["domains"][0]
print(f"  tasks: {len(tt['domains'])} domains, total time {tt['total_proportion']}%; "
      f"top = {dom['name']!r} ({dom['proportion_sum']}%)")

mb = check(requests.get(f"{P}/matching/browse"), "matching browse")
print(f"  matching: {len(mb['families'])} external families, "
      f"{len(mb['unmatched'])} unmatched, summary={json.dumps(mb['summary'])[:160]}")
for f in mb["families"]:
    print(f"    {f['name']}: {f['profile_count']} profiles, {f['needs_review']} to review")
    for s in f.get("sub_families", []):
        for sp in s.get("specializations", []):
            for pr in sp["profiles"]:
                print(f"      {pr['profile_title']} -> {sp['title']} [{pr['level_code']}] "
                      f"conf {pr['confidence']:.2f}")

srch = check(requests.get(f"{P}/matching/search?q=finance"), "search")
print(f"  taxonomy search 'finance': {srch['total']} hits, first = {srch['results'][0]['title']!r}")

print(f"\n{'='*72}")
print(f"FULL E2E PASSED in {(time.perf_counter()-t_start)/60:.1f} min — {CLIENT}/{PROJECT}")
