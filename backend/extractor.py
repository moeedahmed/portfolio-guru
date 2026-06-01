from google import genai
import asyncio
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import List
import httpx

logger = logging.getLogger(__name__)
from models import CBDData, FormTypeRecommendation, FormDraft
from form_schemas import FORM_SCHEMAS
from form_display import public_form_name, sanitize_internal_form_codes
from model_config import gemini_three_five_flash_model

# RCEM Higher EM Curriculum (2025 Update) — Exact Kaizen checkbox labels
# Source: Live Kaizen CBD form screenshot (verified 2026-03-08)
# NOTE: Kaizen's SLO numbering differs from rcemcurriculum.co.uk — use these numbers.
RCEM_KC_MAP = """RCEM Higher EM Curriculum (2025 Update) — Exact Kaizen Checkbox Labels:

SLO1: Care for acutely physiologically stable adult patients presenting to acute care across the full range of complexity (2025 Update)
  KC1: to be expert in assessing and managing all adult patients attending the ED. These capabilities will apply to patients attending with both physical and psychological ill health (2025 Update)

SLO2: Support the ED team by answering clinical questions and making safe decisions (2025 Update)
  KC1: able to support the pre-hospital, medical, nursing and administrative team in answering clinical questions and in making safe decisions for patients with appropriate levels of risk in the ED (2025 Update)
  KC2: aware of when it is appropriate to review patients remotely or directly and able to teach these principles to others (2025 Update)

SLO3: Resuscitate and stabilise patients in the ED knowing when it is appropriate to stop (2025 Update)
  KC1: provide airway management & ventilatory support to critically ill patients (2025 Update)
  KC2: be expert in fluid management and circulatory support in critically ill patients (2025 Update)
  KC3: manage all the life-threatening conditions including peri-arrest & arrest situations in the ED (2025 Update)
  KC4: be expert in caring for ED patients and their relatives and loved ones at the end of the patient's life (2025 Update)
  KC5: effectively lead and support resuscitation teams (2025 Update)

SLO4: Care for acutely injured patients across the full range of complexity (2025 Update)
  KC1: be expert in assessment, investigation and clinical management of patients attending with all injuries, regardless of complexity (2025 Update)
  KC2: provide expert leadership of the Major Trauma Team (2025 Update)

SLO5: Care for children of all ages, at all stages of development and with complex needs (2025 Update)
  KC1: be expert in assessing and managing all children and young adult patients attending the ED (2025 Update)
  KC2: be able to provide airway management & ventilatory support to critically ill paediatric patients (2025 Update)
  KC3: be able to lead and support a multidisciplinary paediatric resuscitation including trauma (2025 Update)
  KC4: be expert in fluid management and circulatory support in critically ill paediatric patients (2025 Update)
  KC5: be able to manage all the life-threatening paediatric conditions including peri-arrest & arrest situations in the ED (2025 Update)
  KC6: be able to assess and formulate a management plan for children and young adults who present with complex medical and social needs (2025 Update)

SLO6: Deliver key procedural skills needed in EM (2025 Update)
  KC1: the clinical knowledge to identify when key EM practical/emergency skills are indicated (2025 Update)
  KC2: the knowledge and psychomotor skills to perform EM procedural skills safely and in a timely fashion (2025 Update)
  KC3: be able to supervise and guide colleagues in delivering procedural skills (2025 Update)

SLO7: Deal with complex or challenging situations in the workplace (2025 Update)
  KC1: have expert communication skills to negotiate, manage complicated or evolving interactions (2025 Update)
  KC2: behave professionally in dealings with colleagues and team members within the ED (2025 Update)
  KC3: work professionally and effectively with those outside the ED (2025 Update)

SLO8: Lead the ED shift (2025 Update)
  KC1: will provide support to ED staff at all levels and disciplines on the ED shift (2025 Update)
  KC2: will be able to liaise with the rest of the acute/urgent care team and wider hospital as shift leader (2025 Update)
  KC3: will maintain situational awareness throughout the shift to ensure safety is optimised (2025 Update)
  KC4: will anticipate challenges, generate options, make decisions and communicate these effectively to the team as lead clinician (2025 Update)

SLO9: Support, supervise & educate others working in the ED (2025 Update)
  KC1: be able to undertake training and supervision of members of the ED team in the clinical environment (2025 Update)
  KC2: be able to prepare and deliver teaching sessions outside of the clinical environment, including simulation, small group work, and didactic presentations (2025 Update)
  KC3: be able to provide effective constructive feedback to colleagues, including debrief (2025 Update)
  KC4: understand the principles necessary to mentor and appraise junior doctors (2025 Update)

SLO10: Participate in research and manage data appropriately (2025 Update)
  KC1: be able to appraise, synthesise, communicate and use research evidence to develop EM care (2025 Update)
  KC2: be able to actively participate in research (2025 Update)

SLO11: Participate in & promote activity to improve quality & safety of patient care (2025 Update)
  KC1: be able to provide clinical leadership on effective Quality Improvement work (2025 Update)
  KC2: be able to support and develop a culture of departmental safety, and good clinical governance (2025 Update)

SLO12: Lead & Manage (2025 Update)
  KC1: be able to demonstrate their involvement in a range of management activities and show an understanding of the relevant medicolegal directives (2025 Update)
  KC2: be able to investigate a patient safety incident, participate and contribute effectively to department clinical governance activities and risk reduction processes (2025 Update)
  KC3: be able to manage the staff rota being aware of relevant employment law and recruitment activities (2025 Update)
  KC4: be able to effectively represent the ED at inter-specialty meetings (2025 Update)
  KC5: demonstrate an understanding of how effective Emergency Medicine Leadership positively impacts on standards of patient care and patient safety (2025 Update)
  KC6: demonstrate a positive impact on the culture of the Emergency Department through attitudes and behaviours that impact positively on colleagues, patients and their relatives (2025 Update)
"""

KC_FULL_TEXT = {
    "SLO11 KC1": (
        "SLO11 KC1: be able to provide clinical leadership on effective "
        "Quality Improvement work (2025 Update)"
    ),
    "SLO11 KC2": (
        "SLO11 KC2: be able to support and develop a culture of departmental "
        "safety, and good clinical governance (2025 Update)"
    ),
    "SLO12 KC2": (
        "SLO12 KC2: be able to investigate a patient safety incident, "
        "participate and contribute effectively to department clinical "
        "governance activities and risk reduction processes (2025 Update)"
    ),
}

_client = None

# Extraction model policy:
# - DeepSeek V4 Flash is the normal text model.
# - Gemini 3.5 Flash is an emergency fallback only for provider/billing/quota failures.
PROVIDERS = [
    {
        "name": "deepseek-v4-flash",
        "type": "openai_compat",
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
    },
    {
        "name": "gemini-3-5-flash-fallback",
        "type": "gemini",
        "model": gemini_three_five_flash_model,
        "env_key": "GOOGLE_API_KEY",
    },
]


def _select_providers(tier: str = ""):
    return PROVIDERS


async def _generate(prompt, retries: int = 1, tier: str = ""):
    """Call the configured extractor LLM.
    Defaults to DeepSeek V4 Flash.
    Returns the response as a plain string.
    """
    import time as _time
    loop = asyncio.get_event_loop()
    last_error = None
    t0 = _time.monotonic()

    providers = _select_providers(tier)

    for provider in providers:
        api_key = os.environ.get(provider["env_key"])
        if not api_key:
            logger.debug("Skipping %s — %s not set", provider["name"], provider["env_key"])
            continue

        for attempt in range(retries + 1):
            try:
                if provider["type"] == "gemini":
                    client = _get_client()
                    model_name = provider["model"]() if callable(provider["model"]) else provider["model"]
                    result = await loop.run_in_executor(
                        None,
                        lambda m=model_name: client.models.generate_content(model=m, contents=prompt)
                    )
                    elapsed = _time.monotonic() - t0
                    logger.info(f"{provider['name']} ({model_name}) responded in {elapsed:.1f}s")
                    return result.text
                else:
                    # OpenAI-compatible DeepSeek endpoint. Use httpx directly so
                    # the live bot does not depend on the optional openai package.
                    model_name = provider["model"]() if callable(provider["model"]) else provider["model"]
                    request_kwargs = {
                        "model": model_name,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2,
                    }
                    if "json" in prompt.lower():
                        request_kwargs["response_format"] = {"type": "json_object"}
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        response = await client.post(
                            f"{provider.get('base_url')}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                            },
                            json=request_kwargs,
                        )
                        response.raise_for_status()
                        response_json = response.json()
                    elapsed = _time.monotonic() - t0
                    logger.info(f"{provider['name']} ({model_name}) responded in {elapsed:.1f}s")
                    return response_json["choices"][0]["message"]["content"]
            except Exception as e:
                last_error = e
                error_msg = str(e).lower()
                is_retryable = any(term in error_msg for term in [
                    "402", "429", "rate", "quota", "balance", "billing",
                    "503", "502", "500", "unavailable", "overloaded",
                ])
                if is_retryable:
                    if attempt < retries:
                        await asyncio.sleep(1)
                        continue
                    logger.warning("%s failed (%s), trying next provider", provider["name"], e)
                    break  # next provider
                else:
                    logger.warning("%s error (%s), trying next provider", provider["name"], e)
                    break  # next provider

    raise last_error or RuntimeError("All providers failed — none configured")

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
    "ESLE":          "cbc7a42f-a2f0-436b-813e-bbf97cce0a34",
    "ESLE_ASSESS":   "4a6f3a91-10ed-45d0-bb82-3e87ae2d6d04",
    "COMPLAINT":     "f7c0ba98-5a47-4e37-b76a-ca3c5c8484cc",
    "SERIOUS_INC":   "9d4a7912-a615-4ae4-9fae-6be966bcf254",
    "EDU_ACT":       "868dc0e7-f4e9-4283-ac52-d9c8b246024b",
    "FORMAL_COURSE": "c7cd9a95-e2aa-4f61-a441-b663f3c933c6",
    "REFLECT_LOG":   "32d0fcb9-05d0-4d6d-b877-ebd5daf0b4e9",
    "TEACH_OBS":     "30668ad8-e1db-4a27-bb2d-3e395e6acfcf",
    # ─── 2021 versions ────────────────────────────────────────────────────
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
    # ─── Management section ───────────────────────────────────────────────
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
    "TEACH_CONFID":       "f614bdcc-5d31-4b5b-b980-1e073e2431db",
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
    # ─── Other ────────────────────────────────────────────────────────────
    "ADD_POST":           "c8049d8b-11f7-4bad-ac6c-c0b3c9ded1bb",
    "ADD_SUPERVISOR":     "87205ea8-ee22-4555-8e30-3a5ffc8b0bd2",
    "HIGHER_PROG":        "c19ca7c4-54ba-4816-b292-8bce1af4a62f",
    "ABSENCE":            "9feb8df3-1c70-4237-bf77-c6520e43c9c2",
    "CCT":                "9425aea9-1fb9-4230-b2a3-ec1712599caa",
    "FILE_UPLOAD":        "108ae04a-d865-4a4a-ba97-9c537563e960",
    "FILE_UPLOAD_2021":   "2db062c4-471e-4216-92f2-d51af84f2246",
    "OOP":                "2b023326-a34f-463e-a921-bf215599b0ac",
}

FORM_TYPE_ALIASES = {
    # User-facing "ESLE" should create the formal ESLE WPBA. The separate
    # reflection-on-ESLE Kaizen form is not exposed as a selectable draft flow.
    "ESLE": "ESLE_ASSESS",
}

FORM_SCHEMA_ALIASES = {
    # The 2021 curriculum form is keyed as ESLE_2021 in Kaizen, but it uses the
    # same assessed-ESLE draft schema as the current ESLE_ASSESS flow.
    "ESLE_2021": "ESLE_ASSESS",
}


def canonical_form_type(form_type: str) -> str:
    """Return the Portfolio Guru canonical form code for user-facing aliases."""
    return FORM_TYPE_ALIASES.get((form_type or "").strip().upper(), form_type)


def schema_form_type(form_type: str) -> str:
    """Return the schema key used to draft/review a Portfolio Guru form code."""
    canonical = canonical_form_type(form_type)
    if canonical in FORM_SCHEMA_ALIASES:
        return FORM_SCHEMA_ALIASES[canonical]
    if canonical.endswith("_2021") and canonical not in FORM_SCHEMAS:
        return canonical[:-5]
    return canonical

# AI-tell patterns to strip from ALL narrative text (humanizer)
# Applied before the user sees any draft — not post-approval
SLOP_PATTERNS = [
    r"\s*—\s*",  # em dashes -> " - "
    r"\s*--\s*",  # double hyphens (AI em-dash approximation) -> " - "
    # Single words
    r"\bdelve\b",
    r"\bnavigate\b",
    r"\bcrucial\b",
    r"\bimportantly\b",
    r"\bcomprehensive\b",
    r"\bmoreover\b",
    r"\bfurthermore\b",
    r"\bunderscore[sd]?\b",
    r"\bpivotal\b",
    r"\bseamless(?:ly)?\b",
    r"\bholistic(?:ally)?\b",
    r"\brobust\b",
    r"\binstrumental\b",
    r"\bmultifaceted\b",
    r"\blandscape\b",
    r"\brealm\b",
    r"\bparadigm\b",
    r"\bfacilitate[sd]?\b",
    r"\bleverag(?:e[sd]?|ing)\b",
    r"\bunlock(?:s|ed|ing)?\b",
    r"\btapestry\b",
    r"\bcommenc(?:e[sd]?|ing)\b",
    r"\bembark(?:s|ed|ing)?\b",
    r"\bmeticulous(?:ly)?\b",
    r"\boverarch(?:ing)?\b",
    # Phrases
    r"\bit's worth noting\b",
    r"\bit is worth noting\b",
    r"\bon the other hand\b",
    r"\bin summary\b",
    r"\bto summarise\b",
    r"\bto summarize\b",
    r"\bin conclusion\b",
    r"\bthis case highlights\b",
    r"\bthis experience underscored\b",
    r"\bthis encounter reinforced\b",
    r"\bmoving forward\b",
    r"\bin this context\b",
    r"\bit is important to note\b",
    r"\bplayed a (?:key|vital|critical|crucial) role\b",
    r"\ba testament to\b",
    r"\bgame.?changer\b",
    r"\bensur(?:e[sd]?|ing)\b",
    r"\benhance[sd]?\b",
    r"\bultimately\b",
    r"\bsignificant(?:ly)?\b",
    r"\bnotably\b",
    r"\bthis case (?:served as|was) a (?:valuable|important|key)\b",
    r"\breinforced (?:the importance|my understanding)\b",
    r"\bhighlighted the (?:importance|need|value)\b",
]

# Fields that should be humanized (narrative text, not dates/dropdowns/names)
_HUMANIZE_FIELDS = {
    "clinical_reasoning", "reflection", "trainee_role", "patient_presentation",
    "case_to_be_discussed", "reflective_comments", "learning_points",
    "circumstances", "replay_differently", "why", "different_outcome",
    "focussing_on", "learned", "further_action", "description",
    "root_causes", "contributing_factors", "resource_details",
    "clinical_scenario", "how_used", "learning_outcomes",
    "key_features", "key_aspects", "pdp_summary", "qi_engagement",
    "qi_understanding", "qi_journey_aspects", "next_pdp",
    "situation", "evidence_evaluation", "apply_to_practice",
    "search_methodology", "communicate_to_patient", "future_research",
    "project_description", "reflective_notes", "resources_used",
    "lessons_learned", "other_comments",
}


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    return _client


# Resuscitation / cardiac / advanced-imaging tropes the LLM tends to hallucinate
# when an image-derived case is sparse (e.g. a rib-fracture screenshot becoming a
# CPR/ALS/ROSC CBD). For image-derived input only, narrative sentences that
# contain any of these terms are stripped unless the source text itself
# anchors the term — see enforce_image_source_grounding.
HIGH_RISK_FABRICATION_TERMS = [
    "cpr", "cardiopulmonary resuscitation",
    "cardiac arrest", "arrest call",
    "als", "advanced life support", "bls",
    "defibrillation", "defibrillator", "shocks delivered",
    "adrenaline", "epinephrine",
    "rosc", "return of spontaneous circulation",
    "asystole", "ventricular fibrillation",
    "ct head", "ct brain",
    "coronary angiography", "primary pci", "cath lab",
]

_IMAGE_INPUT_SOURCES = {"photo", "image", "img"}

_WEAK_ADMIN_RESCUE_ANCHORS = [
    "for cpr",
    "resus: for cpr",
    "reason: for cpr",
    "presenting complaint: for cpr",
    "presentation: for cpr",
]

_SUBSTANTIVE_RESCUE_ANCHORS = [
    "cardiac arrest",
    "arrested",
    "cpr in progress",
    "cpr commenced",
    "chest compressions",
    "compressions started",
    "defibrillation",
    "defibrillated",
    "shock delivered",
    "adrenaline",
    "epinephrine",
    "rosc",
    "return of spontaneous circulation",
    "peri-arrest",
]

_IMAGE_PROCEDURE_SIGNALS = [
    "procedure note",
    "performed",
    "block",
    "serratus",
    "erector spinae",
    "local anaesthetic",
    "levobupivacaine",
    "lidocaine",
    "ultrasound-guided",
    "ultrasound guidance",
    "cannulation",
    "intubation",
    "chest drain",
    "central line",
    "lumbar puncture",
]

_IMAGE_IMAGING_SIGNALS = [
    "ct",
    "x-ray",
    "xray",
    "report",
    "fracture",
    "pneumothorax",
    "haematoma",
    "nodule",
    "impression",
    "findings",
    "no acute",
]

_IMAGE_CBD_REASONING_SIGNALS = [
    "i assessed",
    "i managed",
    "i decided",
    "i considered",
    "i discussed",
    "differential",
    "clinical reasoning",
    "management decision",
    "decision-making",
    "treatment plan",
    "escalated",
]


def _is_image_source(input_source: str | None) -> bool:
    if not input_source:
        return False
    return input_source.lower() in _IMAGE_INPUT_SOURCES


def _source_supports_term(term: str, source_text: str) -> bool:
    """Conservative lexical anchor check: term (or a six-char stem of its
    first word) appears in the source. Used by enforce_image_source_grounding
    to keep terms that the doctor's image evidence actually documents."""
    if not source_text:
        return False
    src_lower = source_text.lower()
    term_lower = term.lower()
    if term_lower in {"cpr", "cardiopulmonary resuscitation"}:
        if any(anchor in src_lower for anchor in _SUBSTANTIVE_RESCUE_ANCHORS):
            return True
        # A lone admin/header phrase like "Resus: For CPR" is too weak to
        # support an invented arrest narrative from image OCR.
        if any(anchor in src_lower for anchor in _WEAK_ADMIN_RESCUE_ANCHORS):
            return False
    if term_lower in src_lower:
        return True
    first_word = term_lower.split()[0]
    if len(first_word) >= 6 and first_word[:6] in src_lower:
        return True
    return False


def _source_has_any(source_text: str, signals: list[str]) -> bool:
    src_lower = (source_text or "").lower()
    return any(signal in src_lower for signal in signals)


def _image_source_supports_cbd(source_text: str) -> bool:
    """Image OCR must contain actual case-management reasoning before CBD is
    allowed to survive as a recommendation."""
    return _source_has_any(source_text, _IMAGE_CBD_REASONING_SIGNALS)


def _source_has_image_procedure_evidence(source_text: str) -> bool:
    return _source_has_any(source_text, _IMAGE_PROCEDURE_SIGNALS)


def _source_has_image_imaging_evidence(source_text: str) -> bool:
    return _source_has_any(source_text, _IMAGE_IMAGING_SIGNALS)


def _dedupe_recommendations(recommendations: list[FormTypeRecommendation]) -> list[FormTypeRecommendation]:
    seen: set[str] = set()
    deduped: list[FormTypeRecommendation] = []
    for rec in recommendations:
        if rec.form_type in seen:
            continue
        seen.add(rec.form_type)
        deduped.append(rec)
    return deduped


def enforce_image_recommendation_grounding(
    recommendations: list[FormTypeRecommendation],
    source_text: str,
) -> list[FormTypeRecommendation]:
    """Post-process image-derived form recommendations.

    The LLM prompt is advisory; this deterministic layer prevents sparse OCR
    and admin/header text from pushing the user into a CBD when the visible
    evidence is really a procedure note, imaging report, or fragment.
    """
    if not source_text:
        return recommendations

    supports_cbd = _image_source_supports_cbd(source_text)
    has_procedure = _source_has_image_procedure_evidence(source_text)
    has_imaging = _source_has_image_imaging_evidence(source_text)

    grounded = list(recommendations)
    if not supports_cbd:
        grounded = [rec for rec in grounded if rec.form_type != "CBD"]

    additions: list[FormTypeRecommendation] = []
    if has_procedure:
        additions.append(FormTypeRecommendation(
            form_type="PROC_LOG",
            rationale="Visible source documents a procedure; log the procedure unless a formal observed assessment is confirmed.",
            uuid=FORM_UUIDS.get("PROC_LOG"),
        ))
        additions.append(FormTypeRecommendation(
            form_type="DOPS",
            rationale="Use if an assessor directly observed the documented procedure.",
            uuid=FORM_UUIDS.get("DOPS"),
        ))
    elif has_imaging and not grounded:
        additions.append(FormTypeRecommendation(
            form_type="REFLECT_LOG",
            rationale="Visible source is an imaging/report fragment without enough case-management reasoning for CBD.",
            uuid=FORM_UUIDS.get("REFLECT_LOG"),
        ))

    return _dedupe_recommendations(additions + grounded)[:3]


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _strip_unsupported_sentences(text: str, source_text: str, banned_terms: list[str]) -> tuple[str, list[str]]:
    """Remove sentences that look wholly fabricated.

    A sentence is stripped only when it contains banned terms AND none of
    those terms appear in the source. If at least one banned term in the
    sentence is anchored in the source, the sentence is plausibly the
    doctor's own narrative — keep it intact (don't second-guess details
    like dose or drug name within an otherwise-supported sentence).

    Returns (cleaned_text, sorted unique list of terms removed).
    """
    if not text or not isinstance(text, str):
        return text, []
    stripped: list[str] = []
    sentences = _SENTENCE_SPLIT.split(text)
    kept: list[str] = []
    for sentence in sentences:
        sentence_lower = sentence.lower()
        banned_in_sentence = [t for t in banned_terms if t in sentence_lower]
        if not banned_in_sentence:
            kept.append(sentence)
            continue
        supported = [t for t in banned_in_sentence if _source_supports_term(t, source_text)]
        if supported:
            kept.append(sentence)
            continue
        stripped.extend(banned_in_sentence)
    cleaned = " ".join(s.strip() for s in kept if s.strip()).strip()
    return cleaned, sorted(set(stripped))


def enforce_image_source_grounding(fields: dict, source_text: str) -> tuple[dict, list[str]]:
    """Strip narrative-field sentences that contain HIGH_RISK_FABRICATION_TERMS
    not anchored in `source_text`. Returns (cleaned_fields, stripped_terms).

    Only applied when the case came from a photo/image — text input is treated
    as user-authored and is not second-guessed. A no-op when source_text is
    empty so callers don't need to special-case it.
    """
    if not source_text:
        return fields, []
    all_stripped: list[str] = []
    for key in list(fields.keys()):
        if key in _HUMANIZE_FIELDS and isinstance(fields[key], str) and fields[key]:
            cleaned, stripped = _strip_unsupported_sentences(
                fields[key], source_text, HIGH_RISK_FABRICATION_TERMS
            )
            fields[key] = cleaned
            all_stripped.extend(stripped)
    return fields, sorted(set(all_stripped))


_REUSE_SIGNALS = (
    "same case", "same one", "previous case", "earlier case", "last case",
    "do this as", "do it as", "file as", "file it as", "log this as", "log it as",
    "make this a", "make it a", "make this an", "make it an",
    "another wpba", "another form", "another type", "different form", "different type",
    "reuse", "redo as", "redo this as", "use the same", "use this for", "use that for",
    "also as", "also do", "also file", "also log",
)


def is_reuse_request(text: str) -> bool:
    """Cheap heuristic: does this message look like 'reuse the last case as X'?

    Should be paired with a `last_filed_case_text` existence check by the caller
    before routing. Keeps the gate cheap (no LLM call) so we don't slow down
    every clinical case input. Returns False for long messages (>200 chars)
    since a real reuse instruction is typically short.
    """
    text_lower = text.lower().strip()
    if not text_lower or len(text_lower) > 200:
        return False
    return any(signal in text_lower for signal in _REUSE_SIGNALS)


def extract_explicit_form_type(text: str, *, require_intent: bool = True) -> str | None:
    """Check if the user is EXPLICITLY REQUESTING a specific form type.

    Two-tier match so a clear directive like "Procedure log for ES Block"
    always wins over a short code that happens to appear as a substring
    elsewhere (e.g. "statin" → "stat").

    1. PRIMARY phrases (e.g. "procedure log", "case-based discussion") are
       full-form names. If any appears anywhere in the text, that's a strong
       enough signal — return that form, no intent phrase required.
    2. SECONDARY keys (e.g. "stat", "cbd") are short codes that only count
       when (a) an intent phrase is present and (b) the code appears as a
       whole word (word boundary), not as part of "statin"/"status"/etc.

    When `require_intent=False`, the intent phrase gate on SECONDARY codes is
    skipped — callers that have already established intent (e.g. the reuse-
    request handler) can use the relaxed match without false positives from
    unrelated text. Word-boundary check still applies.

    Returns the short form key (e.g. "CBD", "DOPS") or None.
    """
    text_lower = text.lower()

    primary_patterns = {
        "PROC_LOG":     ["procedural log", "procedure log", "proc log"],
        "MINI_CEX":     ["mini cex", "mini-cex", "minicex", "clinical evaluation exercise"],
        "CBD":          ["case-based discussion", "case based discussion"],
        "DOPS":         ["directly observed procedural"],
        "LAT":          ["leadership assessment tool"],
        "ACAT":         ["acute care assessment tool"],
        "ACAF":         ["applied critical appraisal", "critical appraisal form"],
        "STAT":         ["structured teaching assessment"],
        "MSF":          ["multi source feedback", "multi-source feedback"],
        "QIAT":         ["quality improvement assessment"],
        "JCF":          ["journal club"],
        "TEACH":        ["teach form", "teaching delivered", "teaching session form"],
        "SDL":          ["self-directed learning", "self directed learning"],
        "US_CASE":      ["ultrasound case", "us case", "pocus case"],
        "ESLE_ASSESS":  ["significant learning event"],
        "COMPLAINT":    ["complaint reflection", "complaint form"],
        "SERIOUS_INC":  ["serious incident", "si reflection", "never event"],
        "EDU_ACT":      ["educational activity", "teaching attended"],
        "FORMAL_COURSE":["formal course", "atls course", "apls course", "als course", "epals"],
    }

    primary_hits = []
    for form_type, keywords in primary_patterns.items():
        for kw in keywords:
            idx = text_lower.find(kw)
            if idx != -1:
                primary_hits.append((idx, len(kw), form_type))
    if primary_hits:
        primary_hits.sort(key=lambda h: (h[0], -h[1]))
        return primary_hits[0][2]

    intent_phrases = [
        "make me a", "create a", "file a", "file as", "submit as",
        "log as", "log a", "do a", "fill in a", "fill a",
        "i want a", "i need a", "use a", "as a ", "do this as",
        "make this a", "treat this as", "this is a", "record as",
        "add as", "add this as", "add this case as", "add this case to",
    ]
    if require_intent:
        has_intent = any(phrase in text_lower for phrase in intent_phrases)
        if not has_intent:
            return None

    secondary_codes = {
        "CBD":         ["cbd"],
        "DOPS":        ["dops"],
        "MINI_CEX":    ["mini cex", "mini-cex", "mini_cex", "minicex"],
        "ACAT":        ["acat"],
        "ACAF":        ["acaf"],
        "STAT":        ["stat"],
        "MSF":         ["msf"],
        "QIAT":        ["qiat"],
        "JCF":         ["jcf"],
        "SDL":         ["sdl"],
        "ESLE_ASSESS": ["esle"],
        "EDU_ACT":     ["edu act"],
    }
    for form_type, codes in secondary_codes.items():
        for code in codes:
            if re.search(rf'\b{re.escape(code)}\b', text_lower):
                return form_type
    return None


async def classify_intent(text: str, case_context: str = "") -> str:
    """Classify user message intent into 5 categories.

    When case_context is provided (user has an active case), the classifier
    can distinguish questions *about that case* from general questions and
    can tell new cases apart from additional detail for the current one.

    Returns one of:
        chitchat, question_general, question_about_case, new_case, edit_detail
    """
    client = _get_client()

    if case_context:
        prompt = f"""You are classifying a message from a user who already has an active clinical case in progress.

Active case (for context — do NOT treat this as the message):
\"\"\"
{case_context[:600]}
\"\"\"

Classify the NEW message below into exactly one category:

- chitchat: greetings, check-ins, status checks (hi, hello, you there, ping, thanks, ok, great, bye, etc.)
- question_general: asking about what the bot does or what forms exist IN GENERAL (not about their specific case)
- question_about_case: asking about their SPECIFIC active case — doubt about form type, asking for suggestions, "is this right for X", "what would be better", "should I use Y instead", "what do you suggest"
- new_case: a COMPLETELY NEW clinical case description (contains different patient details, symptoms, management unrelated to the active case above)
- edit_detail: additional detail, correction, or clarification about the CURRENT active case

Message: {text}

Respond with ONLY one of: chitchat, question_general, question_about_case, new_case, edit_detail"""
    else:
        prompt = f"""Classify this message into exactly one category:

- chitchat: greetings, check-ins, status checks, short social messages (hi, hello, you there, still there, you ok, thanks, bye, ok, great, are you working, hello?, ping, hey, what's up, etc.)
- question_general: asking about what the bot does, how it works, capabilities, help requests
- new_case: a clinical case description suitable for portfolio filing (contains patient details, symptoms, management, procedures, or clinical scenarios)

Message: {text}

Respond with ONLY one of: chitchat, question_general, new_case"""

    text = await _generate(prompt)
    result = text.strip().lower()

    # Normalize response to valid category
    if "chitchat" in result:
        return "chitchat"
    elif "question_about_case" in result:
        return "question_about_case"
    elif "question_general" in result or "question" in result:
        return "question_general"
    elif "edit_detail" in result:
        return "edit_detail"
    else:
        return "new_case"


async def classify_menu_intent(text: str) -> str:
    """Route a short non-clinical message to a top-level command.

    Returns one of: file_case, show_stats, open_settings, manage_credentials,
    show_help, chitchat, ambiguous. Returns ambiguous if the message could be
    a clinical case or otherwise doesn't clearly map to a navigation action.
    """
    prompt = f"""You are routing a short message from a doctor using a medical portfolio bot. Pick the SINGLE best matching command. If the message could plausibly be the start of a clinical case description, return ambiguous.

Commands:
- file_case: user wants to file or draft a clinical case ("file a case", "log a procedure", "draft a CBD")
- show_stats: user wants to see their filing stats, usage, or how many cases this month/week
- open_settings: user wants to change settings (curriculum, portfolio profile, voice profile, preferences)
- manage_credentials: user wants to change/update/reconnect their Kaizen login
- show_help: user is asking what the bot does, how to use it, what forms are supported
- chitchat: greeting, thanks, social message ("hi", "thanks", "you there")
- ambiguous: cannot determine OR could be the start of a clinical case

Message: {text}

Reply with ONE word only from the list."""

    try:
        response = (await _generate(prompt)).strip().lower()
    except Exception as e:
        logger.warning("classify_menu_intent failed: %s", e)
        return "ambiguous"

    valid = (
        "file_case",
        "show_stats",
        "open_settings",
        "manage_credentials",
        "show_help",
        "chitchat",
        "ambiguous",
    )
    for label in valid:
        if label in response:
            return label
    return "ambiguous"


async def answer_question(text: str, case_context: str = "") -> str:
    """Generate a helpful answer about the bot's capabilities.

    When case_context is provided and the question relates to form types,
    the answer is grounded in that specific case rather than being generic.
    """
    client = _get_client()

    # If the user has an active case and is asking about forms/suggestions,
    # give a case-specific answer instead of a generic list
    if case_context:
        text_lower = text.lower()
        case_question_signals = [
            "suggest", "recommend", "right", "better", "instead",
            "which", "what form", "what type", "should i", "best",
            "wrong", "not sure", "doubt",
        ]
        if any(sig in text_lower for sig in case_question_signals):
            prompt = f"""You are Portfolio Guru. The user has an active clinical case and is asking what form type would be best for it.

Active case:
\"\"\"
{case_context[:800]}
\"\"\"

User question: {text}

Analyse the case and suggest the 2-3 best RCEM WPBA form types for THIS specific case.
Available forms: CBD, DOPS, Mini-CEX, ACAT, LAT, ACAF, STAT, MSF, QIAT, JCF, Teaching, Procedural Log, SDL, Ultrasound Case, ESLE, Complaint, Serious Incident, Educational Activity, Formal Course.

Be concise. For each suggestion give the form name and a one-line reason why it fits this case."""
            text = await _generate(prompt)
            return sanitize_internal_form_codes(text.strip())

    # Check if user is asking about specific form types or capabilities
    text_lower = text.lower()
    form_keywords = ["form", "ticket", "type", "mapped", "support", "management", "cbd", "dops", "lat", "qiat", "msf", "available"]
    is_asking_about_forms = any(kw in text_lower for kw in form_keywords)

    if is_asking_about_forms:
        # Direct answer about available forms
        form_list = [
            ("CBD", "Case-Based Discussion"),
            ("DOPS", "Direct Observation of Procedural Skills"),
            ("Mini-CEX", "Mini Clinical Evaluation Exercise"),
            ("LAT", "Leadership Assessment Tool"),
            ("ACAT", "Acute Care Assessment Tool"),
            ("ACAF", "Applied Critical Appraisal Form"),
            ("STAT", "Structured Teaching Assessment Tool"),
            ("MSF", "Multi-Source Feedback"),
            ("QIAT", "Quality Improvement Assessment Tool"),
            ("JCF", "Journal Club Form"),
            ("Teaching", "Teaching Delivered by Trainee"),
            ("Procedural Log", "Procedural Log"),
            ("SDL", "Self-Directed Learning Reflection"),
            ("Ultrasound Case", "Ultrasound Case Reflection"),
            ("ESLE", "Educational Supervisor's Learning Event"),
            ("COMPLAINT", "Reflection on Complaints"),
            ("Serious Incident", "Reflection on Serious Incident"),
            ("Educational Activity", "Educational Activity Attended"),
            ("Formal Course", "Attendance at Formal Course"),
        ]

        # Check if asking about a specific form
        for form_code, form_name in form_list:
            if form_code.lower().replace("_", " ") in text_lower or form_code.lower() in text_lower:
                return sanitize_internal_form_codes(
                    f"✅ Yes, {form_code} ({form_name}) is fully supported with auto-filing to Kaizen."
                )

        # General question about what's supported
        forms_text = "\n".join([f"• {code} — {name}" for code, name in form_list[:10]])
        forms_text += f"\n• ...and {len(form_list) - 10} more"

        return sanitize_internal_form_codes(f"""📋 I support all 19 RCEM WPBA forms with full auto-filing to Kaizen:

{forms_text}

All forms are auto-filled with structured data and saved as drafts in Kaizen.

Describe your case or activity and I'll recommend the right form.""")

    # General question — use AI but with grounded facts
    prompt = f"""You are Portfolio Guru, a Telegram bot that helps RCEM doctors file their clinical cases to the Kaizen e-portfolio.

Answer this question about what you do. Be concise and helpful. Key facts:
- You accept case descriptions via text, voice note, photo, or document (PDF, Word, PowerPoint)
- You support all 19 RCEM WPBA forms: CBD, DOPS, Mini-CEX, ACAT, LAT, ACAF, STAT, MSF, QIAT, JCF, Teaching, Procedural Log, SDL, Ultrasound Case, ESLE, Complaint, Serious Incident, Educational Activity, Formal Course
- All 19 forms have full auto-filing to Kaizen — data is extracted and forms are filled automatically
- The draft is shown for review before filing
- Nothing is submitted to a supervisor - only saved as a draft
- Credentials are encrypted and never shared

Question: {text}

Answer concisely. If the question is about a specific form type, confirm it's supported."""

    text = await _generate(prompt)
    return sanitize_internal_form_codes(text.strip())


async def assess_case_sufficiency(case_description: str) -> dict:
    """Check if a case has enough detail for a quality portfolio entry.
    Returns {"sufficient": True/False, "questions": ["...", "..."]}."""
    prompt = f"""You are a medical portfolio assistant. A doctor has described a clinical case for their e-portfolio entry.
Assess whether the description contains enough detail to write a high-quality entry.

A sufficient case should mention most of:
- What the patient presented with
- What the doctor did (assessment, investigations, management)
- Clinical reasoning (why they made those decisions)
- What they learned or would do differently

Case description:
{case_description}

If the case has enough detail, return: {{"sufficient": true, "questions": []}}
If the case is too thin, return: {{"sufficient": false, "questions": ["specific question 1", "specific question 2"]}}

Rules:
- Ask 2-3 specific questions about what's missing - not generic "tell me more"
- Questions should target the specific gaps: missing reasoning, missing outcome, missing reflection, etc.
- Return ONLY the JSON. No explanation."""

    text = await _generate(prompt)
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {"sufficient": True, "questions": []}
    if "sufficient" not in data:
        data["sufficient"] = True
    if "questions" not in data or not isinstance(data["questions"], list):
        data["questions"] = []
    return data


def _humanize_text(text: str) -> str:
    """Remove AI-sounding phrases from any narrative text field.
    Applied to all narrative fields BEFORE the user sees the draft."""
    if not text or len(text) < 20:
        return text
    result = text
    # Replace em dashes with regular dashes
    result = re.sub(r"\s*—\s*", " - ", result)
    # Replace double hyphens (AI em-dash approximation)
    result = re.sub(r"\s*--\s*", " - ", result)
    # Remove slop words/phrases
    for pattern in SLOP_PATTERNS[1:]:  # skip em dash pattern (already handled)
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    # Fix orphaned commas and double spaces from removals
    result = re.sub(r",\s*,", ",", result)
    result = re.sub(r"\.\s*\.", ".", result)
    result = re.sub(r"  +", " ", result)
    # Fix sentences starting with lowercase after removal
    result = re.sub(r"\.\s+([a-z])", lambda m: ". " + m.group(1).upper(), result)
    result = result.strip()
    return result


def _portfolio_quality_polish(text: str) -> str:
    """Apply small, clinically safer wording fixes before preview.

    This is deliberately conservative: it removes phrases that make portfolio
    drafts sound punitive or overconfident without changing the case facts.
    """
    if not text or len(text) < 10:
        return text

    result = text
    replacements = [
        (r"\bwrong judgement\b", "initial judgement that changed after senior discussion"),
        (r"\bwrong judgment\b", "initial judgement that changed after senior discussion"),
        (r"\bbad judgement\b", "initial judgement that needed senior challenge"),
        (r"\bbad judgment\b", "initial judgement that needed senior challenge"),
        (r"\bmistake\b", "learning point"),
        (r"\brocket train\b", "Rocket drain"),
        (r"\bchest strain\b", "chest drain"),
        (
            r"\b(?:without|with no) (?:any )?septations?,?\s*(?:which )?(?:suggesting|suggested|meaning|meant) (?:it (?:is|was) )?(?:a )?transudative effusion\b",
            "without septations, supporting a non-complex effusion; the wider clinical picture still needed to guide the likely cause and treatment",
        ),
        (
            r"\bno septations? = transudative\b",
            "no septations supported a non-complex effusion, but did not prove the underlying cause",
        ),
        (
            r"\badmit(?:ted)? under ITU on board\b",
            "discussed with ITU/HDU because of the oxygen requirement, with medical admission kept under review",
        ),
        (
            r"\badmit(?:ted)? under ITU on-board\b",
            "discussed with ITU/HDU because of the oxygen requirement, with medical admission kept under review",
        ),
    ]
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    result = re.sub(r"  +", " ", result)
    return result.strip()


def _deidentify_portfolio_text(text: str) -> str:
    """Remove common third-party/person-identifying details from narrative fields."""
    if not text or len(text) < 10:
        return text

    result = text
    # Names of clinicians/third parties should not appear in portfolio prose.
    result = re.sub(
        r"\bDr\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b",
        "the doctor",
        result,
    )
    result = re.sub(
        r"\b(?:Mr|Mrs|Ms|Miss)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b",
        "the patient",
        result,
    )

    tertiary_centre_patterns = [
        r"\bRoyal Brompton(?: Hospital)?\b",
        r"\bGreat Ormond Street(?: Hospital)?\b",
        r"\bSt Thomas'? Hospital\b",
        r"\bGuy'?s Hospital\b",
        r"\bKing'?s College Hospital\b",
    ]
    for pattern in tertiary_centre_patterns:
        result = re.sub(pattern, "a tertiary centre", result, flags=re.IGNORECASE)

    # Rare historical specifics identify patients surprisingly quickly.
    result = re.sub(
        r"\b(last|previous|initial|first|second|third)\s+(repair|operation|surgery|procedure)\s+in\s+(19|20)\d{2}\b",
        r"\1 \2 previously",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(r"  +", " ", result)
    return result.strip()


def _humanize_reflection(text: str) -> str:
    """Legacy alias — calls _humanize_text."""
    return _humanize_text(text)


def _humanize_all_fields(data: dict) -> dict:
    """Apply humanizer to all narrative text fields in a draft dict.
    Non-narrative fields (dates, dropdowns, names, lists) are left untouched."""
    for key, value in data.items():
        if key in _HUMANIZE_FIELDS and isinstance(value, str) and len(value) > 20:
            data[key] = _deidentify_portfolio_text(
                _portfolio_quality_polish(_humanize_text(value))
            )
    return data


_IMAGE_RECOMMENDER_GUARD = """
===== IMAGE-DERIVED INPUT GUARD =====
This case was extracted from a photo/screenshot. The text below contains
only what was visible in the image — it is NOT a free-text case write-up.

Rules for image-derived input:
- Do not invent clinical context. Do not fabricate management decisions,
  resuscitation steps, drugs, or outcomes that the source text does not
  explicitly contain.
- Image evidence is typically procedural, imaging-finding, or note-fragment
  evidence — rarely a full patient management discussion.
- PREFER procedure / reflection / imaging forms: PROC_LOG, DOPS, US_CASE,
  REFLECT_LOG, COMPLAINT, SERIOUS_INC, ESLE.
- Only suggest CBD if the source explicitly documents the trainee's clinical
  reasoning, differentials, management decisions and outcomes. A procedure
  note, imaging finding, or short observation IS NOT a CBD.
- If the source is sparse and procedural (e.g. a regional block note, an
  X-ray finding), PROC_LOG or DOPS should appear before any case-management
  form, and REFLECT_LOG is preferable to CBD.
"""


async def recommend_form_types(case_description: str, input_source: str = "text") -> List[FormTypeRecommendation]:
    """Recommend applicable WPBA form types based on case description.

    `input_source` is "text", "voice", "photo"/"image", or "document". For
    image-derived input the prompt adds a source-grounding guard and biases
    recommendations toward procedure/reflection forms rather than CBD — image
    evidence is usually a procedure or imaging finding, not a managed case.
    """
    system_prompt = """You are an expert RCEM portfolio advisor. Analyse the clinical or educational event described and
recommend the 1-3 most appropriate RCEM Kaizen WPBA form types.

=== AUTHORITATIVE FORM DEFINITIONS (from rcemcurriculum.co.uk official guidance) ===

CBD (Case-Based Discussion)
- Purpose: Assess the trainee's management of a specific patient — clinical reasoning, decision-making,
  application of medical knowledge. Should focus on a written record (case notes, discharge summary,
  clinic letter).
- Requires: A specific patient case the trainee managed. Retrospective discussion with an assessor.
- NOT for: Procedures performed (use DOPS/PROC_LOG), bedside observations (use Mini-CEX),
  shift-level performance (use ESLE or ACAT), teaching activities (use STAT/TEACH).
- Suggest when: Trainee describes managing a patient, making clinical decisions, or wants to discuss
  their clinical reasoning on a case.

Mini-CEX (Mini-Clinical Evaluation Exercise)
- Purpose: Evaluate a clinical encounter — history taking, examination, clinical reasoning — with
  IMMEDIATE feedback. The assessor directly observes the trainee with the patient in real time.
- Requires: Assessor present at the bedside or in the consultation, watching the trainee with the patient.
- NOT for: Retrospective case discussion (use CBD), full shift observation (use ESLE/ACAT),
  procedures (use DOPS).
- Suggest when: Trainee was directly observed by someone during a patient interaction — seeing
  a patient while a consultant watched, or a bedside teaching scenario where competence was assessed.

DOPS (Direct Observation of Procedural Skills)
- Purpose: Assess performance of a specific practical procedure against a structured checklist.
  Immediate feedback on strengths and areas to develop.
- Requires: Trainee personally performed a hands-on procedure AND an assessor observed it.
- NOT for: Logging procedures without an observer (use PROC_LOG), clinical reasoning (use CBD).
- Suggest when: Trainee performed intubation, central line, LP, chest drain, arterial line, IO access,
  cardioversion, pericardiocentesis, or any procedural skill and had an assessor watching.

PROC_LOG (Procedural Log)
- Purpose: Log a procedure performed. Lighter than DOPS — no direct assessor observation required.
- Requires: Trainee performed a procedure.
- NOT for: Replacing DOPS when an assessor was present (prefer DOPS for assessed procedures).
- Suggest when: Trainee performed a procedure but no formal assessment occurred, or wants to log
  volume of procedures.

ACAT (Acute Care Assessment Tool)
- Purpose: Assess a doctor's performance during an acute medical take or a period of acute care
  involving MULTIPLE patients. Covers clinical decision-making, prioritisation, and management across
  a session or ward round.
- Requires: Assessor observed trainee managing multiple patients over a period (e.g. clerking shift,
  acute take, busy resus session, ward round).
- NOT for: Single patient cases (use CBD or Mini-CEX), procedures (use DOPS), teaching.
- Suggest when: Trainee describes a full shift, a resus session involving multiple patients, managing
  the department, or a clinical period where several patients were seen.

ESLE (Extended Supervised Learning Event)
- Purpose: Observe NON-TECHNICAL SKILLS across a substantial shift period (~2-3 hours minimum).
  Covers 4 NTS domains ONLY: (1) Management & Supervision, (2) Teamwork & Cooperation,
  (3) Decision Making, (4) Situational Awareness. Assessor is SUPERNUMERARY (not in clinical numbers).
  First ESLE must be within first 3 months of training year.
- Requires: Assessor physically present and watching the trainee work for a substantial part of a shift.
  The assessment spans multiple interactions/cases. Debrief takes ~1 hour after observation.
- NOT for: Individual case write-ups. Not for single clinical encounters. Not for "learning from" a case.
  The word "learning" in a description does NOT trigger ESLE.
- Suggest when: Description explicitly mentions shift-level observation, NTS feedback, a consultant
  watching them work across a session, or the specific NTS domains listed above.

MSF (Multi-Source Feedback)
- Purpose: Collect 360-degree feedback on generic professional skills (communication, leadership,
  teamwork, reliability) from multiple colleagues — doctors, nurses, allied health professionals,
  admin staff. Trainee does not see individual responses. Feedback given by Educational Supervisor.
- Requires: Trainee wants to initiate a formal MSF round with multiple raters.
- NOT for: Feedback from a single person, individual case feedback, teaching feedback.
- Suggest when: Trainee explicitly mentions wanting colleague feedback, requesting 360 feedback,
  or has been asked to do MSF by their ES.

LAT (Leadership Assessment Tool)
- Purpose: Assess leadership skills in a specific situation — multi-professional resus, EPIC role,
  handover, chairing a meeting, QI leadership. Uses EMLeaders Framework. Can be used in sim,
  clinical, or non-clinical settings. Includes self-reflection (Part 1 by trainee) + assessor feedback (Part 2).
- Requires: Trainee was in a leadership role in a specific identifiable situation.
- NOT for: General clinical care without a leadership element, observer roles, teaching.
- Suggest when: Trainee led a resus, led a trauma call, was shift co-ordinator or EPIC doctor,
  chaired a clinical meeting, managed a major incident as team leader, led a handover.

ACAF (Applied Critical Appraisal Form)
- Purpose: Structured evidence-based medicine form. Trainee identifies a clinical question from
  practice, performs a literature search (PICO), evaluates the evidence, and applies it.
- Requires: Trainee searched the literature to answer a clinical question arising from their work.
- NOT for: Cases where no literature search occurred, general reflections on practice.
- Suggest when: Trainee searched PubMed/guidelines/literature for evidence about a clinical question,
  reviewed a paper, or conducted critical appraisal relevant to their practice.

JCF (Journal Club Form)
- Purpose: Document a formal journal club presentation — where trainee presents and discusses
  a paper to a group.
- Requires: Trainee presented a paper at a formal journal club meeting.
- NOT for: Informal discussion of papers, self-directed reading, literature searches (use ACAF).
- Suggest when: Trainee presented at journal club, led an evidence-based discussion with colleagues
  in a formal educational meeting.

STAT (Structured Teaching Assessment Tool)
- Purpose: Assess a formal teaching session delivered by the trainee — face-to-face or online,
  any setting. Includes bedside teaching, simulation sessions, lectures, tutorials.
- Requires: Trainee DELIVERED a teaching session and an assessor was present to observe it.
- NOT for: Teaching someone informally during patient care (TEACH form), attending a teaching
  session as a learner (EDU_ACT).
- Suggest when: Trainee delivered a formal teaching session (bedside, simulation, lecture, tutorial)
  with an assessor observing.

TEACH (Teaching Delivered by Trainee)
- Purpose: Record teaching delivered during routine clinical work — bedside teaching, supervising
  a junior, informal opportunistic teaching. Lower-threshold than STAT; no formal observation required.
- Requires: Trainee taught or supervised a colleague or junior.
- NOT for: Formal observed teaching sessions (use STAT), attending teaching as a learner.
- Suggest when: Trainee mentored a junior, taught at the bedside, supervised a procedure, or
  delivered opportunistic clinical teaching.

TEACH_OBS (Teaching Observation Tool)
- Purpose: Structured feedback on the trainee's competence at teaching, provided by an observer.
  Process is trainee-led. For formal observed teaching.
- Requires: An assessor observed the trainee's teaching session specifically to give feedback on
  the TEACHING SKILL, not just the content.
- NOT for: Recording that teaching happened (use TEACH), content-focused sessions.
- Suggest when: Trainee wants formal feedback on their teaching ability, had an assessor observe
  and evaluate their teaching style and skills.

QIAT (Quality Improvement Assessment Tool)
- Purpose: Assess a QI project or audit — problem analysis, methodology, measurement, team
  working, stakeholder engagement. Assessed by a supervisor.
- Requires: Trainee completed or contributed to a QI project or audit.
- NOT for: Clinical care, individual cases, teaching. CANNOT be used as a Management Portfolio
  assignment (separate requirement).
- Suggest when: Trainee completed a QI project, an audit, a re-audit, or led/contributed to
  a quality improvement initiative.

SDL (Self-Directed Learning Reflection)
- Purpose: Record and reflect on self-directed learning — reading, online modules, podcasts,
  videos, independent study.
- Requires: Trainee completed a learning activity independently (not a formal course or teaching session).
- NOT for: Formal courses (use FORMAL_COURSE), formal teaching received (use EDU_ACT).
- Suggest when: Trainee completed RCEMLearning module, read a paper independently, listened
  to a medical podcast, watched an educational video, or did self-study.

EDU_ACT (Educational Activity Attended)
- Purpose: Record a teaching session, lecture, or educational event attended as a LEARNER.
- Requires: Trainee attended an educational event (departmental teaching, grand round, lecture,
  educational meeting, simulation day as a participant).
- NOT for: Events where trainee was the TEACHER (use STAT/TEACH), formal courses with
  certificates (use FORMAL_COURSE).
- Suggest when: Trainee attended a teaching session, departmental meeting, educational grand
  round, or learning event as a participant.

FORMAL_COURSE (Attendance at Formal Course)
- Purpose: Document completion of a formal course — ALS, ATLS, APLS, ALSO, leadership
  courses, simulation courses with formal certification.
- Requires: Trainee attended a structured course with defined learning objectives, typically
  resulting in a certificate.
- NOT for: Informal teaching, departmental educational sessions (use EDU_ACT).
- Suggest when: Trainee completed ALS, ATLS, APLS, ALSO, human factors course, simulation
  course, leadership training day, or any certified course.

US_CASE (Ultrasound Case Reflection)
- Purpose: Document and reflect on a specific point-of-care ultrasound (POCUS) case.
- Requires: Trainee performed or interpreted a POCUS scan.
- NOT for: General imaging discussion, CT/MRI, formal radiology.
- Suggest when: Trainee performed FAST scan, cardiac echo, lung ultrasound, IVC assessment,
  vascular access guidance, or any POCUS in clinical practice.

ESLE_ASSESS (ESLE Part 1 & 2 — 2025 Update)
- Purpose: The formal assessed ESLE with structured Part 1 (event timeline) and Part 2 (NTS review).
  Requires two assessors including Educational Supervisor.
- Same context requirements as ESLE above. Use ESLE_ASSESS when the description suggests
  a formal, dual-assessor ESLE with both parts to complete.

REFLECT_LOG (Reflective Practice Log)
- Purpose: General reflective entry — thoughts on clinical practice, professional development,
  or any learning experience that doesn't fit a specific form.
- NOT for: If a more specific form clearly fits, use that instead.
- Suggest when: Trainee wants to reflect generally and no other specific form clearly applies.

COMPLAINT (Reflection on a Patient Complaint)
- Purpose: Reflect on a patient complaint — what happened, response, learning.
- Requires: An actual patient complaint was made about or involving the trainee's care.
- Suggest when: Trainee is reflecting on a formal complaint from a patient or relative.

SERIOUS_INC (Reflection on Serious Incident)
- Purpose: Reflect on a serious incident or never event.
- Requires: A formally declared serious incident or never event.
- Suggest when: Trainee was involved in a serious incident investigation or a never event.

MGMT_* (Management Portfolio forms — Rota, Complaint, Critical Incident, Risk, Project, etc.)
- Purpose: Document completion of a specific management activity as part of the Management
  Portfolio requirement (mandatory 4 assignments for ST3-6 in EM posts).
- Requires: Trainee has actually completed the management activity described.
- Suggest MGMT_ROTA when: Involved in rota planning/management for the department.
- Suggest MGMT_RISK when: Contributed to the departmental risk register.
- Suggest MGMT_RECRUIT when: Participated in recruitment processes — ST4/specialty recruitment panels, shortlisting, interviewing, scoring candidates, ranking, or developing recruitment materials.
- Suggest MGMT_PROJECT when: Led or completed a non-QI project.
- Suggest MGMT_GUIDELINE when: Introduced, reviewed, or updated a clinical guideline.
- Suggest MGMT_COMPLAINT (management version) when: Managed a patient complaint process
  from the management perspective (root cause, response, actions) — distinct from COMPLAINT
  (personal reflection).
- Suggest CRIT_INCIDENT when: Managed a critical incident investigation using root cause analysis.
- Suggest CLIN_GOV when: Attended and contributed to clinical governance meetings over 6 months.
- Suggest BUSINESS_CASE when: Wrote or contributed to a formal business case.
- Suggest COST_IMPROVE when: Led or contributed to a cost improvement / efficiency initiative.
- Suggest EQUIP_SERVICE when: Introduced a new piece of equipment or a new service.
- Suggest APPRAISAL when: Formally appraised a junior colleague.
- Suggest TEACH_CONFID when: Delivered teaching on confidentiality or data protection.

=== DECISION RULES ===

1. Match the form to what ACTUALLY HAPPENED, not to keywords. "Significant learning" ≠ ESLE.
   "Taught someone" alone ≠ STAT (need a formal observed session). "Complicated case" alone ≠ ESLE.

2. Maximum 3 suggestions. Suggest fewer if only 1-2 clearly fit.

3. Do NOT default to CBD for every case. CBD is appropriate for clinical case management discussions —
   if the description is purely a procedure, a teaching session, or a shift-level observation, CBD is wrong.
   If the trainee is REFLECTING on a case (describing what they learned, what they'd do differently,
   cognitive bias, professional development) — REFLECT_LOG is the PRIMARY suggestion, not CBD.
   CBD and REFLECT_LOG can both appear, but reflection-framed descriptions → REFLECT_LOG first.

4. ESLE is one of the hardest to trigger correctly. Only suggest it if the description explicitly mentions
   shift-level observation, a consultant watching across multiple cases/interactions, or NTS feedback.
   A single case — however complex — does not warrant ESLE.

5. Prefer specificity. If DOPS clearly applies, suggest DOPS over CBD. If US_CASE applies,
   suggest it over CBD. CBD is a fallback for case management when no more specific form fits.

6. For teaching: distinguish between STAT (formal, observed, assessor evaluating teaching),
   TEACH (informal/bedside, no formal observation needed), and EDU_ACT (trainee was the learner).

7. Reflection signals — if the description contains ANY of these, include REFLECT_LOG in suggestions:
   - "I learned", "I realise", "I now know", "I would do differently", "fixation bias", "cognitive bias",
   - "missed", "overlooked", "on reflection", "looking back", "this taught me", "I reflect"
   - Describing a case where something went wrong and the trainee is processing it
   In these cases REFLECT_LOG should appear first OR alongside CBD — never be omitted.

8. Return ONLY a JSON array. No markdown, no explanation outside the JSON.
   Format: [{"form_type": "CBD", "rationale": "one-line reason specific to this case"}, ...]

=== END DEFINITIONS ==="""

    system_prompt += "\n" + _PORTFOLIO_SKILL_QUALITY_RUBRIC
    if _is_image_source(input_source):
        system_prompt += "\n" + _IMAGE_RECOMMENDER_GUARD

    prompt = f"{system_prompt}\n\nCase description:\n{case_description}"
    text = await _generate(prompt)
    raw = text.strip()

    # Strip markdown code fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = []

    recommendations = []
    for item in data[:3]:  # Max 3
        form_type = canonical_form_type(item.get("form_type", "CBD"))
        recommendations.append(FormTypeRecommendation(
            form_type=form_type,
            rationale=item.get("rationale", ""),
            uuid=FORM_UUIDS.get(form_type)
        ))

    if _is_image_source(input_source):
        recommendations = enforce_image_recommendation_grounding(
            recommendations, case_description
        )

    return recommendations


def combine_case_inputs(initial: str, additions: list) -> str:
    """Combine initial case text with accumulated additions for re-extraction."""
    parts = [initial.strip()]
    for addition in additions:
        text = addition.strip() if isinstance(addition, str) else str(addition).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _missing_text_value(leave_missing_blank: bool, fallback: str = "Not mentioned in case") -> str:
    return "" if leave_missing_blank else fallback


def _normalise_text_field(value, leave_missing_blank: bool, fallback: str = "Not mentioned in case"):
    if value is None:
        return _missing_text_value(leave_missing_blank, fallback)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or cleaned.lower() in {"not mentioned in case", "to be added", "not specified"}:
            return _missing_text_value(leave_missing_blank, fallback)
        return cleaned
    return str(value).strip()


def _normalise_for_similarity(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _is_absolute_no_outcome(text: str) -> bool:
    """Detect the too-absolute 'No, the clinical outcome would remain the same'
    pattern that should be replaced with softer communication-quality framing
    when the case supports it."""
    lower = text.lower().strip()
    return (
        lower.startswith("no,")
        or lower.startswith("no.")
        or "clinical outcome would remain" in lower
        or "outcome would not be different" in lower
        or "outcome would remain the same" in lower
        or "outcome would be the same" in lower
    )


def _fields_are_repetitive(first: str, second: str) -> bool:
    """Detect reflection fields that say the same thing with minor grammar changes."""
    from difflib import SequenceMatcher

    a = _normalise_for_similarity(first)
    b = _normalise_for_similarity(second)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    a_words = {word for word in a.split() if len(word) > 4}
    b_words = {word for word in b.split() if len(word) > 4}
    if len(a_words & b_words) >= 3 and SequenceMatcher(None, a, b).ratio() >= 0.55:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.72


_REFLECT_LOG_ED_TERMS = (
    " ed ", "ed,", "ed.", "(ed)", " in ed",
    "emergency department", "ed patient", "a&e", "a & e", "accident and emergency",
)

_REFLECT_LOG_ACUTE_ED_TERMS = (
    "stemi", "st elevation", "st-elevation", "acs", "nstemi",
    "catheterisation lab", "catheterization lab", "cath lab", "pci",
    "resus", "resuscitation", "999", "ambulance", "blue light",
    "sepsis 6", "sepsis6", "surgical registrar", "ed senior",
)


def _polish_reflect_log_fields(fields: dict, case_description: str) -> dict:
    """Keep Reflective Practice Log action fields distinct without inventing case facts.

    Derives title, why, different_outcome, focussing_on, and event_type from
    supported sepsis/surgical-referral case facts when the LLM output is
    repetitive or missing. Never introduces clinical facts absent from the
    case description."""
    polished = dict(fields)
    combined = (f"{case_description} " + " ".join(str(fields.get(k, "")) for k in fields)).lower()

    is_sepsis = any(term in combined for term in ("sepsis", "septic", "sirs", "sepsis6", "sepsis 6"))
    is_surgical_ref = any(term in combined for term in ("handover", "refer", "referral", "surgical"))
    is_dual = is_sepsis and is_surgical_ref
    is_stemi = any(term in combined for term in ("stemi", "st elevation", "st-elevation", "acs", "nstemi", "pci", "angiogram"))
    has_communication_context = any(term in combined for term in ("communicat", "patient understand", "anxiety", "explain", "famil", "relative"))
    has_ed_context = _has_reflect_log_ed_context(combined)

    # Replace absolute "No, the clinical outcome would remain the same" with softer
    # communication-quality framing when the case is STEMI/ACS or has clear
    # communication context. Applies before all other derivation so it takes effect
    # regardless of which sepsis/surgical path the rest of the function follows.
    _early_diff = str(polished.get("different_outcome") or "").strip()
    if _early_diff and _is_absolute_no_outcome(_early_diff) and (is_stemi or has_communication_context):
        polished["different_outcome"] = (
            "The clinical escalation was appropriate, but clearer communication "
            "may have improved patient understanding and reduced anxiety."
        )

    if not is_sepsis and not is_surgical_ref:
        replay = str(polished.get("replay_differently") or "").strip()
        focus = str(polished.get("focussing_on") or "").strip()
        repetitive_focus = (
            replay and focus and _fields_are_repetitive(replay, focus)
        )
        # event_type still derived for plain ED encounters even when no
        # sepsis/surgical signal is present.
        polished = _polish_reflect_log_event_type(polished, has_ed_context)
        if repetitive_focus:
            polished["focussing_on"] = (
                "I am turning this into a specific next-shift habit: state the concern early, "
                "name the uncertainty, and agree the next escalation or review point."
            )
            return polished
        return polished if polished != fields else fields

    replay = str(polished.get("replay_differently") or "").strip()

    # --- Date derivation ---
    event_date = str(polished.get("date_of_event") or "").strip()
    encounter_date = str(polished.get("date_of_encounter") or "").strip()
    if not event_date and encounter_date:
        polished["date_of_event"] = encounter_date

    # --- Title derivation ---
    title = str(polished.get("reflection_title") or "").strip()
    if not title:
        if is_dual:
            polished["reflection_title"] = (
                "Balancing sepsis recognition with a structured surgical referral"
            )
        elif is_sepsis:
            polished["reflection_title"] = "Recognition and initial management of sepsis"
        else:
            polished["reflection_title"] = "Structured referral and communication"

    # --- Why derivation ---
    why = str(polished.get("why") or "").strip()
    if not why or _fields_are_repetitive(why, replay):
        if is_dual:
            polished["why"] = (
                "Time-critical sepsis treatment has to run in parallel with a clear, "
                "structured surgical referral so the receiving team grasps the urgency "
                "and the source-control question without losing time to repeat calls or "
                "missed context."
            )
        elif is_sepsis:
            polished["why"] = (
                "Sepsis recognition requires mental commitment to source identification "
                "and time-critical treatment, not just treatment initiation."
            )
        else:
            polished["why"] = (
                "Unstructured referrals can mask urgency and delay the receiving "
                "team's response or lead to repeated calls."
            )

    # --- different_outcome derivation ---
    diff_outcome = str(polished.get("different_outcome") or "").strip()
    if not diff_outcome or _fields_are_repetitive(diff_outcome, replay):
        if is_dual:
            polished["different_outcome"] = (
                "A structured referral that names the physiology, the sepsis treatment "
                "already started, the working diagnosis and the specific decision needed "
                "would give the surgical team a shared mental model, making the urgency "
                "explicit and the next decision quicker and safer, even where the patient's "
                "clinical course stays the same."
            )
        elif is_sepsis:
            polished["different_outcome"] = (
                "Clearer source identification and earlier antibiotics could "
                "reduce the time to definitive treatment."
            )
        else:
            polished["different_outcome"] = (
                "A structured referral that states the working diagnosis, concerns, "
                "and the specific decision needed could result in a faster, more "
                "targeted response from the receiving team."
            )

    # --- focussing_on derivation ---
    focus = str(polished.get("focussing_on") or "").strip()
    if not focus or _fields_are_repetitive(focus, replay):
        if is_dual:
            polished["focussing_on"] = (
                "I am practising a short SBAR-style surgical referral that states the "
                "physiology and key vitals, the sepsis treatment already started (fluids, "
                "antibiotics, analgesia), the working diagnosis and investigations requested, "
                "and the specific decision I need from the surgical team, naming the urgency "
                "explicitly."
            )
        elif is_surgical_ref:
            polished["focussing_on"] = (
                "I am practising a concise SBAR-style referral that states the working diagnosis, "
                "current concerns, treatment already started, and the decision I need from the receiving team."
            )
        elif is_sepsis:
            polished["focussing_on"] = (
                "I am building a habit of treating sepsis early while explicitly naming the likely source, "
                "the treatment already started, and the next review point."
            )
        else:
            polished["focussing_on"] = (
                "I am turning this into a specific next-shift habit: state the concern early, "
                "name the uncertainty, and agree the next escalation or review point."
            )

    polished = _polish_reflect_log_event_type(polished, has_ed_context)

    return polished


def _polish_reflect_log_event_type(polished: dict, has_ed_context: bool) -> dict:
    """Default the Kaizen "Type of event/circumstances" select to an ED option
    when the reflection clearly describes an ED clinical encounter. Leaves any
    non-empty user-supplied value alone."""
    if not has_ed_context:
        return polished
    existing = str(polished.get("event_type") or "").strip()
    if existing:
        return polished
    polished["event_type"] = "ED patient"
    return polished


def _has_reflect_log_ed_context(text: str) -> bool:
    """Return true when the source supports Kaizen's ED event circumstance.

    Direct ED wording is preferred. A short whitelist of time-critical EM
    pathways is also accepted because beta dogfood showed photo-derived STEMI
    reflections can omit the literal word "ED" while still describing an ED
    presentation and transfer pathway.
    """
    lower = (" " + str(text or "").lower() + " ")
    return (
        any(term in lower for term in _REFLECT_LOG_ED_TERMS)
        or any(term in lower for term in _REFLECT_LOG_ACUTE_ED_TERMS)
    )


def _normalise_list_field(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    cleaned = str(value).strip()
    return [cleaned] if cleaned else []


def _normalise_dropdown_field(value, options: list, leave_missing_blank: bool):
    cleaned = _normalise_text_field(value, leave_missing_blank, "")
    if cleaned in options:
        return cleaned
    return "" if leave_missing_blank else (options[0] if options else "")


def _kc_code_prefix(kc_string: str) -> str:
    head = str(kc_string or "").split(":", 1)[0]
    return "".join(head.lower().split())


def _derive_curriculum_links_from_kcs(kcs: List[str]) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for kc in kcs or []:
        match = re.search(r"SLO\s*(\d+)", str(kc), flags=re.IGNORECASE)
        if not match:
            continue
        code = f"SLO{int(match.group(1))}"
        if code not in seen:
            links.append(code)
            seen.add(code)
    return links


def _source_supports_qi_curriculum(case_description: str, schema_key: str) -> bool:
    text = (case_description or "").lower()
    qi_terms = (
        "qiat", "quality improvement", " qi ", "qi project", "run chart",
        "run-chart", "audit", "pdsa", "quality and safety", "patient safety",
        "clinical governance", "governance",
    )
    return schema_key == "QIAT" or any(term in f" {text} " for term in qi_terms)


def _supplement_supported_key_capabilities(
    fields: dict,
    *,
    case_description: str,
    schema_key: str,
    has_kc_tick: bool,
) -> dict:
    """Add narrow, source-tied KCs when a curriculum form under-tags a draft."""
    if not has_kc_tick:
        return fields

    existing = _normalise_list_field(fields.get("key_capabilities"))
    if len(existing) >= 3:
        fields["curriculum_links"] = _derive_curriculum_links_from_kcs(existing)
        return fields

    supplemented = list(existing)
    present_prefixes = {_kc_code_prefix(kc) for kc in existing}

    if _source_supports_qi_curriculum(case_description, schema_key):
        for code in ("SLO11 KC1", "SLO11 KC2", "SLO12 KC2"):
            full_text = KC_FULL_TEXT[code]
            prefix = _kc_code_prefix(full_text)
            if prefix in present_prefixes:
                continue
            supplemented.append(full_text)
            present_prefixes.add(prefix)

    if supplemented != existing:
        fields["key_capabilities"] = supplemented
    if supplemented:
        fields["curriculum_links"] = _derive_curriculum_links_from_kcs(supplemented)
    return fields


def _dropdown_uses_exact_training_years(options: list[str]) -> bool:
    return any(re.fullmatch(r"(?:ST|CT)\d+(?:/CT\d+)?", str(option)) for option in options or [])


def _exact_training_year_mentioned(source_text: str, value: str) -> bool:
    match = re.search(r"(ST|CT)(\d+)", str(value or ""), flags=re.IGNORECASE)
    if not match:
        return False
    prefix, year = match.group(1).upper(), match.group(2)
    source = source_text or ""
    exact_patterns = [
        rf"\b{prefix}\s*{year}\b",
        rf"\b{prefix}-{year}\b",
    ]
    if prefix == "ST":
        exact_patterns.extend([
            rf"\bstage\s*{year}\b",
            rf"\bspecialty\s+trainee\s*{year}\b",
        ])
    return any(re.search(pattern, source, flags=re.IGNORECASE) for pattern in exact_patterns)


def _guard_unsourced_exact_training_stage(fields: dict, schema: dict, source_text: str) -> dict:
    stage_field = next(
        (field for field in schema.get("fields", []) if field.get("key") == "stage_of_training"),
        None,
    )
    if not stage_field:
        return fields
    options = stage_field.get("options") or []
    if not _dropdown_uses_exact_training_years(options):
        return fields
    value = str(fields.get("stage_of_training") or "").strip()
    if not value or not re.search(r"\b(?:ST|CT)\d+", value, flags=re.IGNORECASE):
        return fields
    if _exact_training_year_mentioned(source_text, value):
        return fields
    guarded = dict(fields)
    guarded["stage_of_training"] = ""
    return guarded


_IMAGE_EXTRACTOR_GUARD = """
===== IMAGE-DERIVED INPUT GUARD (NON-NEGOTIABLE) =====
The case description below was extracted from a photo/screenshot — it
contains ONLY what was visible in the image, not a doctor's free-text case.
- Do NOT invent clinical context, management decisions, drugs, outcomes,
  reflections, or learning points that are not explicitly present in the
  source text.
- Do NOT extrapolate from imaging findings into resuscitation, ATLS,
  trauma-team, CPR, defibrillation, ALS, ROSC, CT head, or coronary
  angiography narrative unless those words appear verbatim in the source.
- Leave narrative fields (clinical_reasoning, reflection, description,
  learning_points, etc.) BLANK ("") if the source does not contain content
  for them. A blank field is correct — the doctor will fill it themselves
  in Kaizen. Fabricating content is the worst failure mode.
"""


_CASE_SYNTHESIS_GUIDE = """
===== CASE SYNTHESIS QUALITY BAR =====
Treat the case description as one evidence bundle. It may contain the doctor's
free-text story, voice transcription artefacts, OCR from screenshots, and
clinical note fragments. Use all of it; do not let the first narrative drown
out later image/note evidence.

For clinical reasoning fields:
- Pull in documented objective evidence when present: investigations, BNP,
  ECG rhythm/conduction, bedside echo impression, CXR/CT findings, oxygen
  device/requirement, treatment plan, escalation, and patient communication.
- Reconcile the initial plan with the revised plan. Good CBD drafts should
  show: initial hypothesis/plan -> senior challenge or new evidence -> revised
  management and escalation.
- If pleural ultrasound says "no septations", describe it as a non-complex or
  simple-appearing effusion. Do not state that this proves a transudate unless
  the source explicitly says transudative.

For reflection fields:
- Frame judgement issues as assessable learning, not self-punishment. Avoid
  "wrong judgement", "bad judgement", and "mistake" unless quoting the user.
  Prefer "initially narrow reasoning", "procedure-first plan", "anchoring",
  "fixation", or "senior challenge changed my decision".
- Include the practice change, not just the emotion: integrate bedside
  findings, investigations and senior input before choosing an invasive
  procedure; treat the likely cause; escalate appropriately; communicate the
  plan clearly.
"""


_PORTFOLIO_SKILL_QUALITY_RUBRIC = """
===== PORTFOLIO SKILL QUALITY RUBRIC =====
This product-level rubric imports the durable drafting standards from the
Claude Code portfolio skill and the Medic Portfolio skill. Apply it as product
behaviour, not as a runtime dependency on those skills.

Form choice:
- Match what actually happened, not keywords. CBD is for single-patient
  clinical reasoning and management. DOPS is observed procedure. Procedural Log
  is self-logged procedure. Mini-CEX requires observed patient interaction.
  LAT requires a real leadership role. ACAT/ESLE require shift-level or
  multi-patient observation. Teaching forms require actual teaching.
- Prefer the most specific form before CBD. If the case is mainly POCUS, use
  Ultrasound Case Reflection unless ultrasound was only adjunct to a broader
  CBD/Mini-CEX. If it is mainly a procedure, use DOPS/Procedural Log.
- Suggest multiple entries only when the case genuinely has multiple evidence
  dimensions, such as clinical reasoning plus leadership, procedure plus CBD,
  or teaching plus procedure. Do not collapse distinct evidence into one form.

Drafting:
- Draft into the actual Kaizen field purpose, not a generic prose block.
  Case/clinical-reasoning fields should show what happened, what was considered,
  what decision was made, and why. Reflection fields should show learning and
  future behaviour change.
- Use Driscoll naturally: what happened, why it mattered, what will change.
  Do not label those sections.
- Keep only clinically useful detail. Remove padding, generic lessons and
  assessor-flattering prose.
- If a learning angle is not explicitly in the doctor's words, either leave it
  out or make it clearly grounded in the provided evidence. Never invent
  clinical facts, outcomes, timings, diagnoses or procedures.

Safety and privacy:
- Refer to third parties by role, not by name. Use "the ED consultant", "the
  medical registrar", "the nurse in charge", or "the specialty registrar".
- De-identify patients beyond names. Avoid named hospitals/tertiary centres,
  exact historic years, unusual non-clinical circumstances, and rare detail
  combinations that could identify someone.

Curriculum:
- Select Key Capabilities first, then derive SLOs. Never select a broad KC just
  because it technically fits. Prefer leaf, specific KCs that the case actually
  demonstrates. Aim for 3-6 KCs on substantive cases, but do not pad.
- Bias toward weaker portfolio domains such as leadership, QI/governance,
  research and management only when genuinely supported by the case.

Pre-preview quality check:
- Before showing a draft, scan for: wrong form type, missing objective evidence,
  unsupported clinical claims, blunt self-punitive wording, third-party names,
  over-specific patient identifiers, generic reflection, overbroad KC mapping,
  AI-tell phrasing, and long unscannable paragraphs.
"""


async def extract_cbd_data(
    case_description: str,
    edit_feedback: str = "",
    current_draft: str = "",
    voice_profile_json: str = "",
    leave_missing_blank: bool = True,
    preserve_original_content: bool = True,
    input_source: str = "text",
) -> CBDData:
    """Extract structured CBD data from free-text case description.

    `input_source` defaults to "text". When the case came from an image, the
    prompt receives _IMAGE_EXTRACTOR_GUARD and the resulting fields are run
    through enforce_image_source_grounding so any unsupported resuscitation /
    advanced-imaging narrative the LLM tries to inject is stripped before the
    user sees the draft.
    """
    missing_text_instruction = (
        'If a field cannot be filled from the case description, return an empty string "" for text/date/dropdown fields, null for nullable fields, and [] for list fields.'
        if leave_missing_blank
        else 'If a field cannot be filled from the case description, set it to "Not mentioned in case".'
    )
    preserve_instruction = (
        """
===== WORDING RULES =====
- Keep the doctor's original content exactly as provided wherever possible.
- Do not paraphrase, embellish, or "improve" explicit clinical details.
- If a sentence from the case already fits a field, copy it with only the lightest trimming needed to fit JSON.
"""
        if preserve_original_content
        else ""
    )

    today = date.today()
    yesterday = today - timedelta(days=1)
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    day_of_week = today.strftime("%A")

    system_prompt = f"""You are a medical portfolio assistant. Extract structured data from a doctor's clinical case description for a Case-Based Discussion (CBD) WPBA entry.

Today's date: {today_str} ({day_of_week}). Yesterday: {yesterday_str}.

Return ONLY a JSON object with these exact fields:
{{
  "form_type": "CBD",
  "date_of_encounter": "YYYY-MM-DD format. Resolve relative references: 'today' → {today_str}, 'yesterday' → {yesterday_str}, 'this morning' → {today_str}, 'last [weekday]' → calculate from today. Empty string only if no date can be inferred",
  "patient_age": "age as string e.g. '45-year-old'",
  "patient_presentation": "presenting complaint / chief complaint",
  "clinical_setting": "e.g. 'Emergency Department - Resus', 'Majors', 'Minors'",
  "stage_of_training": null,
  "trainee_role": "e.g. 'Primary clinician with indirect supervision'",
  "clinical_reasoning": "what the trainee thought, investigated, and did — and why",
  "reflection": "what was learned — extract from what was said, do NOT invent learning points",
  "level_of_supervision": "Direct" or "Indirect" or "Distant",
  "supervisor_name": null or "Name if mentioned",
  "curriculum_links": ["SLO1", "SLO3"],
  "key_capabilities": [
    "SLO1 KC1: to be expert in assessing and managing all adult patients attending the ED. These capabilities will apply to patients attending with both physical and psychological ill health (2025 Update)",
    "SLO1 KC2: competent in the assessment and management of adult patients who present with undifferentiated conditions (2025 Update)",
    "SLO3 KC1: able to support the pre-hospital, medical, nursing and administrative team in answering clinical questions and in making safe decisions for patients with appropriate levels of risk in the ED (2025 Update)",
    "SLO3 KC3: able to formulate safe and appropriate management plans for adult patients (2025 Update)"
  ]
}}

Stage of Training mapping:
- FY1/FY2/CT1/CT2 → "Intermediate/ST3"
- ST3 → "Intermediate/ST3"
- ST4/ST5/ST6/SpR/registrar → "Higher/ST4-ST6"
- Paediatric EM trainee → "PEM Sub-specialty"
- ACCS trainee → "ACCS ST1-ST2/CT1-CT2"
- If unclear or not mentioned → null (leave blank — do NOT guess)

===== KEY CAPABILITIES — PRIMARY SELECTION =====

The full KC list is below. Read the case, then select KCs that are DIRECTLY demonstrated.
KCs are what matter — SLOs are just grouping labels derived automatically from whichever KCs you select.

{RCEM_KC_MAP}

INSTRUCTIONS:
1. Read the full case description.
2. For each SLO that is relevant to the case, read KC2, KC3, KC4... FIRST. Ask: does this case directly demonstrate THIS specific numbered capability?
3. Only consider KC1 for an SLO after checking the higher KCs. KC1 is a broad fallback — only include it if the case demonstrates something KC2+ does not already cover for that SLO.
4. Target at least 3 Key Capabilities per case. Most clinical cases demonstrate 3-6 KCs. Select exactly what fits — but aim for adequate curriculum coverage.
5. Use the FULL KC text exactly as written above (including the "(2025 Update)" suffix).
6. Format each as: "SLO_CODE KC_NUM: full description text (2025 Update)"

KC1 RULE (critical): KC1 for most SLOs is written so broadly it technically fits any clinical case.
Do NOT select KC1 just because it "could apply". Only select KC1 if:
- The case specifically demonstrates something unique to KC1 that KC2+ does not cover, OR
- KC1 is the only KC for that SLO

Examples of KC1 being WRONG: selecting SLO1 KC1 just because a patient was assessed. Selecting SLO3 KC1 just because a decision was made. These are true of every case — they add no specificity.
Examples of KC1 being RIGHT: selecting SLO5 KC1 when the trainee performed airway management (KC1 is specific here). Selecting SLO9_RESEARCH KC1 when the trainee critically appraised evidence (only KC for research).

HARD RULES — only select if DIRECTLY demonstrated:
- Resuscitation KCs (SLO5): only if patient was actually resuscitated, intubated, arrested
- Procedure KCs (SLO6_PROC): only if trainee personally performed a named procedure
- Paediatric KCs (SLO6_PAEDS): only if patient was under 16
- Shift leadership KCs (SLO8): only if trainee explicitly led/coordinated the shift
- Teaching KCs (SLO9_TEACH): only if trainee delivered teaching or supervised a junior
- Trauma KCs (SLO4): only if patient had a traumatic injury the trainee managed

For curriculum_links: derive the SLO codes from the KCs you selected (e.g. if you pick SLO1 KC1 and SLO3 KC2, curriculum_links = ["SLO1", "SLO3"])

===== REFLECTION STYLE =====

Write the reflection in direct, first-person clinical language:
- Use "I" statements
- Be specific about learning points
- Avoid: em dashes, "delve", "navigate", "crucial", "importantly", "comprehensive", "moreover", "furthermore", "on the other hand", "in summary"

{_CASE_SYNTHESIS_GUIDE}

{_PORTFOLIO_SKILL_QUALITY_RUBRIC}

===== FORMATTING =====
- Break any narrative field (reflection, clinical_reasoning, description) into 2-3 short paragraphs if it exceeds ~80 words.
- Use natural paragraph breaks: what happened → what I did/thought → what I learned or would change.
- Never write a single block of 100+ words with no paragraph break.

===== GROUNDING RULES (NON-NEGOTIABLE) =====
- Extract ONLY what the doctor explicitly stated or clearly implied. Never invent clinical details.
- {missing_text_instruction}
- Never add diagnoses, investigations, procedures, or clinical reasoning the doctor did not describe.
- It is better to leave a field sparse than to fabricate content. Doctors will reject inaccurate drafts.
- Return ONLY the JSON. No explanation."""
    system_prompt += preserve_instruction
    if _is_image_source(input_source):
        system_prompt += "\n" + _IMAGE_EXTRACTOR_GUARD

    # Inject personal voice profile if available
    if voice_profile_json:
        from voice_profile import build_voice_instruction
        voice_block = build_voice_instruction(voice_profile_json)
        if voice_block:
            system_prompt += f"\n{voice_block}"
    else:
        system_prompt += """

===== DEFAULT WRITING STANDARD =====
Write as an experienced UK EM trainee would write their own portfolio entry:
- First person, professional but not stiff ("I assessed" not "The patient was assessed by the trainee")
- Specific clinical language without being verbose — name the condition, the investigation, the finding
- Short, direct sentences. Vary length slightly to avoid monotony.
- Reflection should sound genuine and personal, not templated — what genuinely surprised you, challenged you, or changed your practice
- Avoid: hedging phrases ("it could be argued"), academic formality ("the aforementioned"), motivational language ("this was a fantastic learning opportunity")
- British English spelling (recognised, organised, haemorrhage, paediatric)
- Sound like a confident registrar writing after a shift, not an AI summarising a textbook
"""

    prompt = f"{system_prompt}\n\nCase description:\n{case_description}"
    if edit_feedback and current_draft:
        prompt += f"\n\nCurrent draft (improve this based on the feedback below):\n{current_draft}\n\nUser feedback:\n{edit_feedback}"
    elif edit_feedback:
        prompt += f"\n\nUser feedback to apply:\n{edit_feedback}"

    text = await _generate(prompt)
    raw = text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        # Retry once with explicit instruction
        retry_prompt = f"Fix the JSON and return ONLY valid JSON. No explanation.\n\nParse error: {e}\n\nOriginal output:\n{raw}"
        retry_text = await _generate(retry_prompt)
        retry_raw = retry_text.strip()
        if retry_raw.startswith("```"):
            retry_raw = retry_raw.split("```")[1]
            if retry_raw.startswith("json"):
                retry_raw = retry_raw[4:]
        retry_raw = retry_raw.strip()
        data = json.loads(retry_raw)

    normalised = {
        "form_type": "CBD",
        "date_of_encounter": _normalise_text_field(data.get("date_of_encounter"), leave_missing_blank, ""),
        "patient_age": _normalise_text_field(data.get("patient_age"), leave_missing_blank, ""),
        "patient_presentation": _normalise_text_field(data.get("patient_presentation"), leave_missing_blank, ""),
        "clinical_setting": _normalise_dropdown_field(
            data.get("clinical_setting"),
            ["Emergency Department", "Acute Medical Ward", "Paediatric Emergency Department",
             "Intensive Care Unit", "Emergency Department Observation Unit", "Minor Injury Unit", "Other"],
            leave_missing_blank
        ),
        "stage_of_training": _normalise_text_field(data.get("stage_of_training"), leave_missing_blank, ""),
        "trainee_role": _normalise_text_field(data.get("trainee_role"), leave_missing_blank, ""),
        "clinical_reasoning": _normalise_text_field(data.get("clinical_reasoning"), leave_missing_blank, ""),
        "reflection": _normalise_text_field(data.get("reflection"), leave_missing_blank, ""),
        "level_of_supervision": _normalise_dropdown_field(
            data.get("level_of_supervision"),
            ["Direct", "Indirect", "Distant"],
            leave_missing_blank
        ),
        "supervisor_name": _normalise_text_field(data.get("supervisor_name"), leave_missing_blank, ""),
        "curriculum_links": _normalise_list_field(data.get("curriculum_links")),
        "key_capabilities": _normalise_list_field(data.get("key_capabilities")),
    }

    # Apply humanizer to ALL narrative fields before user sees the draft
    normalised = _humanize_all_fields(normalised)

    # For image-derived input, strip any resuscitation / advanced-imaging
    # narrative the LLM injected that isn't anchored in the source text.
    if _is_image_source(input_source):
        normalised, stripped = enforce_image_source_grounding(normalised, case_description)
        if stripped:
            logger.warning(
                "Stripped %d fabricated term(s) from image-derived CBD draft: %s",
                len(stripped), stripped,
            )

    return CBDData(**normalised)


async def extract_form_data(
    case_description: str,
    form_type: str,
    edit_feedback: str = "",
    current_draft: str = "",
    voice_profile_json: str = "",
    leave_missing_blank: bool = True,
    preserve_original_content: bool = True,
    input_source: str = "text",
) -> FormDraft:
    """Extract structured data for any non-CBD form type.

    `input_source` controls source-grounding: image inputs get an extra
    prompt block forbidding fabrication and have their narrative fields
    sanitised by enforce_image_source_grounding before returning.
    """
    form_type = canonical_form_type(form_type)
    # _2021 variants share draft schemas with their current-curriculum base
    # forms, except where the user-facing code needs an explicit schema alias.
    schema_key = schema_form_type(form_type)
    if schema_key not in FORM_SCHEMAS:
        raise ValueError(f"Unknown form type: {form_type}")

    schema = FORM_SCHEMAS[schema_key]
    missing_text_instruction = (
        'If a field cannot be filled from the case description, return an empty string "" for text/date/dropdown fields and [] for multi-select or curriculum fields.'
        if leave_missing_blank
        else 'If a field cannot be filled from the case description, set it to "Not mentioned in case".'
    )
    preserve_instruction = (
        """
===== WORDING RULES =====
- Keep the doctor's original content exactly as provided wherever possible.
- Do not paraphrase, embellish, or "improve" explicit clinical details.
- If a sentence from the case already fits a field, copy it with only the lightest trimming needed to fit JSON.
"""
        if preserve_original_content
        else ""
    )

    # Build field definitions for the prompt
    field_defs = []
    for field in schema["fields"]:
        req = "yes" if field["required"] else "no"
        line = f"- {field['key']} | {field['label']} | type: {field['type']} | required: {req}"
        if "options" in field:
            line += f"\n  options: {', '.join(field['options'])}"
        field_defs.append(line)

    field_keys = [f['key'] for f in schema["fields"]]
    # Always add key_capabilities alongside any kc_tick field so hierarchy renders correctly
    has_kc_tick = any(f['type'] == 'kc_tick' for f in schema["fields"])
    if has_kc_tick and "key_capabilities" not in field_keys:
        field_keys = field_keys + ["key_capabilities"]
    json_template = "{\n" + ",\n".join([f'  "{k}": "<extracted value>"' for k in field_keys]) + "\n}"

    # Check if this is a reflection-style form (use schema_key so _2021 variants
    # inherit the same prompt treatment as their 2025 counterparts)
    reflection_forms = {"SDL", "US_CASE", "ESLE", "COMPLAINT", "SERIOUS_INC", "EDU_ACT", "FORMAL_COURSE", "REFLECT_LOG"}
    is_reflection = schema_key in reflection_forms

    reflection_instruction = """
This is a self-reflection form. The trainee is reflecting on their own experience.
Write all text fields in first person ("I managed...", "I reflected on...", "I learned...").
Use British English spelling. Write professionally but naturally.
""" if is_reflection else ""

    # REFLECT_LOG-specific field scoping — prevents repetition across 7 narrative fields
    reflect_log_instruction = """
===== REFLECT_LOG FIELD SCOPING (mandatory — prevents repetition) =====

Each field has a distinct purpose. Do NOT repeat clinical facts across fields.
Each point should appear in EXACTLY ONE field.

Field scoping rules:
- reflection (Description / What happened): Clinical narrative only — what occurred, what you observed, what you did. No learning points, no "I would", no "I now know". Pure account of events.
- replay_differently (What would you do differently): ONE specific concrete action. One sentence or two max. Do not explain WHY here — that is the next field.
- why (Why): The reason behind the "differently" answer. Focus on the cognitive or systemic cause (e.g. fixation bias, workload). Do not repeat the clinical story.
- different_outcome (Would the outcome be different): If the clinical management was appropriate but communication or process could be improved, do NOT write "No, the clinical outcome would remain the same" — instead frame it as a quality-of-care improvement (e.g. "The clinical escalation was appropriate, but clearer communication may have improved patient understanding and reduced anxiety."). If there was a genuine clinical change that would alter the patient's course, describe that counterfactual outcome. Do not restate the diagnosis — just the specific impact.
- focussing_on (What are you focussing on): Forward-looking improvement plan only. It must be more specific than replay_differently and must not be the same sentence in different words. Include the method/framework/check you will use next time, but only if supported by the case.
- learned (What have you learned): Distil to 1-2 genuine learning points. Do not repeat the clinical narrative. Do not repeat focussing_on content. What insight did this case give you?

Example for handover/referral reflections:
- replay_differently: "I would give a more structured handover when referring an unwell surgical patient."
- focussing_on: "I am practising a concise SBAR-style referral that states the working diagnosis, sepsis treatments already started, current instability, and the decision I need from the surgical team."

Anti-repetition rule: if you find yourself writing the words "ECG", "atrial flutter", "fixation bias" (or any other case-specific term) in more than two fields — stop and redistribute. Each key concept appears in ONE field only.
""" if schema_key == "REFLECT_LOG" else ""

    today = date.today()
    yesterday = today - timedelta(days=1)
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    day_of_week = today.strftime("%A")

    system_prompt = f"""You are a medical portfolio assistant. Extract data for a {schema['name']} ({form_type}) WPBA entry.

Today's date: {today_str} ({day_of_week}). Yesterday: {yesterday_str}.
{reflection_instruction}{reflect_log_instruction}
Return ONLY a JSON object with these exact keys:
{json_template}

Field definitions:
{chr(10).join(field_defs)}

===== REQUIRED vs OPTIONAL FIELDS =====

Fields marked required: yes SHOULD be filled from the case where possible. You may infer
ONLY when the inference is directly supported by the doctor's words (e.g. clinical setting
from the department they named, stage of training if their grade is stated). If the case
genuinely does not provide enough to fill a required field, LEAVE IT BLANK ("" or []) —
the user will see the gap and complete it themselves in Kaizen. DO NOT write a placeholder,
DO NOT guess plausible content, DO NOT pad with generic clinical language to make the field
look complete. A blank required field is far better than a fabricated one.

Fields marked required: no are OPTIONAL. Fill them ONLY if the case genuinely provides
information that belongs in that field. If a field is optional and the case does not provide
applicable content, leave it as an empty string "" (or [] for list fields).

DO NOT fabricate content for optional fields. DO NOT restate information from other fields
just to populate an optional field. An empty optional field is correct and expected.

This mirrors how doctors actually fill in their own portfolios — they fill what they experienced,
not every box for completeness.

Rules:
- For dropdown fields: return ONLY one of the listed options. If the case does not explicitly support one option, return an empty string.
- For multi_select fields: return a list of values from the listed options. If none are explicit, return [].
- For kc_tick fields (curriculum_links): return a list of SLO codes ONLY e.g. ["SLO1", "SLO8"].
  Separately, populate "key_capabilities" with FULL KC description strings for those SLOs.
  KC SELECTION RULE: For each relevant SLO, check KC2, KC3, KC4... FIRST. Only include KC1 if no higher-numbered KC fits, or if KC1 captures something specific that the others do not. KC1 for SLO1 and SLO3 are extremely broad — do NOT select them just because a patient was assessed or a decision was made. That is true of every case and adds no value.
  Do NOT pad to reach any minimum number. Do NOT include a KC unless the case explicitly demonstrates it. Quality over quantity.
  Format each KC as: "SLO8 KC1: will provide support to ED staff at all levels... (2025 Update)"
  Use EXACT text from the map. curriculum_links = codes only. key_capabilities = full strings.
  If the form has a kc_tick field, always include "key_capabilities" in the JSON too.
- For date fields: return YYYY-MM-DD format. Resolve relative references using today's date above: "today" → {today_str}, "yesterday" → {yesterday_str}, "this morning/afternoon/evening" → {today_str}, "last [weekday]" → calculate from today. Only return empty string if no date at all can be inferred.
- For text fields: extract directly from the case and keep the doctor's original wording where possible
- Write in direct, first-person clinical language ("I assessed...", "I managed...")
- NEVER use: em dashes, "delve", "navigate", "crucial", "importantly", "comprehensive", "moreover", "furthermore", "holistic", "robust", "multifaceted", "pivotal", "seamless", "facilitate", "leverage", "unlock", "embark", "meticulous", "overarching", "in summary", "it's worth noting", "this case highlights", "moving forward"

{_CASE_SYNTHESIS_GUIDE}

{_PORTFOLIO_SKILL_QUALITY_RUBRIC}

===== FORMATTING =====
- Break any narrative field (reflection, clinical_reasoning, description) into 2-3 short paragraphs if it exceeds ~80 words.
- Use natural paragraph breaks: what happened → what I did/thought → what I learned or would change.
- Never write a single block of 100+ words with no paragraph break.

===== GROUNDING RULES (NON-NEGOTIABLE) =====
- Extract ONLY what the doctor explicitly stated or clearly implied. Never invent clinical details.
- {missing_text_instruction}
- Never add diagnoses, investigations, procedures, or clinical reasoning the doctor did not describe.
- It is better to leave a field sparse than to fabricate content. Doctors will reject inaccurate drafts.
- Return ONLY the JSON object. No explanation.

{RCEM_KC_MAP}

Case description:
{case_description}"""
    system_prompt += preserve_instruction
    if _is_image_source(input_source):
        system_prompt += "\n" + _IMAGE_EXTRACTOR_GUARD

    # Inject personal voice profile if available
    if voice_profile_json:
        from voice_profile import build_voice_instruction
        voice_block = build_voice_instruction(voice_profile_json)
        if voice_block:
            system_prompt += f"\n{voice_block}"
    else:
        system_prompt += """

===== DEFAULT WRITING STANDARD =====
Write as an experienced UK EM trainee would write their own portfolio entry:
- First person, professional but not stiff ("I assessed" not "The patient was assessed by the trainee")
- Specific clinical language without being verbose — name the condition, the investigation, the finding
- Short, direct sentences. Vary length slightly to avoid monotony.
- Reflection should sound genuine and personal, not templated — what genuinely surprised you, challenged you, or changed your practice
- Avoid: hedging phrases ("it could be argued"), academic formality ("the aforementioned"), motivational language ("this was a fantastic learning opportunity")
- British English spelling (recognised, organised, haemorrhage, paediatric)
- Sound like a confident registrar writing after a shift, not an AI summarising a textbook
"""

    if edit_feedback and current_draft:
        system_prompt += f"\n\nCurrent draft (improve based on feedback below):\n{current_draft}\n\nUser feedback:\n{edit_feedback}"
    elif edit_feedback:
        system_prompt += f"\n\nUser feedback to apply:\n{edit_feedback}"

    text = await _generate(system_prompt)
    raw = text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        # Retry once with explicit instruction
        retry_prompt = f"Fix the JSON and return ONLY valid JSON. No explanation.\n\nParse error: {e}\n\nOriginal output:\n{raw}"
        retry_text = await _generate(retry_prompt)
        retry_raw = retry_text.strip()
        if retry_raw.startswith("```"):
            retry_raw = retry_raw.split("```")[1]
            if retry_raw.startswith("json"):
                retry_raw = retry_raw[4:]
        retry_raw = retry_raw.strip()
        data = json.loads(retry_raw)

    normalised = {}
    for field in schema["fields"]:
        key = field["key"]
        field_type = field["type"]
        raw_value = data.get(key)
        if field_type in {"multi_select", "kc_tick"}:
            normalised[key] = _normalise_list_field(raw_value)
        elif field_type == "dropdown":
            normalised[key] = _normalise_dropdown_field(raw_value, field.get("options", []), leave_missing_blank)
        else:
            normalised[key] = _normalise_text_field(raw_value, leave_missing_blank, "")

    if has_kc_tick:
        normalised["key_capabilities"] = _normalise_list_field(data.get("key_capabilities"))

    if schema_key == "DOPS" and has_kc_tick:
        # The LLM tends to pick SLO6 KC1 ("knowledge to identify when …") and
        # stop there. For unstable AF / cardioversion / sedation cases, that
        # leaves the resuscitation evidence unrepresented. Supplement with the
        # SLO3/SLO6 KCs the case text genuinely supports. Use schema_key so the
        # DOPS_2021 variant gets the same augmentation as the 2025 form.
        from dops_filing import suggest_dops_kc_breadth, derive_dops_curriculum_links
        augmented = suggest_dops_kc_breadth(case_description, normalised.get("key_capabilities", []))
        if augmented != normalised.get("key_capabilities", []):
            normalised["key_capabilities"] = augmented
            existing_links = normalised.get("curriculum_links") or []
            derived = derive_dops_curriculum_links(augmented)
            merged = list(dict.fromkeys(list(existing_links) + derived))
            normalised["curriculum_links"] = merged

    normalised = _guard_unsourced_exact_training_stage(normalised, schema, case_description)
    normalised = _supplement_supported_key_capabilities(
        normalised,
        case_description=case_description,
        schema_key=schema_key,
        has_kc_tick=has_kc_tick,
    )

    # Apply humanizer to ALL narrative fields before user sees the draft
    normalised = _humanize_all_fields(normalised)
    if schema_key == "REFLECT_LOG":
        normalised = _polish_reflect_log_fields(normalised, case_description)

    # For image-derived input, strip any unsupported resuscitation / advanced-
    # imaging narrative the LLM tried to inject. Rib-fracture screenshots
    # were being turned into ALS/ROSC drafts — see test_source_grounding.
    if _is_image_source(input_source):
        normalised, stripped = enforce_image_source_grounding(normalised, case_description)
        if stripped:
            logger.warning(
                "Stripped %d fabricated term(s) from image-derived %s draft: %s",
                len(stripped), form_type, stripped,
            )

    return FormDraft(
        form_type=form_type,
        fields=normalised,
        uuid=FORM_UUIDS.get(form_type)
    )


async def review_draft(form_type: str, fields: dict, case_text: str) -> dict:
    """Review a completed draft against WPBA quality criteria.
    Returns structured feedback with scores and suggestions."""
    from form_schemas import FORM_SCHEMAS

    schema = FORM_SCHEMAS.get(form_type, {})
    form_name = schema.get("name", form_type)

    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    day_of_week = today.strftime("%A")

    fields_summary = json.dumps(fields, indent=2, default=str)

    prompt = f"""You are a senior UK Emergency Medicine consultant and WPBA assessor.
Today's date: {today_str} ({day_of_week}).

Review this completed {form_name} ({form_type}) draft against WPBA quality criteria.

ORIGINAL CASE INPUT:
{case_text}

DRAFT FIELDS:
{fields_summary}

FORM SCHEMA: {form_name} ({form_type})

Score the draft on these 5 criteria (each 1-5):

1. **Reflection depth** — Is the reflection analytical (what would I do differently, what did I learn) or just descriptive? 1-2 = descriptive only, 3 = some analysis, 4-5 = genuine insight.

2. **Clinical reasoning** — Does the entry show clear decision-making, differentials, thought process? 1-2 = just lists what happened, 3 = mentions decisions, 4-5 = shows reasoning and uncertainty.

3. **SLO/Curriculum coverage** — Are the selected SLOs genuinely demonstrated by the case, or just tagged? 1-2 = SLOs don't match, 3 = loosely relevant, 4-5 = clearly evidenced. If no SLOs are present, score based on whether the case content would map well to curriculum areas.

4. **Assessor readiness** — Would an assessor have enough detail for a meaningful discussion? 1-2 = too thin, 3 = adequate, 4-5 = rich discussion material.

5. **Language quality** — First-person clinical language, no AI-tells (em dashes, "delve", "crucial", "comprehensive", "facilitate"), professional tone. 1-2 = obvious AI, 3 = mostly natural, 4-5 = reads like a real doctor wrote it.

Return ONLY valid JSON:
{{
  "overall_score": <float, average of 5 scores, 1 decimal>,
  "scores": {{
    "reflection_depth": {{"score": <int 1-5>, "feedback": "<1-2 sentences>"}},
    "clinical_reasoning": {{"score": <int 1-5>, "feedback": "<1-2 sentences>"}},
    "slo_coverage": {{"score": <int 1-5>, "feedback": "<1-2 sentences>"}},
    "assessor_readiness": {{"score": <int 1-5>, "feedback": "<1-2 sentences>"}},
    "language_quality": {{"score": <int 1-5>, "feedback": "<1-2 sentences>"}}
  }},
  "top_suggestion": "<single most impactful improvement suggestion>",
  "verdict": "<ready|improve|weak>"
}}

verdict rules: "ready" if overall_score >= 3.5, "improve" if 2.5-3.4, "weak" if < 2.5
"""
    raw = await _generate(prompt)
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


_RECOVERY_COPY_CACHE: dict[str, str] = {}


def _categorise_filing_error(error_text: str) -> str:
    """Coarse bucket for filing errors, used as a cache key."""
    if not error_text:
        return "unknown"
    e = error_text.lower()
    if "login" in e or "credential" in e or "auth" in e or "password" in e:
        return "login_failed"
    if "timed out" in e or "timeout" in e:
        return "timeout"
    if "could not confirm" in e or "verify" in e or "verification" in e:
        return "unverified_save"
    if "navigation" in e or "load" in e or "page" in e:
        return "load_failed"
    if "missing" in e or "required" in e:
        return "validation"
    return "unknown"


async def compose_filing_recovery_copy(status: str, error_text: str) -> str:
    """One short sentence the bot can show after a failed or partially-saved
    filing — explains what happened in plain terms and suggests a next step.
    Returns "" on LLM failure so the caller can fall back to static text.
    Cached by (status, error_category) so repeated failures don't burn calls.
    """
    category = _categorise_filing_error(error_text)
    cache_key = f"{status}|{category}"
    if cache_key in _RECOVERY_COPY_CACHE:
        return _RECOVERY_COPY_CACHE[cache_key]

    prompt = f"""You are writing a short, calm recovery message for a doctor whose UK Emergency Medicine portfolio filing did not complete cleanly. They are mid-flow and want to know what happened.

Filing status: {status}  (one of: failed, partial)
Underlying error text (may be empty): {error_text or '(none)'}
Error category: {category}

Write ONE sentence (under 25 words) that:
1. States plainly what likely happened (no jargon).
2. Suggests the most useful next step the user can take.

Do not promise that Kaizen has saved the entry. Do not invent details. No exclamation marks. Reply with the sentence only, no quotes."""

    try:
        text = (await _generate(prompt)).strip().strip('"').strip("'").strip()
    except Exception as e:
        logger.warning("compose_filing_recovery_copy failed: %s", e)
        return ""

    if not text or len(text) > 240:
        return ""

    _RECOVERY_COPY_CACHE[cache_key] = text
    return text


async def extract_field_updates(form_type: str, current_fields: dict, instruction: str) -> dict:
    """Parse a natural-language edit instruction against an active draft and
    return a dict of {field_name: new_value} to apply. Returns {} if the
    instruction can't be confidently mapped to existing fields.

    The instruction is something a user might type at the approval step like
    "change the date to last Tuesday" or "set patient age to 67".
    """
    if not instruction or not current_fields:
        return {}

    today = datetime.now().strftime("%Y-%m-%d")
    field_summary = json.dumps(
        {k: (str(v)[:120] if v is not None else None) for k, v in current_fields.items()},
        indent=2,
        default=str,
    )

    prompt = f"""You are updating a draft of a UK Emergency Medicine portfolio entry based on a freeform edit instruction from the doctor.

Form type: {form_type}
Today's date (for relative date references): {today}

Current draft fields (JSON):
{field_summary}

Doctor's instruction:
\"\"\"
{instruction}
\"\"\"

Identify which existing fields (and ONLY those listed above) the doctor wants to change, and what the new value should be.

- Resolve relative dates ("last Tuesday", "yesterday") to absolute YYYY-MM-DD using today's date.
- If a field can't be matched confidently to one of the listed fields, do NOT include it.
- If the instruction is not actually an edit (e.g. a question, a new case), return an empty updates object.
- Keep new values short and matching the existing field's type/format.

Return ONLY valid JSON in this shape:
{{"updates": {{"field_name": "new_value"}}, "summary": "one short sentence describing what changed"}}

If nothing should change, return: {{"updates": {{}}, "summary": ""}}"""

    try:
        raw = await _generate(prompt)
    except Exception as e:
        logger.warning("extract_field_updates failed: %s", e)
        return {}

    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("extract_field_updates JSON parse failed: %s; raw=%r", e, text[:200])
        return {}

    updates = parsed.get("updates") if isinstance(parsed, dict) else None
    if not isinstance(updates, dict):
        return {}

    # Only keep keys that actually exist in the current draft — guards against hallucinated fields
    safe_updates = {k: v for k, v in updates.items() if k in current_fields}
    if safe_updates and parsed.get("summary"):
        safe_updates["__summary__"] = str(parsed["summary"])[:200]
    return safe_updates


async def generate_nudge_copy(stats: dict) -> str:
    """Compose a fresh weekly portfolio check-in. Returns the message body
    (no keyboard) or "" on failure so the caller can fall back to a static
    template. Kept short and varied — same stats should produce different
    phrasings each week.
    """
    cases = stats.get("cases", 0)
    gap = stats.get("gap")  # tuple (label, days) or None
    if gap:
        label, days = gap
        gap_text = f"Longest gap: no {label} in {days} days."
    else:
        gap_text = "No notable gaps."

    prompt = f"""You are writing a friendly weekly portfolio check-in for a UK Emergency Medicine doctor using a Telegram bot.

This week's facts:
- Cases filed this week: {cases}
- Gap status: {gap_text}

Write 2-3 short sentences (under 60 words total). Lead with a clipboard emoji and a punchy heading on its own line, then encouragement or observation, then a one-liner inviting them to start ("Tap below to file a case." or similar).

Vary the phrasing — don't sound robotic. No exclamation marks, no bullet lists, no lecturing. Just plain sentences.

Reply with the message text only, no quotes."""

    try:
        text = (await _generate(prompt)).strip().strip('"').strip("'").strip()
    except Exception as e:
        logger.warning("generate_nudge_copy failed: %s", e)
        return ""

    if not text or len(text) > 500:
        return ""
    return text


async def summarise_recent_activity(case_history: list, just_filed_form_type: str) -> str:
    """One-line observation about the user's recent portfolio activity after a
    successful filing. Returns "" when there is not enough history to say
    anything useful or when the LLM call fails.
    """
    if not case_history or len(case_history) < 2:
        return ""

    from collections import Counter
    from datetime import datetime, timedelta, timezone

    counts = dict(Counter(public_form_name(c.get("form_type", "unknown")) for c in case_history))

    cutoff = datetime.now(timezone.utc) - timedelta(days=42)
    recent_types: set[str] = set()
    for c in case_history:
        filed_at_raw = c.get("filed_at")
        if not filed_at_raw:
            continue
        parsed = None
        try:
            parsed = datetime.fromisoformat(str(filed_at_raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(str(filed_at_raw), fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
        if parsed and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed and parsed > cutoff:
            recent_types.add(public_form_name(c.get("form_type", "unknown")))

    just_filed_name = public_form_name(just_filed_form_type)

    prompt = f"""A doctor just filed a {just_filed_name} for their UK Emergency Medicine ARCP portfolio.

Their last 3 months of activity:
- Total cases: {len(case_history)}
- Counts by assessment type: {counts}
- Assessment types seen in the last 6 weeks: {sorted(recent_types) or 'none other than what was just filed'}

Write ONE friendly, concrete sentence under 25 words. Use full assessment names, not internal form codes. Either acknowledge their progress on the assessment type they just filed, or point out a specific gap they should consider next. No sycophancy, no exclamation marks, no repeating the raw count number. Just the sentence, no quotes."""

    try:
        text = (await _generate(prompt)).strip().strip('"').strip("'").strip()
    except Exception as e:
        logger.warning("summarise_recent_activity failed: %s", e)
        return ""

    text = sanitize_internal_form_codes(text)

    if not text or len(text) > 220:
        return ""
    return text


async def analyse_portfolio_health(case_history: list, training_level: str) -> dict:
    """Analyse filed cases against ARCP requirements.
    case_history: list of dicts with form_type, filed_at, status.
    training_level: Kaizen portfolio profile bucket or legacy grade value.
    Returns structured health analysis.
    """
    from collections import Counter

    total = len(case_history)
    form_counts = dict(Counter(c["form_type"] for c in case_history))

    history_summary = json.dumps(case_history, indent=2, default=str)
    form_dist = json.dumps(form_counts, indent=2)

    prompt = f"""You are a senior UK Emergency Medicine consultant and ARCP assessor.

A doctor with Kaizen portfolio profile {training_level} has filed the following cases via their ePortfolio over the last 6 months:

FILING HISTORY ({total} entries):
{history_summary}

FORM DISTRIBUTION:
{form_dist}

RCEM CURRICULUM SLOs (for reference):
{RCEM_KC_MAP}

Analyse this portfolio against ARCP requirements for this Kaizen portfolio profile. Consider:
- Breadth of form types (CBD, DOPS, Mini-CEX, etc.)
- SLO coverage based on the types of cases filed
- Whether the volume is sufficient for this portfolio profile
- Any obvious gaps that would concern an ARCP panel

Return ONLY valid JSON:
{{
  "total_cases": {total},
  "form_distribution": {form_dist},
  "slo_coverage": {{
    "covered": ["<SLO codes likely covered based on form types and volume>"],
    "gaps": ["<SLO codes likely NOT covered>"]
  }},
  "strengths": ["<2-3 positive observations>"],
  "gaps": ["<2-3 gap observations>"],
  "suggestions": ["<3-4 actionable suggestions to improve portfolio>"],
  "arcp_readiness": "<on_track|needs_attention|at_risk>"
}}

Be specific and practical. Reference actual form types and SLO numbers.
If there are zero cases, return at_risk with appropriate suggestions for getting started.
"""
    raw = await _generate(prompt)
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)
