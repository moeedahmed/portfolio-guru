"""
DOM inspector for unmapped P0 Kaizen form fields.

Navigates to CBD, DOPS, and LAT forms on live Kaizen, extracts all
field element IDs from the AngularJS DOM, and prints a structured
mapping. Does NOT save, submit, or modify any form data.

Prerequisites:
  - KAIZEN_USERNAME and KAIZEN_PASSWORD env vars set
  - Chrome running with remote debugging OR headless Playwright available
  - Backend venv activated

Usage:
    cd /Users/moeedahmed/projects/portfolio-guru/backend
    source venv/bin/activate
    python inspect_p0_fields.py

Output:
    JSON mapping of {form_type: {field_label: element_id}} for each P0 form.
    Copy the discovered IDs into kaizen_form_filer.py FORM_FIELD_MAP entries.

Safety:
    - Read-only DOM inspection (no .fill(), no .click() on Save/Submit)
    - Navigates to /new-section/ URLs (creates blank form page, no draft saved)
    - Closes page after inspection (Kaizen does not auto-save on /new-section/)
"""

import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from kaizen_form_filer import (
    FORM_UUIDS,
    FORM_FIELD_MAP,
    STAGE_SELECT_VALUES,
    _connect_cdp,
    _login,
)
from form_schemas import FORM_SCHEMAS

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── P0 target fields ───────────────────────────────────────────────────────
# Fields we need to discover DOM IDs for.

P0_TARGETS = {
    "CBD": ["clinical_setting", "patient_presentation", "trainee_role", "level_of_supervision"],
    "DOPS": ["procedure_name", "clinical_setting", "indication", "trainee_performance"],
    "LAT": ["clinical_setting", "stage_of_training", "reflection"],
}

# JS snippet to enumerate all Formly fields on a Kaizen form page.
# Returns array of {key, id, tag, type, label} for every rendered field.
ENUMERATE_FIELDS_JS = """() => {
    var results = [];

    // Method 1: Walk all elements with an id that looks like a UUID
    var allEls = document.querySelectorAll('[id]');
    for (var i = 0; i < allEls.length; i++) {
        var el = allEls[i];
        var id = el.id;
        // Skip non-form elements (nav, container divs, etc.)
        if (!id || id.length < 8) continue;
        // Skip known non-field IDs
        if (['main-content', 'page-wrapper', 'sidebar'].indexOf(id) >= 0) continue;

        var tag = el.tagName;
        var type = el.type || '';
        var label = '';

        // Try to find associated label
        var labelEl = document.querySelector('label[for="' + id + '"]');
        if (labelEl) {
            label = labelEl.textContent.trim();
        }

        // For elements inside formly wrappers, try to get the formly label
        if (!label) {
            var wrapper = el.closest('.formly-field, [formly-field]');
            if (wrapper) {
                var wrapperLabel = wrapper.querySelector('label');
                if (wrapperLabel) label = wrapperLabel.textContent.trim();
            }
        }

        // Try Angular scope for model key
        var modelKey = '';
        try {
            var scope = angular.element(el).scope();
            if (scope && scope.options && scope.options.key) {
                modelKey = scope.options.key;
            }
        } catch (e) {}

        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || modelKey) {
            results.push({
                id: id,
                tag: tag,
                type: type,
                label: label,
                modelKey: modelKey,
            });
        }
    }

    // Method 2: Walk Angular formly scopes directly
    var formlyFields = document.querySelectorAll('.formly-field, [formly-field]');
    for (var j = 0; j < formlyFields.length; j++) {
        var ff = formlyFields[j];
        try {
            var fScope = angular.element(ff).scope();
            if (fScope && fScope.options && fScope.options.key) {
                var key = fScope.options.key;
                // Find the actual input/select/textarea inside
                var input = ff.querySelector('input, select, textarea');
                var fieldId = input ? input.id : (ff.id || '');
                var fieldTag = input ? input.tagName : ff.tagName;
                var fieldType = input ? (input.type || '') : '';
                var fieldLabel = '';
                var lbl = ff.querySelector('label');
                if (lbl) fieldLabel = lbl.textContent.trim();

                // Check if already in results
                var exists = false;
                for (var k = 0; k < results.length; k++) {
                    if (results[k].id === fieldId && results[k].modelKey === key) {
                        exists = true;
                        break;
                    }
                }
                if (!exists && fieldId) {
                    results.push({
                        id: fieldId,
                        tag: fieldTag,
                        type: fieldType,
                        label: fieldLabel,
                        modelKey: key,
                    });
                }
            }
        } catch (e) {}
    }

    return results;
}"""


# JS to extract select option values (for dropdowns we need Angular values)
EXTRACT_SELECT_OPTIONS_JS = """(selectId) => {
    var el = document.getElementById(selectId);
    if (!el || el.tagName !== 'SELECT') return null;
    var opts = [];
    for (var i = 0; i < el.options.length; i++) {
        opts.push({
            value: el.options[i].value,
            text: el.options[i].textContent.trim(),
        });
    }
    return opts;
}"""


async def inspect_form(page, form_type: str, form_uuid: str) -> dict:
    """Navigate to a form and extract all field DOM IDs."""
    url = f"https://kaizenep.com/events/new-section/{form_uuid}"
    logger.info(f"Inspecting {form_type} at {url}")

    await page.goto(url, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(5)  # Wait for Angular to fully render

    # Check we're on the right page
    if "kaizenep.com" not in page.url:
        logger.error(f"  Not on Kaizen page: {page.url}")
        return {"error": "navigation_failed", "url": page.url}

    # If the form has a stage_of_training dropdown that's already mapped,
    # set it to "Higher" to reveal conditional fields (like procedural skills)
    existing_map = FORM_FIELD_MAP.get(form_type, {})
    stage_id = existing_map.get("stage_of_training")
    if stage_id and stage_id != "startDate":
        logger.info(f"  Setting stage_of_training to Higher to reveal conditional fields...")
        try:
            higher_val = STAGE_SELECT_VALUES.get("Higher", "")
            if higher_val:
                await page.select_option(f'[id="{stage_id}"]', value=higher_val)
                await asyncio.sleep(5)
        except Exception as e:
            logger.warning(f"  Could not set stage: {e}")

    # Enumerate all fields
    fields = await page.evaluate(ENUMERATE_FIELDS_JS)

    # For each select field, also grab options
    for field in fields:
        if field["tag"] == "SELECT" and field["id"]:
            try:
                opts = await page.evaluate(EXTRACT_SELECT_OPTIONS_JS, field["id"])
                field["options"] = opts
            except Exception:
                pass

    # Match against P0 targets
    targets = P0_TARGETS.get(form_type, [])
    schema = FORM_SCHEMAS.get(form_type, {})
    schema_fields = {f["key"]: f for f in schema.get("fields", [])}

    matched = {}
    unmatched_targets = list(targets)

    for target_key in targets:
        schema_field = schema_fields.get(target_key, {})
        target_label = schema_field.get("label", target_key).lower()

        for field in fields:
            field_label = field.get("label", "").lower()
            field_key = field.get("modelKey", "").lower()

            # Match by Angular model key or label text
            if (target_key.lower() == field_key or
                target_key.lower().replace("_", "") == field_key.lower().replace("_", "") or
                (target_label and target_label in field_label) or
                (target_label and field_label and field_label in target_label)):
                matched[target_key] = {
                    "dom_id": field["id"],
                    "tag": field["tag"],
                    "type": field["type"],
                    "label": field.get("label", ""),
                    "model_key": field.get("modelKey", ""),
                    "options": field.get("options"),
                }
                if target_key in unmatched_targets:
                    unmatched_targets.remove(target_key)
                break

    return {
        "form_type": form_type,
        "all_fields": fields,
        "matched_p0": matched,
        "unmatched_p0": unmatched_targets,
        "field_count": len(fields),
    }


async def main():
    username = os.environ.get("KAIZEN_USERNAME")
    password = os.environ.get("KAIZEN_PASSWORD")
    if not username or not password:
        print("ERROR: Set KAIZEN_USERNAME and KAIZEN_PASSWORD env vars.")
        print("  export KAIZEN_USERNAME='your_email'")
        print("  export KAIZEN_PASSWORD='your_password'")
        sys.exit(1)

    page, pw = await _connect_cdp()

    try:
        # Login
        if "kaizenep.com" not in page.url or "auth" in page.url:
            logger.info("Logging in to Kaizen...")
            ok = await _login(page, username, password)
            if not ok:
                print("ERROR: Login failed")
                sys.exit(1)

        results = {}
        for form_type, targets in P0_TARGETS.items():
            uuid = FORM_UUIDS.get(form_type)
            if not uuid:
                logger.error(f"{form_type}: no UUID found")
                continue

            result = await inspect_form(page, form_type, uuid)
            results[form_type] = result

            # Print summary for this form
            print(f"\n{'='*60}")
            print(f"  {form_type} — {result['field_count']} fields found")
            print(f"{'='*60}")

            if result.get("matched_p0"):
                print("\n  MATCHED P0 fields (copy these to FORM_FIELD_MAP):")
                for key, info in result["matched_p0"].items():
                    print(f'    "{key}": "{info["dom_id"]}",  # {info["tag"]} — {info["label"]}')
                    if info.get("options"):
                        print(f"      options: {[o['text'] for o in info['options'][:5]]}...")

            if result.get("unmatched_p0"):
                print(f"\n  UNMATCHED P0 fields (need manual inspection):")
                for key in result["unmatched_p0"]:
                    schema_field = FORM_SCHEMAS.get(form_type, {}).get("fields", [])
                    label = next((f["label"] for f in schema_field if f["key"] == key), key)
                    print(f"    {key} — look for: '{label}'")

            print(f"\n  ALL discovered fields:")
            for f in result.get("all_fields", []):
                mk = f.get("modelKey", "")
                print(f"    id={f['id']:<40} tag={f['tag']:<10} key={mk:<30} label={f.get('label','')[:50]}")

        # Write results to file
        output_path = os.path.join(os.path.dirname(__file__), "p0_inspection_results.json")
        with open(output_path, "w") as fp:
            json.dump(results, fp, indent=2, default=str)
        print(f"\nFull results written to: {output_path}")

        # Print copy-paste snippet for FORM_FIELD_MAP updates
        print("\n" + "="*60)
        print("  COPY-PASTE SNIPPET for kaizen_form_filer.py")
        print("="*60)
        for form_type, result in results.items():
            if result.get("matched_p0"):
                print(f"\n    # Add to FORM_FIELD_MAP['{form_type}']:")
                for key, info in result["matched_p0"].items():
                    print(f'        "{key}": "{info["dom_id"]}",')

    finally:
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
