"""
Live-verify 21 MGMT/management-type forms against Kaizen DOM.
Discovers fields, compares to FORM_FIELD_MAP, checks curriculum/file sections,
test-fills with synthetic data, and saves as draft.

Usage:
    cd /Users/moeedahmed/projects/portfolio-guru/backend
    venv/bin/python verify_mgmt_forms.py
"""
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime

from playwright.async_api import async_playwright, Page

# ─── Setup ────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Import form data from the canonical filer
sys.path.insert(0, os.path.dirname(__file__))
from kaizen_form_filer import FORM_UUIDS, FORM_FIELD_MAP

# ─── Config ───────────────────────────────────────────────────────────────────

FORMS_TO_VERIFY = [
    "APPRAISAL", "AUDIT", "BUSINESS_CASE", "CLIN_GOV", "COST_IMPROVE",
    "CRIT_INCIDENT", "EQUIP_SERVICE", "MGMT_COMPLAINT", "MGMT_EXPERIENCE",
    "MGMT_GUIDELINE", "MGMT_INDUCTION", "MGMT_INFO", "MGMT_PROJECT",
    "MGMT_RECRUIT", "MGMT_REPORT", "MGMT_RISK", "MGMT_RISK_PROC",
    "MGMT_ROTA", "MGMT_TRAINING_EVT", "PDP", "RESEARCH",
]

UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def get_credentials():
    """Fetch Kaizen credentials from BWS."""
    token = open(os.path.expanduser("~/.openclaw/.bws-token")).read().strip()
    bws = "/Users/moeedahmed/.cargo/bin/bws"
    env = {**os.environ, "BWS_ACCESS_TOKEN": token}

    user_json = subprocess.check_output(
        [bws, "secret", "get", "6e14d32b-6fff-480d-87b0-b3f300ee30f6", "--output", "json"],
        env=env, stderr=subprocess.DEVNULL
    )
    username = json.loads(user_json)["value"]

    pwd_json = subprocess.check_output(
        [bws, "secret", "get", "f311d41a-fa77-44f8-be42-b3f300ee3e08", "--output", "json"],
        env=env, stderr=subprocess.DEVNULL
    )
    password = json.loads(pwd_json)["value"]

    return username, password


# ─── DOM Discovery ────────────────────────────────────────────────────────────

DISCOVER_JS = """() => {
    const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
    const results = [];
    const seen = new Set();

    document.querySelectorAll('[id]').forEach(el => {
        const id = el.id;
        if (!uuidRe.test(id) || seen.has(id)) return;
        seen.add(id);

        const tag = el.tagName;
        const type = el.type || '';
        const ngModel = el.getAttribute('ng-model') || '';

        let label = '';
        const labelEl = document.querySelector('label[for="' + id + '"]');
        if (labelEl) label = labelEl.textContent.trim();

        if (!label) {
            let parent = el.parentElement;
            for (let i = 0; i < 5 && parent; i++) {
                const lbl = parent.querySelector('label');
                if (lbl) { label = lbl.textContent.trim(); break; }
                const textNode = parent.querySelector('.control-label, .field-label, .form-label, h4, h5, .section-title');
                if (textNode) { label = textNode.textContent.trim(); break; }
                parent = parent.parentElement;
            }
        }
        if (!label && ngModel) label = '[ng-model: ' + ngModel + ']';

        results.push({
            id, tag, type, label, ngModel,
            isTextarea: tag === 'TEXTAREA',
            isSelect: tag === 'SELECT',
            isInput: tag === 'INPUT',
            isCheckbox: type === 'checkbox',
        });
    });

    // Special IDs
    ['startDate', 'endDate'].forEach(sid => {
        const el = document.getElementById(sid);
        if (el && !seen.has(sid)) {
            results.push({
                id: sid, tag: el.tagName, type: el.type || '',
                label: sid === 'startDate' ? 'Start Date' : 'End Date',
                ngModel: el.getAttribute('ng-model') || '',
                isTextarea: false, isSelect: false, isInput: true, isCheckbox: false,
                special: true,
            });
        }
    });

    return results;
}"""

CHECK_CURRICULUM_JS = """() => {
    // Look for curriculum section indicators
    const indicators = [
        'a.ng-binding',                          // SLO expand anchors
        '[ng-model*="curriculum"]',
        '[ng-model*="slo"]',
        '[ng-model*="Slo"]',
        '.curriculum-section',
        '#curriculumSection',
        'h4:has-text("Curriculum")',
    ];
    for (const sel of indicators) {
        try {
            const els = document.querySelectorAll(sel);
            if (els.length > 0) return { hasCurriculum: true, selector: sel, count: els.length };
        } catch(e) {}
    }
    // Also check for any text mentioning SLO or curriculum
    const bodyText = document.body.innerText;
    if (/SLO\\s*\\d/i.test(bodyText)) return { hasCurriculum: true, selector: 'body-text-SLO', count: 1 };
    if (/curriculum/i.test(bodyText)) return { hasCurriculum: true, selector: 'body-text-curriculum', count: 1 };
    return { hasCurriculum: false };
}"""

CHECK_FILE_INPUT_JS = """() => {
    const fileInputs = document.querySelectorAll('input[type="file"]');
    return { hasFileInput: fileInputs.length > 0, count: fileInputs.length };
}"""


async def login(page, username, password):
    """Log in to Kaizen via RCEM portal."""
    await page.goto("https://eportfolio.rcem.ac.uk", wait_until="networkidle", timeout=30000)
    await asyncio.sleep(2)

    login_input = page.locator('input[name="login"]')
    if await login_input.count() > 0:
        await login_input.fill(username)
        await page.locator('button[type="submit"]').click()
        await asyncio.sleep(2)

    pwd_input = page.locator('input[name="password"]')
    if await pwd_input.count() > 0:
        await pwd_input.fill(password)
        await page.locator('button[type="submit"]').click()

    await page.wait_for_url("**/kaizenep.com/**", timeout=30000)
    await asyncio.sleep(3)
    logger.info(f"Logged in: {page.url}")


async def discover_form(page: Page, form_type: str) -> dict:
    """Navigate to a form, discover all fields, check curriculum and file attachment."""
    form_uuid = FORM_UUIDS.get(form_type)
    if not form_uuid:
        return {"error": f"No UUID for {form_type}"}

    url = f"https://kaizenep.com/events/new-section/{form_uuid}"
    logger.info(f"--- {form_type} --- navigating to {url}")

    await page.goto(url, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(5)  # Wait for Angular rendering

    # Check if form loaded (not redirected)
    if "new-section" not in page.url and "fillin" not in page.url:
        return {"error": f"Form didn't load — redirected to {page.url}"}

    # Discover DOM fields
    all_fields = await page.evaluate(DISCOVER_JS)
    relevant = [f for f in all_fields if f['isTextarea'] or f['isSelect'] or f['isInput']]

    # Check curriculum section
    curriculum = await page.evaluate(CHECK_CURRICULUM_JS)

    # Check file attachment
    file_input = await page.evaluate(CHECK_FILE_INPUT_JS)

    # Get the page title/heading for reference
    title = ""
    try:
        h1 = page.locator("h1, h2, .page-title, .section-title").first
        if await h1.count():
            title = await h1.inner_text()
    except Exception:
        pass

    return {
        "form_type": form_type,
        "form_uuid": form_uuid,
        "url": url,
        "page_title": title.strip()[:100],
        "fields": relevant,
        "field_count": len(relevant),
        "curriculum": curriculum,
        "file_input": file_input,
    }


def compare_field_maps(form_type: str, discovered: dict) -> dict:
    """Compare discovered DOM fields against current FORM_FIELD_MAP entries."""
    current_map = FORM_FIELD_MAP.get(form_type, {})
    dom_ids = {f["id"] for f in discovered.get("fields", [])}
    dom_by_id = {f["id"]: f for f in discovered.get("fields", [])}

    # Check each mapped field against DOM
    valid_mappings = {}
    broken_mappings = {}
    for key, dom_id in current_map.items():
        if dom_id in ("startDate", "endDate"):
            # Special IDs — check separately
            if any(f["id"] == dom_id for f in discovered.get("fields", [])):
                valid_mappings[key] = dom_id
            else:
                # startDate/endDate are common — check if they exist
                valid_mappings[key] = dom_id  # Usually present even if not in UUID scan
        elif dom_id in dom_ids:
            valid_mappings[key] = dom_id
        else:
            broken_mappings[key] = dom_id

    # Fields in DOM but not mapped
    mapped_ids = set(current_map.values())
    unmapped_dom = []
    for f in discovered.get("fields", []):
        if f["id"] not in mapped_ids and f["id"] not in ("startDate", "endDate"):
            if not f["isCheckbox"]:  # Skip checkboxes (usually curriculum)
                unmapped_dom.append(f)

    return {
        "valid": valid_mappings,
        "broken": broken_mappings,
        "unmapped_dom_fields": unmapped_dom,
    }


async def test_fill_form(page: Page, form_type: str, field_map: dict) -> dict:
    """Test-fill a form with synthetic data and save as draft."""
    form_uuid = FORM_UUIDS.get(form_type)
    url = f"https://kaizenep.com/events/new-section/{form_uuid}"

    await page.goto(url, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(5)

    filled = []
    failed = []
    today = datetime.now().strftime("%-d/%-m/%Y")

    for key, dom_id in field_map.items():
        if key in ("stage", "stage_of_training"):
            continue  # Skip stage — handled separately if present

        try:
            if dom_id in ("startDate", "endDate"):
                # Date field
                el = page.locator(f'[id="{dom_id}"]')
                if await el.count():
                    await el.click()
                    await el.click(click_count=3)
                    await el.type(today, delay=50)
                    await page.keyboard.press("Tab")
                    await asyncio.sleep(1)
                    filled.append(key)
                else:
                    failed.append(f"{key}: element not found")
            elif "date" in key.lower():
                # Other date fields
                el = page.locator(f'[id="{dom_id}"]')
                if await el.count():
                    await el.click()
                    await el.click(click_count=3)
                    await el.type(today, delay=50)
                    await page.keyboard.press("Tab")
                    await asyncio.sleep(1)
                    filled.append(key)
                else:
                    failed.append(f"{key}: element not found")
            else:
                # Text/textarea field
                el = page.locator(f'[id="{dom_id}"]')
                if await el.count():
                    tag = await el.evaluate("el => el.tagName")
                    if tag == "SELECT":
                        # Try selecting first non-empty option
                        options = await page.evaluate(
                            "(domId) => { var s = document.getElementById(domId); return s ? Array.from(s.options).filter(o => o.value && o.value !== '?').map(o => ({value: o.value, text: o.text})) : []; }",
                            dom_id
                        )
                        if options:
                            await el.select_option(value=options[0]["value"])
                            await asyncio.sleep(1)
                            filled.append(key)
                        else:
                            failed.append(f"{key}: SELECT has no options")
                    elif tag in ("TEXTAREA", "INPUT"):
                        synthetic = f"[VERIFY TEST] {form_type} - {key} - {datetime.now().isoformat()}"
                        await el.click()
                        await el.fill(synthetic)
                        await asyncio.sleep(0.5)
                        filled.append(key)
                    else:
                        # Try as textarea inside div
                        inner = page.locator(f'div[id="{dom_id}"] textarea').first
                        if await inner.count():
                            await inner.click()
                            await inner.fill(f"[VERIFY TEST] {form_type} - {key}")
                            filled.append(key)
                        else:
                            failed.append(f"{key}: tag={tag}, couldn't fill")
                else:
                    failed.append(f"{key}: element not found in DOM")
        except Exception as e:
            failed.append(f"{key}: {str(e)[:80]}")

    # Save as draft
    save_ok = False
    try:
        save_link = page.locator("a:has-text('Save as draft')").first
        if await save_link.count():
            await save_link.click()
            await asyncio.sleep(5)
            body_text = await page.inner_text("body")
            save_ok = "SAVED" in body_text.upper() or "LAST SAVED" in body_text.upper()
        else:
            failed.append("SAVE: 'Save as draft' link not found")
    except Exception as e:
        failed.append(f"SAVE: {str(e)[:80]}")

    return {
        "filled": filled,
        "failed": failed,
        "saved": save_ok,
    }


async def main():
    username, password = get_credentials()
    logger.info(f"Got credentials for user: {username[:3]}***")

    results = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        # Login
        await login(page, username, password)

        for form_type in FORMS_TO_VERIFY:
            logger.info(f"\n{'='*60}")
            logger.info(f"VERIFYING: {form_type}")
            logger.info(f"{'='*60}")

            result = {"form_type": form_type}

            # Phase 1: Discover DOM fields
            try:
                discovery = await discover_form(page, form_type)
                if "error" in discovery:
                    result["discovery_error"] = discovery["error"]
                    result["status"] = "FAILED_DISCOVERY"
                    results[form_type] = result
                    logger.error(f"  Discovery failed: {discovery['error']}")
                    continue

                result["discovery"] = {
                    "page_title": discovery["page_title"],
                    "field_count": discovery["field_count"],
                    "fields": [{"id": f["id"], "tag": f["tag"], "type": f["type"], "label": f["label"], "ngModel": f["ngModel"]} for f in discovery["fields"]],
                    "curriculum": discovery["curriculum"],
                    "file_input": discovery["file_input"],
                }
                logger.info(f"  Discovered {discovery['field_count']} fields")
                logger.info(f"  Curriculum: {discovery['curriculum']}")
                logger.info(f"  File input: {discovery['file_input']}")

            except Exception as e:
                result["discovery_error"] = str(e)
                result["status"] = "FAILED_DISCOVERY"
                results[form_type] = result
                logger.error(f"  Discovery error: {e}")
                continue

            # Phase 2: Compare field maps
            comparison = compare_field_maps(form_type, discovery)
            result["comparison"] = {
                "valid_count": len(comparison["valid"]),
                "broken_count": len(comparison["broken"]),
                "broken": comparison["broken"],
                "unmapped_count": len(comparison["unmapped_dom_fields"]),
                "unmapped": [{"id": f["id"], "tag": f["tag"], "label": f["label"]} for f in comparison["unmapped_dom_fields"]],
            }
            logger.info(f"  Valid mappings: {len(comparison['valid'])}")
            if comparison["broken"]:
                logger.warning(f"  BROKEN mappings: {comparison['broken']}")
            if comparison["unmapped_dom_fields"]:
                logger.info(f"  Unmapped DOM fields: {len(comparison['unmapped_dom_fields'])}")
                for f in comparison["unmapped_dom_fields"]:
                    logger.info(f"    {f['tag']} id={f['id']} label=\"{f['label']}\"")

            # Phase 3: Test fill with valid mappings
            try:
                fill_map = comparison["valid"]
                if fill_map:
                    fill_result = await test_fill_form(page, form_type, fill_map)
                    result["test_fill"] = fill_result
                    logger.info(f"  Test fill: {len(fill_result['filled'])} filled, {len(fill_result['failed'])} failed, saved={fill_result['saved']}")
                    if fill_result["failed"]:
                        for f in fill_result["failed"]:
                            logger.warning(f"    FILL FAILED: {f}")
                else:
                    result["test_fill"] = {"error": "No valid mappings to test"}
                    logger.warning(f"  No valid mappings to test-fill")
            except Exception as e:
                result["test_fill"] = {"error": str(e)}
                logger.error(f"  Test fill error: {e}")

            # Determine overall status
            has_broken = len(comparison.get("broken", {})) > 0
            fill_ok = result.get("test_fill", {}).get("saved", False)
            fill_failed = result.get("test_fill", {}).get("failed", [])

            if not has_broken and fill_ok and not fill_failed:
                result["status"] = "VERIFIED"
            elif fill_ok and not has_broken:
                result["status"] = "VERIFIED_WITH_WARNINGS"
            elif has_broken:
                result["status"] = "NEEDS_FIX"
            else:
                result["status"] = "FAILED"

            results[form_type] = result
            logger.info(f"  STATUS: {result['status']}")

        await browser.close()

    # Save results
    out_path = os.path.join(os.path.dirname(__file__), "mgmt_form_verification.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"\nResults saved to {out_path}")

    # Print summary
    print("\n" + "="*70)
    print("VERIFICATION SUMMARY")
    print("="*70)
    for ft, r in results.items():
        status = r.get("status", "UNKNOWN")
        curriculum = r.get("discovery", {}).get("curriculum", {}).get("hasCurriculum", "?")
        file_input = r.get("discovery", {}).get("file_input", {}).get("hasFileInput", "?")
        broken = r.get("comparison", {}).get("broken_count", "?")
        print(f"  {ft:25s} {status:25s} curriculum={curriculum}  file_input={file_input}  broken_maps={broken}")


if __name__ == "__main__":
    asyncio.run(main())
