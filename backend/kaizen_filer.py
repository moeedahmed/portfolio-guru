"""
Generic Kaizen form filer — deterministic Playwright.
Handles all 19 RCEM 2025 Update forms.

Usage:
    result = await file_to_kaizen(form_type, fields, username, password)
    # result: {"status": "success"|"partial"|"failed", "filled": [...], "skipped": [...], "error": str|None}
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import async_playwright, Page, Browser

logger = logging.getLogger(__name__)

# ─── Form UUIDs (2025 Update) ───────────────────────────────────────────────

FORM_UUIDS = {
    "CBD":           "3ce5989a-b61c-4c24-ab12-711bf928b181",
    "DOPS":          "159831f9-6d22-4e77-851b-87e30aee37a2",
    "LAT":           "eb1c7547-0f41-49e7-95de-8adffd849924",
    "ACAT":          "6577ab06-8340-47e3-952a-708a5f800dcc",
    "ACAF":          "15e67ae8-868b-4358-9b96-30a4a272f02c",
    "STAT":          "41ff54b8-35a7-414b-9bd6-97fb1c3eb189",
    "MSF":           "5f71ac04-ff45-44d2-b7a1-f8b921a8a4c8",
    "MINI_CEX":      "647665f4-a992-4541-9e17-33ba6fd1d347",
    "JCF":           "3daa9559-3c31-4ab4-883c-9a991632a9ca",
    "QIAT":          "a0aa5cfc-57be-4622-b974-51d334268d57",
    "TEACH":         "1ffbd272-8447-439c-aa03-ff99e2dbc04d",
    "PROC_LOG":      "2d6ebac1-4633-49d1-9dc0-fa0d39a98afc",
    "SDL":           "743885d8-c1b8-4566-bc09-8ed9b0e09829",
    "US_CASE":       "558b196a-8168-4cc6-b363-6f6e4b08397a",
    "ESLE":          "cbc7a42f-a2f0-436b-813e-bbf97cce0a34",
    "COMPLAINT":     "f7c0ba98-5a47-4e37-b76a-ca3c5c8484cc",
    "SERIOUS_INC":   "9d4a7912-a615-4ae4-9fae-6be966bcf254",
    "EDU_ACT":       "868dc0e7-f4e9-4283-ac52-d9c8b246024b",
    "FORMAL_COURSE": "c7cd9a95-e2aa-4f61-a441-b663f3c933c6",
}


# ─── Field → DOM ID mapping ─────────────────────────────────────────────────
# Maps schema field keys to Kaizen DOM element IDs.
# Keys MUST match form_schemas.py field keys exactly.



FORM_FIELD_MAP = {
    "CBD": {
        "date_of_encounter": "startDate",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "clinical_reasoning": "60772a97-92eb-4dbe-a813-6a5293be82f9",
        "reflection": "610b5c60-99ac-4902-9407-22974d6a5799",
    },
    "DOPS": {
        "date_of_encounter": "startDate",
        "procedure_name": "60772a97-92eb-4dbe-a813-6a5293be82f9",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "reflection": "610b5c60-99ac-4902-9407-22974d6a5799",
    },
    "MINI_CEX": {
        "date_of_encounter": "startDate",
        "clinical_setting": "f091f9c5-6c77-48be-9b96-05ebe1b56a07",
        "patient_presentation": "60772a97-92eb-4dbe-a813-6a5293be82f9",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "reflection": "610b5c60-99ac-4902-9407-22974d6a5799",
    },
    "ACAT": {
        "date_of_encounter": "startDate",
        "placement": "286d64f5-2aa0-41eb-aba6-a7bc523f133c",
        "clinical_setting": "e1ae9b5b-85b2-45e4-9c1f-f322c7a6dc31",
        "cases_observed": "60772a97-92eb-4dbe-a813-6a5293be82f9",
        "reflection": "610b5c60-99ac-4902-9407-22974d6a5799",
    },
    "LAT": {
        "date_of_encounter": "startDate",
        "leadership_context": "325c4423-ff20-4667-918b-c2f2a323acd0",
        "clinical_reasoning": "4c88f4f8-32bb-43c6-905b-411b7915affd",
    },
    "ACAF": {
        "date_of_encounter": "startDate",
        "situation": "4ea8380a-86dc-4483-926e-c0deb4a7e021",
        "pico_population": "eaaffbe3-1bbe-4600-92ec-ecad6d6483bb",
        "pico_intervention": "66ce86c5-086c-485c-a46b-76e259ee3f77",
        "pico_comparison": "3f2ca5b6-b65e-48b2-b2e4-53130ce2951d",
        "pico_outcome": "b1013e86-5e07-47e0-ac01-ecc332601b8c",
        "search_methodology": "e68164a5-435e-472f-a2d3-5880eb5eae14",
        "evidence_evaluation": "ae5f23c6-3ea1-4836-b1f4-db664f6d5d95",
        "apply_to_practice": "a277f5d6-9d68-4408-ac45-20ccc97dd746",
        "communicate_to_patient": "eda3bc11-3115-475e-93ba-5f186f6f2ecc",
        "future_research": "8fb576f7-aa62-496d-ac40-0cc5d7e9649e",
        "reflection": "e5b279ff-63fc-46cf-a0a0-c87c8dfcc78b",
    },
    "STAT": {
        "date_of_encounter": "startDate",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "learner_group": "d00ac11e-528f-494d-b1ff-6835fb989995",
        "setting": "bf181d9c-4875-4010-805e-f675aaeb4e72",
        "delivery": "2a417a1c-351f-47f2-abe0-e46ee452d8ae",
        "number_of_learners": "55ca2e2a-53ae-46a2-ac9f-3ee693bb6440",
        "session_length": "94398bfe-970c-456c-9cf4-618f51a0becc",
        "session_title": "8b19c437-be46-4ef9-be67-97a1b8d7e200",
    },
    "MSF": {
        "date_of_encounter": "startDate",
    },
    "QIAT": {
        "date_of_encounter": "startDate",
        "stage_of_training": "415a72f2-7cf3-420a-bee4-9a7aed746612",
        "placement": "9ba2f736-84a4-41eb-b7da-695734d4ec62",
        "pdp_summary": "99bfcd58-1cc3-4f79-9832-32c9d315e1a5",
        "qi_engagement": "fd738d73-9b88-4bfb-8c67-a7d7a0defa57",
        "qi_understanding": "dab68d71-46ca-46a6-97e8-e2f2a6b29a82",
        "involved_in_project": "2e2096f3-f65e-465c-bdd6-effadbe743dc",
        "qi_journey_aspects": "8a8f2bce-26fa-4baa-81d3-5b567ce9d45c",
        "next_pdp": "09a89221-ab2c-42f6-8462-1333540f8cf8",
    },
    "JCF": {
        "date_of_encounter": "startDate",
        "learner_group": "d00ac11e-528f-494d-b1ff-6835fb989995",
        "setting": "bf181d9c-4875-4010-805e-f675aaeb4e72",
        "delivery": "2a417a1c-351f-47f2-abe0-e46ee452d8ae",
        "number_of_learners": "55ca2e2a-53ae-46a2-ac9f-3ee693bb6440",
        "session_length": "94398bfe-970c-456c-9cf4-618f51a0becc",
        "paper_title": "8b19c437-be46-4ef9-be67-97a1b8d7e200",
    },
    "TEACH": {
        "date_of_teaching": "startDate",
        "title_of_session": "6b62a9ef-b0bf-498c-b10b-410fa97766c3",
        "recognised_courses": "17d7899f-0564-4e51-9817-54444e43822c",
        "learning_outcomes": "ddd8c881-91c6-46fd-84e9-32e89f617877",
    },
    "PROC_LOG": {
        "date_of_activity": "startDate",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "year_of_training": "036fe50f-5357-4da5-9fd6-d5c2e8d96ba4",
        "age_of_patient": "ca4f531c-ea4b-4587-a964-ee471abf1193",
        "reflective_comments": "f4557928-23fa-40b0-9f14-9357f5e7e1f3",
    },
    "SDL": {
        "reflection_title": "d7d43710-117b-4cae-8903-203b82c72f58",
        "resource_details": "a732400a-6f84-439a-938c-eae849459fa7",
        "reflection": "e99119c8-7cff-4d04-a2ad-0246dddfdac1",
    },
    "US_CASE": {
        "case_reflection_title": "3d201298-05a3-4d5a-862a-0a079ee11f77",
        "date_of_case": "a2777351-37e3-4568-9cac-a822f82092c4",
        "location": "c7a9f5dd-fd2b-4b02-bb4d-a254e65504cb",
        "patient_gender": "13808567-1775-4efb-a364-4b0a453b7992",
        "patient_age": "8720bd82-f350-46ff-9696-b8be5490a4c8",
        "equipment_used": "cfe23d74-2a38-4e3e-9636-913651898777",
        "clinical_scenario": "bbab795a-fe08-4599-ad2d-3c34c4c6b240",
        "how_used": "05d2c806-ef77-49f1-b582-61e6dff50273",
        "usable_images": "1e402c8c-4b79-4ee1-9162-3b9cb9cef780",
        "interpret_images": "e8b5b356-f813-4ee0-b69a-a2034c4edd2b",
        "changed_management": "e9419598-3230-4e50-810f-29893a6a8c42",
        "learning_points": "df24a5de-14a4-4d25-9c93-b93ac76991b9",
        "other_comments": "317ddbf2-a3dc-4ee7-a53c-fa1369d2c929",
    },
    "ESLE": {
        "reflection_title": "a525d382-30ea-48d2-b3ee-e325473eeb5c",
        "date_of_esle": "c00e55ce-0eec-4725-9044-70317bafb75d",
        "circumstances": "750468a4-7f96-481d-99fa-8c5af70958fd",
        "replay_differently": "be609110-389a-4411-969e-ee4289f691ed",
        "why": "761354f7-908a-4101-b7d8-66d324a62658",
        "different_outcome": "5998869f-feb1-4cc4-865c-19ac975b7e0e",
        "focussing_on": "54bb61c4-dc39-4e56-9e22-b1acd21edabb",
        "learned": "0a463c2f-f443-45b7-bd21-eb7c77b4e3f2",
        "further_action": "bfa0ce31-71d0-48de-bfdf-4f28304b94dc",
    },
    "COMPLAINT": {
        "reflection_title": "fe902ad2-a932-489f-bb01-2ae6dda100f4",
        "date_of_complaint": "dbc66064-26b7-4d96-93d0-fced3a9fc998",
        "key_features": "99d5b202-bf78-4f34-884a-f5c91d4539d0",
        "key_aspects": "6dee7933-3979-4c13-afc7-914f1514c8e7",
        "learning_points": "552bb938-8929-40c3-ad3c-428dd4f114e7",
        "further_action": "88ffd062-b49f-4af2-aa1b-913b94906570",
    },
    "SERIOUS_INC": {
        "reflection_title": "53009feb-16b4-4335-a81c-f5f5b4d93917",
        "date_of_incident": "f9e0bf51-a74e-4c6c-8fc8-9d627ebf318b",
        "description": "a4feee19-b600-4c42-a286-15a81f8835c2",
        "root_causes": "c5d0833f-db51-4dd2-9f4c-0972c1b7c54d",
        "contributing_factors": "898a8f93-6651-4bd8-a0f9-940ac60d908d",
        "learning_points": "b4b852e9-dbf3-4e6b-b3db-a309a6cf9f68",
        "further_action": "49b3a49c-5e69-4c87-9786-562777f6744b",
    },
    "EDU_ACT": {
        "date_of_education": "startDate",
        "title_of_education": "772f10f2-f292-4bc8-b349-bd6fff6679b7",
        "delivered_by": "0120a77c-d1bb-4c8c-9155-1460e0778613",
        "learning_points": "83dd2eb4-bf25-4d79-8001-59a76f7c2cc3",
    },
    "FORMAL_COURSE": {
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "project_description": "f6b7732d-3bdc-496f-bde9-4a103f3f77f8",
        "reflective_notes": "a3a5ee55-b018-4b73-a347-dd69595f4598",
        "resources_used": "4b99584e-6fea-48f9-9cfb-317c6de5223b",
        "lessons_learned": "310b69ab-6738-4eac-9b1f-6dfb1ed810b6",
    },
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _to_uk_date(iso_date: str) -> str:
    """Convert YYYY-MM-DD or various formats to d/m/yyyy for Kaizen."""
    if not iso_date:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y"):
        try:
            dt = datetime.strptime(iso_date.strip(), fmt)
            return f"{dt.day}/{dt.month}/{dt.year}"
        except ValueError:
            continue
    return iso_date  # Return as-is if no format matches


async def _login(page: Page, username: str, password: str) -> bool:
    """Log in to Kaizen. Returns True on success."""
    try:
        await page.goto("https://eportfolio.rcem.ac.uk", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        await page.fill('input[name="login"]', username)
        await page.fill('input[name="password"]', password)
        await page.click('button[type="submit"]')

        await page.wait_for_url("**/kaizenep.com/**", timeout=30000)
        await asyncio.sleep(2)
        logger.info(f"Kaizen login success: {page.url}")
        return True
    except Exception as e:
        logger.error(f"Kaizen login failed: {e}")
        return False


async def _fill_field(page: Page, dom_id: str, value: Any, field_key: str) -> bool:
    """Fill a single field by its DOM id. Returns True if filled."""
    if value is None or value == "" or value == []:
        return False

    try:
        el = page.locator(f"#{dom_id}")
        if not await el.count():
            logger.warning(f"Field not found: #{dom_id} ({field_key})")
            return False

        tag = await el.evaluate("el => el.tagName")

        # Date fields (d/m/yyyy)
        if dom_id == "startDate" or dom_id == "endDate" or "date" in field_key.lower():
            uk_date = _to_uk_date(str(value))
            if uk_date:
                await el.click()
                await el.fill("")
                await el.type(uk_date, delay=50)
                # Press Tab to trigger date picker close
                await el.press("Tab")
                await asyncio.sleep(0.3)
                return True
            return False

        # Select dropdowns
        if tag == "SELECT":
            try:
                await el.select_option(label=str(value))
                return True
            except Exception:
                # Try partial match — Kaizen options sometimes have extra text
                options = await el.evaluate("""el => {
                    return Array.from(el.options).map(o => ({value: o.value, text: o.text}))
                }""")
                val_lower = str(value).lower()
                for opt in options:
                    if val_lower in opt["text"].lower():
                        await el.select_option(value=opt["value"])
                        return True
                logger.warning(f"No matching option for #{dom_id}: '{value}' in {[o['text'] for o in options]}")
                return False

        # Textareas and text inputs
        if tag in ("TEXTAREA", "INPUT"):
            await el.click()
            await el.fill(str(value))
            return True

        return False

    except Exception as e:
        logger.warning(f"Error filling #{dom_id} ({field_key}): {e}")
        return False


async def _tick_curriculum(page: Page, slo_codes: List[str]) -> int:
    """Tick KC checkboxes for the given SLO codes. Returns number ticked."""
    if not slo_codes:
        return 0

    ticked = 0
    # KC checkboxes are inside the curriculum section — find by kzt-search area
    # Each SLO is a collapsible section; KCs are checkboxes within
    checkboxes = await page.query_selector_all("input[type=checkbox]")

    for cb in checkboxes:
        cb_id = await cb.get_attribute("id") or ""
        if cb_id == "filledOnSameDevice":
            continue

        # Get the label text for this checkbox
        label_text = await cb.evaluate("""el => {
            let lbl = el.closest('label');
            if (lbl) return lbl.textContent.trim();
            let next = el.nextElementSibling;
            if (next) return next.textContent.trim();
            let parent = el.parentElement;
            if (parent) return parent.textContent.trim();
            return '';
        }""")

        # Check if any of our SLO codes appear in the label
        for slo in slo_codes:
            slo_clean = slo.replace("SLO", "").strip()
            if slo_clean and (slo in label_text or f"SLO{slo_clean}" in label_text):
                is_checked = await cb.is_checked()
                if not is_checked:
                    await cb.click()
                    ticked += 1
                break

    logger.info(f"Ticked {ticked} curriculum checkboxes for SLOs: {slo_codes}")
    return ticked


async def _save_draft(page: Page) -> bool:
    """Click Save as Draft. Never Submit/Send. Returns True on success."""
    # Kaizen uses an <a> tag with ng-click for "Save as draft", NOT a <button>
    save_selectors = [
        'a:has-text("Save as draft")',
        'a:has-text("Save as Draft")',
        'a:has-text("Save draft")',
        'button:has-text("Save as Draft")',
        'button:has-text("Save Draft")',
        'button:has-text("Save")',
    ]

    for selector in save_selectors:
        try:
            el = page.locator(selector).first
            if await el.count() > 0:
                el_text = (await el.inner_text()).strip()
                # SAFETY: never click Submit/Send
                if any(danger in el_text.lower() for danger in ["submit", "send"]):
                    logger.warning(f"BLOCKED dangerous element: '{el_text}'")
                    continue
                await el.click()
                await asyncio.sleep(3)
                logger.info(f"Clicked save: '{el_text}'")
                return True
        except Exception as e:
            logger.debug(f"Save selector {selector} failed: {e}")
            continue

    # Fallback: Kaizen has autosave — if we filled fields, they should be saved
    # Check Angular scope for autosave status
    try:
        autosave = await page.evaluate("""() => {
            try {
                let scope = angular.element(document.querySelector('form')).scope();
                return scope?.eventCtrl?.options?.autosaveisEnabled || false;
            } catch(e) { return false; }
        }""")
        if autosave:
            logger.info("Autosave is enabled — fields should be saved automatically")
            return True
    except Exception:
        pass

    logger.warning("No save button/link found and autosave not confirmed")
    return False


# ─── Main entry point ────────────────────────────────────────────────────────

async def file_to_kaizen(
    form_type: str,
    fields: Dict[str, Any],
    username: str,
    password: str,
    curriculum_links: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    File a form to Kaizen as a draft.

    Args:
        form_type: Short code (CBD, DOPS, etc.)
        fields: Dict of field_key → value (from extractor)
        username: Kaizen username
        password: Kaizen password
        curriculum_links: Optional list of SLO codes to tick

    Returns:
        {
            "status": "success" | "partial" | "failed",
            "filled": ["field1", "field2", ...],
            "skipped": ["field3", ...],
            "error": None | "error message",
        }
    """
    uuid = FORM_UUIDS.get(form_type)
    if not uuid:
        return {"status": "failed", "filled": [], "skipped": [], "error": f"Unknown form type: {form_type}"}

    field_map = FORM_FIELD_MAP.get(form_type, {})
    if not field_map:
        return {"status": "failed", "filled": [], "skipped": [], "error": f"No field mapping for: {form_type}"}

    filled = []
    skipped = []
    browser = None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            # Login
            if not await _login(page, username, password):
                return {"status": "failed", "filled": [], "skipped": [], "error": "Login failed"}

            # Navigate to form
            form_url = f"https://kaizenep.com/events/new-section/{uuid}"
            await page.goto(form_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(5)  # Kaizen SPA is slow

            # Verify we're on the form page
            if "new-section" not in page.url:
                return {"status": "failed", "filled": [], "skipped": [],
                        "error": f"Form page didn't load — redirected to {page.url}"}

            # Fill each mapped field
            for field_key, dom_id in field_map.items():
                value = fields.get(field_key)
                if value is None or value == "" or value == []:
                    skipped.append(field_key)
                    continue

                success = await _fill_field(page, dom_id, value, field_key)
                if success:
                    filled.append(field_key)
                else:
                    skipped.append(field_key)

            # Tick curriculum checkboxes
            if curriculum_links:
                await _tick_curriculum(page, curriculum_links)

            # Save as draft
            saved = await _save_draft(page)

            # Determine status
            if saved and len(filled) > 0:
                if len(skipped) == 0:
                    status = "success"
                else:
                    status = "partial"
            elif len(filled) > 0:
                status = "partial"
            else:
                status = "failed"

            return {
                "status": status,
                "filled": filled,
                "skipped": skipped,
                "error": None if saved else "Save button not found or click failed",
            }

    except Exception as e:
        logger.error(f"Kaizen filer error for {form_type}: {e}", exc_info=True)
        return {
            "status": "failed",
            "filled": filled,
            "skipped": skipped,
            "error": str(e),
        }
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
