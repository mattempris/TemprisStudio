"""End-to-end Phase 1 run through the real HTTP API with the real sample JDs.

Creates a throwaway project under client-mercer-demo, walks every stage, then
cleans up. Reports clearly at whichever stage it stops, so a mid-pipeline API
outage is distinguishable from a code failure.
"""
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:9400"
CLIENT = "mercer-demo"
PROJECT_NAME = f"jastudio-e2e-{int(time.time())}"
BEFORE_DIR = Path(r"C:\Users\matt_\OneDrive\Desktop\JAStudio\Legacy jaStudio\2. Job Profile\a.Before")

http = httpx.Client(base_url=BASE, timeout=1200.0)


def step(msg: str) -> None:
    print(f"\n{'=' * 66}\n{msg}\n{'=' * 66}")


def check(resp: httpx.Response, what: str) -> dict:
    if resp.status_code >= 400:
        print(f"  FAILED {what}: HTTP {resp.status_code} {resp.text[:400]}")
        sys.exit(1)
    return resp.json() if resp.content else {}


def wait_for_job(handle: dict, label: str) -> dict:
    """Poll the project summary until the job clears (the WS stream is exercised
    separately; polling keeps this script dependency-free)."""
    print(f"  job {handle['job_id']} ({handle['stage']}) started")
    deadline = time.time() + 1200
    while time.time() < deadline:
        time.sleep(4)
        s = check(http.get(f"/api/projects/{CLIENT}/{proj}/summary"), "summary")
        if s["active_job_id"] is None:
            print(f"  {label} finished")
            return s
        print(f"    …still running ({s['active_job_stage']})")
    print(f"  TIMED OUT waiting for {label}")
    sys.exit(1)


step("create project")
created = check(
    http.post("/api/projects", json={"client_slug": CLIENT, "project_name": PROJECT_NAME}), "create project"
)
proj = created["project_slug"]
print(f"  {CLIENT}/{proj}")

try:
    step("upload the 7 real sample job descriptions")
    files = sorted(BEFORE_DIR.glob("*.txt"))
    payload = [("files", (f.name, f.read_bytes(), "text/plain")) for f in files]
    up = check(http.post(f"/api/projects/{CLIENT}/{proj}/ingest/files", files=payload), "upload")
    print(f"  added {len(up['added'])}, errors {len(up['errors'])}, total {up['total_records']}")
    assert up["total_records"] == len(files), up
    for a in up["added"]:
        print(f"    {a['filename']}: {a['chars']} chars")

    step("step 1 — strip")
    h = check(http.post(f"/api/projects/{CLIENT}/{proj}/strip"), "strip")
    s = wait_for_job(h, "strip")
    print(f"  stripped_records={s['stripped_records']}")
    assert s["stripped_records"] == len(files)

    step("step 2 — embed + dedupe threshold sweep")
    h = check(http.post(f"/api/projects/{CLIENT}/{proj}/dedupe/build"), "dedupe build")
    wait_for_job(h, "dedupe build")
    for thr in (0.95, 0.90, 0.85, 0.75):
        t0 = time.time()
        prev = check(
            http.get(f"/api/projects/{CLIENT}/{proj}/dedupe/preview", params={"threshold": thr}), "preview"
        )
        ms = (time.time() - t0) * 1000
        print(f"  threshold {thr}: {prev['group_count']} groups, "
              f"{prev['items_merged_away']} merged ({ms:.0f}ms)")
    conf = check(
        http.post(f"/api/projects/{CLIENT}/{proj}/dedupe/confirm", json={"threshold": 0.9}), "confirm dedupe"
    )
    print(f"  confirmed {conf['groups']} distinct jobs")

    step("step 3 — normalize")
    h = check(http.post(f"/api/projects/{CLIENT}/{proj}/normalize"), "normalize")
    s = wait_for_job(h, "normalize")
    print(f"  normalized_profiles={s['normalized_profiles']}")

    step("steps 4-5 — build tree + k preview sweep")
    h = check(http.post(f"/api/projects/{CLIENT}/{proj}/cluster/build"), "cluster build")
    wait_for_job(h, "cluster build")
    n = s["normalized_profiles"]
    for kp in (3, 4, 5):
        t0 = time.time()
        prev = check(
            http.get(
                f"/api/projects/{CLIENT}/{proj}/cluster/preview-cut",
                params={"k_families": 2, "k_categories": min(3, kp), "k_profiles": kp},
            ),
            "preview-cut",
        )
        ms = (time.time() - t0) * 1000
        print(f"  k_profiles={kp}: sizes={prev['profile_sizes']} "
              f"singletons={prev['singleton_profiles']} ({ms:.0f}ms)")
    # guardrail must reject a non-nesting combination
    bad = http.get(
        f"/api/projects/{CLIENT}/{proj}/cluster/preview-cut",
        params={"k_families": 5, "k_categories": 3, "k_profiles": 4},
    )
    print(f"  non-nesting k rejected with HTTP {bad.status_code}")
    assert bad.status_code == 422, bad.text

    step("step 6 — stability, routing, naming")
    h = check(
        http.post(
            f"/api/projects/{CLIENT}/{proj}/cluster/confirm",
            json={"k_families": 2, "k_categories": 3, "k_profiles": 4, "gate": 0.58, "n_perturb": 20},
        ),
        "cluster confirm",
    )
    s = wait_for_job(h, "cluster confirm")
    hier = check(http.get(f"/api/projects/{CLIENT}/{proj}/cluster/hierarchy"), "hierarchy")
    print("  hierarchy:")
    for fam in hier["families"]:
        print(f"    {fam['name']}")
        for cat in fam["categories"]:
            print(f"      {cat['name']}")
            for prof in cat["profiles"]:
                titles = [t for it in prof["items"] for t in it["source_titles"]]
                print(f"        {prof['name']}  <- {titles}")
                for it in prof["items"]:
                    flag = ""
                    if it["routed_by_llm"]:
                        flag = f" [routed conf={it['route_confidence']:.2f}"
                        flag += ", MOVED]" if it["moved_by_llm"] else "]"
                    stab = f"{it['stability_score']:.2f}" if it["stability_score"] is not None else "n/a"
                    print(f"          stability={stab}{flag}")

    step("step 7 — job profiles + JE")
    h = check(
        http.post(f"/api/projects/{CLIENT}/{proj}/profiles/generate", params={"run_je": "true"}),
        "profile generation",
    )
    s = wait_for_job(h, "profile generation + JE")
    print(f"  job_profiles={s['job_profiles']} je_results={s['je_results']}")

    listing = check(http.get(f"/api/projects/{CLIENT}/{proj}/profiles"), "list profiles")
    print("\n  aggregate-first profile list (what the browser shows by default):")
    for row in listing["profiles"]:
        score = f"{row.get('aggregate_score', 0):.1f}" if row.get("has_je") else "—"
        rng = (
            f"{row.get('spread_low', 0):.0f}-{row.get('spread_high', 0):.0f}"
            if row.get("has_je")
            else "—"
        )
        print(f"    {row['title'][:44]:44s} {score:>6s}  {row.get('level_name', '—'):26s} range {rng}")

    if listing["profiles"] and listing["profiles"][0].get("has_je"):
        key = listing["profiles"][0]["profile_key"]
        det = check(http.get(f"/api/projects/{CLIENT}/{proj}/profiles/{key}/je"), "je detail")
        print(f"\n  drill-down for {key}:")
        print(f"    persona scores: {det['persona_scores']}")
        print(f"    domain rollups (Balanced): {det['domain_rollups']['Balanced']}")
        first_domain = det["framework"]["domains"][0]["name"]
        print(f"    sample rationale: {det['personas']['Balanced'][first_domain]['Rationale'][:150]}")

        step("exports")
        caps = check(http.get(f"/api/projects/{CLIENT}/{proj}/export/capabilities"), "caps")
        print(f"  capabilities: {caps}")
        for fmt in ("html", "docx"):
            r = http.get(f"/api/projects/{CLIENT}/{proj}/profiles/{key}/export/{fmt}")
            print(f"  {fmt}: HTTP {r.status_code}, {len(r.content)} bytes")
            assert r.status_code == 200 and len(r.content) > 500
        r = http.get(f"/api/projects/{CLIENT}/{proj}/profiles/{key}/export/pdf")
        print(f"  pdf: HTTP {r.status_code} ({'available' if r.status_code == 200 else 'unavailable as expected on Windows'})")

    print("\n" + "=" * 66)
    print("END-TO-END PHASE 1 PIPELINE PASSED")
    print("=" * 66)

finally:
    step("cleanup")
    from azure.identity import ClientSecretCredential
    from azure.storage.blob import BlobServiceClient

    from app.core.config import get_settings

    cfg = get_settings()
    svc = BlobServiceClient(
        account_url=f"https://{cfg.azure_blob_account}.blob.core.windows.net",
        credential=ClientSecretCredential(cfg.azure_tenant_id, cfg.azure_client_id, cfg.azure_client_secret),
    )
    cc = svc.get_container_client(f"client-{CLIENT}")
    deleted = 0
    for blob in cc.list_blobs(name_starts_with=f"job-architecture/{proj}/"):
        cc.delete_blob(blob.name)
        deleted += 1
    print(f"  deleted {deleted} blobs for {proj}")
