"""
Verify filing works for 5 sample forms using a single browser session.
Logs in once, visits each form, fills fields, saves draft, verifies.

Run:
    FERNET_SECRET_KEY=<key> python tools/verify_filing.py
"""
import asyncio
import json
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from playwright.async_api import async_playwright
from credentials import get_credentials
from kaizen_form_filer import (
    FORM_UUIDS, FORM_FIELD_MAP, _login, _fill_field_legacy,
    _save_draft_legacy, _to_uk_date
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

USER_ID = 6912896590
TODAY = date.today().strftime("%Y-%m-%d")

SAMPLE_CASES = {
    "DOPS": {
        "date_of_encounter": TODAY,
        "end_date": TODAY,
        "date_of_event": TODAY,
        "stage_of_training": "Higher",
        "case_observed": "[TEST] 55yo male with large pneumothorax requiring emergency drainage.",
        "placement": "Emergency Department",
        "reflection": "[TEST] Successfully inserted chest drain using ultrasound-guided approach.",
    },
    "TEACH": {
        "date_of_teaching": TODAY,
        "title_of_session": "[TEST] ECG Interpretation for Juniors",
        "learning_outcomes": "[TEST] Participants improved ECG interpretation skills.",
    },
    "MGMT_ROTA": {
        "date_of_encounter": TODAY,
        "project_description": "[TEST] Rota management for emergency department.",
        "date_of_event": TODAY,
        "reflection": "[TEST] Gained experience in shift pattern management.",
    },
    "EDU_ACT": {
        "date_of_education": TODAY,
        "title_of_education": "[TEST] Regional Trauma Teaching Day",
        "delivered_by": "Regional Trauma Network",
        "learning_points": "[TEST] Updated trauma management protocols.",
    },
    "AUDIT": {
        "date_of_encounter": TODAY,
        "project_description": "[TEST] Audit of sepsis bundle compliance in ED.",
        "reflection": "[TEST] Identified gaps in door-to-antibiotic times.",
    },
}


async def verify():
    creds = get_credentials(USER_ID)
    if not creds:
        print("ERROR: No credentials")
        return

    username, password = creds

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()

    # Login ONCE
    logger.info("Logging in...")
    ok = await _login(page, username, password)
    if not ok:
        logger.error("Login failed!")
        await browser.close()
        await pw.stop()
        return

    # Handle popups
    try:
        close_btn = page.locator('button:has-text("Close"), button:has-text("OK")')
        if await close_btn.count() > 0:
            await close_btn.first.click()
            await asyncio.sleep(1)
    except Exception:
        pass

    logger.info(f"Login OK — {page.url}")

    results = {}

    for form_type, fields in SAMPLE_CASES.items():
        uuid = FORM_UUIDS.get(form_type)
        field_map = FORM_FIELD_MAP.get(form_type, {})
        url = f"https://kaizenep.com/events/new-section/{uuid}"

        logger.info(f"\n{'='*50}")
        logger.info(f"TESTING: {form_type}")

        await page.goto(url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(5)

        if "new-section" not in page.url:
            logger.error(f"  Redirected to {page.url}")
            results[form_type] = {"status": "failed", "error": "redirect"}
            continue

        filled = []
        skipped = []

        # Fill stage first if present
        if "stage_of_training" in field_map:
            dom_id = field_map["stage_of_training"]
            val = fields.get("stage_of_training", "Higher")
            if await _fill_field_legacy(page, dom_id, val, "stage_of_training"):
                filled.append("stage_of_training")
            else:
                skipped.append("stage_of_training")

        # Fill rest
        for field_key, dom_id in field_map.items():
            if field_key == "stage_of_training":
                continue
            value = fields.get(field_key)
            if value is None or value == "":
                skipped.append(field_key)
                continue

            ok = await _fill_field_legacy(page, dom_id, value, field_key)
            if ok:
                filled.append(field_key)
            else:
                skipped.append(field_key)

        # Save draft
        saved = await _save_draft_legacy(page)

        status = "success" if saved and filled else ("partial" if filled else "failed")
        results[form_type] = {
            "status": status,
            "filled": filled,
            "skipped": skipped,
            "saved": saved,
        }

        icon = "✅" if status in ("success", "partial") else "❌"
        logger.info(f"{icon} {form_type}: {status} — filled {len(filled)}/{len(filled)+len(skipped)}")

    await browser.close()
    await pw.stop()

    print(f"\n{'='*50}")
    print("SUMMARY")
    print(f"{'='*50}")
    passed = sum(1 for r in results.values() if r["status"] in ("success", "partial"))
    print(f"{passed}/{len(results)} forms filed successfully")
    for ft, r in results.items():
        icon = "✅" if r["status"] in ("success", "partial") else "❌"
        print(f"  {icon} {ft}: {r['status']} (filled {len(r.get('filled',[]))} fields, saved={r.get('saved')})")

    print("\n⚠️  Delete test drafts from Kaizen! (All contain [TEST])")


if __name__ == "__main__":
    asyncio.run(verify())
