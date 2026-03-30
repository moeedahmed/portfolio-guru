"""
Generic Kaizen form filer — deterministic Playwright.
Handles 20+ RCEM 2025 Update forms.

Usage:
    result = await file_to_kaizen(form_type, fields, username, password)
    # result: {"status": "success"|"partial"|"failed", "filled": [...], "skipped": [...], "error": str|None}
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import re
import tempfile
import urllib.request

from playwright.async_api import async_playwright, Page, Browser, BrowserContext

logger = logging.getLogger(__name__)

KAIZEN_USE_CDP = os.environ.get("KAIZEN_USE_CDP", "").lower() in ("1", "true", "yes")
CDP_URL = os.environ.get("KAIZEN_CDP_URL", "http://localhost:18800")


async def connect_cdp_browser() -> tuple[Page | None, any]:
    """
    Connect to an existing managed browser via CDP.
    Returns (page, playwright_instance) or (None, None) on failure.
    The caller must NOT close the browser — it's shared.
    """
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(CDP_URL)

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

# Emoji stripping — portfolio entries must NEVER contain emojis
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001f926-\U0001f937"
    "\U00010000-\U0010ffff"
    "\u2640-\u2642"
    "\u2600-\u2B55"
    "\u200d"
    "\u23cf"
    "\u23e9"
    "\u231a"
    "\ufe0f"
    "\u3030"
    "]+",
    flags=re.UNICODE,
)


def _strip_emojis(text: str) -> str:
    """Remove all emojis from text before filing to Kaizen."""
    return _EMOJI_RE.sub("", text).strip()

# ─── Kaizen Select Values (discovered from live DOM) ────────────────────────

STAGE_SELECT_VALUES = {
    "ACCS":         "string:39b9fe64-b1e7-4726-81e2-73aaead0ee95",
    "Intermediate": "string:0669c338-e695-40f9-8fae-aee2ee7d68e1",
    "Higher":       "string:3815019a-e2be-4824-a4fb-555b55ffeab2",
    "PEM":          "string:fc7caa86-b83c-48d0-9b86-0fb73617d2b5",
}

# SLO checkbox Angular node IDs (all 2025 Update forms use SLO-level, no KC sub-tree)
SLO_CHECKBOX_IDS = {
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

# ─── Form UUIDs (2025 Update) ───────────────────────────────────────────────

FORM_UUIDS = {
    # ─── 2025 Update versions (preferred) ─────────────────────────────────
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
    "ESLE_ASSESS":   "4a6f3a91-10ed-45d0-bb82-3e87ae2d6d04",  # ESLE: Part 1 & 2 (2025 Update - v3)
    "COMPLAINT":     "f7c0ba98-5a47-4e37-b76a-ca3c5c8484cc",
    "SERIOUS_INC":   "9d4a7912-a615-4ae4-9fae-6be966bcf254",
    "EDU_ACT":       "868dc0e7-f4e9-4283-ac52-d9c8b246024b",
    "FORMAL_COURSE": "c7cd9a95-e2aa-4f61-a441-b663f3c933c6",
    "REFLECT_LOG":   "32d0fcb9-05d0-4d6d-b877-ebd5daf0b4e9",  # Reflective Practice Log (2025 Update)
    "TEACH_OBS":     "30668ad8-e1db-4a27-bb2d-3e395e6acfcf",  # Teaching observation tool (2025 Update)
    # ─── Management section (2021/2025 shared) ────────────────────────────
    "MGMT_ROTA":          "ffc650a7-309d-42e0-8886-21521114bfb2",
    "MGMT_RISK":          "4a349b8d-6f9f-478f-b623-4f083d6ce87b",
    "MGMT_RECRUIT":       "2a2c04a5-388a-4b38-ad74-06bacfd39594",
    "MGMT_PROJECT":       "6b5f60e2-0237-4429-9870-a2bd8cceeb97",
    "MGMT_RISK_PROC":     "957ab9dc-de1e-4b87-b38f-9bd4f54cb9a1",
    "MGMT_TRAINING_EVT":  "2cd1ddb3-7d33-45dd-9269-c09209568391",
    "MGMT_GUIDELINE":     "8121d957-ed22-4799-b9fa-d3eb52c9a37a",
    "MGMT_INFO":          "9d396397-94bc-4905-b27b-547c938868de",
    "MGMT_INDUCTION":     "fb37ecae-334a-40e2-aa6e-043a24952283",
    "MGMT_EXPERIENCE":    "73805ea3-ee61-4a59-a57d-d89aca660309",
    "MGMT_REPORT":        "0131f31d-a78c-41cb-8147-15fc1e2c42df",
    "TEACH_CONFID":       "f614bdcc-5d31-4b5b-b980-1e073e2431db",  # Teach Confidentiality (2025 Update)
    "APPRAISAL":          "099be248-10de-4241-99ec-970d947963ae",
    "BUSINESS_CASE":      "8a720578-cee6-4e19-b9ff-fb0f95a3019c",
    "CLIN_GOV":           "d5a56390-d229-41f6-b67f-3231a3390f75",
    "MGMT_COMPLAINT":     "89217cd1-cfae-4006-b35e-221c46f5a645",
    "COST_IMPROVE":       "1cc77669-859f-4d2a-9588-f3d0de69f40f",
    "CRIT_INCIDENT":      "b6445c81-388b-4f48-b510-b080b406b74e",
    "EQUIP_SERVICE":      "ec09e28d-86f3-4bdc-8547-ef3ab0a5388e",
    # ─── Research, Audit & QI ─────────────────────────────────────────────
    "AUDIT":              "33c454df-eb86-49f1-8ec0-ee2ccbe8c574",
    "RESEARCH":           "3d4c6a82-f7ab-4b11-bb36-c7487de4ff2d",
    # ─── Educational Review & Meetings ────────────────────────────────────
    "EDU_MEETING":        "cf3c4b40-12e6-46ca-b7a7-4914bf792f6b",
    "EDU_MEETING_SUPP":   "35e1bd6b-4de3-441b-82f7-ef236a8f7a7c",
    "PDP":                "c2b716dd-2d2a-462e-8df0-70760673448c",
    # ─── Training Post & Supervisor ───────────────────────────────────────
    "ADD_POST":           "c8049d8b-11f7-4bad-ac6c-c0b3c9ded1bb",
    "ADD_SUPERVISOR":     "87205ea8-ee22-4555-8e30-3a5ffc8b0bd2",
    # ─── Progression ─────────────────────────────────────────────────────
    "HIGHER_PROG":        "c19ca7c4-54ba-4816-b292-8bce1af4a62f",
    # ─── Other ────────────────────────────────────────────────────────────
    "ABSENCE":            "9feb8df3-1c70-4237-bf77-c6520e43c9c2",
    "CCT":                "9425aea9-1fb9-4230-b2a3-ec1712599caa",
    "FILE_UPLOAD":        "108ae04a-d865-4a4a-ba97-9c537563e960",
    "OOP":                "2b023326-a34f-463e-a921-bf215599b0ac",
}


# ─── Field → DOM ID mapping ─────────────────────────────────────────────────
# Maps schema field keys to Kaizen DOM element IDs.
# Keys MUST match form_schemas.py field keys exactly.



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
        # TODO: reflection UUID needs discovery via CDP on live QIAT form
        # Field label: "4.2 Reflections and Learning"
        # "reflection": "<UUID_TO_DISCOVER>",
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
    # ─── Discovered 2026-03-21 via Playwright DOM inspection ────────────────
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
    "ESLE_ASSESS": {
        "date_of_encounter": "startDate",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "date_of_esle": "2c86886b-0a18-4771-9b25-6c2272fdad6b",
        "reflection": "488e8e63-300d-4ed9-a4f4-eaee53608f05",
    },
    "TEACH_CONFID": {
        "date_of_encounter": "startDate",
        "stage_of_training": "e0864e88-62cf-43aa-a9e5-51abd98a1cce",
        "project_description": "b647ee6e-9bb7-4b86-bfdd-45aa0254211c",
        "reflection": "a7d84918-a496-484a-9d77-d0425646d29f",
        "resources_used": "b588a189-cc03-4f04-abda-be1a4190ec68",
        "lessons_learned": "d5cc387b-165c-4b7f-8679-5d7597b00beb",
    },
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
    "EDU_MEETING": {
        "date_of_encounter": "startDate",
        "meeting_date": "ab023afe-255d-4b50-b1f4-b379e960d7c2",
        "meeting_type": "e4836763-cb73-4520-81b6-678666303d53",
        "reflection": "dbfb3151-671d-4c24-9b7e-cafba9df088b",
    },
    "EDU_MEETING_SUPP": {
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
    """Log in to Kaizen via RCEM portal. Two-step: username → password."""
    try:
        await page.goto("https://eportfolio.rcem.ac.uk", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        # Step 1: Username
        login_input = page.locator('input[name="login"]')
        if await login_input.count() > 0:
            await login_input.fill(username)
            await page.locator('button[type="submit"]').click()
            await asyncio.sleep(2)

        # Step 2: Password (may be on same or separate page)
        pwd_input = page.locator('input[name="password"]')
        if await pwd_input.count() > 0:
            await pwd_input.fill(password)
            await page.locator('button[type="submit"]').click()
        else:
            # Try single-page login
            await page.fill('input[name="password"]', password)
            await page.click('button[type="submit"]')

        await page.wait_for_url("**/kaizenep.com/**", timeout=30000)
        await asyncio.sleep(3)
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
        # Special handling for stage_of_training — use known select values
        if field_key == "stage_of_training":
            return await _fill_stage_of_training(page, dom_id, value)

        # Use attribute selector [id="..."] instead of #id — UUID-style IDs
        # starting with digits are invalid CSS selectors with # prefix
        el = page.locator(f'[id="{dom_id}"]')
        if not await el.count():
            logger.warning(f"Field not found: [id=\"{dom_id}\"] ({field_key})")
            return False

        tag = await el.evaluate("el => el.tagName")

        # Date fields (d/m/yyyy)
        if dom_id == "startDate" or dom_id == "endDate" or "date" in field_key.lower():
            uk_date = _to_uk_date(str(value))
            if uk_date:
                await el.click()
                await el.fill("")
                await el.type(uk_date, delay=50)
                await el.press("Tab")
                await asyncio.sleep(0.3)
                return True
            return False

        # Select dropdowns
        if tag == "SELECT":
            return await _fill_select(el, dom_id, value, field_key)

        # Textareas and text inputs — strip emojis before filing
        if tag in ("TEXTAREA", "INPUT"):
            clean_value = _strip_emojis(str(value))
            await el.click()
            await el.fill(clean_value)
            return True

        return False

    except Exception as e:
        logger.warning(f"Error filling #{dom_id} ({field_key}): {e}")
        return False


async def _fill_stage_of_training(page: Page, dom_id: str, value: Any) -> bool:
    """Fill the stage of training select using known Kaizen values."""
    val_str = str(value).lower()
    select_value = None

    # Map common AI-generated values to Kaizen select values
    if "higher" in val_str or "st4" in val_str or "st5" in val_str or "st6" in val_str:
        select_value = STAGE_SELECT_VALUES["Higher"]
    elif "intermediate" in val_str or "st3" in val_str:
        select_value = STAGE_SELECT_VALUES["Intermediate"]
    elif "accs" in val_str or "st1" in val_str or "st2" in val_str or "ct1" in val_str or "ct2" in val_str:
        select_value = STAGE_SELECT_VALUES["ACCS"]
    elif "pem" in val_str:
        select_value = STAGE_SELECT_VALUES["PEM"]
    else:
        # Default to Higher (most common for Moeed = ST5)
        select_value = STAGE_SELECT_VALUES["Higher"]
        logger.info(f"Defaulting stage to Higher for value: '{value}'")

    try:
        el = page.locator(f'[id="{dom_id}"]')
        if await el.count() > 0:
            await el.select_option(value=select_value)
            await asyncio.sleep(5)  # Wait for curriculum section to load after stage selection
            logger.info(f"Selected stage of training: {select_value}")
            return True
    except Exception as e:
        logger.warning(f"Stage selection failed: {e}")
    return False


async def _fill_select(el, dom_id: str, value: Any, field_key: str) -> bool:
    """Fill a generic select dropdown with label or partial match."""
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


async def _expand_curriculum_section(page: Page) -> None:
    """Expand the curriculum accordion/section so SLO checkboxes become visible.

    Non-fatal — if no expand control is found, continues silently
    (some forms may already have it expanded).
    """
    try:
        # Look for curriculum section header / expand toggle
        expand_candidates = [
            page.get_by_text("Curriculum", exact=False).locator("xpath=ancestor-or-self::*[contains(@class,'accordion') or contains(@class,'panel') or contains(@class,'collapse') or contains(@class,'expand')]"),
            page.locator('[class*="curriculum"] [class*="expand"], [class*="curriculum"] [class*="toggle"]'),
            page.locator('[class*="accordion"] >> text=Curriculum'),
            page.get_by_text("Should you want to link to the curriculum"),
            page.get_by_text("Curriculum LO/SLO"),
        ]

        for candidate in expand_candidates:
            try:
                if await candidate.count() > 0:
                    first = candidate.first
                    # Click the element or its parent accordion header
                    await first.click(timeout=3000)
                    logger.info("Curriculum section: clicked expand control")
                    await asyncio.sleep(2)  # Let Angular render the SLO checkboxes
                    return
            except Exception:
                continue

        # Fallback: try clicking any collapsed accordion panel that might contain curriculum
        accordion_headers = page.locator('[class*="accordion-toggle"], [class*="panel-heading"], [class*="section-header"]')
        count = await accordion_headers.count()
        for i in range(count):
            header = accordion_headers.nth(i)
            try:
                text = (await header.inner_text()).strip().lower()
                if "curriculum" in text or "slo" in text or "learning outcome" in text:
                    await header.click(timeout=3000)
                    logger.info(f"Curriculum section: expanded accordion header: '{text}'")
                    await asyncio.sleep(2)
                    return
            except Exception:
                continue

        logger.info("Curriculum section: no expand control found (may already be expanded)")
    except Exception as e:
        logger.warning(f"Curriculum expand step failed (non-fatal): {e}")


async def _tick_kc_leaves(page: Page, wanted_kcs: Dict[str, List[int]]) -> int:
    """Expand SLO sections and tick specific KC leaf checkboxes.

    For each SLO with wanted KCs:
      1. Find the SLO node via Angular scope and expand it
      2. Find child KC checkboxes by label text matching KC{n}
      3. Tick the specific KCs

    Falls back to SLO-level tick if expansion or KC finding fails.
    """
    total = 0
    fallback_slos = []

    for slo_key, kc_nums in wanted_kcs.items():
        slo_node_id = SLO_CHECKBOX_IDS.get(slo_key)
        if not slo_node_id:
            logger.warning(f"KC tick: unknown SLO '{slo_key}', skipping")
            continue

        # Step 1: Expand the SLO to reveal KC sub-checkboxes
        expand_result = await page.evaluate("""(args) => {
            const sloNodeId = args.sloNodeId;
            const cbs = document.querySelectorAll('input[type="checkbox"]');
            for (const cb of cbs) {
                if (cb.id === 'filledOnSameDevice') continue;
                try {
                    const scope = angular.element(cb).scope();
                    if (!scope || !scope.node || scope.node._id !== sloNodeId) continue;

                    // Method 1: Angular scope toggleNode
                    if (typeof scope.toggleNode === 'function') {
                        scope.toggleNode(scope.node);
                        scope.$apply();
                        return {expanded: true, method: 'toggleNode'};
                    }
                    if (typeof scope.toggle === 'function') {
                        scope.toggle();
                        scope.$apply();
                        return {expanded: true, method: 'toggle'};
                    }
                    // Method 2: Set node.collapsed/expanded
                    if (scope.node.collapsed !== undefined) {
                        scope.node.collapsed = false;
                        scope.$apply();
                        return {expanded: true, method: 'collapsed=false'};
                    }
                    if (scope.node.expanded !== undefined) {
                        scope.node.expanded = true;
                        scope.$apply();
                        return {expanded: true, method: 'expanded=true'};
                    }
                    // Method 3: Click expand icon in DOM
                    const container = cb.closest('li, .tree-node, .node-content, div[class*="node"]') || cb.parentElement;
                    if (container) {
                        const expander = container.querySelector(
                            '.tree-branch-head, .fa-plus-square-o, .fa-plus-square, .fa-caret-right, ' +
                            '.glyphicon-plus, [class*="expand"], [class*="toggle-icon"], ' +
                            'i.fa, i.glyphicon, span.expand'
                        );
                        if (expander) {
                            expander.click();
                            return {expanded: true, method: 'dom-click'};
                        }
                    }
                    // Method 4: Children already exist
                    if (scope.node.children && scope.node.children.length > 0) {
                        return {expanded: true, method: 'already-has-children', childCount: scope.node.children.length};
                    }
                    return {expanded: false, reason: 'no expansion method found',
                            nodeKeys: Object.keys(scope.node).slice(0, 15)};
                } catch(e) {
                    return {expanded: false, reason: e.toString()};
                }
            }
            return {expanded: false, reason: 'SLO node not found in DOM'};
        }""", {"sloNodeId": slo_node_id})

        logger.info(f"KC expand {slo_key}: {expand_result}")

        if not expand_result.get("expanded"):
            logger.warning(f"KC tick: could not expand {slo_key} ({expand_result.get('reason')}), falling back to SLO tick")
            fallback_slos.append(slo_key)
            continue

        await asyncio.sleep(2)  # Wait for KC sub-tree to render

        # Step 2: Find and tick specific KC checkboxes by label text
        kc_result = await page.evaluate("""(args) => {
            const kcNums = args.kcNums;
            const sloNodeId = args.sloNodeId;
            let ticked = 0;
            const found = [];
            const notFound = [];

            const cbs = document.querySelectorAll('input[type="checkbox"]');
            for (const kcNum of kcNums) {
                let kcFound = false;
                const patterns = [
                    new RegExp('KC\\\\s*' + kcNum + '(?:\\\\b|\\\\s|$|:)', 'i'),
                    new RegExp('\\\\b' + kcNum + '\\\\.\\\\d', 'i'),
                    new RegExp('\\\\.' + kcNum + '(?:\\\\b|\\\\s)', 'i'),
                ];

                for (const cb of cbs) {
                    if (cb.id === 'filledOnSameDevice') continue;
                    try {
                        const scope = angular.element(cb).scope();
                        if (!scope || !scope.node) continue;

                        // Verify this is a child of our SLO by walking parent chain
                        let isChild = false;
                        let node = scope.node;
                        for (let i = 0; i < 5 && node; i++) {
                            if (node._id === sloNodeId) { isChild = true; break; }
                            node = node.parent || null;
                            if (!node || node._id === scope.node._id) break;
                        }
                        if (!isChild && scope.node.parentId === sloNodeId) isChild = true;
                        if (!isChild && scope.node._parentId === sloNodeId) isChild = true;
                        if (!isChild) continue;

                        // Match by node title or surrounding DOM text
                        const nodeTitle = String(scope.node.title || scope.node.name || scope.node.label || '');
                        const container = cb.closest('li, label, .tree-node, div') || cb.parentElement;
                        const labelText = container ? container.textContent : '';
                        const combined = nodeTitle + ' ' + labelText;

                        for (const pattern of patterns) {
                            if (pattern.test(combined)) {
                                if (!cb.checked) {
                                    cb.click();
                                    ticked++;
                                }
                                found.push('KC' + kcNum + ': ' + nodeTitle.substring(0, 60));
                                kcFound = true;
                                break;
                            }
                        }
                    } catch(e) {}
                    if (kcFound) break;
                }
                if (!kcFound) notFound.push(kcNum);
            }
            return {ticked, found, notFound};
        }""", {"kcNums": kc_nums, "sloNodeId": slo_node_id})

        kc_ticked = kc_result.get("ticked", 0)
        total += kc_ticked

        if kc_result.get("found"):
            logger.info(f"KC tick {slo_key}: ticked {kc_result['found']}")
        if kc_result.get("notFound"):
            logger.warning(f"KC tick {slo_key}: KC(s) not found: {kc_result['notFound']}, falling back to SLO tick")
            if slo_key not in fallback_slos:
                fallback_slos.append(slo_key)

    # Fallback: tick SLO-level for any KCs that couldn't be found
    if fallback_slos:
        logger.info(f"KC fallback: ticking whole SLOs {fallback_slos}")
        fb_result = await page.evaluate("""(args) => {
            const wanted = new Set(args.wanted);
            const sloIds = args.sloIds;
            let ticked = 0;
            const cbs = document.querySelectorAll('input[type="checkbox"]');
            for (const cb of cbs) {
                if (cb.id === 'filledOnSameDevice') continue;
                try {
                    const scope = angular.element(cb).scope();
                    if (!scope || !scope.node) continue;
                    for (const [key, id] of Object.entries(sloIds)) {
                        if (id === scope.node._id && wanted.has(key) && !cb.checked) {
                            cb.click();
                            ticked++;
                        }
                    }
                } catch(e) {}
            }
            return {ticked};
        }""", {"wanted": fallback_slos, "sloIds": SLO_CHECKBOX_IDS})
        total += fb_result.get("ticked", 0)

    return total


async def _tick_curriculum(page: Page, slo_codes: List[str]) -> int:
    """Tick SLO/KC checkboxes via Angular scope node IDs.

    Accepts mixed codes:
      - "SLO3" — tick the whole SLO (existing behaviour, kept)
      - "SLO3 KC3" — expand SLO3, tick only KC3 beneath it (new)

    Falls back to SLO-level tick if KC expansion or finding fails.
    Retries up to 3 times with increasing waits because the curriculum
    section loads asynchronously after stage_of_training selection.
    """
    if not slo_codes:
        return 0

    # Parse into wanted_slos (whole-SLO ticks) and wanted_kcs (leaf KC ticks)
    wanted_slos = set()
    wanted_kcs: Dict[str, List[int]] = {}  # {"SLO3": [3, 5], "SLO8": [2]}

    for code in slo_codes:
        code = code.strip().upper()
        kc_match = re.match(r'SLO\s*(\d+)\s+KC\s*(\d+)', code)
        if kc_match:
            slo_key = f"SLO{kc_match.group(1)}"
            kc_num = int(kc_match.group(2))
            wanted_kcs.setdefault(slo_key, []).append(kc_num)
        else:
            clean = code.replace(" ", "")
            if clean.startswith("SLO"):
                wanted_slos.add(clean)
            elif clean.isdigit():
                wanted_slos.add(f"SLO{clean}")

    if not wanted_slos and not wanted_kcs:
        return 0

    total_ticked = 0

    # Retry loop — curriculum section loads async after stage selection
    for attempt in range(3):
        wait_secs = 3 + (attempt * 3)  # 3s, 6s, 9s
        if attempt > 0:
            logger.info(f"Curriculum retry {attempt + 1}/3 — waiting {wait_secs}s for checkboxes…")
        await asyncio.sleep(wait_secs)

        # Check how many checkboxes exist in the DOM
        checkbox_count = await page.evaluate("""() => {
            const cbs = document.querySelectorAll('input[type="checkbox"]');
            let count = 0;
            for (const cb of cbs) {
                if (cb.id === 'filledOnSameDevice') continue;
                count++;
            }
            return {count};
        }""")
        logger.info(f"Curriculum attempt {attempt + 1}: found {checkbox_count.get('count', 0)} checkboxes")

        if checkbox_count.get("count", 0) == 0:
            continue  # No checkboxes yet — retry

        # Phase 1: Tick whole SLOs via Angular node IDs
        if wanted_slos:
            result = await page.evaluate("""(args) => {
                const wanted = new Set(args.wanted);
                const sloIds = args.sloIds;
                let ticked = 0;
                let unticked = 0;
                let matched = [];

                const checkboxes = document.querySelectorAll('input[type="checkbox"]');
                for (const cb of checkboxes) {
                    if (cb.id === 'filledOnSameDevice') continue;
                    try {
                        const scope = angular.element(cb).scope();
                        if (!scope || !scope.node) continue;
                        const nodeId = scope.node._id;

                        let sloKey = null;
                        for (const [key, id] of Object.entries(sloIds)) {
                            if (id === nodeId) { sloKey = key; break; }
                        }
                        if (sloKey) matched.push(sloKey);
                        if (!sloKey || sloKey === 'header') continue;

                        const isWanted = wanted.has(sloKey);
                        const isChecked = cb.checked;
                        if (isWanted && !isChecked) { cb.click(); ticked++; }
                        else if (!isWanted && isChecked) { cb.click(); unticked++; }
                    } catch(e) {}
                }
                return {ticked, unticked, matched};
            }""", {"wanted": list(wanted_slos), "sloIds": SLO_CHECKBOX_IDS})

            slo_ticked = result.get("ticked", 0)
            total_ticked += slo_ticked
            logger.info(f"SLO ticking: ticked {slo_ticked}, unticked {result.get('unticked', 0)} "
                         f"for {list(wanted_slos)}, matched: {result.get('matched', [])}")

        # Phase 2: KC leaf-level ticking
        if wanted_kcs:
            kc_ticked = await _tick_kc_leaves(page, wanted_kcs)
            total_ticked += kc_ticked

        if total_ticked > 0:
            return total_ticked

    logger.warning(f"Curriculum: failed to tick after 3 attempts for SLOs={list(wanted_slos)}, KCs={wanted_kcs}")
    return total_ticked


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

    # Autosave fallback removed — it reported success without confirming the entry
    # was actually created, causing false-success messages to the user.
    # If no save button was found and clicked, we must report failure.
    logger.warning("No save button/link found — cannot confirm entry was saved")
    return False


async def _verify_entry_saved(page: Page, form_type: str) -> bool:
    """
    After saving, navigate to the activities list and confirm a new entry
    with today's date AND the correct form type name exists. Returns True if
    verified, False otherwise.

    This prevents false-success — where the save button click appeared to work
    but the entry was never actually created in Kaizen.
    """
    from datetime import date
    today = date.today()
    # Kaizen uses DD/MM/YYYY in the activities list
    today_str = today.strftime("%d/%m/%Y")
    # Also check short month format e.g. "21 Mar 2026"
    today_str_alt = today.strftime("%-d %b %Y")

    # Map form_type codes to text fragments that appear in the Kaizen activities list
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
        "PROC_LOG": ["proc", "procedural skills", "procedure log"],
        "SDL": ["sdl", "self-directed learning", "self directed"],
        "US_CASE": ["us case", "ultrasound"],
        "COMPLAINT": ["complaint"],
        "SERIOUS_INC": ["serious incident", "serious_inc"],
        "EDU_ACT": ["educational activity", "edu_act"],
        "FORMAL_COURSE": ["formal course"],
        "REFLECT_LOG": ["reflective practice", "reflective log", "reflect"],
        "TEACH_OBS": ["teaching observation"],
    }

    # Get keywords for this form type (case-insensitive matching)
    keywords = form_type_keywords.get(form_type, [form_type.lower().replace("_", " ")])

    try:
        await page.goto("https://kaizenep.com/activities", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(4)

        # Get all activity rows — each row typically contains date + form type
        rows = await page.query_selector_all("tr, .activity-item, .event-item, [class*='activity'], [class*='row']")
        body_text = await page.inner_text("body")
        body_lower = body_text.lower()

        # First check: today's date must appear on the page at all
        has_today = today_str in body_text or today_str_alt in body_text
        if not has_today:
            logger.warning(f"Post-save verification FAILED: today's date ({today_str}) not found in activities list")
            return False

        # Second check: look for a row/line that has BOTH today's date AND the form type
        # Split body into lines and check each for co-occurrence
        for line in body_text.split("\n"):
            line_has_date = today_str in line or today_str_alt in line
            if not line_has_date:
                continue
            line_lower = line.lower()
            for kw in keywords:
                if kw in line_lower:
                    logger.info(f"Post-save verification: found '{kw}' with today's date in activities list ✓")
                    return True

        # Fallback: check nearby text blocks (some SPAs render date and form name in sibling elements)
        # Look for form type keyword anywhere on page that also has today's date
        for kw in keywords:
            if kw in body_lower:
                logger.info(f"Post-save verification: found form keyword '{kw}' on activities page with today's date (weak match) ✓")
                return True

        logger.warning(f"Post-save verification FAILED: today's date found but no '{form_type}' entry detected")
        return False

    except Exception as e:
        # If verification fails due to navigation error, log but don't block —
        # return None to signal "unverified" so caller can downgrade to partial
        logger.warning(f"Post-save verification error (inconclusive): {e}")
        return None  # type: ignore[return-value]


async def _submit_entry(page: Page) -> bool:
    """Click Submit/Save (for self-contained log forms with no assessor).
    Only use when the form does NOT require an assessor assignment.
    Returns True on success."""
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
                # Never click Send (not Submit) — Send implies sending to another person
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
    logger.warning("No submit button found — falling back to _save_draft()")
    return False


# ─── File attachment helpers ──────────────────────────────────────────────────

def _download_drive_file(url: str) -> Optional[str]:
    """Download a Google Drive file to a temp path. Returns path or None."""
    try:
        # Convert sharing URL to direct download URL
        file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
        if file_id_match:
            file_id = file_id_match.group(1)
            direct_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        else:
            direct_url = url

        suffix = ".pdf"
        if "." in url.split("/")[-1]:
            suffix = "." + url.split("/")[-1].rsplit(".", 1)[-1][:10]

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        urllib.request.urlretrieve(direct_url, tmp.name)
        tmp.close()
        logger.info(f"Downloaded Drive file to {tmp.name}")
        return tmp.name
    except Exception as e:
        logger.warning(f"Failed to download Drive file: {e}")
        return None


async def _attach_file(page: Page, file_path: str) -> bool:
    """Attach a file using the form's file input element."""
    try:
        if not os.path.isfile(file_path):
            logger.warning(f"Attachment file not found: {file_path}")
            return False

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


# ─── Main entry point ────────────────────────────────────────────────────────

async def file_to_kaizen(
    form_type: str,
    fields: Dict[str, Any],
    username: str,
    password: str,
    curriculum_links: Optional[List[str]] = None,
    submit: bool = False,
    attachment_path: Optional[str] = None,
    attachment_drive_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    File a form to Kaizen as a draft.

    Args:
        form_type: Short code (CBD, DOPS, etc.)
        fields: Dict of field_key → value (from extractor)
        username: Kaizen username
        password: Kaizen password
        curriculum_links: Optional list of SLO codes ("SLO3") or KC codes ("SLO3 KC3")
        submit: If True, submit instead of saving as draft
        attachment_path: Optional local file path to attach (PDF, certificate, etc.)
        attachment_drive_url: Optional Google Drive URL to download and attach

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
        # No DOM mapping yet — return partial so the router can escalate to browser-use
        all_field_keys = list(fields.keys())
        return {"status": "partial", "filled": [], "skipped": all_field_keys, "error": f"No field mapping for {form_type} — needs browser-use"}

    filled = []
    skipped = []
    browser = None
    cdp_pw = None
    use_cdp = KAIZEN_USE_CDP

    try:
        page = None

        # CDP mode: connect to managed browser
        if use_cdp:
            page, cdp_pw = await connect_cdp_browser()
            if page is None:
                use_cdp = False  # fallback to headless

        # Headless mode (default or CDP fallback)
        if not use_cdp:
            pw = await async_playwright().start()
            cdp_pw = pw
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()

        # Login (skip if CDP page is already on Kaizen)
        if use_cdp and "kaizenep.com" in page.url:
            logger.info("CDP: already logged in, skipping login")
        else:
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

        # Fill stage_of_training FIRST — curriculum checkboxes appear after
        if "stage_of_training" in field_map:
            st_dom = field_map["stage_of_training"]
            st_val = fields.get("stage_of_training", "Higher")
            if await _fill_field(page, st_dom, st_val, "stage_of_training"):
                filled.append("stage_of_training")
            else:
                skipped.append("stage_of_training")

        # Fill remaining mapped fields
        for field_key, dom_id in field_map.items():
            if field_key == "stage_of_training":
                continue  # Already handled above
            value = fields.get(field_key)
            if value is None or value == "" or value == []:
                skipped.append(field_key)
                continue

            success = await _fill_field(page, dom_id, value, field_key)
            if success:
                filled.append(field_key)
            else:
                skipped.append(field_key)

        # Expand curriculum section then tick SLO/KC checkboxes
        if curriculum_links:
            await _expand_curriculum_section(page)
            await _tick_curriculum(page, curriculum_links)

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

        # Clean up temp file
        if temp_attachment:
            try:
                os.unlink(temp_attachment)
            except OSError:
                pass

        # Save or submit
        if submit:
            saved = await _submit_entry(page)
            if not saved:
                saved = await _save_draft(page)
        else:
            saved = await _save_draft(page)

        # Post-save verification — confirm the entry actually appeared in Kaizen
        # Post-save verification — confirm the entry actually appeared in Kaizen
        # Skip on submit path: submitted entries are live immediately, no SPA delay.
        # Verification only needed for draft saves where autosave may silently fail.
        if saved and len(filled) > 0 and not submit:
            verified = await _verify_entry_saved(page, form_type)

        # Determine status
        if not saved:
            # Save failed — report as failed even if some fields were filled
            status = "failed"
            save_error = "Save button not found or click failed"
            if len(filled) > 0:
                save_error += f" ({len(filled)} fields were filled but draft was NOT saved)"
        elif verified is False:
            # Save appeared to work but entry not found in activities list
            status = "failed"
            save_error = "Entry not found in your portfolio after saving — it may not have saved correctly. Please check Kaizen manually."
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
        # Only close browser in headless mode — CDP browser is shared
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
