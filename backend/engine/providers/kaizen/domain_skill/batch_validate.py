#!/usr/bin/env python3
"""Batch validate all pending 2025 forms via browser-harness.
Writes results to _validation_log.json as it goes for progress tracking.
"""
import json, os, sys, time
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 1)  # unbuffered

BH_DIR = "/Users/moeedahmed/Developer/browser-harness"
SS_DIR = BH_DIR + "/agent-workspace/domain-skills/kaizen-rcem/screenshots"
UUID_FILE = BH_DIR + "/agent-workspace/domain-skills/kaizen-rcem/2025-uuids.json"
LOG_FILE = BH_DIR + "/agent-workspace/domain-skills/kaizen-rcem/_validation_log.json"

with open(UUID_FILE) as f:
    data = json.load(f)

already_done = [
    "3ce5989a-b61c-4c24-ab12-711bf928b181",
    "159831f9-6d22-4e77-851b-87e30aee37a2",
    "647665f4-a992-4541-9e17-33ba6fd1d347",
    "6577ab06-8340-47e3-952a-708a5f800dcc",
    "5f71ac04-ff45-44d2-b7a1-f8b921a8a4c8",
    "1ffbd272-8447-439c-aa03-ff99e2dbc04d",
    "3d4c6a82-f7ab-4b11-bb36-c7487de4ff2d",
]

pending = {k: v for k, v in data["forms"].items() if v not in already_done}
print(f"Total pending: {len(pending)} forms")

# Load existing log to resume
try:
    with open(LOG_FILE) as f:
        log = json.load(f)
    print(f"Resuming from log with {len(log)} results")
except:
    log = {}

results = log  # use dict for uuid -> result

def short_name(label):
    return label.split("(")[0].strip()[:30].replace(" ", "-").replace("/","-").replace(":","").replace(",","")

batch = list(pending.items())
total = len(batch)

for idx, (name, uuid) in enumerate(batch):
    if uuid in results:
        print(f"[{idx+1}/{total}] {short_name(name)} — already done, skipping")
        continue
    
    try:
        goto_url("https://kaizenep.com/events/new-section/" + uuid)
        wait_for_load()
        time.sleep(8)
        
        url = page_info().get("url", "")
        ok = uuid in url
        
        field_count = 0
        if ok:
            inputs = js("Array.from(document.querySelectorAll('input:not([type=hidden]), textarea, select')).length")
            field_count = inputs or 0
        
        label = short_name(name)
        slug = label.lower().replace(" ", "-")[:25]
        
        if ok:
            try:
                capture_screenshot(SS_DIR + f"/validate-{slug}.png", full=True)
            except Exception:
                pass
        
        results[uuid] = {
            "name": name,
            "ok": ok,
            "fields": field_count,
            "slug": slug,
        }
        
        status = "OK" if ok else "FAIL"
        print(f"[{idx+1}/{total}] {status}: {label} ({uuid[:12]}...) fields={field_count}")
        
        # Save progress every 5 forms
        if (idx + 1) % 5 == 0 or idx == total - 1:
            with open(LOG_FILE, "w") as f:
                json.dump(results, f, indent=2)
            
    except Exception as e:
        print(f"[{idx+1}/{total}] ERROR: {short_name(name)}: {e}")
        results[uuid] = {"name": name, "ok": False, "error": str(e)}

# Final save
with open(LOG_FILE, "w") as f:
    json.dump(results, f, indent=2)

oks = sum(1 for v in results.values() if v.get("ok"))
fails = sum(1 for v in results.values() if not v.get("ok", True))
print(f"\nDone: {oks} OK, {fails} FAIL out of {len(results)} total")
