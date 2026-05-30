"""
Canonical Kaizen form filer — AngularJS-aware Playwright via CDP.

Connects to a managed Chrome instance, fills any WPBA form type,
handles dates correctly (type char-by-char, not .fill()), expands
SLO trees, ticks KCs, and saves as draft.

Covers 44 form types total:
  - 44 forms live-verified against DOM (21 core + 21 MGMT forms verified 2026-04-01,
    EDU_MEETING + EDU_MEETING_SUPP verified 2026-04-02)

Two entry points:
  - fill_kaizen_form()  — new CDP-based filer (verified forms)
  - file_to_kaizen()    — legacy-compatible wrapper used by bot.py / filer_router.py

Usage:
    result = await fill_kaizen_form(
        form_type="CBD", fields={...}, username="...", password="...",
        draft_uuid="...", save_as_draft=True
    )
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright, Page, Browser, BrowserContext

logger = logging.getLogger(__name__)

CDP_URL = os.environ.get("KAIZEN_CDP_URL", "http://localhost:18800")
KAIZEN_USE_CDP = os.environ.get("KAIZEN_USE_CDP", "").lower() in ("1", "true", "yes")

# ─── Emoji stripping — portfolio entries must NEVER contain emojis ────────────

_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001f926-\U0001f937"
    "\U00010000-\U0010ffff"
    "\u2640-\u2642"
    "\u2600-\u2B55"
    "\u200d\u23cf\u23e9\u231a\ufe0f\u3030"
    "]+",
    flags=re.UNICODE,
)


def _strip_emojis(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()


def _is_other_choice(value: Any) -> bool:
    return "other" in str(value or "").strip().lower()


# ─── Date helper ──────────────────────────────────────────────────────────────

def _to_uk_date(raw: str) -> str:
    """Convert various date formats to d/m/yyyy for Kaizen."""
    if not raw:
        return ""
    # Already in d/m/yyyy or dd/mm/yyyy — pass through
    if re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", raw.strip()):
        return raw.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d %B %Y", "%d %b %Y"):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            return f"{dt.day}/{dt.month}/{dt.year}"
        except ValueError:
            continue
    return raw


# ─── Stage select Angular values ─────────────────────────────────────────────

STAGE_SELECT_VALUES = {
    "ACCS":         "string:39b9fe64-b1e7-4726-81e2-73aaead0ee95",
    "Intermediate": "string:0669c338-e695-40f9-8fae-aee2ee7d68e1",
    "Higher":       "string:3815019a-e2be-4824-a4fb-555b55ffeab2",
    "PEM":          "string:fc7caa86-b83c-48d0-9b86-0fb73617d2b5",
}

# QIAT uses a different stage dropdown with individual training years
QIAT_STAGE_VALUES = {
    "ST1":          "string:fa1bc1e8-7ceb-4cff-9cc7-3b5792c13221",
    "CT1":          "string:fa1bc1e8-7ceb-4cff-9cc7-3b5792c13221",
    "ST2":          "string:810193d7-6a93-43e8-accf-7f1bbddf3e25",
    "CT2":          "string:810193d7-6a93-43e8-accf-7f1bbddf3e25",
    "ST3":          "string:ccaa6478-7dc1-42e7-a16d-ca82201bbd7a",
    "CT3":          "string:ccaa6478-7dc1-42e7-a16d-ca82201bbd7a",
    "ST4":          "string:85c16bea-6a19-465c-9262-2498e297f856",
    "ST5":          "string:8ff9eb55-a10a-4634-bb01-1a66d7ae12c2",
    "ST6":          "string:de4b4b48-a631-4f37-80f9-1e186cef82cb",
    "ST7":          "string:79ac1346-5660-44c4-a1a1-b9cfc3897af5",
    "Higher":       "string:85c16bea-6a19-465c-9262-2498e297f856",  # Default Higher → ST4
    "Intermediate": "string:ccaa6478-7dc1-42e7-a16d-ca82201bbd7a",  # Default Intermediate → ST3
    "OOP":          "string:12db10f8-7dad-466b-855f-e864262a6d76",
    "Non-training": "string:ea626ede-3c78-43a5-a5a2-37b6c8c5078c",
}

# ─── Kaizen URL patterns ─────────────────────────────────────────────────────
# New section (create draft):
#   https://kaizenep.com/events/new-section/{form_uuid}
#
# Edit existing draft:
#   https://kaizenep.com/events/fillin/{document_id}?autosave={autosave_id}
#
# After first save on a /new-section/ URL, Kaizen redirects to a URL with a
# `doc=` query parameter. The document_id and autosave_id can be parsed from
# the redirected URL and stored to allow returning to the same draft later.
#
# Example flow:
#   1. POST /new-section/{form_uuid}      → form loaded, no draft yet
#   2. Save as draft                      → URL becomes ?doc={doc_id}&autosave={autosave_id}
#   3. To return: GET /fillin/{doc_id}?autosave={autosave_id}

KAIZEN_URL_PATTERNS = {
    "new_form":     "https://kaizenep.com/events/new-section/{form_uuid}",
    "edit_draft":   "https://kaizenep.com/events/fillin/{doc_id}?autosave={autosave_id}",
    "doc_id_query": "doc",         # query param name in the saved URL
    "autosave_query": "autosave",  # query param name in the saved URL
}


# ─── Kaizen quirks observed in live forms ────────────────────────────────────
# 1. When you re-open an existing draft, Kaizen sometimes RESETS startDate and
#    endDate to today's date. The script must always re-fill these fields when
#    editing an existing draft, even if they were previously populated.
#
# 2. Kaizen sometimes AUTO-POPULATES the optional `event-description` field
#    with a truncated version of the form title (e.g. "PSIRF Roundtable") when
#    the draft is reopened. The script should clear this field unless the user
#    has explicitly set a description.
#
# 3. The Description (optional) field at the top of every form has the field
#    name `event-description` but is rendered as a textarea, not an input.

KAIZEN_QUIRKS = {
    "dates_reset_on_reopen": True,
    "event_description_auto_populated_on_reopen": True,
    "event_description_is_textarea": True,
}


# SLO Angular node IDs — used for navigation/lookup ONLY, never for ticking.
#
# IMPORTANT: SLOs must NEVER be ticked. Per RCEM portfolio guidance, only Key
# Capabilities (KCs) are checked. SLOs are parent nodes that must be expanded
# (clicked as a link) to reveal their child KC checkboxes. The script then
# ticks the relevant KC checkboxes only.
#
# These IDs are kept here for:
#   - Identifying which SLO a discovered KC belongs to
#   - Locating the SLO link to click for expansion
#   - Cross-referencing with the in-form curriculum tree
#
# KC node IDs are NOT hardcoded — they are lazy-loaded by Angular when each
# SLO is expanded, and must be discovered at runtime by reading the DOM after
# expansion.
SLO_NODE_IDS = {
    "header": "8b012340-36e6-4a67-b182-d3509a855837",
    "SLO1":   "426c9d2e-27b2-45a5-9461-c875dec29148",
    "SLO2":   "850d9e21-d9ed-4345-8177-8a2f18e5d6d2",
    "SLO3":   "020dc71f-2c21-4ccb-9aa6-f3c827854632",
    "SLO4":   "b2ba65fb-6fdc-458a-a412-37cad63fd6ec",
    "SLO5":   "fa194764-7a17-4ad7-b6e1-496414974499",
    "SLO6":   "e6ae7acb-9127-41b3-9074-922d5ba58edb",
    "SLO7":   "24eeeda0-b3d1-47ad-87bf-53f8470a0344",
    "SLO8":   "a5f64f22-93cf-4a64-af29-e1c8a5f8f843",
    "SLO9":   "b51e1dba-16aa-413f-bc7d-e4d01da9a083",
    "SLO10":  "1cdbba1b-9b81-4357-8ed9-45c9895469d7",
    "SLO11":  "4d6678f1-cc39-4640-8545-a05aaf249aeb",
    "SLO12":  "9d138719-c7c8-4138-b637-964d76a33658",
}

# Backwards-compatible alias — DEPRECATED, use SLO_NODE_IDS instead
SLO_CHECKBOX_IDS = SLO_NODE_IDS

# ─── Common header fields (present on every Kaizen WPBA form) ────────────────
# These three fields appear at the top of every form before the form-specific
# response section. The script must fill startDate and endDate for every form.
# `event-description` is optional but available on all forms.

COMMON_HEADER_FIELDS = {
    "date_of_encounter": {
        "field_id": "startDate",
        "label": "Date occurred on",
        "tag": "INPUT",
        "type": "date",
        "required": True,
    },
    "end_date": {
        "field_id": "endDate",
        "label": "End date",
        "tag": "INPUT",
        "type": "date",
        "required": True,
    },
    "description": {
        "field_id": "event-description",
        "label": "Description (optional)",
        "tag": "TEXTAREA",
        "type": "text",
        "required": False,
        # POLICY: Always fill this field unless the user explicitly says not to.
        # Although Kaizen marks it optional, this field is what appears next
        # to each event in the timeline view. An empty description means every
        # event looks identical when scanning. A short one-line summary makes
        # entries searchable, scannable, and easier for the assessor to triage.
        "always_fill_policy": True,
        "recommended_max_chars": 80,
        "recommended_content": "One-line summary of the event, distinct from in-form fields. "
                                "Example for CRIT_INCIDENT: 'PSIRF roundtable lead - young person death from prolonged OOHCA'. "
                                "Example for LAT: 'Resus team leadership - young person OOHCA blue-light pre-alert'. "
                                "Example for REFLECT_LOG: 'Hot debrief leadership using STOP5 after young person death'.",
    },
}

COMMON_HEADER_FIELD_MAP = {
    "date_of_encounter": "startDate",
    "end_date": "endDate",
    "event_description": "event-description",
}


# ─── Tag-based curriculum tagging (for forms WITHOUT an in-form curriculum tree) ─
# Some Kaizen forms (e.g. Critical Incident, MGMT_*, CLIN_GOV, MGMT_REPORT) do
# not embed a curriculum tree in the form body. For those forms, curriculum
# linkage is done via the "Add tags" modal accessed from the form header.
#
# The tag tree shares the SAME backing data structure as the in-form curriculum
# trees on QIAT/STAT — so SLO_CHECKBOX_IDS above can be reused to identify SLOs
# inside the tag modal.
#
# To add curriculum tags via the modal, the script should:
# CRITICAL RULE: Only KCs are ever ticked. SLOs are NEVER checked, only
# expanded for navigation. RCEM portfolio guidance is explicit on this — the
# learning outcome (SLO) is the parent category, and the specific Key
# Capabilities (KCs) are what evidence is mapped to.
#
# To add curriculum tags via the modal, the script should:
#   1. Click the "Add tags" button (button[ng-click*="addTags"] near the form header)
#   2. Wait for the dialog (role="dialog") to appear
#   3. Click "2021 EM Curriculum (2025 Update)" LINK to expand (do NOT tick the checkbox)
#   4. Click "Specialty Learning Outcomes - Higher (2025 Update)" LINK to expand SLOs
#   5. Click the relevant SLO LINK to expand its KCs (lazy-loaded — Angular only
#      renders children after parent is clicked). Do NOT tick the SLO checkbox.
#   6. Tick the relevant KC checkboxes only — never the parent SLO
#   7. Click "Save" / close the dialog
#
# The tag tree exposes the same SLO + KC structure as the in-form curriculum
# tree on QIAT/STAT/LAT. Use SLO_NODE_IDS to identify and click SLO links for
# expansion. KC node IDs are lazy-loaded and must be discovered at runtime by
# reading the DOM after each SLO is expanded.

TAG_TREE_CURRICULUM = {
    "field_id": "8bc374b7-4b07-4e16-984a-4af6eae806ef",  # also the kz-tree element ID
    "collections": {
        "2021_em_curriculum": "4564c4f2-f649-41a5-a040-abffa0c3947d",
        "2021_em_curriculum_2025_update": "8bc374b7-4b07-4e16-984a-4af6eae806ef",
        "paeds_em_subspecialty": "2af7a427-0cbf-4308-a284-3c493128bbbd",
    },
    # Use SLO_NODE_IDS to navigate/expand SLO nodes in the tag tree.
    # KCs are discovered at runtime after SLO expansion. Never tick SLOs.
}


# Forms that do NOT have an in-form curriculum tree and require tag-based mapping
# (verified empirically from live Kaizen 2026-04-06)
FORMS_USING_TAG_BASED_CURRICULUM = {
    "CRIT_INCIDENT", "CLIN_GOV", "MGMT_REPORT", "MGMT_PROJECT",
    "MGMT_ROTA", "MGMT_RISK", "MGMT_RECRUIT", "MGMT_RISK_PROC",
    "MGMT_TRAINING_EVT", "MGMT_GUIDELINE", "MGMT_INFO", "MGMT_INDUCTION",
    "MGMT_EXPERIENCE", "MGMT_COMPLAINT", "BUSINESS_CASE",
    "COST_IMPROVE", "EQUIP_SERVICE", "APPRAISAL",
    # Reflective entries that may also use tag-based linking:
    "COMPLAINT", "SERIOUS_INC",
}



# ─── File upload procedure (Attach files button) ─────────────────────────────
# Every Kaizen form has an "Attach files" section near the bottom with an
# "Upload" button. The file input element is NOT in the DOM until the Upload
# button is clicked — Angular creates it dynamically and immediately triggers
# a native file chooser.
#
# Procedure:
#   1. Locate the Upload button (button[aria-label="Upload files for Attach files"])
#   2. Click it (this opens a native file chooser dialog)
#   3. Pass the file path(s) via Playwright's file_chooser.setFiles()
#   4. Wait for upload to complete (status text changes to "Uploaded")
#   5. The uploaded file appears in a list with Replace/Remove actions
#
# Multiple files can be uploaded at once by passing an array to setFiles().
#
# IMPORTANT for portfolio-guru: when uploading documents that originate from
# Google Drive or other external sources, ensure they are first redacted of
# any patient-identifiable information. Portfolio entries must comply with
# RCEM/NHS confidentiality guidance — no patient names, no internal incident
# reference numbers, no exact dates that could identify a specific case.

KAIZEN_FILE_UPLOAD = {
    "button_aria_label": "Upload files for Attach files",
    "supports_multiple": True,
    "uploaded_status_text": "Uploaded",
    "actions_after_upload": ["Replace", "Remove"],
}


# ─── Drive backup requirement (CRITICAL UX RULE) ─────────────────────────────
# Kaizen does NOT allow re-opening or downloading attachments after they have
# been uploaded — once a file is on a draft, the user cannot review its contents
# inside Kaizen. This means any file the script generates and uploads must ALSO
# be backed up to a location where the user can review it before submitting the
# draft.
#
# Mandatory workflow for any script-generated attachment:
#   1. Create the file locally (PDF, document, etc.)
#   2. Upload it to the user's Google Drive (use gws drive +upload --parent <folder_id>)
#      The folder should be the same project/case folder the source materials
#      came from, so the user can review attachments alongside originals.
#   3. Upload it to the Kaizen draft via Playwright file_chooser
#   4. Report both the Drive URL and the Kaizen attachment status to the user
#
# The script must NEVER upload to Kaizen without first backing up to Drive,
# because the user has no other way to review the file before submission.

DRIVE_BACKUP_REQUIRED = True
DRIVE_UPLOAD_COMMAND = "gws drive +upload {file_path} --parent {folder_id}"


# ─── Edit-mode discipline (CRITICAL UX RULE) ─────────────────────────────────
# When editing an existing draft (not creating a new one), the script must
# only modify the fields it has been explicitly asked to change. It must NOT
# re-fill, overwrite, or normalise any other field, even if those fields look
# stale or incorrect, because:
#   - The user may have manually edited fields between sessions
#   - Kaizen sometimes resets dates or descriptions on reopen, but the user
#     may have a reason for those values
#   - Overwriting unrelated fields wastes effort and erases user changes
#
# Procedure for edit mode:
#   1. Identify the specific field(s) the user asked to change
#   2. Read the current value of those fields (for comparison/audit)
#   3. Modify ONLY those fields
#   4. Save as draft
#   5. Do not touch dates, descriptions, dropdowns, or curriculum tags unless
#      they were explicitly part of the edit request

EDIT_MODE_RULES = {
    "only_modify_explicit_fields": True,
    "preserve_user_manual_changes": True,
    "never_normalise_dates": True,
    "never_clear_auto_populated_fields_unless_asked": True,
}


# ─── Form field → DOM ID mapping (verified live for core forms) ──────────────

FORM_FIELD_MAP = {
    "CBD": {
        "date_of_encounter": "startDate",
        "end_date": "endDate",
        "date_of_event": "5391f8de-de63-4db3-9e08-baaa2a380cfe",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "clinical_reasoning": "60772a97-92eb-4dbe-a813-6a5293be82f9",
        "reflection": "610b5c60-99ac-4902-9407-22974d6a5799",
    },
    "DOPS": {
        "date_of_encounter": "startDate",
        "end_date": "endDate",
        "placement": "286d64f5-2aa0-41eb-aba6-a7bc523f133c",
        "date_of_event": "5391f8de-de63-4db3-9e08-baaa2a380cfe",
        "case_observed": "60772a97-92eb-4dbe-a813-6a5293be82f9",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        # Kaizen exposes one DOPS procedure dropdown; the extractor may call it
        # either procedure_name (schema label) or procedural_skill (2025 field).
        "procedure_name": "8def931e-3a00-43ac-8529-44cdaf34be2d",
        "procedural_skill": "8def931e-3a00-43ac-8529-44cdaf34be2d",
        "reflection": "610b5c60-99ac-4902-9407-22974d6a5799",
    },
    "MINI_CEX": {
        "date_of_encounter": "startDate",
        "end_date": "endDate",
        "clinical_setting": "f091f9c5-6c77-48be-9b96-05ebe1b56a07",
        "patient_presentation": "60772a97-92eb-4dbe-a813-6a5293be82f9",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "reflection": "610b5c60-99ac-4902-9407-22974d6a5799",
    },
    "ACAT": {
        "date_of_encounter": "startDate",
        "end_date": "endDate",
        "placement": "286d64f5-2aa0-41eb-aba6-a7bc523f133c",
        "clinical_setting": "e1ae9b5b-85b2-45e4-9c1f-f322c7a6dc31",
        "cases_observed": "60772a97-92eb-4dbe-a813-6a5293be82f9",
        "reflection": "610b5c60-99ac-4902-9407-22974d6a5799",
    },
    "LAT": {
        "date_of_encounter": "startDate",
        "end_date": "endDate",
        "date_of_event": "ebccdd92-bfac-44b4-abde-d7958118ff05",
        "trainee_post": "b4b62e33-3359-4504-9877-17f2e38e9fd0",
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
    "JCF": {
        "date_of_encounter": "startDate",
        "learner_group": "d00ac11e-528f-494d-b1ff-6835fb989995",
        "setting": "bf181d9c-4875-4010-805e-f675aaeb4e72",
        "delivery": "2a417a1c-351f-47f2-abe0-e46ee452d8ae",
        "number_of_learners": "55ca2e2a-53ae-46a2-ac9f-3ee693bb6440",
        "session_length": "94398bfe-970c-456c-9cf4-618f51a0becc",
        "paper_title": "8b19c437-be46-4ef9-be67-97a1b8d7e200",
    },
    "QIAT": {
        "date_of_encounter": "startDate",
        "stage_of_training": "415a72f2-7cf3-420a-bee4-9a7aed746612",
        "placement": "9ba2f736-84a4-41eb-b7da-695734d4ec62",
        "date_of_completion": "c00175cc-4b38-4ff4-b7c2-2c00f1bee840",
        "pdp_summary": "99bfcd58-1cc3-4f79-9832-32c9d315e1a5",
        "qi_engagement": "fd738d73-9b88-4bfb-8c67-a7d7a0defa57",
        "qi_understanding": "dab68d71-46ca-46a6-97e8-e2f2a6b29a82",
        "involved_in_project": "2e2096f3-f65e-465c-bdd6-effadbe743dc",
        "qi_journey_aspects": "8a8f2bce-26fa-4baa-81d3-5b567ce9d45c",
        "next_pdp": "09a89221-ab2c-42f6-8462-1333540f8cf8",
    },
    "TEACH": {
        "date_of_teaching": "startDate",
        "date_of_teaching_activity": "e90d9f84-68fc-4dbf-a8be-977180ffc2cb",
        "title_of_session": "6b62a9ef-b0bf-498c-b10b-410fa97766c3",
        "recognised_courses": "17d7899f-0564-4e51-9817-54444e43822c",
        "learning_outcomes": "ddd8c881-91c6-46fd-84e9-32e89f617877",
    },
    "PROC_LOG": {
        # Kaizen Procedural Log has three date inputs: event start/end dates,
        # plus the section's visible required "Date of Activity" field. Do not
        # map date_of_activity to startDate or the required section field stays
        # blank even when the event dates are populated.
        "date_occurred_on": "startDate",
        "date_of_activity": "8f76bc6b-68b7-4654-9116-75e421fceccd",
        "end_date": "endDate",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "year_of_training": "036fe50f-5357-4da5-9fd6-d5c2e8d96ba4",
        # Procedural skill dropdowns — UUIDs reused from TEACH (same 2025 Update
        # dropdown content; verify with DOM scrape if filling silently fails).
        "higher_procedural_skill": "8def931e-3a00-43ac-8529-44cdaf34be2d",
        "higher_procedural_skill_other": "4fea8fcc-185c-4917-bfe0-2dc63f7dccb3",
        "procedure_other": "4fea8fcc-185c-4917-bfe0-2dc63f7dccb3",
        "intermediate_procedural_skill": "31bd55b7-0e32-4918-8cc0-4ba33af83772",
        "accs_procedural_skill": "eed0e8dc-075d-4661-aea5-2c3238af4c5b",
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
    "REFLECT_LOG": {
        "date_of_encounter": "startDate",
        "reflection_title": "7a2db156-b42d-46fd-afa7-8e4913161f04",
        "date_of_event": "6b916431-25b5-402a-a2e3-611a4f1f4e80",
        "event_type": "af0d96f8-9fea-4302-9cb1-06ea7500f0e1",
        "reflection": "de8bddc8-e93a-4dab-bed7-323e81726504",
        "replay_differently": "2991fd89-e3c2-4c6c-a52d-14ffdf0a431b",
        "why": "a040b7b2-207c-4cf3-84a5-2a054eb1f4e7",
        "different_outcome": "79f071d3-f02e-41d2-84f9-cceec3fba23e",
        "focussing_on": "af780513-4b58-483b-be1f-e54a97334c13",
        "learned": "dec63ced-7db0-478a-9122-bb09b93cb933",
    },
    "TEACH_OBS": {
        "date_of_encounter": "startDate",
        "date_of_teaching": "0f76a9ce-c3ed-4151-96d0-88e555938cd8",
        "learner_group": "945ccd30-ba06-4685-85fb-9f86993467fa",
        "number_of_learners": "4253c1fd-0ba8-49a7-80de-68d1a2f2a271",
        "setting": "128cfd18-58be-4ff4-99fa-1afa9e435de5",
        "title_of_session": "ec9aa7b2-416f-49c1-81c1-9b67d80fd7ba",
        "session_description": "29b9bb19-6336-4eff-8f90-b0f8d28b5d89",
        "session_length": "c778559a-ee2b-47c8-bfef-533bebac0150",
        "reflection": "d50f8e73-b864-4e8e-bda6-fd70af10a945",
    },
    # ESLE Part 1 & 2 — actual WPBA. Trainee fills Part 1, assessor fills Part 2.
    "ESLE_PART1_2": {
        "date_of_encounter": "startDate",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "date_of_esle": "2c86886b-0a18-4771-9b25-6c2272fdad6b",
        "reflection": "488e8e63-300d-4ed9-a4f4-eaee53608f05",
    },
    # ESLE Reflection — supplementary reflective entry. Does not go to assessor.
    "ESLE_REFLECTION": {
        "date_of_encounter": "startDate",
        "reflection_title": "a525d382-30ea-48d2-b3ee-e325473eeb5c",
        "date_of_esle": "c00e55ce-0eec-4725-9044-70317bafb75d",
        "reflection": "750468a4-7f96-481d-99fa-8c5af70958fd",
        "replay_differently": "be609110-389a-4411-969e-ee4289f691ed",
        "why": "761354f7-908a-4101-b7d8-66d324a62658",
        "different_outcome": "5998869f-feb1-4cc4-865c-19ac975b7e0e",
        "focussing_on": "54bb61c4-dc39-4e56-9e22-b1acd21edabb",
        "learned": "0a463c2f-f443-45b7-bd21-eb7c77b4e3f2",
        "further_learning": "bfa0ce31-71d0-48de-bfdf-4f28304b94dc",
    },
    # MSF — trainee fills self-evaluation (Part 1); assessors fill ratings (Part 2)
    "MSF": {
        "date_of_encounter": "startDate",
        "date_msf": "b699e57a-6f7d-463e-a5a3-f78c2bdbe50c",
        # Self-ratings (Good Clinical Care)
        "medical_knowledge": "0d9c69df-2514-4ac7-8776-b3d1037e0e51",
        "problem_solving": "24677f0f-b345-4d6c-a79f-934bcc9f1536",
        "note_keeping": "461875da-67a3-4415-80e6-82a1ad483a77",
        "emergency_skills": "adfee495-7172-4933-949a-fa76765f18c3",
        "clinical_care_comments": "21eaf63d-9cf5-41bc-92d4-0cf912eea240",
        # Relationships with patients
        "empathy": "c06ef500-bcb7-4abf-8b09-af1428e99729",
        "communication_patients": "97e34377-43c8-45b6-bb22-dc89ae068bb5",
        "patient_respect": "667066ff-4f3e-412b-a836-b4e5b1f6d54b",
        "psychosocial": "c371fa8e-ece1-44b4-95dc-16c93ae46ce1",
        "explanations": "34f30a5f-c78b-456d-a147-96b47d3d94eb",
        "patient_comments": "37d84160-410d-40c8-ad63-a6b1f919fc19",
        # Relationships with colleagues
        "team_player": "b3b5854c-7bd1-4bd5-8e77-7ae65b3c6127",
        "seeks_advice": "c1540ce8-e2d8-429f-9e98-06a83b455790",
        "empathy_colleagues": "d3837087-6d48-4e6c-bf61-e94fa345a1ae",
        "clear_instructions": "eab610fa-73f7-4662-81d3-4de59ecdc062",
        "colleague_respect": "30cd661e-e769-43f7-ac0c-621cc6459625",
        "communication_colleagues": "e274ee13-a731-4aa8-aa5c-e271655e84a7",
        "reliable": "3015b9f8-3f0d-41e0-ae14-eed6a1d44d19",
        "leadership": "83511f96-7b69-44e3-aa4b-3986704d9e2b",
        "takes_responsibility": "d0676b0f-d549-497f-b3eb-00c9d3fc5b1e",
        "colleague_comments": "3a928d94-e2a2-430b-a700-c62f4eb4a6b8",
        # Teaching
        "teaching_structured": "0f04464e-f6e9-4fda-8665-ad40bff4d71d",
        "teaching_enthusiastic": "ffe0befd-a903-4c73-a83a-b52e4a885e00",
        "teaching_beneficial": "376f291d-4750-4b1d-9b03-5734b4cfadc2",
        "teaching_presentation": "71f615cb-e393-49f6-b8a5-9baf7cd1b1bb",
        "teaching_varied": "1d3d7816-ed3c-438b-b1c0-bdb7ee4c7109",
        "teaching_comments": "9af0ba57-7a78-4cc4-b830-682537826d77",
        # Global
        "overall_rating": "ae01a0b0-0288-4404-9ffd-82c5e0fca947",
        "performance_rating": "f1a6e224-c3d0-45fe-9dc1-5560e7b03f09",
        "general_comments": "3086f426-a0e2-459e-baf8-58577c04af55",
    },
    "TEACH_CONFID": {
        "date_of_encounter": "startDate",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "project_description": "b647ee6e-9bb7-4b86-bfdd-45aa0254211c",
        "reflection": "a7d84918-a496-484a-9d77-d0425646d29f",
        "resources_used": "b588a189-cc03-4f04-abda-be1a4190ec68",
        "lessons_learned": "d5cc387b-165c-4b7f-8679-5d7597b00beb",
    },
    # ─── MGMT forms (live-verified 2026-04-01 — all have curriculum section, no file attachment) ──
    "APPRAISAL": {
        "date_of_encounter": "startDate",
        "project_description": "f594f362-caf3-45e5-b8ee-616876b0c4e7",
        "date_of_event": "5e8746e6-918b-48c4-a0d1-5ca235e1e5ce",
        "reflection": "416c6f84-34ec-41e3-ab1b-2ddaab18f526",
    },
    "PDP": {
        "date_of_encounter": "startDate",
        "title": "70464420-b51e-4702-938f-bdad8ff38d3a",
        "reflection": "eff0a911-1c19-4cd6-a416-27a4bfe949bc",
        "how_addressed": "2bda92c7-15d7-4571-bbf5-a4379cf0be60",
        "edt_plans": "b3d55641-62b6-4f03-84ef-af377a55df6c",
        "access_areas": "2e26c165-4bc3-4acd-8099-67b48752f723",
        "timescale": "d3b5b712-09ac-46ef-bdf6-24a97967d64c",
        "evidence_of_achievement": "86d15299-4657-420c-b4aa-745ad5de6db0",
    },
    "BUSINESS_CASE": {
        "date_of_encounter": "startDate",
        "project_description": "f6b7732d-3bdc-496f-bde9-4a103f3f77f8",
        "reflection": "a3a5ee55-b018-4b73-a347-dd69595f4598",
        "resources_used": "4b99584e-6fea-48f9-9cfb-317c6de5223b",
        "lessons_learned": "310b69ab-6738-4eac-9b1f-6dfb1ed810b6",
    },
    "CLIN_GOV": {
        "date_of_encounter": "startDate",
        "project_description": "f594f362-caf3-45e5-b8ee-616876b0c4e7",
        "date_of_event": "5e8746e6-918b-48c4-a0d1-5ca235e1e5ce",
        "reflection": "416c6f84-34ec-41e3-ab1b-2ddaab18f526",
    },
    "AUDIT": {
        "date_of_encounter": "startDate",
        "title": "2b9c010d-0e5d-456a-afbd-ea33e63af3a3",
        "rcem_audit": "a7f44694-d848-4b67-85bc-94e0a2953af1",
        "date_of_event": "0653f095-95f9-4344-b825-f336791d1ded",
    },
    "RESEARCH": {
        "date_of_encounter": "startDate",
        "title": "bae7ff4d-373c-4583-952e-3bbcbcd13d2e",
        "date_started": "7fbf5f39-c9b1-4e2c-8c3d-455f159935fe",
        "date_finished": "025d5d3f-363b-470d-9c74-7427a8b898fd",
        "publication": "6ff4f3d2-2ad0-42d6-a86e-04bd196cee1b",
        "poster": "644af8ed-8b54-4544-9e0e-2f22feb3c7ef",
        "presentation": "f0f29cc4-255f-4126-800f-f2b061fc26b8",
        "local_presentation": "dc7376a1-571f-49e0-a0d4-2fcbb50cee20",
        "bestbets": "6e9261d0-37ab-4765-a0dc-707af6c9e697",
        "abstract": "5e93bc8c-8cde-4ea9-9f8b-cc03b0fc4d5e",
        "higher_degree": "f4392cb6-bd32-4913-b998-ae0b20d31774",
        "other": "81171c2c-f2aa-4831-91a1-53b734993e55",
    },
    "EDU_MEETING": {  # NO CURRICULUM SECTION
        "date_of_encounter": "startDate",
        "meeting_date": "ab023afe-255d-4b50-b1f4-b379e960d7c2",
        "meeting_type": "e4836763-cb73-4520-81b6-678666303d53",
        "reflection": "dbfb3151-671d-4c24-9b7e-cafba9df088b",
    },
    "EDU_MEETING_SUPP": {  # NO CURRICULUM SECTION
        "date_of_encounter": "startDate",
        "reviewer_name": "e62ea307-c9e3-4648-b7b5-528b74b41276",
        "reviewer_role": "06e9ae9d-3b8e-4797-931f-662c485bcded",
        "review_date": "8bd8a4c5-8221-451c-a0d1-47923c02cc0f",
        "reflection": "e9303965-c00b-4e8f-a7ef-5e6f7e7168ec",
        "feedback_given": "350771c5-6448-4257-ae92-3f48c58eb2b3",
        "action_taken": "8b46ca07-1b77-4914-b183-81cab3d9f954",
    },
    "CRIT_INCIDENT": {
        "date_of_encounter": "startDate",
        "end_date": "endDate",
        "description": "event-description",
        "project_description": "f594f362-caf3-45e5-b8ee-616876b0c4e7",
        "date_of_event": "5e8746e6-918b-48c4-a0d1-5ca235e1e5ce",
        "reflection": "416c6f84-34ec-41e3-ab1b-2ddaab18f526",
    },
    "COST_IMPROVE": {
        "date_of_encounter": "startDate",
        "project_description": "f6b7732d-3bdc-496f-bde9-4a103f3f77f8",
        "reflection": "a3a5ee55-b018-4b73-a347-dd69595f4598",
        "resources_used": "4b99584e-6fea-48f9-9cfb-317c6de5223b",
        "lessons_learned": "310b69ab-6738-4eac-9b1f-6dfb1ed810b6",
    },
    "EQUIP_SERVICE": {
        "date_of_encounter": "startDate",
        "project_description": "f594f362-caf3-45e5-b8ee-616876b0c4e7",
        "date_of_event": "5e8746e6-918b-48c4-a0d1-5ca235e1e5ce",
        "reflection": "416c6f84-34ec-41e3-ab1b-2ddaab18f526",
    },
    "MGMT_ROTA": {
        "date_of_encounter": "startDate",
        "project_description": "f594f362-caf3-45e5-b8ee-616876b0c4e7",
        "date_of_event": "5e8746e6-918b-48c4-a0d1-5ca235e1e5ce",
        "reflection": "416c6f84-34ec-41e3-ab1b-2ddaab18f526",
    },
    "MGMT_RISK": {
        "date_of_encounter": "startDate",
        "project_description": "f594f362-caf3-45e5-b8ee-616876b0c4e7",
        "date_of_event": "5e8746e6-918b-48c4-a0d1-5ca235e1e5ce",
        "reflection": "416c6f84-34ec-41e3-ab1b-2ddaab18f526",
    },
    "MGMT_RECRUIT": {
        "date_of_encounter": "startDate",
        "project_description": "f594f362-caf3-45e5-b8ee-616876b0c4e7",
        "date_of_event": "5e8746e6-918b-48c4-a0d1-5ca235e1e5ce",
        "reflection": "416c6f84-34ec-41e3-ab1b-2ddaab18f526",
    },
    "MGMT_PROJECT": {
        "date_of_encounter": "startDate",
        "title": "93c34027-0abf-4c2f-9caf-6a45065610f0",
        "scope": "875855a2-1521-4d82-9f13-208f536a67c6",
        "output": "db6c8f0a-4e03-48a1-8eb4-7f16b358c59d",
        "start_date": "144a72bc-d1b5-4c28-a119-9e29e3bc19a9",
        "finish_date": "c69115c7-339c-4c38-aa41-b65038157143",
        "evidence_references": "1733bca8-c619-4c34-a9f3-0477234c0b7b",
        "people_engaged": "dab8c918-caa9-4d8a-a637-49bd4a50c4de",
        "other_resources": "08050d70-1bf7-4614-ab72-abbd8726abbc",
        "supervisor_meetings": "c0ad12ac-c6ed-45d1-a50f-b330abfbac8a",
        "reflection": "a7c089c2-e407-470f-859c-cca8e413da3d",
        "reflection_on_learning": "7772b34f-9476-420b-a061-8dc07b04b78c",
    },
    "MGMT_RISK_PROC": {
        "date_of_encounter": "startDate",
        "project_description": "b647ee6e-9bb7-4b86-bfdd-45aa0254211c",
        "reflection": "a7d84918-a496-484a-9d77-d0425646d29f",
        "resources_used": "b588a189-cc03-4f04-abda-be1a4190ec68",
        "lessons_learned": "d5cc387b-165c-4b7f-8679-5d7597b00beb",
    },
    "MGMT_TRAINING_EVT": {
        "date_of_encounter": "startDate",
        "project_description": "f594f362-caf3-45e5-b8ee-616876b0c4e7",
        "date_of_event": "5e8746e6-918b-48c4-a0d1-5ca235e1e5ce",
        "reflection": "416c6f84-34ec-41e3-ab1b-2ddaab18f526",
    },
    "MGMT_GUIDELINE": {
        "date_of_encounter": "startDate",
        "project_description": "f594f362-caf3-45e5-b8ee-616876b0c4e7",
        "date_of_event": "5e8746e6-918b-48c4-a0d1-5ca235e1e5ce",
        "reflection": "416c6f84-34ec-41e3-ab1b-2ddaab18f526",
    },
    "MGMT_INFO": {
        "date_of_encounter": "startDate",
        "project_description": "b647ee6e-9bb7-4b86-bfdd-45aa0254211c",
        "reflection": "a7d84918-a496-484a-9d77-d0425646d29f",
        "resources_used": "b588a189-cc03-4f04-abda-be1a4190ec68",
        "lessons_learned": "d5cc387b-165c-4b7f-8679-5d7597b00beb",
    },
    "MGMT_INDUCTION": {
        "date_of_encounter": "startDate",
        "project_description": "f594f362-caf3-45e5-b8ee-616876b0c4e7",
        "date_of_event": "5e8746e6-918b-48c4-a0d1-5ca235e1e5ce",
        "reflection": "416c6f84-34ec-41e3-ab1b-2ddaab18f526",
    },
    "MGMT_EXPERIENCE": {
        "date_of_encounter": "startDate",
        "date_of_activity": "cf8c49ed-24b6-43a2-9e24-38006a3b410d",
        "activity_type": "b17d0c63-6dfe-4841-888c-85a7f324cd0d",
        "reflection": "6b2ad7a4-5e6f-447c-9dd5-bb0baa713869",
    },
    "MGMT_REPORT": {
        "date_of_encounter": "startDate",
        "project_description": "f594f362-caf3-45e5-b8ee-616876b0c4e7",
        "date_of_event": "5e8746e6-918b-48c4-a0d1-5ca235e1e5ce",
        "reflection": "416c6f84-34ec-41e3-ab1b-2ddaab18f526",
    },
    "MGMT_COMPLAINT": {
        "date_of_encounter": "startDate",
        "project_description": "f594f362-caf3-45e5-b8ee-616876b0c4e7",
        "date_of_event": "5e8746e6-918b-48c4-a0d1-5ca235e1e5ce",
        "reflection": "416c6f84-34ec-41e3-ab1b-2ddaab18f526",
    },
}

FORM_TYPE_ALIASES = {
    # Portfolio Guru exposes the formal ESLE WPBA as ESLE_ASSESS. Kaizen's
    # historical DOM map name is ESLE_PART1_2.
    "ESLE_ASSESS": "ESLE_PART1_2",
    # Bare ESLE is kept as a compatibility alias for older drafts/callbacks.
    "ESLE": "ESLE_PART1_2",
}


def canonical_form_type(form_type: str) -> str:
    return FORM_TYPE_ALIASES.get((form_type or "").strip().upper(), form_type)


# Schema-required fields that do not have a one-to-one Kaizen DOM control.
# "merge:*" entries are folded into a mapped field before filing. "safe_skip"
# entries remain skipped/partial because no verified deterministic DOM target is
# known; this is deliberate and prevents silently writing evidence to the wrong
# Kaizen field.
SCHEMA_REQUIRED_FIELD_HANDLING = {
    "CBD": {
        "clinical_setting": "merge:clinical_reasoning",
        "patient_presentation": "merge:clinical_reasoning",
        "trainee_role": "merge:clinical_reasoning",
        "level_of_supervision": "merge:clinical_reasoning",
    },
    "DOPS": {
        "clinical_setting": "merge:placement",
        "indication": "merge:case_observed",
        "trainee_performance": "merge:case_observed",
    },
    "LAT": {
        "clinical_setting": "merge:trainee_post",
        "stage_of_training": "safe_skip:no_verified_dom_field",
        "reflection": "merge:clinical_reasoning",
    },
    "MINI_CEX": {
        "clinical_reasoning": "merge:patient_presentation",
    },
    "MSF": {
        "stage_of_training": "safe_skip:self_evaluation_form_has_no_verified_stage_dom",
    },
    "QIAT": {
        "reflection": "safe_skip:no_verified_dom_field",
    },
    "SDL": {
        "learning_activity_type": "merge:resource_details",
    },
    "TEACH_OBS": {
        "stage_of_training": "safe_skip:no_verified_dom_field",
    },
    "AUDIT": {
        "reflection": "safe_skip:no_verified_dom_field",
    },
    "RESEARCH": {
        "reflection": "safe_skip:no_verified_dom_field",
    },
}


def required_field_handling(form_type: str, field_key: str) -> str | None:
    form_type = canonical_form_type(form_type)
    handling_key = form_type[:-5] if form_type.endswith("_2021") else form_type
    return (
        SCHEMA_REQUIRED_FIELD_HANDLING.get(form_type, {}).get(field_key)
        or SCHEMA_REQUIRED_FIELD_HANDLING.get(handling_key, {}).get(field_key)
    )


def _append_section(existing: Any, label: str, value: Any) -> str:
    existing_text = str(existing).strip() if existing is not None else ""
    value_text = str(value).strip() if value is not None else ""
    if not value_text:
        return existing_text
    section = f"{label}: {value_text}"
    if not existing_text:
        return section
    if value_text in existing_text:
        return existing_text
    return f"{existing_text}\n\n{section}"


def normalise_fields_for_deterministic_filing(form_type: str, fields: dict) -> dict:
    """Adapt extractor schema fields to the deterministic Kaizen field map.

    This only performs mappings with a verified target in FORM_FIELD_MAP. Fields
    listed as safe_skip in SCHEMA_REQUIRED_FIELD_HANDLING are left untouched so
    the filer reports them as skipped/partial instead of silently misfiling.
    """
    form_type = canonical_form_type(form_type)
    handling_key = form_type[:-5] if form_type.endswith("_2021") else form_type
    out = dict(fields or {})

    if handling_key == "DOPS":
        from dops_filing import normalise_dops_fields
        return normalise_dops_fields(out)

    if handling_key == "CBD":
        for key, label in (
            ("clinical_setting", "Clinical setting"),
            ("patient_presentation", "Patient presentation"),
            ("trainee_role", "Trainee role"),
            ("level_of_supervision", "Level of supervision"),
        ):
            if out.get(key):
                out["clinical_reasoning"] = _append_section(out.get("clinical_reasoning"), label, out[key])
                out.pop(key, None)

    elif handling_key == "MINI_CEX" and out.get("clinical_reasoning"):
        out["patient_presentation"] = _append_section(
            out.get("patient_presentation"), "Clinical assessment", out["clinical_reasoning"]
        )
        out.pop("clinical_reasoning", None)

    elif handling_key == "LAT":
        if out.get("clinical_setting") and not out.get("trainee_post"):
            out["trainee_post"] = out["clinical_setting"]
            out.pop("clinical_setting", None)
        if out.get("reflection"):
            out["clinical_reasoning"] = _append_section(out.get("clinical_reasoning"), "Reflection", out["reflection"])
            out.pop("reflection", None)

    elif handling_key == "SDL" and out.get("learning_activity_type"):
        value = out["learning_activity_type"]
        if isinstance(value, (list, tuple, set)):
            value = ", ".join(str(item) for item in value if str(item).strip())
        out["resource_details"] = _append_section(out.get("resource_details"), "Learning activity type", value)
        out.pop("learning_activity_type", None)

    return out


def _first_present_value(fields: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = fields.get(key)
        if value is None or value == "" or value == []:
            continue
        return str(value).strip()
    return ""


def _trim_to_word_boundary(text: str, max_chars: int) -> str:
    """Return a complete-looking one-line summary without ellipsis."""
    if len(text) <= max_chars:
        return text
    trimmed = text[:max_chars].rstrip()
    boundary = max(trimmed.rfind(" "), trimmed.rfind(","), trimmed.rfind(";"), trimmed.rfind(":"))
    if boundary >= max(40, int(max_chars * 0.55)):
        trimmed = trimmed[:boundary]
    trimmed = trimmed.rstrip(" ,;:-.")
    return f"{trimmed}." if trimmed else ""


def _one_line_event_summary(text: str, max_chars: int = 110) -> str:
    """Format Kaizen's timeline Description as a short complete summary."""
    summary = _strip_emojis(str(text or ""))
    summary = re.sub(r"[*_`#>\[\]]", "", summary)
    summary = re.sub(r"\s+", " ", summary).strip()
    if not summary:
        return ""

    was_clipped = bool(re.search(r"(?:\.\.\.|…)+$", summary))
    summary = re.sub(r"(?:\.\.\.|…)+$", "", summary).strip(" ,;:-")
    if was_clipped and not re.search(r"[.!?]$", summary):
        last_space = summary.rfind(" ")
        if last_space > 20:
            summary = summary[:last_space].rstrip(" ,;:-.")
        if summary:
            summary = f"{summary}."
    first_sentence = re.split(r"(?<=[.!?])\s+", summary, maxsplit=1)[0].strip()
    if first_sentence and len(first_sentence) <= max_chars:
        return first_sentence
    return _trim_to_word_boundary(first_sentence or summary, max_chars)


def _short_event_description(form_type: str, fields: dict) -> str:
    """Build the Kaizen timeline description from available case facts."""
    candidates = (
        "event_description",
        "case_title",
        "reflection_title",
        "session_title",
        "title_of_session",
        "paper_title",
        "title_of_education",
        "procedure_name",
        "procedural_skill",
        "patient_presentation",
        "clinical_setting",
        "clinical_reasoning",
        "case_observed",
        "leadership_context",
        "situation",
        "clinical_scenario",
        "description",
        "reflection",
    )
    parts = []
    for key in candidates:
        value = fields.get(key)
        if value is None or value == "" or value == []:
            continue
        text = str(value).strip()
        if key in {"clinical_setting", "procedure_name", "procedural_skill"} and parts:
            continue
        if text and text not in parts:
            parts.append(text)
        if len(" - ".join(parts)) >= 90:
            break
    return _one_line_event_summary(" - ".join(parts))


def apply_common_header_defaults(form_type: str, fields: dict, field_map: dict | None = None) -> tuple[dict, dict]:
    """Ensure Kaizen's static wrapper fields are filled whenever possible."""
    out = dict(fields or {})
    field_map = field_map or FORM_FIELD_MAP.get(canonical_form_type(form_type), {})
    defaulted = []

    start_keys = tuple(key for key, dom_id in field_map.items() if dom_id == "startDate") + ("date_of_encounter",)
    end_keys = tuple(key for key, dom_id in field_map.items() if dom_id == "endDate") + ("end_date",)
    date_source_keys = (
        "date_of_encounter",
        "date_of_event",
        "date_of_activity",
        "date_of_education",
        "date_of_teaching",
        "date_of_teaching_activity",
        "date_of_case",
        "date_of_complaint",
        "date_of_incident",
        "date_of_completion",
        "end_date",
    )

    source_date = _first_present_value(out, date_source_keys)
    if not source_date:
        source_date = date.today().isoformat()
        defaulted.append("date_of_encounter")

    if not _first_present_value(out, start_keys):
        out["date_of_encounter"] = source_date
    if not _first_present_value(out, end_keys):
        out["end_date"] = source_date
        if "end_date" not in defaulted:
            defaulted.append("end_date")

    if out.get("event_description"):
        out["event_description"] = _one_line_event_summary(out["event_description"])
    else:
        description = _short_event_description(form_type, out)
        if description:
            out["event_description"] = description

    meta = {
        "defaulted_fields": defaulted,
        "activity_date": source_date,
        "event_description": out.get("event_description") or "",
    }
    return out, meta


def drop_consumed_unmapped_schema_fields(form_type: str, fields: dict) -> dict:
    """Remove schema-only source fields once their mapped target is populated."""
    form_type = canonical_form_type(form_type)
    handling_key = form_type[:-5] if form_type.endswith("_2021") else form_type
    field_map = FORM_FIELD_MAP.get(form_type, {})
    out = dict(fields or {})
    for key, handling in SCHEMA_REQUIRED_FIELD_HANDLING.get(handling_key, {}).items():
        if key in field_map or not handling.startswith("merge:"):
            continue
        target = handling.split(":", 1)[1]
        if out.get(target):
            out.pop(key, None)
    return out

# ─── Form type UUIDs (for creating new forms) ────────────────────────────────

FORM_UUIDS = {
    "CBD":           "3ce5989a-b61c-4c24-ab12-711bf928b181",
    "DOPS":          "159831f9-6d22-4e77-851b-87e30aee37a2",
    "MINI_CEX":      "647665f4-a992-4541-9e17-33ba6fd1d347",
    "LAT":           "eb1c7547-0f41-49e7-95de-8adffd849924",
    "ACAT":          "6577ab06-8340-47e3-952a-708a5f800dcc",
    "ACAF":          "15e67ae8-868b-4358-9b96-30a4a272f02c",
    "STAT":          "41ff54b8-35a7-414b-9bd6-97fb1c3eb189",
    "MSF":           "5f71ac04-ff45-44d2-b7a1-f8b921a8a4c8",
    "JCF":           "3daa9559-3c31-4ab4-883c-9a991632a9ca",
    "QIAT":          "a0aa5cfc-57be-4622-b974-51d334268d57",
    "TEACH":         "1ffbd272-8447-439c-aa03-ff99e2dbc04d",
    "PROC_LOG":      "2d6ebac1-4633-49d1-9dc0-fa0d39a98afc",
    "SDL":           "743885d8-c1b8-4566-bc09-8ed9b0e09829",
    "US_CASE":       "558b196a-8168-4cc6-b363-6f6e4b08397a",
    # ESLE forms on Kaizen — two separate forms exist:
    #   - "ESLE: Part 1 & 2" = the actual ESLE WPBA. Trainee fills Part 1,
    #     assessor fills Part 2. Use ESLE_PART1_2 for this.
    #   - "Reflection on ESLE" = supplementary reflective entry that does NOT
    #     go to an assessor. Use ESLE_REFLECTION for this.
    # When the user asks for "an ESLE", they almost always mean ESLE_PART1_2
    # unless they explicitly say "reflection on ESLE".
    "ESLE_PART1_2":  "4a6f3a91-10ed-45d0-bb82-3e87ae2d6d04",  # actual ESLE WPBA (Part 1 trainee, Part 2 assessor)
    "ESLE_REFLECTION": "cbc7a42f-a2f0-436b-813e-bbf97cce0a34",  # supplementary reflective entry, no assessor
    "COMPLAINT":     "f7c0ba98-5a47-4e37-b76a-ca3c5c8484cc",
    "SERIOUS_INC":   "9d4a7912-a615-4ae4-9fae-6be966bcf254",
    "EDU_ACT":       "868dc0e7-f4e9-4283-ac52-d9c8b246024b",
    "FORMAL_COURSE": "c7cd9a95-e2aa-4f61-a441-b663f3c933c6",
    "REFLECT_LOG":   "32d0fcb9-05d0-4d6d-b877-ebd5daf0b4e9",
    "TEACH_OBS":     "30668ad8-e1db-4a27-bb2d-3e395e6acfcf",
    "TEACH_CONFID":  "f614bdcc-5d31-4b5b-b980-1e073e2431db",
    # ─── MGMT/admin forms (live-verified 2026-04-01) ──────────────────────────
    "CRIT_INCIDENT":      "b6445c81-388b-4f48-b510-b080b406b74e",
    "MGMT_RISK":          "4a349b8d-6f9f-478f-b623-4f083d6ce87b",
    "AUDIT":              "33c454df-eb86-49f1-8ec0-ee2ccbe8c574",
    "CLIN_GOV":           "d5a56390-d229-41f6-b67f-3231a3390f75",
    "MGMT_TRAINING_EVT":  "2cd1ddb3-7d33-45dd-9269-c09209568391",
    "EQUIP_SERVICE":      "ec09e28d-86f3-4bdc-8547-ef3ab0a5388e",
    "MGMT_EXPERIENCE":    "73805ea3-ee61-4a59-a57d-d89aca660309",
    "PDP":                "c2b716dd-2d2a-462e-8df0-70760673448c",
    "MGMT_RECRUIT":       "2a2c04a5-388a-4b38-ad74-06bacfd39594",
    "BUSINESS_CASE":      "8a720578-cee6-4e19-b9ff-fb0f95a3019c",
    "COST_IMPROVE":       "1cc77669-859f-4d2a-9588-f3d0de69f40f",
    "MGMT_PROJECT":       "6b5f60e2-0237-4429-9870-a2bd8cceeb97",
    "APPRAISAL":          "099be248-10de-4241-99ec-970d947963ae",
    "MGMT_GUIDELINE":     "8121d957-ed22-4799-b9fa-d3eb52c9a37a",
    "MGMT_INFO":          "9d396397-94bc-4905-b27b-547c938868de",
    "MGMT_RISK_PROC":     "957ab9dc-de1e-4b87-b38f-9bd4f54cb9a1",
    "MGMT_INDUCTION":     "fb37ecae-334a-40e2-aa6e-043a24952283",
    "RESEARCH":           "3d4c6a82-f7ab-4b11-bb36-c7487de4ff2d",
    "MGMT_ROTA":          "ffc650a7-309d-42e0-8886-21521114bfb2",
    "MGMT_REPORT":        "0131f31d-a78c-41cb-8147-15fc1e2c42df",
    "MGMT_COMPLAINT":     "89217cd1-cfae-4006-b35e-221c46f5a645",
    "EDU_MEETING_SUPP":   "35e1bd6b-4de3-441b-82f7-ef236a8f7a7c",
    "EDU_MEETING":        "cf3c4b40-12e6-46ca-b7a7-4914bf792f6b",
    # ─── Utility form types (not portfolio evidence) ─────────────────────────
    "ADD_POST":           "c8049d8b-11f7-4bad-ac6c-c0b3c9ded1bb",
    "ADD_SUPERVISOR":     "87205ea8-ee22-4555-8e30-3a5ffc8b0bd2",
    "HIGHER_PROG":        "c19ca7c4-54ba-4816-b292-8bce1af4a62f",
    "ABSENCE":            "9feb8df3-1c70-4237-bf77-c6520e43c9c2",
    "CCT":                "9425aea9-1fb9-4230-b2a3-ec1712599caa",
    "FILE_UPLOAD":        "108ae04a-d865-4a4a-ba97-9c537563e960",
    "OOP":                "2b023326-a34f-463e-a921-bf215599b0ac",
    # ─── 2021 curriculum variants ─────────────────────────────────────────────
    "CBD_2021":           "310b903a-8c97-44e0-8ec3-4bf692b33441",
    "DOPS_2021":          "27a300c6-245a-4fed-943e-fe2976686d0d",
    "ACAT_2021":          "2a8a02fe-c085-4cd7-a78e-b024a359011a",
    "ACAF_2021":          "37978f7b-1770-40ed-8bf1-53a96ae13c25",
    "STAT_2021":          "262e7e37-9f74-414f-bc88-fb6ff5ce2239",
    "MINI_CEX_2021":      "26978104-5583-46c4-9799-07555a18b3d4",
    "JCF_2021":           "efb238d0-66f7-487d-b18a-cfda78c8e733",
    "ESLE_2021":          "e4417335-969c-4a4e-a04f-cc272afc1ab8",
    "TEACH_2021":         "98c35142-6b8d-4958-86c5-4dfd06f22143",
    "PROC_LOG_2021":      "25527933-81e6-484f-b4dd-7ea23c2e3919",
    "SDL_2021":           "5f679c9f-ed61-4dc9-afc9-2c1f98ba3983",
    "US_CASE_2021":       "eede404a-cfab-442f-8c4c-0a1160cc45f1",
    "COMPLAINT_2021":     "6c8cd525-dae4-479c-8836-864691a74832",
    "SERIOUS_INC_2021":   "e2df1663-1b94-403a-91fa-37f568161ed5",
    "EDU_ACT_2021":       "7a40ed0e-0280-4e16-b3dc-468022d84575",
    "FORMAL_COURSE_2021": "1889dfd7-4267-4b77-a062-357740c2ed4d",
    "TEACH_OBS_2021":     "e43a8b88-2bea-4bdb-a5aa-02e0cd388698",
    "TEACH_CONFID_2021":  "563d2c82-46b5-41d7-b601-58a45b347a3a",
}

# ─── Per-form field normalisers ──────────────────────────────────────────────
# Forms whose extractor output needs schema-specific reshaping before deterministic
# filling. Keyed by canonical form_type. Default is identity (no transform).
from dops_filing import normalise_dops_fields

FORM_FIELD_NORMALISERS = {
    "DOPS": normalise_dops_fields,
}


_FORM_FIELD_MAP_VARIANT_BASES = {
    "CBD_2021": "CBD",
    "DOPS_2021": "DOPS",
    "ACAT_2021": "ACAT",
    "ACAF_2021": "ACAF",
    "STAT_2021": "STAT",
    "MINI_CEX_2021": "MINI_CEX",
    "JCF_2021": "JCF",
    "ESLE_2021": "ESLE_PART1_2",
    "TEACH_2021": "TEACH",
    "PROC_LOG_2021": "PROC_LOG",
    "SDL_2021": "SDL",
    "US_CASE_2021": "US_CASE",
    "COMPLAINT_2021": "COMPLAINT",
    "SERIOUS_INC_2021": "SERIOUS_INC",
    "EDU_ACT_2021": "EDU_ACT",
    "FORMAL_COURSE_2021": "FORMAL_COURSE",
    "TEACH_OBS_2021": "TEACH_OBS",
    "TEACH_CONFID_2021": "TEACH_CONFID",
}

for _variant, _base in _FORM_FIELD_MAP_VARIANT_BASES.items():
    if _base in FORM_FIELD_MAP:
        FORM_FIELD_MAP.setdefault(_variant, FORM_FIELD_MAP[_base])


# ─── JS snippets (passed as separate strings, NEVER f-string interpolated) ───

EXPAND_SLO_JS = """(sloText) => {
    var anchors = document.querySelectorAll('a.ng-binding');
    for (var i = 0; i < anchors.length; i++) {
        if (anchors[i].textContent.indexOf(sloText) !== -1) {
            anchors[i].click();
            return true;
        }
    }
    return false;
}"""

# Edit-existing-draft (view-section / fillin) re-renders the curriculum tree
# with different Angular classes than new-section, so the strict `a.ng-binding`
# selector misses the SLO anchors. Walk a broader set of clickable elements,
# and as a last resort match by SLO number alone.
EXPAND_SLO_FALLBACK_JS = """(sloText) => {
    var nodes = document.querySelectorAll('a, button, [ng-click], [data-target], .panel-heading, .accordion-toggle');
    for (var i = 0; i < nodes.length; i++) {
        var text = (nodes[i].textContent || '').trim();
        if (text.indexOf(sloText) !== -1) {
            nodes[i].click();
            return true;
        }
    }
    var m = sloText.match(/SLO\\s*\\d+/);
    if (m) {
        var sloOnly = m[0].replace(/\\s+/g, '');
        for (var j = 0; j < nodes.length; j++) {
            var t2 = (nodes[j].textContent || '').replace(/\\s+/g, '');
            if (t2.indexOf(sloOnly) !== -1) {
                nodes[j].click();
                return true;
            }
        }
    }
    return false;
}"""

TICK_KC_JS = """(prefix) => {
    function variants(raw) {
        var out = [raw];
        var m = raw.match(/\\bSLO\\s*(\\d+)\\s*KC\\s*(\\d+)\\b/i);
        if (m) {
            out.push('SLO' + m[1] + ' Key Capability ' + m[2]);
            out.push('SLO ' + m[1] + ' Key Capability ' + m[2]);
        }
        return out.map(function(v) { return v.toLowerCase().replace(/\\s+/g, ' ').trim(); });
    }
    var wanted = variants(prefix);
    var selectors = ['span.ng-binding.ng-scope', 'span.ng-binding'];
    for (var s = 0; s < selectors.length; s++) {
        var spans = document.querySelectorAll(selectors[s]);
        for (var i = 0; i < spans.length; i++) {
            var txt = spans[i].textContent.trim();
            var normalised = txt.toLowerCase().replace(/\\s+/g, ' ').trim();
            if (wanted.some(function(v) { return normalised.indexOf(v) !== -1; })) {
                var li = spans[i].parentElement;
                while (li && li.tagName !== 'LI') { li = li.parentElement; }
                if (li) {
                    var cb = li.querySelector('input[type="checkbox"]');
                    if (cb) {
                        cb.click();
                        return { found: true, checked: cb.checked, text: txt.slice(0, 70) };
                    }
                }
                return { found: true, no_cb: true };
            }
        }
    }
    return { found: false };
}"""

# Edit-existing-draft view re-renders KC rows without `span.ng-binding.ng-scope`,
# and sometimes nests the checkbox outside the LI ancestor. Fall back to a
# broader text scan across labels/divs/list items, climb to whichever ancestor
# carries a checkbox, and click the label as a last resort.
TICK_KC_FALLBACK_JS = """(prefix) => {
    function normalise(s) { return (s || '').toLowerCase().replace(/\\s+/g, ' ').trim(); }
    var m = prefix.match(/SLO\\s*(\\d+)\\s*KC\\s*(\\d+)/i);
    if (!m) return { found: false };
    var sloNum = m[1], kcNum = m[2];
    var patterns = [
        'slo' + sloNum + ' kc' + kcNum,
        'slo ' + sloNum + ' kc ' + kcNum,
        'slo' + sloNum + ' key capability ' + kcNum,
        'slo ' + sloNum + ' key capability ' + kcNum,
    ];
    var nodes = document.querySelectorAll('li, label, .list-group-item, .checkbox, div.ng-scope, span');
    for (var i = 0; i < nodes.length; i++) {
        var t = normalise(nodes[i].textContent);
        if (!patterns.some(function(p) { return t.indexOf(p) !== -1; })) continue;
        var container = nodes[i];
        var hops = 0;
        while (container && hops < 6) {
            var cb = container.querySelector ? container.querySelector('input[type="checkbox"]') : null;
            if (cb) {
                if (!cb.checked) cb.click();
                return { found: true, checked: cb.checked, text: t.slice(0, 70) };
            }
            container = container.parentElement;
            hops++;
        }
        var lbl = nodes[i].querySelector ? nodes[i].querySelector('label') : null;
        if (lbl) {
            lbl.click();
            return { found: true, checked: true, text: t.slice(0, 70) };
        }
    }
    return { found: false };
}"""

COUNT_TICKED_JS = """() => {
    var cbs = document.querySelectorAll('input[type="checkbox"]:checked');
    return cbs.length;
}"""


# ─── CDP connection ──────────────────────────────────────────────────────────

async def _connect_cdp() -> tuple:
    """Connect to managed Chrome via CDP, or fall back to headless Chromium."""
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(CDP_URL, no_defaults=True)
        # Reuse existing Kaizen page if available
        for context in browser.contexts:
            for page in context.pages:
                if "kaizenep.com" in page.url:
                    logger.info(f"CDP: reusing Kaizen page: {page.url}")
                    return page, pw
        # Open new page
        if browser.contexts:
            page = await browser.contexts[0].new_page()
        else:
            ctx = await browser.new_context()
            page = await ctx.new_page()
        logger.info("CDP: opened new page")
        return page, pw
    except Exception as e:
        logger.warning(f"CDP not available ({e}) — falling back to headless Chromium")
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        logger.info("Headless: launched new browser")
        return page, pw


# ─── Login ────────────────────────────────────────────────────────────────────

async def _login(page: Page, username: str, password: str) -> bool:
    """Log in to Kaizen via RCEM portal (two-step: username → password)."""
    try:
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
        logger.info(f"Login success: {page.url}")
        return True
    except Exception as e:
        logger.error(f"Login failed: {e}")
        return False


# ─── Date filling (THE critical fix) ─────────────────────────────────────────

async def _fill_date(page: Page, dom_id: str, raw_value: str) -> bool:
    """
    Fill a date field using click + triple_click + type + Tab.
    This is the ONLY way to trigger AngularJS watchers on date inputs.
    Never use .fill() or el.value = ... for dates.
    """
    uk_date = _to_uk_date(raw_value)
    if not uk_date:
        return False

    el = page.locator(f'[id="{dom_id}"]')
    if not await el.count():
        logger.warning(f"Date field not found: {dom_id}")
        return False

    try:
        # Try normal click first exactly as originally written to preserve signature for mock tests
        await el.click()
        await el.click(click_count=3)
    except Exception as e:
        logger.warning(f"Normal click on date field #{dom_id} failed or timed out: {e}. Trying force=True.")
        try:
            await el.click(force=True, timeout=2000)
            await el.click(click_count=3, force=True, timeout=2000)
        except Exception as force_e:
            logger.warning(f"Forced click on date field #{dom_id} also failed: {force_e}. Attempting focus fallback.")
            try:
                await el.focus()
            except Exception as focus_e:
                logger.error(f"Focus fallback on date field #{dom_id} failed: {focus_e}")

    try:
        await el.type(uk_date, delay=50)  # type char by char
    except Exception as e:
        logger.warning(f"Date type failed for {dom_id}; using JS event fallback: {e}")
        ok = await page.evaluate(
            """({domId, value}) => {
                const el = document.getElementById(domId);
                if (!el) return false;
                el.focus();
                el.value = value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
                return true;
            }""",
            {"domId": dom_id, "value": uk_date},
        )
        if not ok:
            return False
    await page.keyboard.press("Tab")  # trigger Angular watcher
    await asyncio.sleep(1)

    # Verify — Kaizen strips leading zeros (28/03/2026 → 28/3/2026)
    def _norm(d):
        parts = d.split("/") if d else []
        if len(parts) == 3:
            return f"{int(parts[0])}/{int(parts[1])}/{parts[2]}"
        return d

    val = await el.evaluate("el => el.value")
    if val and _norm(val) == _norm(uk_date):
        logger.info(f"Date filled: {dom_id} = {val}")
        return True

    # Kaizen's edit-existing-draft view (view-section / fillin URLs) wires the
    # date field through a different Angular directive than new-section. The
    # type+Tab path silently leaves the model untouched there. Retry by setting
    # the value directly and dispatching the events Angular watches for, then
    # re-verify before giving up.
    logger.warning(
        f"Date verify mismatch on type+Tab path: expected {uk_date}, got {val}. "
        "Retrying via direct JS value assignment."
    )
    await page.evaluate(
        """({domId, value}) => {
            const el = document.getElementById(domId);
            if (!el) return false;
            el.focus();
            el.value = value;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
            return true;
        }""",
        {"domId": dom_id, "value": uk_date},
    )
    await asyncio.sleep(1)
    val = await el.evaluate("el => el.value")
    if val and _norm(val) == _norm(uk_date):
        logger.info(f"Date filled via JS fallback: {dom_id} = {val}")
        return True
    logger.warning(f"Date verify mismatch after JS fallback: expected {uk_date}, got {val}")
    return False


# ─── Stage dropdown ──────────────────────────────────────────────────────────

async def _fill_stage(page: Page, dom_id: str, stage_label: str) -> bool:
    """Fill stage of training dropdown using Angular select value."""
    # Empty stage means the user didn't declare a training level. Better to
    # skip and leave the dropdown untouched than to guess and write the wrong
    # stage onto a real portfolio draft.
    stage_label = (stage_label or "").strip()
    if not stage_label:
        logger.info(f"Stage skipped: no stage_label provided for {dom_id}")
        return False

    # QIAT uses a different stage dropdown with individual year values
    is_qiat_stage = (dom_id == "415a72f2-7cf3-420a-bee4-9a7aed746612")
    values_map = QIAT_STAGE_VALUES if is_qiat_stage else STAGE_SELECT_VALUES

    # Normalise label
    stage_key = stage_label
    for key in values_map:
        if key.lower() in stage_label.lower():
            stage_key = key
            break
    if not is_qiat_stage and stage_key == stage_label:
        stage_text = stage_label.lower()
        if re.search(r"\bst[4-6]\b", stage_text):
            stage_key = "Higher"
        elif re.search(r"\bst3\b", stage_text):
            stage_key = "Intermediate"
        elif re.search(r"\b(st[12]|ct[12])\b", stage_text) or "accs" in stage_text:
            stage_key = "ACCS"

    angular_value = values_map.get(stage_key)
    if not angular_value:
        logger.warning(f"Unknown stage: {stage_label}")
        return False

    el = page.locator(f'[id="{dom_id}"]')
    if not await el.count():
        # Try generic stage selector
        el = page.locator('select[ng-model*="stage"], select[ng-model*="Stage"]').first
        if not await el.count():
            logger.warning(f"Stage dropdown not found: {dom_id}")
            return False

    await el.select_option(value=angular_value)
    await asyncio.sleep(5)  # MUST wait 5s for curriculum section to load
    logger.info(f"Stage set: {stage_key}")
    return True


# ─── Text field filling ─────────────────────────────────────────────────────

async def _fill_text(page: Page, dom_id: str, value: str) -> bool:
    """Fill a text/textarea field. Strips emojis."""
    clean = _strip_emojis(str(value))
    if not clean:
        return False

    # Try multiple selector patterns
    for selector in [
        f'[id="{dom_id}"]',
        f'textarea[id="{dom_id}"]',
        f'div[id="{dom_id}"] textarea',
    ]:
        el = page.locator(selector).first
        if await el.count():
            try:
                # Try normal click first exactly as originally written to preserve signature for mock tests
                await el.click()
            except Exception as e:
                logger.warning(f"Normal click on text field {selector} failed or timed out: {e}. Trying force=True.")
                try:
                    await el.click(force=True, timeout=2000)
                except Exception as force_e:
                    logger.warning(f"Forced click on text field {selector} also failed: {force_e}. Attempting focus fallback.")
                    try:
                        await el.focus()
                    except Exception as focus_e:
                        logger.error(f"Focus fallback on text field {selector} failed: {focus_e}")
            try:
                await el.fill(clean)
            except Exception as e:
                logger.warning(f"Text fill failed for {dom_id}; using JS event fallback: {e}")
                ok = await page.evaluate(
                    """({domId, value}) => {
                        const el = document.getElementById(domId);
                        if (!el) return false;
                        el.focus();
                        el.value = value;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                        return true;
                    }""",
                    {"domId": dom_id, "value": clean},
                )
                if not ok:
                    return False
            await asyncio.sleep(0.5)
            logger.info(f"Text filled: {dom_id} ({len(clean)} chars)")
            return True

    logger.warning(f"Text field not found: {dom_id}")
    return False


# ─── Select dropdown filling ────────────────────────────────────────────────

async def _fill_select(page: Page, dom_id: str, value: str) -> bool:
    """Fill a generic select dropdown by label text (exact or partial match)."""
    el = page.locator(f'[id="{dom_id}"]')
    if not await el.count():
        return False

    try:
        await el.select_option(label=value)
        await asyncio.sleep(1)
        logger.info(f"Select set: {dom_id} = {value}")
        return True
    except Exception:
        # Try by value
        try:
            await el.select_option(value=value)
            await asyncio.sleep(1)
            return True
        except Exception:
            # Try partial label match (e.g. procedure dropdowns have numbered prefixes)
            try:
                options = await page.evaluate(
                    "(domId) => { var s = document.getElementById(domId); return s ? Array.from(s.options).map(o => o.text) : []; }",
                    dom_id
                )
                match = next((o for o in options if value.lower() in o.lower()), None)
                if match:
                    await el.select_option(label=match)
                    await asyncio.sleep(1)
                    logger.info(f"Select partial match: {dom_id} = {match}")
                    return True
            except Exception as e:
                logger.warning(f"Select fill failed for {dom_id}: {e}")
            return False


async def _normalise_dops_select_value(page: Page, field_key: str, dom_id: str, value: Any) -> str:
    """Map DOPS select aliases to exact rendered Kaizen option labels."""
    if field_key != "placement":
        return str(value)
    try:
        options = await page.evaluate(
            "(domId) => { var s = document.getElementById(domId); return s ? Array.from(s.options).map(o => o.text) : []; }",
            dom_id,
        )
        from dops_filing import normalise_dops_placement
        return normalise_dops_placement(str(value), options)
    except Exception as e:
        logger.warning(f"DOPS placement option normalisation failed for {dom_id}: {e}")
        return str(value)


async def _fill_assessor_invite(page: Page, query: str, expected_name: str = "") -> bool:
    """Pick one assessor from Kaizen's typeahead before sending a WPBA.

    This is deliberately stricter than ordinary text filling because selecting
    the wrong assessor sends live portfolio work to a real person.
    """
    query = str(query or "").strip()
    expected_name = str(expected_name or "").strip()
    if not query:
        return False

    invite = page.locator("input#invites").first
    if not await invite.count():
        logger.warning("Assessor invite field not found")
        return False

    await invite.click()
    await page.keyboard.press("Meta+A")
    await invite.type(query, delay=40)
    await asyncio.sleep(3)

    suggestions = await page.locator("#invites_listbox .tt-suggestion").all()
    if not suggestions:
        logger.warning(f"No assessor suggestions for query: {query}")
        return False

    target_tokens = [t.lower() for t in re.findall(r"[A-Za-z]+", expected_name or query) if len(t) > 2]
    for index, suggestion in enumerate(suggestions):
        text = (await suggestion.inner_text()).strip()
        haystack = text.lower()
        if target_tokens and not all(token in haystack for token in target_tokens):
            continue
        try:
            # Twitter Typeahead reliably updates Angular validators when a
            # suggestion is selected through keyboard navigation.
            for _ in range(index + 1):
                await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
        except TypeError:
            await suggestion.click()
        except Exception:
            clicked = await page.evaluate(
                """(label) => {
                  const items = Array.from(document.querySelectorAll('#invites_listbox .tt-suggestion'));
                  const item = items.find(el => (el.textContent || '').trim() === label);
                  if (!item) return false;
                  item.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                  item.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                  item.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                  return true;
                }""",
                text,
            )
            if not clicked:
                raise
        await asyncio.sleep(1)
        selection_verified = await page.evaluate(
            """(tokens) => {
              const input = document.querySelector('#invites');
              const root = input?.closest('.form-group, .twitter-typeahead, body') || document.body;
              const text = (root.textContent || '') + ' ' + (input?.value || '');
              const haystack = text.toLowerCase();
              const tokenMatch = tokens.every(token => haystack.includes(token));
              const validEmail = !input || input.className.includes('ng-valid-email');
              return tokenMatch && validEmail;
            }""",
            target_tokens,
        )
        if target_tokens and not selection_verified:
            logger.warning("Assessor typeahead click did not verify selected assessor")
            return False
        logger.info(f"Assessor selected from typeahead: {text}")
        return True

    visible = []
    for suggestion in suggestions[:5]:
        try:
            visible.append((await suggestion.inner_text()).strip())
        except Exception:
            pass
    logger.warning(f"Assessor suggestions did not match expected target: {visible}")
    return False


# ─── Curriculum links (SLO expansion + KC ticking) ──────────────────────────

async def _fill_curriculum_links(
    page: Page,
    slo_codes: List[str],
    kc_targets: List[str],
    stage_label: str,
) -> tuple:
    """Expand the relevant SLO accordions and tick the matching KCs.

    slo_codes:  ["SLO1", "SLO6", ...] — used to expand the SLO containers.
    kc_targets: full KC strings or KC codes ("SLO6 KC1: ...") — what to tick.
                Falls back to slo_codes if no kc_targets given (legacy callers).
    """
    ticked = []
    errors = []

    if not kc_targets:
        kc_targets = slo_codes
    if not slo_codes and not kc_targets:
        return ticked, errors

    # Derive SLO numbers from whichever list we have. KC strings like
    # "SLO6 KC1: ..." also contain the SLO number, so this works for both.
    slos = set()
    for source in (slo_codes, kc_targets):
        for entry in source:
            m = re.search(r"SLO(\d+)", entry)
            if m:
                slos.add(m.group(0))

    stage_prefix = "Higher"
    if stage_label:
        for s in ("Higher", "Intermediate", "ACCS", "PEM"):
            if s.lower() in stage_label.lower():
                stage_prefix = s
                break

    for slo in sorted(slos):
        slo_text = f"{stage_prefix} {slo}:"
        expanded = await page.evaluate(EXPAND_SLO_JS, slo_text)
        if not expanded:
            # edit-existing-draft view renders SLO anchors with different
            # classes; the strict selector misses them. Try the broader scan.
            expanded = await page.evaluate(EXPAND_SLO_FALLBACK_JS, slo_text)
            if expanded:
                logger.info(f"Expanded via fallback: {slo_text}")
        if expanded:
            logger.info(f"Expanded: {slo_text}")
        else:
            logger.warning(f"Could not expand: {slo_text}")
            errors.append(f"SLO expand failed: {slo_text}")
        await asyncio.sleep(3)  # MUST wait 3s for Angular to render KCs

    for target in kc_targets:
        result = await page.evaluate(TICK_KC_JS, target)
        if not (result.get("found") and result.get("checked")):
            # Either the KC row was not located, or it was found without a
            # reachable checkbox. Re-scan with the broader selector set.
            fallback = await page.evaluate(TICK_KC_FALLBACK_JS, target)
            if fallback.get("found") and fallback.get("checked"):
                result = fallback
                logger.info(f"KC ticked via fallback: {target}")
        if result.get("found") and result.get("checked"):
            ticked.append(target)
            logger.info(f"KC ticked: {target}")
        elif result.get("found") and result.get("no_cb"):
            errors.append(f"KC found but no checkbox: {target}")
        else:
            errors.append(f"KC not found: {target}")
        await asyncio.sleep(0.5)

    return ticked, errors


# ─── Save ─────────────────────────────────────────────────────────────────────

async def _save_form(page: Page, as_draft: bool) -> bool:
    """Save the form as draft. Never Submit/Send. Returns True on success.

    The bare `a:has-text("Save")` and `button:has-text("Save")` fallbacks cover
    Kaizen's edit-existing-draft page (`/events/fillin/...`), which renders the
    save control as a plain Save anchor rather than the "Save as draft" used on
    `new-section`.
    """
    if as_draft:
        selectors = [
            'a:has-text("Save as draft")',
            'a:has-text("Save as Draft")',
            'a:has-text("Save draft")',
            'button:has-text("Save as Draft")',
            'button:has-text("Save Draft")',
            'a:has-text("Save")',
            'button:has-text("Save")',
        ]
    else:
        selectors = [
            'a:has-text("Send to assessor")',
            'button:has-text("Send to assessor")',
        ]

    for selector in selectors:
        try:
            el = page.locator(selector).first
            if await el.count() > 0:
                el_text = (await el.inner_text()).strip()
                if any(danger in el_text.lower() for danger in ["submit", "send", "sign", "approve", "reject", "delete"]):
                    if not as_draft and "send to assessor" in el_text.lower():
                        pass
                    else:
                        logger.warning(f"BLOCKED dangerous element: '{el_text}'")
                        continue
                await el.click()
                await asyncio.sleep(5)
                logger.info(f"Clicked save: '{el_text}'")
                body_text = await page.inner_text("body")
                if "LAST SAVED" in body_text.upper() or "SAVED" in body_text.upper():
                    logger.info("Form saved successfully")
                return True
        except Exception as e:
            logger.debug(f"Save selector {selector} failed: {e}")
            continue

    logger.error("Save button not found")
    return False


async def _verify_sent_to_assessor(page: Page, expected_assessor: str = "") -> bool:
    """Best-effort verification that a WPBA left the trainee-side draft state."""
    body = (await page.inner_text("body")).lower()
    expected_tokens = [t.lower() for t in re.findall(r"[A-Za-z]+", expected_assessor or "") if len(t) > 2]
    if "awaiting response" in body or "awaiting assessor" in body or "awaiting completion" in body:
        return not expected_tokens or all(token in body for token in expected_tokens)
    # A trainee-owned draft/section still showing these controls has not reached
    # the assessor queue, even if Kaizen accepted the form save.
    if "fill in" in body and "delete" in body and "preview" in body:
        return False
    return False


# ─── Verification pass ───────────────────────────────────────────────────────

async def _verify_fields(page: Page, fields: dict, field_map: dict, filled_keys: List[str]) -> List[str]:
    """Post-fill verification. Returns list of issues."""
    issues = []

    # Check date fields
    for key in ("date", "date_occurred_on", "date_of_encounter", "end_date", "date_of_education", "date_of_activity",
                "date_of_teaching", "date_of_case", "date_of_complaint", "date_of_incident"):
        if key in fields and key in field_map:
            dom_id = field_map[key]
            val = await page.evaluate(
                "(domId) => { var el = document.getElementById(domId); return el ? el.value : null; }",
                dom_id
            )
            expected = _to_uk_date(fields[key])
            def _norm_date(d):
                parts = d.split("/") if d else []
                return f"{int(parts[0])}/{int(parts[1])}/{parts[2]}" if len(parts) == 3 else d
            if val and _norm_date(val) != _norm_date(expected):
                issues.append(f"date mismatch: {key} expected {expected}, got {val}")

    # Check text fields have content
    for key in filled_keys:
        if key in ("date", "end_date", "stage", "curriculum_links") or "date" in key:
            continue
        dom_id = field_map.get(key)
        if not dom_id or dom_id in ("startDate", "endDate"):
            continue
        val = await page.evaluate(
            "(domId) => { var el = document.getElementById(domId); return el ? (el.value || el.textContent || '').trim() : null; }",
            dom_id
        )
        if not val or len(val) < 5:
            issues.append(f"{key} appears empty (dom_id={dom_id})")

    # Check KCs
    if fields.get("curriculum_links"):
        ticked_count = await page.evaluate(COUNT_TICKED_JS)
        if ticked_count == 0:
            issues.append("No KCs ticked")

    return issues


# ─── Post-filing QA verification (non-blocking) ──────────────────────────────

_QA_READ_FIELD_JS = """(domId) => {
    const el = document.getElementById(domId);
    if (!el) return {missing: true};
    const tag = el.tagName;
    if (tag === 'SELECT') {
        return {tag, selectedIndex: el.selectedIndex, value: el.value || ''};
    }
    if (tag === 'INPUT' && el.type === 'checkbox') {
        return {tag, type: 'checkbox', checked: !!el.checked};
    }
    return {tag, value: (el.value || el.textContent || '').trim()};
}"""

_QA_READ_KC_JS = """(prefix) => {
    function variants(raw) {
        var out = [raw];
        var m = raw.match(/\\bSLO\\s*(\\d+)\\s*KC\\s*(\\d+)\\b/i);
        if (m) {
            out.push('SLO' + m[1] + ' Key Capability ' + m[2]);
            out.push('SLO ' + m[1] + ' Key Capability ' + m[2]);
        }
        return out.map(function(v) { return v.toLowerCase().replace(/\\s+/g, ' ').trim(); });
    }
    var wanted = variants(prefix);
    var spans = document.querySelectorAll('span.ng-binding');
    for (var i = 0; i < spans.length; i++) {
        var txt = spans[i].textContent.trim();
        var normalised = txt.toLowerCase().replace(/\\s+/g, ' ').trim();
        if (wanted.some(function(v) { return normalised.indexOf(v) !== -1; })) {
            var li = spans[i].parentElement;
            while (li && li.tagName !== 'LI') { li = li.parentElement; }
            if (li) {
                var cb = li.querySelector('input[type="checkbox"]');
                if (cb) return !!cb.checked;
            }
        }
    }
    return false;
}"""


async def _verify_filing_qa(
    page: Page,
    form_type: str,
    expected_fields: Dict[str, Any],
    field_map: Dict[str, str],
) -> Dict[str, Any]:
    """Non-blocking post-filing QA pass.

    Reads each mapped DOM element after the draft was saved and categorises
    every field into one of three buckets:

      - filled              — DOM element has a non-empty value / checked state
      - empty_expected      — DOM is empty but the caller passed a value for it
      - empty_acceptable    — DOM is empty and the caller did not set it

    Curriculum KCs from `expected_fields["key_capabilities"]` (or
    `expected_fields["curriculum_links"]` as a fallback) are probed by text
    and reported as `kc:<target>` entries inside the same buckets.

    Also returns:
      - counts    — {filled, drafted, empty_but_drafted, empty_not_drafted}
      - score     — {filled_pct, drafted_pct, band: GREEN|AMBER|RED}
      - gaps      — per-field detail for fields that were drafted but empty,
                    including dom_id so each gap becomes a DOM mapping task

    The result is non-blocking: DOM probe errors degrade to "empty" with a
    WARNING log and never raise.
    """
    from post_filing_qa import score_qa_buckets
    from qa_fix_script import is_fixable_gap, record_gap

    discovery_url = page.url

    filled: List[str] = []
    empty_expected: List[str] = []
    empty_acceptable: List[str] = []
    gaps: List[Dict[str, Any]] = []
    field_states: Dict[str, Dict[str, Any]] = {}

    def _is_meaningful(value: Any) -> bool:
        return value not in (None, "", [], {}, ())

    def _expected_preview(value: Any, limit: int = 80) -> str:
        if value is None:
            return ""
        if isinstance(value, (list, tuple, set)):
            value = ", ".join(str(item) for item in value if str(item).strip())
        text = str(value).strip()
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _classify_kind(state: Dict[str, Any]) -> str:
        if state.get("tag") == "SELECT":
            return "dropdown"
        if state.get("type") == "checkbox":
            return "checkbox"
        if state.get("tag") == "TEXTAREA":
            return "textarea"
        return "text"

    for key, dom_id in field_map.items():
        try:
            state = await page.evaluate(_QA_READ_FIELD_JS, dom_id)
        except Exception as exc:
            logger.warning(f"QA[{form_type}]: error reading {key} (dom_id={dom_id}): {exc}")
            state = {"error": str(exc)}

        if not isinstance(state, dict):
            state = {"value": state}

        field_states[key] = state
        is_filled = False
        missing_dom = bool(state.get("missing"))
        if missing_dom:
            logger.warning(f"QA[{form_type}]: DOM element missing for {key} (dom_id={dom_id})")
        elif state.get("error"):
            pass
        elif state.get("tag") == "SELECT":
            is_filled = int(state.get("selectedIndex") or 0) > 0
        elif state.get("type") == "checkbox":
            is_filled = bool(state.get("checked"))
        else:
            is_filled = bool(str(state.get("value") or "").strip())

        expected_value = expected_fields.get(key)
        was_drafted = _is_meaningful(expected_value)

        if is_filled:
            filled.append(key)
        elif was_drafted:
            empty_expected.append(key)
            gap = {
                "field": key,
                "dom_id": dom_id,
                "form_type": form_type,
                "kind": _classify_kind(state),
                "missing_dom": missing_dom,
                "expected_preview": _expected_preview(expected_value),
                "reason": (
                    "dom_element_missing" if missing_dom
                    else "value_not_persisted"
                ),
            }
            gaps.append(gap)
            if is_fixable_gap(gap):
                record_gap(
                    form_type=form_type,
                    field_key=key,
                    gap_kind=gap["kind"],
                    reason=gap["reason"],
                    dom_id=dom_id,
                    discovery_url=discovery_url,
                    expected_preview=gap["expected_preview"],
                )
        else:
            empty_acceptable.append(key)

    kc_targets = (
        expected_fields.get("key_capabilities")
        or expected_fields.get("curriculum_links")
        or []
    )
    if isinstance(kc_targets, (str, bytes)):
        kc_targets = [kc_targets]
    for target in kc_targets:
        target_str = str(target)
        try:
            is_checked = await page.evaluate(_QA_READ_KC_JS, target_str)
        except Exception as exc:
            logger.warning(f"QA[{form_type}]: error reading KC {target_str}: {exc}")
            is_checked = False
        label = f"kc:{target_str}"
        if is_checked:
            filled.append(label)
        else:
            empty_expected.append(label)
            gap = {
                "field": label,
                "dom_id": None,
                "form_type": form_type,
                "kind": "kc_checkbox",
                "missing_dom": False,
                "expected_preview": target_str,
                "reason": "kc_not_ticked",
            }
            gaps.append(gap)
            if is_fixable_gap(gap):
                record_gap(
                    form_type=form_type,
                    field_key=label,
                    gap_kind="kc_checkbox",
                    reason="kc_not_ticked",
                    discovery_url=discovery_url,
                    expected_preview=target_str,
                )

    score = score_qa_buckets(
        filled=filled,
        empty_expected=empty_expected,
        empty_acceptable=empty_acceptable,
    )

    logger.info(f"QA[{form_type}] filled ({len(filled)}): {filled}")
    logger.info(f"QA[{form_type}] empty (expected, {len(empty_expected)}): {empty_expected}")
    logger.info(f"QA[{form_type}] empty (acceptable, {len(empty_acceptable)}): {empty_acceptable}")
    logger.info(
        f"QA[{form_type}] score: band={score['band']} "
        f"filled_pct={score['filled_pct']} drafted_pct={score['drafted_pct']}"
    )
    if gaps:
        logger.info(f"QA[{form_type}] gaps ({len(gaps)}): "
                    + ", ".join(f"{g['field']}({g['reason']})" for g in gaps))

    return {
        "filled": filled,
        "empty_expected": empty_expected,
        "empty_acceptable": empty_acceptable,
        "counts": {
            "filled": len(filled),
            "drafted": len(filled) + len(empty_expected),
            "empty_but_drafted": len(empty_expected),
            "empty_not_drafted": len(empty_acceptable),
        },
        "score": score,
        "gaps": gaps,
    }


# ─── Main entry point ────────────────────────────────────────────────────────

async def fill_kaizen_form(
    form_type: str,
    fields: dict,
    username: str,
    password: str,
    draft_uuid: str = None,
    save_as_draft: bool = True,
    screenshot_path: str = None,
) -> dict:
    """
    Fill a Kaizen form via CDP-connected Playwright.

    Returns:
        {
            "status": "success" | "partial" | "failed",
            "filled": [field_keys...],
            "skipped": [field_keys...],
            "errors": [error_strings...],
            "screenshot": path_or_None,
        }
    """
    filled = []
    skipped = []
    errors = []
    pw = None
    form_type = canonical_form_type(form_type)
    fields = normalise_fields_for_deterministic_filing(form_type, fields)

    try:
        # Connect to managed Chrome
        page, pw = await _connect_cdp()
        if not page:
            return {"status": "failed", "filled": [], "skipped": [], "errors": ["CDP connection failed"], "screenshot": None}

        # Check if we need to login
        current_url = page.url
        if "kaizenep.com" not in current_url:
            logged_in = await _login(page, username, password)
            if not logged_in:
                return {"status": "failed", "filled": [], "skipped": [], "errors": ["Login failed"], "screenshot": None}

        # Navigate to the form
        if draft_uuid:
            url = f"https://kaizenep.com/events/fillin/{draft_uuid}"
        else:
            form_uuid = FORM_UUIDS.get(form_type)
            if not form_uuid:
                return {"status": "failed", "filled": [], "skipped": [], "errors": [f"Unknown form type: {form_type}"], "screenshot": None}
            url = f"https://kaizenep.com/events/new-section/{form_uuid}"

        await page.goto(url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(4)  # Wait for Angular rendering

        # Get field map for this form type
        base_field_map = FORM_FIELD_MAP.get(form_type, {})
        if not base_field_map:
            return {"status": "failed", "filled": [], "skipped": [], "errors": [f"No field map for: {form_type}"], "screenshot": None}
        field_map = {**COMMON_HEADER_FIELD_MAP, **base_field_map}

        if form_type == "DOPS":
            from dops_filing import normalise_dops_fields
            fields = normalise_dops_fields(fields)
        fields, header_meta = apply_common_header_defaults(form_type, fields, field_map)
        fields = drop_consumed_unmapped_schema_fields(form_type, fields)

        if (
            form_type == "PROC_LOG"
            and _is_other_choice(fields.get("higher_procedural_skill"))
            and not (fields.get("higher_procedural_skill_other") or fields.get("procedure_other"))
        ):
            return {
                "status": "failed",
                "filled": [],
                "skipped": [],
                "errors": ["higher_procedural_skill is Other but higher_procedural_skill_other/procedure_other is missing"],
                "screenshot": None,
            }

        # ─── STEP 1: Stage of training (MUST be first — loads curriculum) ─────
        stage_value = fields.get("stage") or fields.get("stage_of_training")
        stage_dom_id = field_map.get("stage") or field_map.get("stage_of_training")
        if stage_value and stage_dom_id:
            ok = await _fill_stage(page, stage_dom_id, stage_value)
            if ok:
                filled.append("stage")
            else:
                errors.append("stage: fill failed")

        # ─── STEP 2: Date fields ─────────────────────────────────────────────
        date_keys = [k for k in fields if "date" in k.lower()]
        for key in date_keys:
            dom_id = field_map.get(key)
            if not dom_id:
                skipped.append(key)
                continue
            ok = await _fill_date(page, dom_id, fields[key])
            if ok:
                filled.append(key)
            else:
                errors.append(f"{key}: date fill failed")

        # ─── STEP 3: Text and select fields ──────────────────────────────────
        skip_keys = {"stage", "stage_of_training", "curriculum_links", "assessor_email", "assessor_query", "assessor_name"}
        skip_keys.update(date_keys)
        delayed_proc_log_other = {}
        if form_type == "PROC_LOG":
            for other_key in ("higher_procedural_skill_other", "procedure_other"):
                if fields.get(other_key):
                    delayed_proc_log_other[other_key] = fields[other_key]
                    skip_keys.add(other_key)

        for key, value in fields.items():
            if key in skip_keys or not value:
                continue

            dom_id = field_map.get(key)
            if not dom_id:
                skipped.append(key)
                continue

            # Detect field type
            tag = await page.evaluate(
                "(domId) => { var el = document.getElementById(domId); return el ? el.tagName : null; }",
                dom_id
            )

            if tag == "SELECT":
                if form_type == "DOPS":
                    value = await _normalise_dops_select_value(page, key, dom_id, value)
                ok = await _fill_select(page, dom_id, str(value))
            elif tag in ("TEXTAREA", "INPUT"):
                ok = await _fill_text(page, dom_id, str(value))
            elif tag == "DIV":
                # Some fields wrap textarea in a div
                ok = await _fill_text(page, dom_id, str(value))
            else:
                ok = await _fill_text(page, dom_id, str(value))

            if ok:
                filled.append(key)
            else:
                errors.append(f"{key}: fill failed (tag={tag})")

        for key, value in delayed_proc_log_other.items():
            dom_id = field_map.get(key)
            ok = await _fill_text(page, dom_id, str(value)) if dom_id else False
            if ok:
                filled.append(key)
            else:
                errors.append(f"{key}: fill failed")

        if (
            form_type == "PROC_LOG"
            and _is_other_choice(fields.get("higher_procedural_skill"))
            and not any(k in filled for k in ("higher_procedural_skill_other", "procedure_other"))
        ):
            errors.append("Other procedural skill detail was not filled")

        # ─── STEP 4: Curriculum links (SLO expansion + KC ticking) ───────────
        slo_codes = fields.get("curriculum_links", [])
        kc_targets = fields.get("key_capabilities", []) or slo_codes
        if slo_codes or kc_targets:
            ticked, kc_errors = await _fill_curriculum_links(
                page, slo_codes, kc_targets, stage_value or "Higher"
            )
            if ticked:
                filled.append(f"curriculum_links ({len(ticked)} KCs)")
            errors.extend(kc_errors)

        # ─── STEP 5: Assessor invite before live send ────────────────────────
        if not save_as_draft:
            assessor_query = fields.get("assessor_query") or fields.get("assessor_email")
            if assessor_query:
                assessor_ok = await _fill_assessor_invite(
                    page,
                    str(assessor_query),
                    str(fields.get("assessor_name") or ""),
                )
                if assessor_ok:
                    filled.append("assessor")
                else:
                    errors.append("assessor: selection failed")
            else:
                errors.append("assessor: missing assessor_query/assessor_email for live send")

        # ─── STEP 6: Verification pass ───────────────────────────────────────
        verify_issues = await _verify_fields(page, fields, field_map, filled)
        if verify_issues:
            for issue in verify_issues:
                logger.warning(f"Verify: {issue}")
            errors.extend(verify_issues)

        if not save_as_draft and any(error.startswith("assessor:") for error in errors):
            return {
                "status": "failed",
                "filled": filled,
                "skipped": skipped,
                "errors": errors + ["Live send blocked because assessor selection was not verified"],
                "screenshot": None,
            }

        # ─── STEP 7: Save ────────────────────────────────────────────────────
        saved = await _save_form(page, save_as_draft)
        if not saved:
            errors.append("Save may have failed")
        elif not save_as_draft:
            sent = await _verify_sent_to_assessor(
                page,
                str(fields.get("assessor_name") or fields.get("assessor_query") or fields.get("assessor_email") or ""),
            )
            if not sent:
                errors.append("assessor: live send was not verified after saving")

        # ─── STEP 8: Screenshot ──────────────────────────────────────────────
        if screenshot_path:
            try:
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"Screenshot saved: {screenshot_path}")
            except Exception as e:
                errors.append(f"Screenshot failed: {e}")
                screenshot_path = None

        # Determine status
        if not filled:
            status = "failed"
        elif errors:
            status = "partial"
        else:
            status = "success"

        return {
            "status": status,
            "filled": filled,
            "skipped": skipped,
            "errors": errors,
            "final_url": page.url,
            "screenshot": screenshot_path,
            **header_meta,
        }

    except Exception as e:
        logger.error(f"fill_kaizen_form failed: {e}", exc_info=True)
        return {
            "status": "failed",
            "filled": filled,
            "skipped": skipped,
            "errors": [str(e)],
            "screenshot": None,
        }
    finally:
        if pw:
            await pw.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# Legacy-compatible API — used by bot.py, filer_router.py, tests
# Ported from kaizen_filer.py (the old filer) to keep imports working.
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Form display names (as they appear in Kaizen's "Saved drafts" section) ──

FORM_DISPLAY_NAMES = {
    "CBD":           "CBD",
    "DOPS":          "DOPS",
    "LAT":           "LAT",
    "ACAT":          "ACAT",
    "ACAF":          "ACAF",
    "STAT":          "STAT",
    "MSF":           "MSF",
    "MINI_CEX":      "Mini-CEX",
    "JCF":           "JCF",
    "QIAT":          "QIAT",
    "TEACH":         "Teaching Observation",
    "PROC_LOG":      "Procedural Skills Log",
    "SDL":           "Self-Directed Learning",
    "US_CASE":       "Ultrasound Case",
    "ESLE_PART1_2":  "ESLE: Part 1 & 2 (2025 Update)",
    "ESLE_REFLECTION": "Reflection on ESLE (2025 Update)",
    "COMPLAINT":     "Complaint",
    "SERIOUS_INC":   "Serious Incident",
    "EDU_ACT":       "Educational Activity",
    "FORMAL_COURSE": "Formal Course",
    "REFLECT_LOG":   "Reflective Practice Log",
    "TEACH_OBS":          "Teaching Observation Tool",
    "TEACH_CONFID":       "Teach Confidentiality",
    "AUDIT":              "Audit",
    "RESEARCH":           "Research",
    "APPRAISAL":          "Appraisal",
    "PDP":                "Personal Development Plan",
    "BUSINESS_CASE":      "Business Case",
    "CLIN_GOV":           "Clinical Governance",
    "EDU_MEETING":        "Educational Meeting",
    "EDU_MEETING_SUPP":   "Educational Meeting Supplementary",
    "CRIT_INCIDENT":      "Critical Incident",
    "COST_IMPROVE":       "Cost Improvement Plan",
    "EQUIP_SERVICE":      "Introduction of Equipment",
    "MGMT_ROTA":          "Management: Rota",
    "MGMT_RISK":          "Management: Risk Register",
    "MGMT_RECRUIT":       "Management: Recruitment",
    "MGMT_PROJECT":       "Management: Project Record",
    "MGMT_RISK_PROC":     "Management: Procedure to Reduce Risk",
    "MGMT_TRAINING_EVT":  "Management: Organising a Training Event",
    "MGMT_GUIDELINE":     "Management: Introduction of Guideline",
    "MGMT_INFO":          "Management: Information Management",
    "MGMT_INDUCTION":     "Management: Induction Programme",
    "MGMT_EXPERIENCE":    "Management: Experience",
    "MGMT_REPORT":        "Management: Writing a Report",
    "MGMT_COMPLAINT":     "Management: Complaint",
}

for _alias, _target in FORM_TYPE_ALIASES.items():
    if _target in FORM_UUIDS:
        FORM_UUIDS.setdefault(_alias, FORM_UUIDS[_target])
    if _target in FORM_FIELD_MAP:
        FORM_FIELD_MAP.setdefault(_alias, FORM_FIELD_MAP[_target])
    if _target in FORM_DISPLAY_NAMES:
        FORM_DISPLAY_NAMES.setdefault(_alias, FORM_DISPLAY_NAMES[_target])


# ─── CDP connection (legacy — used by file_to_kaizen / delete_all_drafts) ────

async def connect_cdp_browser() -> tuple:
    """
    Connect to an existing managed browser via CDP.
    Returns (page, playwright_instance) or (None, None) on failure.
    The caller must NOT close the browser — it's shared.
    """
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(CDP_URL, no_defaults=True)

        # Look for an existing Kaizen page
        for context in browser.contexts:
            for page in context.pages:
                if "kaizenep.com" in page.url:
                    logger.info(f"CDP: reusing existing Kaizen page: {page.url}")
                    return page, pw

        # No Kaizen page found — open a new one in the first context
        if browser.contexts:
            page = await browser.contexts[0].new_page()
        else:
            ctx = await browser.new_context()
            page = await ctx.new_page()
        logger.info("CDP: opened new page in managed browser")
        return page, pw
    except Exception as e:
        logger.warning(f"CDP connection failed ({e}), falling back to headless")
        return None, None


async def _cdp_re_login(page: Page, username: str, password: str) -> bool:
    """Re-authenticate Kaizen inside the persistent CDP browser session.

    Used when the CDP-connected Chrome has a stale Kaizen session (common
    after prolonged inactivity). Navigates the existing CDP page to the
    RCEM SSO portal and fills the login form **inside the persistent
    profile**, so saved cookies/passwords remain available and the session
    is refreshed for future filing attempts.

    This is preferred over headless Playwright login because:
    - The persistent Chrome profile has saved cookies/passwords from prior
      manual logins
    - SSO portals sometimes block headless Chrome
    - MFA tokens cached in the profile can reduce re-authentication friction
    """
    try:
        await page.goto("https://eportfolio.rcem.ac.uk",
                        wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        login_input = page.locator('input[name="login"]')
        if await login_input.count() > 0:
            await login_input.fill(username)
            await page.locator('button[type="submit"]').click()
            await asyncio.sleep(3)

            pwd_input = page.locator('input[name="password"]')
            if await pwd_input.count() > 0:
                await pwd_input.fill(password)
                await page.locator('button[type="submit"]').click()

        await page.wait_for_url("**/kaizenep.com/**", timeout=45000)
        await asyncio.sleep(3)
        logger.info(f"CDP re-login succeeded: {page.url}")
        return True
    except Exception as e:
        logger.error(f"CDP re-login failed: {e}")
        return False


# ─── Legacy field filling (used by file_to_kaizen) ──────────────────────────

async def _fill_field_legacy(page: Page, dom_id: str, value: Any, field_key: str, form_type: str = "") -> bool:
    """Fill a single field by its DOM id. Returns True if filled."""
    if value is None or value == "" or value == []:
        return False

    try:
        if field_key == "stage_of_training":
            return await _fill_stage(page, dom_id, str(value))

        el = page.locator(f'[id="{dom_id}"]')
        if not await el.count():
            logger.warning(f"Field not found: [id=\"{dom_id}\"] ({field_key})")
            return False

        tag = await el.evaluate("el => el.tagName")

        # Date fields
        if dom_id == "startDate" or dom_id == "endDate" or "date" in field_key.lower():
            return await _fill_date(page, dom_id, str(value))

        # Select dropdowns
        if tag == "SELECT":
            if form_type == "DOPS" and field_key == "placement":
                value = await _normalise_dops_select_value(page, field_key, dom_id, value)
            return await _fill_select(page, dom_id, str(value))

        # Textareas and text inputs
        if tag in ("TEXTAREA", "INPUT"):
            clean_value = _strip_emojis(str(value))
            try:
                # Try normal click first exactly as originally written to preserve signature for mock tests
                await el.click()
            except Exception as e:
                logger.warning(f"Normal click on legacy field #{dom_id} ({field_key}) failed or timed out: {e}. Trying force=True.")
                try:
                    await el.click(force=True, timeout=2000)
                except Exception as force_e:
                    logger.warning(f"Forced click on legacy field #{dom_id} also failed: {force_e}. Attempting focus fallback.")
                    try:
                        await el.focus()
                    except Exception as focus_e:
                        logger.error(f"Focus fallback on legacy field #{dom_id} failed: {focus_e}")
            try:
                await el.fill(clean_value)
            except Exception as e:
                logger.warning(f"Legacy fill failed for #{dom_id} ({field_key}); using JS event fallback: {e}")
                ok = await page.evaluate(
                    """({domId, value}) => {
                        const el = document.getElementById(domId);
                        if (!el) return false;
                        el.focus();
                        el.value = value;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                        return true;
                    }""",
                    {"domId": dom_id, "value": clean_value},
                )
                if not ok:
                    return False
            return True

        return False

    except Exception as e:
        logger.warning(f"Error filling #{dom_id} ({field_key}): {e}")
        return False



# ─── Legacy submit/verify ────────────────────────────────────────────────────

async def _submit_entry(page: Page) -> bool:
    """Click Submit/Save (for self-contained log forms with no assessor)."""
    submit_selectors = [
        'a:has-text("Submit")',
        'button:has-text("Submit")',
        'a:has-text("Save")',
        'button:has-text("Save")',
    ]
    for selector in submit_selectors:
        try:
            el = page.locator(selector).first
            if await el.count() > 0:
                el_text = (await el.inner_text()).strip()
                if "send" in el_text.lower() and "submit" not in el_text.lower():
                    logger.warning(f"Skipping 'Send' element (use Submit): '{el_text}'")
                    continue
                await el.click()
                await asyncio.sleep(3)
                logger.info(f"Submitted entry: '{el_text}'")
                return True
        except Exception as e:
            logger.debug(f"Submit selector {selector} failed: {e}")
            continue
    logger.warning("No submit button found — falling back to _save_form()")
    return False


def _saved_draft_url(url: str) -> bool:
    """Return True when Kaizen has moved from new form creation to a saved draft URL."""
    if not url:
        return False
    return "/events/fillin/" in url or ("kaizenep.com/events/" in url and "doc=" in url)


def _activity_date_variants(fields: Optional[Dict[str, Any]] = None) -> List[str]:
    """Dates Kaizen may show in activities for a saved entry."""
    if not fields:
        fields = {}

    candidates = []
    for key in (
        "date_of_activity",
        "date_of_encounter",
        "date_of_event",
        "date_of_education",
        "date_of_teaching",
        "date_of_case",
        "date_of_complaint",
        "date_of_incident",
        "end_date",
    ):
        value = fields.get(key)
        if value:
            candidates.append(str(value))

    today = date.today()
    candidates.append(f"{today.day}/{today.month}/{today.year}")

    variants = []
    for raw in candidates:
        uk_date = _to_uk_date(raw)
        parts = uk_date.split("/")
        if len(parts) == 3:
            try:
                dt = date(int(parts[2]), int(parts[1]), int(parts[0]))
                variants.extend([
                    f"{dt.day}/{dt.month}/{dt.year}",
                    f"{dt.day:02d}/{dt.month:02d}/{dt.year}",
                    dt.strftime("%-d %b %Y"),
                    dt.strftime("%d %b %Y"),
                    dt.strftime("%-d %B %Y"),
                    dt.strftime("%d %B %Y"),
                ])
                continue
            except ValueError:
                pass
        variants.append(raw)
        variants.append(uk_date)

    return list(dict.fromkeys(v for v in variants if v))


async def _verify_entry_saved(page: Page, form_type: str, fields: Optional[Dict[str, Any]] = None) -> bool:
    """
    After saving, navigate to the activities list and confirm a new entry
    with the activity date (not necessarily today's date) AND the correct form
    type name exists.
    """
    form_type_keywords = {
        "CBD": ["case-based discussion", "case based discussion", "cbd"],
        "DOPS": ["dops", "direct observation"],
        "MINI_CEX": ["mini-cex", "mini cex", "minicex"],
        "ACAT": ["acat", "acute care assessment"],
        "LAT": ["lat", "leadership assessment"],
        "ACAF": ["acaf", "appraisal of clinical activity"],
        "STAT": ["stat", "structured assessment"],
        "MSF": ["msf", "multi-source feedback"],
        "QIAT": ["qiat", "quality improvement"],
        "JCF": ["jcf", "journal club"],
        "TEACH": ["teach", "teaching observation"],
        "PROC_LOG": ["proc", "procedural log", "procedural skills", "procedure log"],
        "SDL": ["sdl", "self-directed learning", "self directed"],
        "US_CASE": ["us case", "ultrasound"],
        "COMPLAINT": ["complaint"],
        "SERIOUS_INC": ["serious incident", "serious_inc"],
        "EDU_ACT": ["educational activity", "edu_act"],
        "FORMAL_COURSE": ["formal course"],
        "REFLECT_LOG": ["reflective practice", "reflective log", "reflect"],
        "TEACH_OBS": ["teaching observation"],
    }

    keywords = form_type_keywords.get(form_type, [form_type.lower().replace("_", " ")])
    expected_dates = _activity_date_variants(fields)

    if _saved_draft_url(page.url):
        logger.info(f"Post-save verification: saved draft URL detected ({page.url})")
        return True

    try:
        await page.goto("https://kaizenep.com/activities", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(4)

        body_text = await page.inner_text("body")

        matching_dates = [d for d in expected_dates if d in body_text]
        if not matching_dates:
            logger.warning(f"Post-save verification FAILED: expected date(s) {expected_dates} not found in activities list")
            return False

        for line in body_text.split("\n"):
            line_has_date = any(d in line for d in matching_dates)
            if not line_has_date:
                continue
            line_lower = line.lower()
            for kw in keywords:
                if kw in line_lower:
                    logger.info(f"Post-save verification: found '{kw}' with expected date in activities list")
                    return True

        for kw in keywords:
            if kw in body_text.lower():
                logger.info(f"Post-save verification: found form keyword '{kw}' on activities page with expected date (weak match)")
                return True

        logger.warning(f"Post-save verification FAILED: expected date found but no '{form_type}' entry detected")
        return False

    except Exception as e:
        logger.warning(f"Post-save verification error (inconclusive): {e}")
        return None  # type: ignore[return-value]


# ─── File attachment helpers ─────────────────────────────────────────────────

def _get_drive_filename(file_id: str) -> Optional[str]:
    """Fetch the original filename from Google Drive via gog CLI."""
    try:
        bws_cmd = (
            'BWS_ACCESS_TOKEN=$(cat ~/.openclaw/.bws-token) '
            '/Users/moeedahmed/.cargo/bin/bws secret get '
            'd79c847e-50e1-4b6b-9623-b3f70157cad8 --output json 2>/dev/null '
            "| python3 -c \"import sys,json; print(json.load(sys.stdin)['value'])\""
        )
        gog_cmd = (
            f'GOG_KEYRING_PASSWORD=$({bws_cmd}) '
            f'gog drive get {file_id} --account drmoeedahmed@gmail.com -j 2>/dev/null'
        )
        result = subprocess.run(
            gog_cmd, shell=True, capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            name = data.get("file", {}).get("name") or data.get("name")
            if name:
                logger.info(f"Drive filename for {file_id}: {name}")
                return name
    except Exception as e:
        logger.warning(f"Failed to fetch Drive filename for {file_id}: {e}")
    return None


def _download_drive_file(url: str) -> Optional[str]:
    """Download a Google Drive file to a temp dir, preserving original filename."""
    try:
        file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
        if file_id_match:
            file_id = file_id_match.group(1)
            direct_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        else:
            file_id = None
            direct_url = url

        # Resolve the original Drive filename
        drive_filename = None
        if file_id:
            drive_filename = _get_drive_filename(file_id)

        if not drive_filename:
            # Fallback: extract from URL path or use file_id
            url_path = url.rstrip("/").split("/")[-1]
            if "." in url_path and not url_path.startswith("d"):
                drive_filename = url_path
            elif file_id:
                drive_filename = f"file_{file_id}.pdf"
            else:
                drive_filename = "attachment.pdf"
            logger.warning(
                f"Could not fetch Drive filename for file_id={file_id}, "
                f"using fallback: {drive_filename}"
            )

        # Sanitise filename — remove path separators
        drive_filename = drive_filename.replace("/", "_").replace("\\", "_")

        tmpdir = tempfile.mkdtemp()
        dest_path = os.path.join(tmpdir, drive_filename)
        urllib.request.urlretrieve(direct_url, dest_path)
        logger.info(f"Downloaded Drive file to {dest_path}")

        assert os.path.basename(dest_path) == drive_filename, (
            f"Filename mismatch: expected {drive_filename}, got {os.path.basename(dest_path)}"
        )
        return dest_path
    except Exception as e:
        logger.warning(f"Failed to download Drive file: {e}")
        return None


async def _attach_file(page: Page, file_path: str) -> bool:
    """Attach a file using Kaizen's Upload button/file chooser flow."""
    try:
        if not os.path.isfile(file_path):
            logger.warning(f"Attachment file not found: {file_path}")
            return False

        upload_selectors = [
            'button[aria-label="Upload files for Attach files"]',
            'button.btn-info:has-text("Upload")',
            'button:has-text("Upload")',
        ]

        for selector in upload_selectors:
            upload_button = page.locator(selector).first
            try:
                if not await upload_button.is_visible(timeout=2000):
                    continue

                async with page.expect_file_chooser(timeout=5000) as chooser_info:
                    await upload_button.click()
                chooser = await chooser_info.value
                await chooser.set_files(file_path)
                await asyncio.sleep(2)

                filename = os.path.basename(file_path)
                verification_targets = [
                    page.get_by_text(filename, exact=False),
                    page.get_by_text(KAIZEN_FILE_UPLOAD["uploaded_status_text"], exact=False),
                    page.get_by_text("Remove", exact=False),
                    page.get_by_text("Replace", exact=False),
                ]
                for target in verification_targets:
                    try:
                        if await target.first.is_visible(timeout=5000):
                            logger.info(f"Attached file via upload button: {file_path}")
                            return True
                    except Exception:
                        continue

                logger.info(f"Attached file via upload button without visible confirmation: {file_path}")
                return True
            except Exception as e:
                logger.debug(f"Upload button selector failed ({selector}): {e}")
                continue

        # Legacy/static fallback for pages where the input is already present.
        file_input = page.locator('input[type="file"]')
        count = await file_input.count()
        if count > 0:
            await file_input.first.set_input_files(file_path)
            await asyncio.sleep(2)
            logger.info(f"Attached file: {file_path}")
            return True

        logger.warning("No file input element found on form")
        return False
    except Exception as e:
        logger.warning(f"File attachment failed: {e}")
        return False


# ─── Draft deduplication ─────────────────────────────────────────────────────

async def _find_existing_draft(page: Page, form_type: str) -> bool:
    """
    Navigate to the activities page and look for a saved draft matching this form type.
    If found, click into it so the page is now on the existing draft form.
    Returns True if an existing draft was found and opened, False otherwise.
    """
    display_name = FORM_DISPLAY_NAMES.get(form_type, form_type)
    logger.info(f"Looking for existing draft of type '{display_name}' ({form_type})")

    try:
        await page.goto("https://kaizenep.com/activities", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(4)

        drafts_header = page.locator("text=Saved drafts").first
        try:
            if await drafts_header.is_visible(timeout=3000):
                await drafts_header.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        for search_text in [display_name, form_type]:
            draft_link = page.locator(f"a:has-text('{search_text}')").first
            try:
                if await draft_link.is_visible(timeout=3000):
                    logger.info(f"Found existing draft matching '{search_text}' — clicking into it")
                    await draft_link.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=30000)
                    await asyncio.sleep(5)
                    logger.info(f"Opened existing draft at: {page.url}")
                    return True
            except Exception:
                continue

        for selector in ["tr:has-text('draft')", "[class*='draft']", "[class*='saved']"]:
            try:
                rows = page.locator(selector)
                count = await rows.count()
                for i in range(count):
                    row = rows.nth(i)
                    row_text = await row.inner_text()
                    if display_name.lower() in row_text.lower() or form_type.lower() in row_text.lower():
                        link = row.locator("a").first
                        if await link.is_visible(timeout=2000):
                            logger.info(f"Found draft in '{selector}' row: {row_text[:80]}")
                            await link.click()
                            await page.wait_for_load_state("domcontentloaded", timeout=30000)
                            await asyncio.sleep(5)
                            logger.info(f"Opened existing draft at: {page.url}")
                            return True
            except Exception:
                continue

        logger.info(f"No existing draft found for '{display_name}' — will create new")
        return False

    except Exception as e:
        logger.warning(f"Draft search failed ({e}) — will create new form")
        return False


# ─── Bulk draft deletion ─────────────────────────────────────────────────────

async def delete_all_drafts_of_type(
    form_type: str,
    username: str,
    password: str,
    max_deletions: int = 100,
) -> Dict[str, Any]:
    """
    Utility to delete all saved drafts of a given form type.
    Use to clean up duplicate drafts.
    """
    display_name = FORM_DISPLAY_NAMES.get(form_type, form_type)
    deleted = 0
    errors = 0
    browser = None
    cdp_pw = None
    use_cdp = KAIZEN_USE_CDP

    try:
        page = None
        if use_cdp:
            page, cdp_pw = await connect_cdp_browser()
            if page is None:
                use_cdp = False

        if not use_cdp:
            pw = await async_playwright().start()
            cdp_pw = pw
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()

        if use_cdp and "kaizenep.com" in page.url:
            logger.info("CDP: already logged in, skipping login")
        else:
            if not await _login(page, username, password):
                return {"deleted": 0, "errors": 0, "error": "Login failed"}

        for iteration in range(max_deletions):
            await page.goto("https://kaizenep.com/activities", wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(4)

            drafts_header = page.locator("text=Saved drafts").first
            try:
                if await drafts_header.is_visible(timeout=3000):
                    await drafts_header.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            found = False
            for search_text in [display_name, form_type]:
                draft_link = page.locator(f"a:has-text('{search_text}')").first
                try:
                    if await draft_link.is_visible(timeout=3000):
                        await draft_link.click()
                        await page.wait_for_load_state("domcontentloaded", timeout=30000)
                        await asyncio.sleep(3)
                        found = True
                        break
                except Exception:
                    continue

            if not found:
                logger.info(f"No more drafts of type '{display_name}' to delete — done after {deleted} deletions")
                break

            for btn_text in ["Delete", "Discard", "Remove", "Delete draft", "Discard draft"]:
                delete_btn = page.locator(f"button:has-text('{btn_text}')").first
                try:
                    if await delete_btn.is_visible(timeout=2000):
                        await delete_btn.click()
                        await asyncio.sleep(1)
                        for confirm_text in ["Confirm", "Yes", "OK", "Delete"]:
                            confirm_btn = page.locator(f"button:has-text('{confirm_text}')").first
                            try:
                                if await confirm_btn.is_visible(timeout=2000):
                                    await confirm_btn.click()
                                    await asyncio.sleep(2)
                                    break
                            except Exception:
                                continue
                        deleted += 1
                        logger.info(f"Deleted draft #{deleted} of type '{display_name}'")
                        break
                except Exception:
                    continue
            else:
                logger.warning(f"Could not find delete button on draft form — skipping")
                errors += 1
                if errors >= 3:
                    break

        return {"deleted": deleted, "errors": errors, "error": None}

    except Exception as e:
        logger.error(f"Draft deletion error: {e}", exc_info=True)
        return {"deleted": deleted, "errors": errors, "error": str(e)}
    finally:
        if browser and not use_cdp:
            try:
                await browser.close()
            except Exception:
                pass
        if cdp_pw:
            try:
                await cdp_pw.stop()
            except Exception:
                pass


# ─── Legacy main entry point (file_to_kaizen) ───────────────────────────────

async def file_to_kaizen(
    form_type: str,
    fields: Dict[str, Any],
    username: str,
    password: str,
    curriculum_links: Optional[List[str]] = None,
    submit: bool = False,
    attachment_path: Optional[str] = None,
    attachment_drive_url: Optional[str] = None,
    reuse_draft: bool = False,
) -> Dict[str, Any]:
    """
    File a form to Kaizen as a draft (legacy API).

    Used by filer_router.py and bot.py. Wraps the old filer logic
    for backward compatibility.
    """
    form_type = canonical_form_type(form_type)
    uuid = FORM_UUIDS.get(form_type)
    if not uuid:
        return {"status": "failed", "filled": [], "skipped": [], "error": f"Unknown form type: {form_type}"}

    base_field_map = FORM_FIELD_MAP.get(form_type, {})
    if not base_field_map:
        all_field_keys = list(fields.keys())
        return {"status": "partial", "filled": [], "skipped": all_field_keys, "error": f"No field mapping for {form_type} — needs browser-use"}
    field_map = {**COMMON_HEADER_FIELD_MAP, **base_field_map}

    filled = []
    skipped = []
    fields = normalise_fields_for_deterministic_filing(form_type, fields)
    if form_type == "PROC_LOG" and fields.get("date_of_activity") and not fields.get("date_occurred_on"):
        fields = dict(fields)
        fields["date_occurred_on"] = fields["date_of_activity"]
    normaliser = FORM_FIELD_NORMALISERS.get(form_type)
    if normaliser:
        fields = normaliser(fields)
    fields, header_meta = apply_common_header_defaults(form_type, fields, field_map)
    fields = drop_consumed_unmapped_schema_fields(form_type, fields)
    browser = None
    cdp_pw = None
    use_cdp = KAIZEN_USE_CDP

    try:
        page = None

        if use_cdp:
            page, cdp_pw = await connect_cdp_browser()
            if page is None:
                use_cdp = False

        if not use_cdp:
            pw = await async_playwright().start()
            cdp_pw = pw
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()

        if use_cdp and "kaizenep.com" in page.url:
            logger.info("CDP: already logged in, skipping login")
        elif use_cdp:
            # CDP browser connected but page isn't on Kaizen — session may have
            # expired. Try re-login inside the persistent CDP browser first.
            if not await _cdp_re_login(page, username, password):
                logger.warning("CDP re-login failed; falling back to headless Playwright")
                # Fall through to the headless path below
                if not await _login(page, username, password):
                    return {
                        "status": "failed", "filled": [], "skipped": [],
                        "error": (
                            "Your Kaizen session expired and we couldn't re-authenticate. "
                            "Use /settings to reconnect your credentials."
                        ),
                    }
        else:
            if not await _login(page, username, password):
                return {"status": "failed", "filled": [], "skipped": [], "error": "Login failed"}

        # Navigate to a new form by default. Reusing same-form drafts can
        # overwrite repeated ARCP tickets that legitimately share form type/date.
        reused_draft = False
        if reuse_draft:
            reused_draft = await _find_existing_draft(page, form_type)

        if not reused_draft:
            form_url = f"https://kaizenep.com/events/new-section/{uuid}"
            await page.goto(form_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(5)

            if "new-section" not in page.url:
                return {"status": "failed", "filled": [], "skipped": [],
                        "error": f"Form page didn't load — redirected to {page.url}"}

        # Fill stage_of_training FIRST
        if "stage_of_training" in field_map:
            st_dom = field_map["stage_of_training"]
            st_val = fields.get("stage_of_training", "Higher")
            if await _fill_field_legacy(page, st_dom, st_val, "stage_of_training", form_type):
                filled.append("stage_of_training")
            else:
                skipped.append("stage_of_training")

        # Fill remaining mapped fields
        for field_key, dom_id in field_map.items():
            if field_key == "stage_of_training":
                continue
            if field_key not in fields:
                continue
            value = fields.get(field_key)
            if value is None or value == "" or value == []:
                skipped.append(field_key)
                continue

            success = await _fill_field_legacy(page, dom_id, value, field_key, form_type)
            if success:
                filled.append(field_key)
            else:
                skipped.append(field_key)

        # Default procedural skills dropdowns to n/a for non-procedural forms
        _proc_dropdowns = {
            "eed0e8dc-075d-4661-aea5-2c3238af4c5b": "ACCS Procedural Skills",
            "31bd55b7-0e32-4918-8cc0-4ba33af83772": "Intermediate Procedural Skills",
            "8def931e-3a00-43ac-8529-44cdaf34be2d": "Higher EM Procedural Skills",
        }
        for dd_id, dd_label in _proc_dropdowns.items():
            try:
                sel = page.locator(f"select#{dd_id}")
                if await sel.count() > 0 and await sel.input_value() == "":
                    # No selection made — default to n/a
                    await sel.select_option(label="- n/a -")
                    logger.info(f"Defaulted {dd_label} to n/a")
            except Exception:
                pass

        # Curriculum links
        if curriculum_links:
            slo_codes = curriculum_links
            kc_targets = fields.get("key_capabilities", []) or curriculum_links
            ticked, kc_errors = await _fill_curriculum_links(
                page, slo_codes, kc_targets, fields.get("stage_of_training", "Higher")
            )

        # Handle file attachment
        temp_attachment = None
        if attachment_drive_url and not attachment_path:
            temp_attachment = _download_drive_file(attachment_drive_url)
            if temp_attachment:
                attachment_path = temp_attachment

        if attachment_path:
            if await _attach_file(page, attachment_path):
                filled.append("attachment")
            else:
                skipped.append("attachment")

        if temp_attachment:
            try:
                shutil.rmtree(os.path.dirname(temp_attachment), ignore_errors=True)
            except OSError:
                pass

        # Save or submit
        if submit:
            saved = await _submit_entry(page)
            if not saved:
                saved = await _save_form(page, True)
        else:
            saved = await _save_form(page, True)

        saved_url = page.url if saved and not submit else None

        # Post-save verification
        verified = None
        if saved and len(filled) > 0 and not submit:
            verified = await _verify_entry_saved(page, form_type, fields)

        # Post-filing QA — non-blocking, monitoring only.
        filing_qa: Optional[Dict[str, List[str]]] = None
        if saved and not submit:
            try:
                filing_qa = await _verify_filing_qa(page, form_type, fields, field_map)
            except Exception as qa_exc:
                logger.warning(f"Post-filing QA error (non-blocking) for {form_type}: {qa_exc}")
                filing_qa = None

        # Determine status
        if not saved:
            status = "failed"
            save_error = "Save button not found or click failed"
            if len(filled) > 0:
                save_error += f" ({len(filled)} fields were filled but draft was NOT saved)"
        elif verified is False:
            status = "partial"
            save_error = "Save was clicked, but I could not confirm the entry in the activities list. Please check Kaizen manually."
        elif len(filled) > 0:
            if len(skipped) == 0:
                status = "success"
            else:
                status = "partial"
            save_error = None
        else:
            status = "failed"
            save_error = "No fields were filled"

        return {
            "status": status,
            "filled": filled,
            "skipped": skipped,
            "error": save_error,
            "saved_url": saved_url,
            "filing_qa": filing_qa,
            **header_meta,
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
        if browser and not use_cdp:
            try:
                await browser.close()
            except Exception:
                pass
        if cdp_pw:
            try:
                await cdp_pw.stop()
            except Exception:
                pass


# ─── Test Draft Detection and Deletion ───────────────────────────────────────

TEST_KEYWORDS = [
    "test", "testing", "[TEST]", "sample", "dummy", 
    "placeholder", "mock", "fake", "trial", "demo"
]

async def detect_and_delete_test_drafts(
    username: str,
    password: str,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Detect and delete test drafts from Kaizen activities page.
    
    Scans all saved drafts, opens each one, checks case narrative and
    reflection fields for test keywords, flags matches for deletion.
    
    Args:
        username: Kaizen username
        password: Kaizen password
        dry_run: If True, only report what would be deleted (default: True)
    
    Returns:
        Dict with "found", "deleted", "errors", and "details" keys
    """
    found_drafts = []
    deleted = 0
    errors = 0
    browser = None
    cdp_pw = None
    use_cdp = KAIZEN_USE_CDP
    
    try:
        page = None
        if use_cdp:
            page, cdp_pw = await connect_cdp_browser()
            if page is None:
                use_cdp = False
        
        if not use_cdp:
            pw = await async_playwright().start()
            cdp_pw = pw
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
        
        if use_cdp and "kaizenep.com" in page.url:
            logger.info("CDP: already logged in, skipping login")
        else:
            if not await _login(page, username, password):
                return {"found": 0, "deleted": 0, "errors": 1, "details": ["Login failed"]}
        
        # Navigate to activities page
        await page.goto("https://kaizenep.com/activities", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(4)
        
        # Expand Saved drafts section
        drafts_header = page.locator("text=Saved drafts").first
        try:
            if await drafts_header.is_visible(timeout=3000):
                await drafts_header.click()
                await asyncio.sleep(1)
        except Exception:
            pass
        
        # Find all draft edit links
        draft_links = await page.locator('a[href*="/events/edit/"], a[href*="/events/fillin/"]').all()
        draft_info = []
        
        for link in draft_links:
            try:
                href = await link.get_attribute('href')
                title = await link.inner_text()
                if href:
                    draft_info.append({
                        "title": title.strip(),
                        "url": f"https://kaizenep.com{href}" if href.startswith("/") else href,
                        "href": href
                    })
            except Exception:
                continue
        
        logger.info(f"Found {len(draft_info)} total drafts to scan")
        
        # Scan each draft for test keywords
        for draft in draft_info:
            try:
                await page.goto(draft["url"], wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)
                
                content_text = ""
                
                # Try to find case narrative field
                narrative_selectors = [
                    'textarea[name*="case" i]',
                    'textarea[name*="narrative" i]',
                    'textarea[ng-model*="case" i]',
                    'textarea[ng-model*="narrative" i]',
                    'textarea[id*="case" i]',
                ]
                
                for sel in narrative_selectors:
                    try:
                        field = page.locator(sel).first
                        if await field.is_visible(timeout=1000):
                            content_text += " " + await field.input_value()
                    except Exception:
                        continue
                
                # Try to find reflection field
                reflection_selectors = [
                    'textarea[name*="reflection" i]',
                    'textarea[name*="reflect" i]',
                    'textarea[ng-model*="reflection" i]',
                    'textarea[id*="reflection" i]',
                ]
                
                for sel in reflection_selectors:
                    try:
                        field = page.locator(sel).first
                        if await field.is_visible(timeout=1000):
                            content_text += " " + await field.input_value()
                    except Exception:
                        continue
                
                # Check for test keywords (case insensitive)
                content_lower = content_text.lower()
                matched_keywords = [kw for kw in TEST_KEYWORDS if kw.lower() in content_lower]
                
                if matched_keywords:
                    found_drafts.append({
                        "title": draft["title"],
                        "url": draft["url"],
                        "keywords": matched_keywords
                    })
                    logger.info(f"Flagged test draft: {draft['title']} (matched: {matched_keywords})")
                    
                    if not dry_run:
                        # Find and click delete button
                        delete_btn = page.locator('button:has-text("Delete"), a:has-text("Delete"), button[title*="delete" i]').first
                        try:
                            if await delete_btn.is_visible(timeout=3000):
                                await delete_btn.click()
                                await asyncio.sleep(1)
                                
                                # Confirm deletion if prompted
                                confirm_btn = page.locator('button:has-text("Yes"), button:has-text("Confirm"), button:has-text("Delete").first').first
                                try:
                                    if await confirm_btn.is_visible(timeout=2000):
                                        await confirm_btn.click()
                                        await asyncio.sleep(1)
                                except Exception:
                                    pass
                                
                                deleted += 1
                                logger.info(f"Deleted: {draft['title']}")
                        except Exception as e:
                            logger.error(f"Failed to delete {draft['title']}: {e}")
                            errors += 1
                            
            except Exception as e:
                logger.error(f"Error scanning draft {draft.get('title', 'unknown')}: {e}")
                errors += 1
                continue
        
        return {
            "found": len(found_drafts),
            "deleted": deleted,
            "errors": errors,
            "details": found_drafts,
            "dry_run": dry_run
        }
        
    except Exception as e:
        logger.error(f"detect_and_delete_test_drafts error: {e}", exc_info=True)
        return {
            "found": len(found_drafts),
            "deleted": deleted,
            "errors": errors + 1,
            "details": found_drafts,
            "error": str(e)
        }
    finally:
        if browser and not use_cdp:
            try:
                await browser.close()
            except Exception:
                pass
        if cdp_pw:
            try:
                await cdp_pw.stop()
            except Exception:
                pass
