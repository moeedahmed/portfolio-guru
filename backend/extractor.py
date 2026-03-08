from google import genai
import asyncio
import json
import logging
import os
import re
from datetime import date
from typing import List, Callable, Any

logger = logging.getLogger(__name__)
from models import CBDData, FormTypeRecommendation, FormDraft
from form_schemas import FORM_SCHEMAS

# RCEM Higher EM Curriculum (2025 Update) — Exact Kaizen checkbox labels
# Source: Live Kaizen CBD form screenshot (verified 2026-03-08)
# NOTE: Kaizen's SLO numbering differs from rcemcurriculum.co.uk — use these numbers.
RCEM_KC_MAP = """RCEM Higher EM Curriculum (2025 Update) — Exact Kaizen Checkbox Labels:

SLO1: Care for acutely physiologically stable adult patients presenting to acute care across the full range of complexity (2025 Update)
  KC1: to be expert in assessing and managing all adult patients attending the ED. These capabilities will apply to patients attending with both physical and psychological ill health (2025 Update)

SLO3: Support the ED team by answering clinical questions and making safe decisions (2025 Update)
  KC1: able to support the pre-hospital, medical, nursing and administrative team in answering clinical questions and in making safe decisions for patients with appropriate levels of risk in the ED (2025 Update)
  KC2: aware of when it is appropriate to review patients remotely or directly and able to teach these principles to others (2025 Update)

SLO4: Care for acutely injured patients across the full range of complexity (2025 Update)
  KC1: be expert in assessment, investigation and clinical management of patients attending with all injuries, regardless of complexity (2025 Update)
  KC2: provide expert leadership of the Major Trauma Team (2025 Update)

SLO5: Resuscitate and stabilise patients in the ED knowing when it is appropriate to stop (2025 Update)
  KC1: provide airway management & ventilatory support to critically ill patients (2025 Update)
  KC2: be expert in fluid management and circulatory support in critically ill patients (2025 Update)
  KC3: manage all the life-threatening conditions including peri-arrest & arrest situations in the ED (2025 Update)
  KC4: be expert in caring for ED patients and their relatives and loved ones at the end of the patient's life (2025 Update)
  KC5: effectively lead and support resuscitation teams (2025 Update)

SLO6_PAEDS: Care for children of all ages, at all stages of development and with complex needs (2025 Update)
  KC1: be expert in assessing and managing all children and young adult patients attending the ED (2025 Update)
  KC2: be able to provide airway management & ventilatory support to critically ill paediatric patients (2025 Update)
  KC3: be able to lead and support a multidisciplinary paediatric resuscitation including trauma (2025 Update)
  KC4: be expert in fluid management and circulatory support in critically ill paediatric patients (2025 Update)
  KC5: be able to manage all the life-threatening paediatric conditions including peri-arrest & arrest situations in the ED (2025 Update)
  KC6: be able to assess and formulate a management plan for children and young adults who present with complex medical and social needs (2025 Update)

SLO6_PROC: Deliver key procedural skills needed in EM (2025 Update)
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

SLO9_TEACH: Support, supervise & educate others working in the ED (2025 Update)
  KC1: be able to undertake training and supervision of members of the ED team in the clinical environment (2025 Update)
  KC2: be able to prepare and deliver teaching sessions outside of the clinical environment, including simulation, small group work, and didactic presentations (2025 Update)
  KC3: be able to provide effective constructive feedback to colleagues, including debrief (2025 Update)
  KC4: understand the principles necessary to mentor and appraise junior doctors (2025 Update)

SLO9_RESEARCH: Participate in research and manage data appropriately (2025 Update)
  KC1: be able to appraise, synthesise, communicate and use research evidence to develop EM care (2025 Update)
  KC2: be able to actively participate in research (2025 Update)

SLO10: Participate in & promote activity to improve quality & safety of patient care (2025 Update)
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

_client = None

PRIMARY_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-2.0-flash"


async def _gemini_generate(prompt, retries: int = 2, delay: int = 1):
    """Call Gemini generate_content with automatic model fallback.
    Tries PRIMARY_MODEL first with retries, then FALLBACK_MODEL.
    Fast-path: minimal retries to keep latency low.
    """
    import time as _time
    client = _get_client()
    loop = asyncio.get_event_loop()
    last_error = None
    t0 = _time.monotonic()

    for model in [PRIMARY_MODEL, FALLBACK_MODEL]:
        for attempt in range(retries if model == PRIMARY_MODEL else 1):
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda m=model: client.models.generate_content(model=m, contents=prompt)
                )
                elapsed = _time.monotonic() - t0
                logger.info(f"Gemini {model} responded in {elapsed:.1f}s")
                return result
            except Exception as e:
                error_msg = str(e).lower()
                if any(term in error_msg for term in ["503", "unavailable", "overloaded", "404"]):
                    last_error = e
                    if model == PRIMARY_MODEL and attempt < retries - 1:
                        await asyncio.sleep(delay)
                    elif model == PRIMARY_MODEL:
                        logger.warning(f"{PRIMARY_MODEL} failed after {retries} retries ({_time.monotonic()-t0:.1f}s), falling back to {FALLBACK_MODEL}")
                    continue
                raise
    raise last_error


async def _gemini_call_with_retry(fn: Callable[..., Any], *args, retries: int = 3, delay: int = 2) -> Any:
    """Legacy wrapper — kept for any call sites that still use it directly."""
    last_error = None
    loop = asyncio.get_event_loop()
    for attempt in range(retries):
        try:
            return await loop.run_in_executor(None, lambda: fn(*args))
        except Exception as e:
            error_msg = str(e).lower()
            if any(term in error_msg for term in ["503", "unavailable", "overloaded"]):
                last_error = e
                if attempt < retries - 1:
                    await asyncio.sleep(delay)
                continue
            raise
    raise last_error

FORM_UUIDS = {
    # 2025 update versions (preferred)
    "CBD":      "3ce5989a-b61c-4c24-ab12-711bf928b181",  # CBD 2025 update
    "DOPS":     "159831f9-6d22-4e77-851b-87e30aee37a2",  # DOPS ST3-ST6 2025 update
    "LAT":      "eb1c7547-0f41-49e7-95de-8adffd849924",  # LAT 2025 update v9
    "ACAT":     "6577ab06-8340-47e3-952a-708a5f800dcc",  # ACAT ACCS 2025 update
    "ACAF":     "15e67ae8-868b-4358-9b96-30a4a272f02c",  # ACAF 2025 update
    "STAT":     "41ff54b8-35a7-414b-9bd6-97fb1c3eb189",  # STAT 2025 update
    "MSF":      "5f71ac04-ff45-44d2-b7a1-f8b921a8a4c8",  # MSF
    "MINI_CEX": "647665f4-a992-4541-9e17-33ba6fd1d347",  # Mini-CEX 2025 update
    "JCF":      "3daa9559-3c31-4ab4-883c-9a991632a9ca",  # Journal Club 2025 update
    "QIAT":     "a0aa5cfc-57be-4622-b974-51d334268d57",  # EM QIAT 2025 update
    # New forms — 9 added
    "TEACH":        "1ffbd272-8447-439c-aa03-ff99e2dbc04d",  # Teaching Delivered By Trainee 2025
    "PROC_LOG":     "2d6ebac1-4633-49d1-9dc0-fa0d39a98afc",  # Procedural Log ST3-ST6 2025
    "SDL":          "743885d8-c1b8-4566-bc09-8ed9b0e09829",  # Self-directed Learning Reflection 2025
    "US_CASE":      "558b196a-8168-4cc6-b363-6f6e4b08397a",  # Ultrasound Case Reflection 2025
    "ESLE":         "cbc7a42f-a2f0-436b-813e-bbf97cce0a34",  # Reflection on ESLE 2025
    "COMPLAINT":    "f7c0ba98-5a47-4e37-b76a-ca3c5c8484cc",  # Reflection on Complaints 2025
    "SERIOUS_INC":  "9d4a7912-a615-4ae4-9fae-6be966bcf254",  # Reflection on Serious Incident 2025
    "EDU_ACT":      "868dc0e7-f4e9-4283-ac52-d9c8b246024b",  # Educational Activity Attended 2025
    "FORMAL_COURSE":"c7cd9a95-e2aa-4f61-a441-b663f3c933c6",  # Attendance at Formal Course 2025
}

# AI-tell patterns to strip from ALL narrative text (humanizer)
# Applied before the user sees any draft — not post-approval
SLOP_PATTERNS = [
    r"\s*—\s*",  # em dashes -> " - "
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


def extract_explicit_form_type(text: str) -> str | None:
    """
    Check if the user explicitly named a form type in their message.
    Returns the short form key (e.g. "CBD", "DOPS") or None.
    No AI call — pure pattern match for speed.
    """
    text_lower = text.lower()
    patterns = {
        "CBD":          ["cbd", "case-based discussion", "case based discussion"],
        "DOPS":         ["dops", "directly observed procedural", "procedural skill"],
        "MINI_CEX":     ["mini cex", "mini-cex", "minicex", "clinical evaluation exercise"],
        "LAT":          ["lat", "leadership assessment tool"],
        "ACAT":         ["acat", "acute care assessment tool"],
        "ACAF":         ["acaf", "applied critical appraisal", "critical appraisal form"],
        "STAT":         ["stat", "structured teaching assessment"],
        "MSF":          ["msf", "multi source feedback", "multi-source feedback", "360"],
        "QIAT":         ["qiat", "quality improvement assessment"],
        "JCF":          ["jcf", "journal club"],
        "TEACH":        ["teach form", "teaching delivered", "teaching session form"],
        "PROC_LOG":     ["proc log", "procedural log", "procedure log"],
        "SDL":          ["sdl", "self-directed learning", "self directed learning"],
        "US_CASE":      ["ultrasound case", "us case", "pocus case"],
        "ESLE":         ["esle", "significant learning event"],
        "COMPLAINT":    ["complaint reflection", "complaint form"],
        "SERIOUS_INC":  ["serious incident", "si reflection", "never event"],
        "EDU_ACT":      ["educational activity", "edu act", "teaching attended"],
        "FORMAL_COURSE":["formal course", "atls", "apls", "als course", "epals"],
    }
    for form_type, keywords in patterns.items():
        if any(kw in text_lower for kw in keywords):
            return form_type
    return None


async def classify_intent(text: str) -> str:
    """Classify user message intent: 'chitchat', 'question', or 'case'."""
    client = _get_client()

    prompt = """Classify this message into exactly one category:

- chitchat: greetings, thanks, short social messages (hi, hello, thanks, bye, ok, great, etc.)
- question: asking about what the bot does, how it works, capabilities, help requests
- case: a clinical case description suitable for portfolio filing (contains patient details, symptoms, management, procedures, or clinical scenarios)

Message: """

    contents = f"{prompt}{text}\n\nRespond with ONLY one word: chitchat, question, or case"
    response = await _gemini_generate(contents)
    result = response.text.strip().lower()

    # Normalize response
    if "chitchat" in result:
        return "chitchat"
    elif "question" in result:
        return "question"
    else:
        return "case"


async def answer_question(text: str) -> str:
    """Generate a helpful answer about the bot's capabilities."""
    client = _get_client()

    prompt = """You are Portfolio Guru, a Telegram bot that helps RCEM doctors file their clinical cases to the Kaizen e-portfolio.

Answer this question about what you do. Be concise and helpful. Key facts:
- You accept case descriptions via text, voice note, or photo
- You extract structured data and create a CBD (Case-Based Discussion) draft
- The draft is shown for review before filing
- Nothing is submitted to a supervisor - only saved as a draft
- Credentials are encrypted and never shared

Question: """

    contents = f"{prompt}{text}"
    response = await _gemini_generate(contents)
    return response.text.strip()


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

    response = await _gemini_generate(prompt)
    raw = response.text.strip()
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


def _humanize_reflection(text: str) -> str:
    """Legacy alias — calls _humanize_text."""
    return _humanize_text(text)


def _humanize_all_fields(data: dict) -> dict:
    """Apply humanizer to all narrative text fields in a draft dict.
    Non-narrative fields (dates, dropdowns, names, lists) are left untouched."""
    for key, value in data.items():
        if key in _HUMANIZE_FIELDS and isinstance(value, str) and len(value) > 20:
            data[key] = _humanize_text(value)
    return data


async def recommend_form_types(case_description: str) -> List[FormTypeRecommendation]:
    """Recommend applicable WPBA form types based on case description."""
    client = _get_client()

    system_prompt = """Analyze this clinical case and recommend which WPBA forms apply.

Rules:
- CBD: Always if trainee managed a clinical case (retrospective reasoning discussion)
- LAT: ONLY if the trainee was explicitly the shift leader or shift co-ordinator, or managed a major incident as lead
- DOPS: If trainee personally performed a hands-on procedure (intubation, central line, LP, chest drain, etc.)
- ACAT: If description covers a full shift or multiple patients observed
- MINI_CEX: If someone directly observed the trainee seeing a patient (real-time bedside observation)
- ACAF: If trainee searched literature or critically appraised evidence
- JCF: If trainee presented at a journal club
- STAT: If trainee delivered a structured teaching session to a group
- QIAT: If trainee completed or is presenting a QI project
- MSF: If trainee is requesting 360-degree colleague feedback
- TEACH: If trainee delivered teaching (bedside, sim, lecture) or supervised a junior
- PROC_LOG: If trainee performed a procedure and wants to log it (lighter than DOPS — no assessor needed)
- SDL: If trainee did self-directed learning (podcast, article, online module, video)
- US_CASE: If trainee performed or interpreted a point-of-care ultrasound scan
- ESLE: If trainee is reflecting on an event with significant learning (near-miss, unexpected outcome, difficult situation)
- COMPLAINT: If trainee is reflecting on a patient complaint
- SERIOUS_INC: If trainee is reflecting on a serious incident or never event
- EDU_ACT: If trainee attended a teaching session, lecture, or educational event (as learner, not teacher)
- FORMAL_COURSE: If trainee attended a formal course (ATLS, APLS, ALS, etc.)
- Never recommend more than 3 forms

Return ONLY a JSON array:
[{"form_type": "CBD", "rationale": "one-line reason"}, ...]

Only include forms that clearly apply. CBD is almost always applicable.
Be conservative — do not recommend a form unless the case description clearly demonstrates that activity."""

    prompt = f"{system_prompt}\n\nCase description:\n{case_description}"
    response = await _gemini_generate(prompt)
    raw = response.text.strip()

    # Strip markdown code fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Default to CBD only
        data = [{"form_type": "CBD", "rationale": "Clinical case management"}]

    recommendations = []
    for item in data[:3]:  # Max 3
        form_type = item.get("form_type", "CBD")
        recommendations.append(FormTypeRecommendation(
            form_type=form_type,
            rationale=item.get("rationale", ""),
            uuid=FORM_UUIDS.get(form_type)
        ))

    # Ensure CBD is always included
    if not any(r.form_type == "CBD" for r in recommendations):
        recommendations.insert(0, FormTypeRecommendation(
            form_type="CBD",
            rationale="Clinical case management",
            uuid=FORM_UUIDS["CBD"]
        ))

    return recommendations


async def extract_cbd_data(case_description: str, edit_feedback: str = "", current_draft: str = "", voice_profile_json: str = "") -> CBDData:
    """Extract structured CBD data from free-text case description."""
    client = _get_client()

    system_prompt = f"""You are a medical portfolio assistant. Extract structured data from a doctor's clinical case description for a Case-Based Discussion (CBD) WPBA entry.

Return ONLY a JSON object with these exact fields:
{{
  "form_type": "CBD",
  "date_of_encounter": "YYYY-MM-DD — today if not mentioned",
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

The full KC list is below. Read the case, then pick 3-6 KCs that are DIRECTLY demonstrated.
KCs are what matter — SLOs are just grouping labels derived automatically from whichever KCs you select.

{RCEM_KC_MAP}

INSTRUCTIONS:
1. Read the full case description.
2. Go through the KC list above and ask: "Does this case directly demonstrate this specific capability?"
3. Select every KC where the answer is YES — there is no upper limit per SLO.
4. Do NOT default to KC1 for each SLO. Read each KC description carefully.
5. Aim for 3-6 KCs total across the whole case. More is better than fewer if warranted.
6. Use the FULL KC text exactly as written above (including the "(2025 Update)" suffix).
7. Format each as: "SLO_CODE KC_NUM: full description text (2025 Update)"

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

===== FORMATTING =====
- Break any narrative field (reflection, clinical_reasoning, description) into 2-3 short paragraphs if it exceeds ~80 words.
- Use natural paragraph breaks: what happened → what I did/thought → what I learned or would change.
- Never write a single block of 100+ words with no paragraph break.

===== GROUNDING RULES (NON-NEGOTIABLE) =====
- Extract ONLY what the doctor explicitly stated or clearly implied. Never invent clinical details.
- If a field cannot be filled from the case description, set it to "Not mentioned in case" — do NOT generate plausible-sounding content to fill gaps.
- Never add diagnoses, investigations, procedures, or clinical reasoning the doctor did not describe.
- It is better to leave a field sparse than to fabricate content. Doctors will reject inaccurate drafts.
- Today's date: {date.today()}
- Return ONLY the JSON. No explanation."""

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

    response = await _gemini_generate(prompt)
    raw = response.text.strip()

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
        retry_response = await _gemini_generate(retry_prompt)
        retry_raw = retry_response.text.strip()
        if retry_raw.startswith("```"):
            retry_raw = retry_raw.split("```")[1]
            if retry_raw.startswith("json"):
                retry_raw = retry_raw[4:]
        retry_raw = retry_raw.strip()
        data = json.loads(retry_raw)

    # Coerce null required-ish fields to sensible defaults
    if not data.get("date_of_encounter"):
        data["date_of_encounter"] = str(date.today())
    if not data.get("patient_presentation"):
        data["patient_presentation"] = "Not specified"
    if not data.get("trainee_role"):
        data["trainee_role"] = "Not mentioned in case"
    if not data.get("clinical_reasoning"):
        data["clinical_reasoning"] = "Not mentioned in case"
    if not data.get("reflection"):
        data["reflection"] = "Reflection not extracted - please edit"

    # Apply humanizer to ALL narrative fields before user sees the draft
    data = _humanize_all_fields(data)

    # Ensure key_capabilities exists
    if "key_capabilities" not in data:
        data["key_capabilities"] = []

    return CBDData(**data)


async def extract_form_data(case_description: str, form_type: str, edit_feedback: str = "", current_draft: str = "", voice_profile_json: str = "") -> FormDraft:
    """Extract structured data for any non-CBD form type."""
    if form_type not in FORM_SCHEMAS:
        raise ValueError(f"Unknown form type: {form_type}")

    schema = FORM_SCHEMAS[form_type]
    client = _get_client()

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

    # Check if this is a reflection-style form
    reflection_forms = {"SDL", "US_CASE", "ESLE", "COMPLAINT", "SERIOUS_INC", "EDU_ACT", "FORMAL_COURSE"}
    is_reflection = form_type in reflection_forms

    reflection_instruction = """
This is a self-reflection form. The trainee is reflecting on their own experience.
Write all text fields in first person ("I managed...", "I reflected on...", "I learned...").
Use British English spelling. Write professionally but naturally.
""" if is_reflection else ""

    system_prompt = f"""You are a medical portfolio assistant. Extract data for a {schema['name']} ({form_type}) WPBA entry.
{reflection_instruction}
Return ONLY a JSON object with these exact keys:
{json_template}

Field definitions:
{chr(10).join(field_defs)}

Rules:
- For dropdown fields: return ONLY one of the listed options. If unclear, use the first option.
- For multi_select fields: return a list of values from the listed options.
- For kc_tick fields (curriculum_links): return a list of SLO codes ONLY e.g. ["SLO1", "SLO8"].
  Separately, populate "key_capabilities" with 3-5 FULL KC description strings for those SLOs.
  Format each KC as: "SLO8 KC1: will provide support to ED staff at all levels... (2025 Update)"
  Use EXACT text from the map. curriculum_links = codes only. key_capabilities = full strings.
  If the form has a kc_tick field, always include "key_capabilities" in the JSON too.
- For date fields: return YYYY-MM-DD. Use today if not mentioned: {date.today()}
- For text fields: extract directly from the case, be concise and clinical
- Write in direct, first-person clinical language ("I assessed...", "I managed...")
- NEVER use: em dashes, "delve", "navigate", "crucial", "importantly", "comprehensive", "moreover", "furthermore", "holistic", "robust", "multifaceted", "pivotal", "seamless", "facilitate", "leverage", "unlock", "embark", "meticulous", "overarching", "in summary", "it's worth noting", "this case highlights", "moving forward"

===== FORMATTING =====
- Break any narrative field (reflection, clinical_reasoning, description) into 2-3 short paragraphs if it exceeds ~80 words.
- Use natural paragraph breaks: what happened → what I did/thought → what I learned or would change.
- Never write a single block of 100+ words with no paragraph break.

===== GROUNDING RULES (NON-NEGOTIABLE) =====
- Extract ONLY what the doctor explicitly stated or clearly implied. Never invent clinical details.
- If a field cannot be filled from the case description, set it to "Not mentioned in case" — do NOT generate plausible-sounding content to fill gaps.
- Never add diagnoses, investigations, procedures, or clinical reasoning the doctor did not describe.
- It is better to leave a field sparse than to fabricate content. Doctors will reject inaccurate drafts.
- Return ONLY the JSON object. No explanation.

{RCEM_KC_MAP}

Case description:
{case_description}"""

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

    response = await _gemini_generate(system_prompt)
    raw = response.text.strip()

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
        retry_response = await _gemini_generate(retry_prompt)
        retry_raw = retry_response.text.strip()
        if retry_raw.startswith("```"):
            retry_raw = retry_raw.split("```")[1]
            if retry_raw.startswith("json"):
                retry_raw = retry_raw[4:]
        retry_raw = retry_raw.strip()
        data = json.loads(retry_raw)

    # Apply humanizer to ALL narrative fields before user sees the draft
    data = _humanize_all_fields(data)

    return FormDraft(
        form_type=form_type,
        fields=data,
        uuid=FORM_UUIDS.get(form_type)
    )
