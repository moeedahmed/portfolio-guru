#!/usr/bin/env python3
"""Kaizen Field UUID Validator — validates structural mapping against live DOM.

Tests that every form type UUID and field UUID from the existing Playwright
mapping still resolves correctly through browser-harness CDP.

Run: BU_CDP_WS=ws://... python3 validate_kaizen_uuids.py
"""

import json, os, sys, time
from pathlib import Path

# ── Config ──
BH_DIR = Path(__file__).parent.parent.parent  # 4 up from kaizen-rcem/
SS_DIR = BH_DIR / "agent-workspace" / "domain-skills" / "kaizen-rcem" / "screenshots" / "validation"
SS_DIR.mkdir(parents=True, exist_ok=True)

# Import the existing field maps
sys.path.insert(0, str(Path("/Users/moeedahmed/projects/portfolio-guru/backend")))
# We import the data structures directly
from kaizen_form_filer import FORM_FIELD_MAP, FORM_UUIDS

# ── Validation Steps ──

def test_form_creation():
    """Test that each form type UUID creates the correct form."""
    results = {}
    for form_name, form_uuid in FORM_UUIDS.items():
        navigate(f"/events/new-section/{form_uuid}")
        time.sleep(5)
        
        # Check we got the right form
        url = page_info().get("url", "")
        if form_uuid in url:
            print(f"  ✅ {form_name}: /events/new-section/{form_uuid}")
            capture_screenshot(SS_DIR / f"form-{form_name}.png")
            
            # Check field DOM IDs exist
            field_map = FORM_FIELD_MAP.get(form_name, {})
            found = 0
            for field_key, dom_id in field_map.items():
                if dom_id in ("startDate", "endDate"):
                    found += 1
                    continue
                exists = js(f"!!document.getElementById('{dom_id}')")
                if exists:
                    found += 1
                else:
                    print(f"    ❌ Field '{field_key}' (id={dom_id}) not found in DOM")
            
            total = len([k for k in field_map if k not in ("date_of_encounter", "end_date", "date_occurred_on")])
            results[form_name] = {"uuid": form_uuid, "fields_found": f"{found}/{len(field_map)}"}
        else:
            print(f"  ❌ {form_name}: expected {form_uuid}, got {url}")
            results[form_name] = {"uuid": form_uuid, "error": "navigation failed"}
    
    return results

def test_stage_dropdown(stage_dom_id="e0864e88-62cf-43aa-a9e5-51abd98a1cce"):
    """Verify the stage of training select exists."""
    exists = js(f"!!document.getElementById('{stage_dom_id}')")
    print(f"  Stage dropdown (id={stage_dom_id}): {'✅' if exists else '❌'}")
    return exists

def test_login_form():
    """Verify login form elements are present at eportfolio.rcem.ac.uk."""
    new_tab("https://eportfolio.rcem.ac.uk")
    time.sleep(4)
    
    checks = {
        "username_field": "document.querySelector('input[name=login]') !== null",
        "password_field": "document.querySelector('input[name=password]') !== null",
        "submit_button": "document.querySelector(\"button[type=submit]\") !== null",
    }
    results = {}
    for name, expr in checks.items():
        results[name] = js(expr)
        print(f"  {name}: {'✅' if results[name] else '❌'}")
    
    return results

def test_field_uuids_on_live_forms():
    """Open CBD form and verify field IDs."""
    cbd_uuid = FORM_UUIDS.get("CBD")
    if not cbd_uuid:
        print("  ❌ No CBD UUID found")
        return {}
    
    navigate(f"/events/new-section/{cbd_uuid}")
    time.sleep(5)
    
    cbd_fields = FORM_FIELD_MAP.get("CBD", {})
    results = {}
    for field_key, dom_id in cbd_fields.items():
        if dom_id in ("startDate", "endDate"):
            results[field_key] = "date_field"
            continue
        exists = js(f"!!document.getElementById('{dom_id}')")
        results[field_key] = exists
        if not exists:
            print(f"  ❌ CBD.{field_key} (id={dom_id}) not found")
    
    return results

# ── Main ──

def main():
    print("╔══════════════════════════════════════════╗")
    print("║  Kaizen UUID Validation Suite            ║")
    print("╚══════════════════════════════════════════╝")
    
    results = {}
    
    print("\n[1/4] Login test...")
    results["login"] = test_login_form()
    
    print(f"\n[2/4] Testing {len(FORM_UUIDS)} form type UUIDs...")
    results["form_creation"] = test_form_creation()
    
    print(f"\n[3/4] Testing selectors on live CBD form...")
    results["field_uuids"] = test_field_uuids_on_live_forms()
    
    print(f"\n[4/4] Summary:")
    form_ok = sum(1 for v in results.get("form_creation", {}).values() if "fields_found" in v)
    form_fail = sum(1 for v in results.get("form_creation", {}).values() if "error" in v)
    print(f"  Form UUIDs: {form_ok}/{len(FORM_UUIDS)} OK, {form_fail} failed")
    
    # Save results
    out_path = BH_DIR / "agent-workspace" / "domain-skills" / "kaizen-rcem" / "validation-results.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n  Results saved to {out_path}")

if __name__ == "__main__":
    main()
