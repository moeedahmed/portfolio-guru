"""
Discover DOM field IDs for unmapped Kaizen forms.
Logs in via credential store, navigates to each form, extracts UUID-based field IDs.
"""
import asyncio
import json
import os
import re
import sys

# Load FERNET key from BWS before importing credentials
from playwright.async_api import async_playwright

# Forms to discover (not yet in FORM_FIELD_MAP)
FORMS_TO_DISCOVER = [
    "REFLECT_LOG", "TEACH_OBS", "ESLE_ASSESS", "TEACH_CONFID",
    "APPRAISAL", "PDP", "BUSINESS_CASE", "CLIN_GOV", "AUDIT", "RESEARCH",
    "EDU_MEETING", "EDU_MEETING_SUPP", "CRIT_INCIDENT", "COST_IMPROVE",
    "EQUIP_SERVICE", "MGMT_ROTA", "MGMT_RISK", "MGMT_RECRUIT",
    "MGMT_PROJECT", "MGMT_RISK_PROC", "MGMT_TRAINING_EVT",
    "MGMT_GUIDELINE", "MGMT_INFO", "MGMT_INDUCTION", "MGMT_EXPERIENCE",
    "MGMT_REPORT", "MGMT_COMPLAINT",
]

# UUID regex for DOM IDs
UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


async def login(page, username, password):
    """Log in to Kaizen via RCEM portal."""
    await page.goto("https://eportfolio.rcem.ac.uk", wait_until="networkidle", timeout=30000)
    await asyncio.sleep(2)

    # Step 1: Username
    login_input = page.locator('input[name="login"]')
    if await login_input.count() > 0:
        await login_input.fill(username)
        await page.locator('button[type="submit"]').click()
        await asyncio.sleep(2)

    # Step 2: Password
    pwd_input = page.locator('input[name="password"]')
    if await pwd_input.count() > 0:
        await pwd_input.fill(password)
        await page.locator('button[type="submit"]').click()

    await page.wait_for_url("**/kaizenep.com/**", timeout=30000)
    await asyncio.sleep(3)
    print(f"Logged in: {page.url}")


async def discover_form_fields(page, form_type, form_uuid):
    """Navigate to a form and extract all UUID-based field IDs with labels."""
    url = f"https://kaizenep.com/events/new-section/{form_uuid}"
    print(f"\n--- {form_type} ---")
    print(f"  URL: {url}")

    await page.goto(url, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(4)  # Wait for Angular rendering

    # Extract all elements with UUID-pattern IDs
    fields = await page.evaluate("""() => {
        const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
        const results = [];
        const seen = new Set();

        // Find all elements with UUID IDs
        document.querySelectorAll('[id]').forEach(el => {
            const id = el.id;
            if (!uuidRe.test(id) || seen.has(id)) return;
            seen.add(id);

            const tag = el.tagName;
            const type = el.type || '';
            const name = el.name || '';
            const ngModel = el.getAttribute('ng-model') || '';

            // Find label
            let label = '';

            // Check for explicit <label for="...">
            const labelEl = document.querySelector(`label[for="${id}"]`);
            if (labelEl) {
                label = labelEl.textContent.trim();
            }

            // Check parent/ancestor for label text
            if (!label) {
                let parent = el.parentElement;
                for (let i = 0; i < 5 && parent; i++) {
                    const lbl = parent.querySelector('label');
                    if (lbl) {
                        label = lbl.textContent.trim();
                        break;
                    }
                    // Check for text in preceding sibling or wrapper
                    const textNode = parent.querySelector('.control-label, .field-label, .form-label, h4, h5, .section-title');
                    if (textNode) {
                        label = textNode.textContent.trim();
                        break;
                    }
                    parent = parent.parentElement;
                }
            }

            // Check ng-model for hints
            if (!label && ngModel) {
                label = '[ng-model: ' + ngModel + ']';
            }

            results.push({
                id: id,
                tag: tag,
                type: type,
                name: name,
                label: label,
                ngModel: ngModel,
                isTextarea: tag === 'TEXTAREA',
                isSelect: tag === 'SELECT',
                isInput: tag === 'INPUT',
                isCheckbox: type === 'checkbox',
            });
        });

        // Also check for startDate and endDate
        ['startDate', 'endDate'].forEach(specialId => {
            const el = document.getElementById(specialId);
            if (el && !seen.has(specialId)) {
                results.push({
                    id: specialId,
                    tag: el.tagName,
                    type: el.type || '',
                    name: el.name || '',
                    label: specialId === 'startDate' ? 'Start Date' : 'End Date',
                    ngModel: el.getAttribute('ng-model') || '',
                    isTextarea: false,
                    isSelect: false,
                    isInput: true,
                    isCheckbox: false,
                    special: true,
                });
            }
        });

        return results;
    }""")

    # Filter to only form-relevant fields (inputs, textareas, selects)
    relevant = [f for f in fields if f['isTextarea'] or f['isSelect'] or f['isInput']]

    for f in relevant:
        print(f"  [{f['tag']}/{f['type']}] id={f['id']}  label=\"{f['label']}\"  ng-model=\"{f['ngModel']}\"")

    return {
        "form_type": form_type,
        "form_uuid": form_uuid,
        "url": url,
        "fields": relevant,
        "all_uuid_elements": fields,
    }


async def main():
    from kaizen_form_filer import FORM_UUIDS

    # Get credentials
    from credentials import get_credentials
    creds = get_credentials(6912896590)
    if not creds:
        print("ERROR: No credentials found for user 6912896590")
        sys.exit(1)
    username, password = creds

    results = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        # Login
        await login(page, username, password)

        # Discover each unmapped form
        for form_type in FORMS_TO_DISCOVER:
            form_uuid = FORM_UUIDS.get(form_type)
            if not form_uuid:
                print(f"SKIP {form_type}: no UUID in FORM_UUIDS")
                continue

            try:
                result = await discover_form_fields(page, form_type, form_uuid)
                results[form_type] = result
            except Exception as e:
                print(f"ERROR {form_type}: {e}")
                results[form_type] = {"error": str(e)}

        await browser.close()

    # Save results
    out_path = os.path.join(os.path.dirname(__file__), "discovered_fields.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {out_path}")

    # Print Python dict format for pasting into FORM_FIELD_MAP
    print("\n\n# ─── Discovered FORM_FIELD_MAP entries ───")
    from form_schemas import FORM_SCHEMAS
    for form_type, data in results.items():
        if "error" in data:
            print(f"# {form_type}: ERROR - {data['error']}")
            continue

        schema = FORM_SCHEMAS.get(form_type, {})
        schema_fields = {f["key"]: f for f in schema.get("fields", [])}

        print(f'    "{form_type}": {{')

        fields = data.get("fields", [])
        # Try to match schema fields to discovered DOM IDs
        for field_key, field_info in schema_fields.items():
            if field_info.get("type") in ("kc_tick",):
                continue  # Skip curriculum — handled separately

            field_label = field_info.get("label", "").lower()
            matched_id = None

            # Check for startDate/endDate special cases
            if "date" in field_key.lower():
                for f in fields:
                    if f["id"] == "startDate":
                        matched_id = "startDate"
                        break

            # Try label matching
            if not matched_id:
                for f in fields:
                    dom_label = f["label"].lower().strip()
                    if dom_label and (field_label in dom_label or dom_label in field_label):
                        matched_id = f["id"]
                        break

            # Try ng-model matching
            if not matched_id:
                for f in fields:
                    ng = f["ngModel"].lower()
                    if field_key.lower().replace("_", "") in ng.replace("_", "").replace(".", ""):
                        matched_id = f["id"]
                        break

            if matched_id:
                print(f'        "{field_key}": "{matched_id}",')
            else:
                if field_info.get("type") != "kc_tick":
                    print(f'        # UNMATCHED: {field_key} (label: "{field_info.get("label")}")')

        print(f'    }},')


if __name__ == "__main__":
    asyncio.run(main())
