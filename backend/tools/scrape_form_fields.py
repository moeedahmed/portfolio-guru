"""
Scrape real DOM field IDs from every Kaizen form page.

Logs in once, visits each form URL, extracts all input/textarea/select
elements with their IDs and labels, saves to /tmp/kaizen_field_scrape.json.

Run:
    FERNET_SECRET_KEY=<key> python tools/scrape_form_fields.py
"""
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from playwright.async_api import async_playwright
from credentials import get_credentials
from kaizen_form_filer import FORM_UUIDS, FORM_FIELD_MAP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

USER_ID = 6912896590
OUTPUT_PATH = "/tmp/kaizen_field_scrape.json"

# Skip forms we know work
SKIP_FORMS = {"CBD", "REFLECT_LOG"}

# JS to extract all fillable fields from a Kaizen form page
EXTRACT_JS = """
() => {
    const results = [];

    // Standard inputs, textareas, selects
    const elements = document.querySelectorAll(
        'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea, select'
    );

    for (const el of elements) {
        const id = el.id || el.name || '';
        if (!id) continue;

        // Find the closest label
        let label = '';

        // Check for explicit label
        const labelEl = document.querySelector(`label[for="${id}"]`);
        if (labelEl) {
            label = labelEl.textContent.trim();
        }

        // Check for parent label
        if (!label) {
            const parentLabel = el.closest('label');
            if (parentLabel) {
                label = parentLabel.textContent.trim();
            }
        }

        // Check for preceding sibling or Angular label patterns
        if (!label) {
            const prev = el.previousElementSibling;
            if (prev && (prev.tagName === 'LABEL' || prev.tagName === 'SPAN')) {
                label = prev.textContent.trim();
            }
        }

        // Check Angular-style: look for text in parent div before the input
        if (!label) {
            const parent = el.closest('.form-group, .field-wrapper, div');
            if (parent) {
                const textNodes = parent.querySelectorAll('label, span.field-label, .control-label');
                if (textNodes.length > 0) {
                    label = textNodes[0].textContent.trim();
                }
            }
        }

        results.push({
            id: id,
            tag: el.tagName,
            type: el.type || '',
            name: el.name || '',
            label: label,
            placeholder: el.placeholder || '',
        });
    }

    return results;
}
"""


async def scrape_all_forms():
    creds = get_credentials(USER_ID)
    if not creds:
        logger.error("No credentials found")
        return

    username, password = creds

    forms_to_scrape = {
        ft: uuid for ft, uuid in FORM_UUIDS.items()
        if ft in FORM_FIELD_MAP and ft not in SKIP_FORMS
    }

    logger.info(f"Will scrape {len(forms_to_scrape)} forms")

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()

    # Login once
    logger.info("Logging in to Kaizen...")
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

    try:
        await page.wait_for_url("**/kaizenep.com/**", timeout=30000)
    except Exception as e:
        logger.error(f"Login failed: {e}")
        await browser.close()
        await pw.stop()
        return

    await asyncio.sleep(3)
    logger.info(f"Login OK — at {page.url}")

    # Handle any popups (shared device, org selector)
    try:
        close_btn = page.locator('button:has-text("Close"), button:has-text("OK"), .modal-close')
        if await close_btn.count() > 0:
            await close_btn.first.click()
            await asyncio.sleep(1)
    except Exception:
        pass

    results = {}
    failed = []

    for form_type, uuid in forms_to_scrape.items():
        url = f"https://kaizenep.com/events/new-section/{uuid}"
        logger.info(f"Scraping {form_type}...")

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(5)  # Wait for Angular to render

            # Check we're on the right page
            if "new-section" not in page.url:
                logger.warning(f"  {form_type}: redirected to {page.url} — skipping")
                failed.append(form_type)
                continue

            # Extract fields
            fields = await page.evaluate(EXTRACT_JS)

            # Filter to interesting fields (skip navigation/chrome)
            meaningful = [
                f for f in fields
                if f["id"] not in ("", "search", "searchInput", "typeahead")
                and not f["id"].startswith("ng-")
                and f["type"] not in ("submit", "button", "search")
            ]

            results[form_type] = {
                "uuid": uuid,
                "url": url,
                "field_count": len(meaningful),
                "fields": meaningful,
            }
            logger.info(f"  {form_type}: {len(meaningful)} fields found")

        except Exception as e:
            logger.error(f"  {form_type}: FAILED — {e}")
            failed.append(form_type)

    await browser.close()
    await pw.stop()

    # Save results
    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, indent=2)

    logger.info(f"\nDone! {len(results)}/{len(forms_to_scrape)} forms scraped")
    if failed:
        logger.info(f"Failed: {failed}")
    logger.info(f"Results saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(scrape_all_forms())
