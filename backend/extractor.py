from google import genai
import asyncio
import json
import os
import re
from datetime import date
from typing import List, Callable, Any
from models import CBDData, FormTypeRecommendation, FormDraft
from form_schemas import FORM_SCHEMAS

_client = None


async def _gemini_call_with_retry(fn: Callable[..., Any], *args, retries: int = 3, delay: int = 2) -> Any:
    """Retry Gemini API calls on 503/UNAVAILABLE/overloaded errors."""
    last_error = None
    for attempt in range(retries):
        try:
            return fn(*args)
        except Exception as e:
            error_msg = str(e).lower()
            if any(term in error_msg for term in ["503", "unavailable", "overloaded"]):
                last_error = e
                if attempt < retries - 1:
                    await asyncio.sleep(delay)
                continue
            raise  # Re-raise non-retryable errors immediately
    raise last_error  # All retries exhausted

FORM_UUIDS = {
    # 2025 update versions (preferred)
    "CBD":  "3ce5989a-b61c-4c24-ab12-711bf928b181",  # CBD 2025 update
    "DOPS": "159831f9-6d22-4e77-851b-87e30aee37a2",  # DOPS ST3-ST6 2025 update
    "LAT":  "eb1c7547-0f41-49e7-95de-8adffd849924",  # LAT 2025 update v9
    "ACAT": "6577ab06-8340-47e3-952a-708a5f800dcc",  # ACAT ACCS 2025 update
    "ACAF": "15e67ae8-868b-4358-9b96-30a4a272f02c",  # ACAF 2025 update
    "STAT": "41ff54b8-35a7-414b-9bd6-97fb1c3eb189",  # STAT 2025 update
    "MSF":  "5f71ac04-ff45-44d2-b7a1-f8b921a8a4c8",  # MSF
    "MINI_CEX": "647665f4-a992-4541-9e17-33ba6fd1d347",  # Mini-CEX 2025 update
    "JCF":  "3daa9559-3c31-4ab4-883c-9a991632a9ca",  # Journal Club 2025 update
    "QIAT": "a0aa5cfc-57be-4622-b974-51d334268d57",  # EM QIAT 2025 update
}

# Words/phrases to remove from reflection (humanizer)
SLOP_PATTERNS = [
    r"\s*—\s*",  # em dashes -> regular dashes or remove
    r"\bdelve\b",
    r"\bnavigate\b",
    r"\bcrucial\b",
    r"\bit's worth noting\b",
    r"\bimportantly\b",
    r"\bcomprehensive\b",
    r"\bon the other hand\b",
    r"\bin summary\b",
    r"\bto summarise\b",
    r"\bto summarize\b",
    r"\bmoreover\b",
    r"\bfurthermore\b",
]


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    return _client


async def classify_intent(text: str) -> str:
    """Classify user message intent: 'chitchat', 'question', or 'case'."""
    client = _get_client()

    prompt = """Classify this message into exactly one category:

- chitchat: greetings, thanks, short social messages (hi, hello, thanks, bye, ok, great, etc.)
- question: asking about what the bot does, how it works, capabilities, help requests
- case: a clinical case description suitable for portfolio filing (contains patient details, symptoms, management, procedures, or clinical scenarios)

Message: """

    contents = f"{prompt}{text}\n\nRespond with ONLY one word: chitchat, question, or case"
    response = await _gemini_call_with_retry(
        lambda: client.models.generate_content(model="gemini-3-flash-preview", contents=contents)
    )
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
    response = await _gemini_call_with_retry(
        lambda: client.models.generate_content(model="gemini-3-flash-preview", contents=contents)
    )
    return response.text.strip()


def _humanize_reflection(text: str) -> str:
    """Remove AI-sounding phrases from reflection text."""
    result = text
    # Replace em dashes with regular dashes
    result = re.sub(r"\s*—\s*", " - ", result)
    # Remove slop words/phrases
    for pattern in SLOP_PATTERNS[1:]:  # skip em dash pattern (already handled)
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    # Clean up double spaces
    result = re.sub(r"  +", " ", result)
    result = result.strip()
    return result


async def recommend_form_types(case_description: str) -> List[FormTypeRecommendation]:
    """Recommend applicable WPBA form types based on case description."""
    client = _get_client()

    system_prompt = """Analyze this clinical case and recommend which WPBA forms apply.

Rules:
- CBD: Always if trainee managed a clinical case (retrospective reasoning discussion)
- LAT: If resus leadership, leading a shift, coordinating the department, managing a major incident
- DOPS: If trainee personally performed a procedure
- ACAT: If description covers a full shift or multiple patients observed
- MINI_CEX: If someone directly observed the trainee seeing a patient (real-time bedside observation)
- ACAF: If trainee searched literature or critically appraised evidence
- JCF: If trainee presented at a journal club
- STAT: If trainee delivered a structured teaching session
- QIAT: If trainee completed or is presenting a QI project
- MSF: If trainee is requesting 360-degree colleague feedback
- Never recommend more than 3 forms

Return ONLY a JSON array:
[{"form_type": "CBD", "rationale": "one-line reason"}, ...]

Only include forms that clearly apply. CBD is almost always applicable."""

    prompt = f"{system_prompt}\n\nCase description:\n{case_description}"
    response = await _gemini_call_with_retry(
        lambda: client.models.generate_content(model="gemini-3-flash-preview", contents=prompt)
    )
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


async def extract_cbd_data(case_description: str) -> CBDData:
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
  "stage_of_training": "Higher/ST4-ST6",
  "trainee_role": "e.g. 'Primary clinician with indirect supervision'",
  "clinical_reasoning": "what the trainee thought, investigated, and did — and why",
  "reflection": "what was learned from this case / learning points — write in direct, first-person clinical language",
  "level_of_supervision": "Direct" or "Indirect" or "Distant",
  "supervisor_name": null or "Name if mentioned",
  "curriculum_links": ["SLO1", "SLO3"],
  "key_capabilities": [
    "SLO1 KC1: to be expert in assessing and managing all adult patients attending the ED. These capabilities will apply to patients attending with both physical and psychological ill health (2025 Update)",
    "SLO3 KC1: able to support the pre-hospital, medical, nursing and administrative team in answering clinical questions and in making safe decisions for patients with appropriate levels of risk in the ED (2025 Update)",
    "SLO3 KC2: aware of when it is appropriate to review patients remotely or directly and able to teach these principles to others (2025 Update)"
  ]
}}

Stage of Training mapping:
- FY1/FY2/CT1/CT2 → "Intermediate/ST3"
- ST3/ST4/ST5/ST6/SpR/registrar → "Higher/ST4-ST6"
- Paediatric EM trainee → "PEM"
- ACCS trainee → "ACCS"
- If unclear, default to "Higher/ST4-ST6"

===== CURRICULUM LINKS — STRICT RULES =====

RCEM Higher EM Curriculum (2025 Update) — Exact Kaizen Checkbox Labels:
Source: Live Kaizen CBD form screenshot (verified 2026-03-08)
IMPORTANT: Use these EXACT SLO numbers and KC texts — Kaizen's numbering differs from the curriculum website.

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

SLO6 (Paeds): Care for children of all ages, at all stages of development and with complex needs (2025 Update)
  KC1: be expert in assessing and managing all children and young adult patients attending the ED. These capabilities will apply to patients attending with both physical and psychological ill health and social concerns (2025 Update)
  KC2: be able to provide airway management & ventilatory support to critically ill paediatric patients (2025 Update)
  KC3: be able to lead and support a multidisciplinary paediatric resuscitation including trauma (2025 Update)
  KC4: be expert in fluid management and circulatory support in critically ill paediatric patients (2025 Update)
  KC5: be able to manage all the life-threatening paediatric conditions including peri-arrest & arrest situations in the ED (2025 Update)
  KC6: be able to assess and formulate a management plan for children and young adults who present with complex medical and social needs (2025 Update)

SLO6 (Procedures): Deliver key procedural skills needed in EM (2025 Update)
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

SLO9 (Teaching): Support, supervise & educate others working in the ED (2025 Update)
  KC1: be able to undertake training and supervision of members of the ED team in the clinical environment (2025 Update)
  KC2: be able to prepare and deliver teaching sessions outside of the clinical environment, including simulation, small group work, and didactic presentations (2025 Update)
  KC3: be able to provide effective constructive feedback to colleagues, including debrief (2025 Update)
  KC4: understand the principles necessary to mentor and appraise junior doctors (2025 Update)

SLO9 (Research): Participate in research and manage data appropriately (2025 Update)
  KC1: be able to appraise, synthesise, communicate and use research evidence to develop EM care (2025 Update)
  KC2: be able to actively participate in research (2025 Update)

SLO10: Participate in & promote activity to improve quality & safety of patient care (2025 Update)
  KC1: be able to provide clinical leadership on effective Quality Improvement work (2025 Update)
  KC2: be able to support and develop a culture of departmental safety, and good clinical governance (2025 Update)

SLO12: Lead & Manage (2025 Update)
  KC1: be able to demonstrate their involvement in a range of management activities and show an understanding of the relevant medicolegal directives (elements not completed in Intermediate) (2025 Update)
  KC2: be able to investigate a patient safety incident, participate and contribute effectively to department clinical governance activities and risk reduction processes (2025 Update)
  KC3: be able to manage the staff rota being aware of relevant employment law and recruitment activities including interviews and involvement in workforce planning (2025 Update)
  KC4: be able to effectively represent the ED at inter-specialty meetings (2025 Update)
  KC5: demonstrate an understanding of how effective Emergency Medicine Leadership positively impacts on standards of patient care and patient safety (2025 Update)
  KC6: demonstrate a positive impact on the culture of the Emergency Department through attitudes and behaviours that impact positively on colleagues, patients and their relatives (2025 Update)

CRITICAL — Only select SLOs if the case DIRECTLY DEMONSTRATES that capability.
Use Kaizen's SLO numbering (above) — NOT the curriculum website numbering.

✅ SELECT SLO5 if: Patient was actually resuscitated, intubated, had cardiac arrest, airway emergency, or required immediate stabilisation
❌ DO NOT select SLO5 if: Patient was GCS 15, stable, or just "in resus" for observation

✅ SELECT SLO6 (Procedures) if: Trainee personally performed a procedure (LP, central line, chest drain, intubation, etc.)
❌ DO NOT select SLO6 (Procedures) if: Procedure was indicated but not performed, or performed by someone else

✅ SELECT SLO4 if: Patient had trauma/injury that trainee managed
❌ DO NOT select SLO4 if: Medical patient with incidental minor injury

✅ SELECT SLO6 (Paeds) if: Patient was a child/young person under 16
❌ DO NOT select SLO6 (Paeds) if: Adult patient

✅ SELECT SLO8 if: Trainee led the shift, coordinated the department
❌ DO NOT select SLO8 if: Trainee just saw patients on a shift

✅ SELECT SLO9 (Teaching) if: Trainee delivered teaching or supervised a junior colleague
❌ DO NOT select SLO9 (Teaching) if: Trainee was taught BY a consultant

✅ SELECT SLO5 if: Patient was a child/young person under 16
❌ DO NOT select SLO5 if: Adult patient

✅ SELECT SLO8 if: Trainee led the shift, coordinated the department
❌ DO NOT select SLO8 if: Trainee just saw patients on a shift

NEGATIVE EXAMPLES (do NOT select):
- "Patient in resus with headache, GCS 15, LP performed" → NO SLO3 (patient stable, no resuscitation)
- "Discussed management with consultant" → NO SLO9 (no teaching/supervision by trainee)
- "Busy shift with multiple patients" → NO SLO8 (no shift leadership)

Return 2-4 SLOs that are DIRECTLY demonstrated. Aim for breadth across the case.

===== KEY CAPABILITIES — SELECT 3-5 PER FORM =====

Key Capabilities are sub-competencies within each SLO. Use the FULL KC description text from the list above.
Format: return as a list of strings like: "SLO1 KC1: Competent in ECG, clinical image, and biochemical assay interpretation"

Rules:
- Only select KCs if you selected the parent SLO
- Select ALL KCs that are directly demonstrated — aim for 3 to 5 total
- Use the FULL KC description text, not just the code
- If a case demonstrates multiple aspects of one SLO, select multiple KCs from that SLO
- It is better to select 4-5 well-matched KCs than to underselect

===== REFLECTION STYLE =====

Write the reflection in direct, first-person clinical language:
- Use "I" statements
- Be specific about learning points
- Avoid: em dashes, "delve", "navigate", "crucial", "importantly", "comprehensive", "moreover", "furthermore", "on the other hand", "in summary"

===== RULES =====
- Extract only what is stated or clearly implied. Do not fabricate clinical details.
- For unspecified fields, use a reasonable placeholder like "Not specified in description".
- Today's date: {date.today()}
- Return ONLY the JSON. No explanation."""

    prompt = f"{system_prompt}\n\nCase description:\n{case_description}"
    response = await _gemini_call_with_retry(
        lambda: client.models.generate_content(model="gemini-3-flash-preview", contents=prompt)
    )
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
        retry_response = await _gemini_call_with_retry(
            lambda: client.models.generate_content(model="gemini-3-flash-preview", contents=retry_prompt)
        )
        retry_raw = retry_response.text.strip()
        if retry_raw.startswith("```"):
            retry_raw = retry_raw.split("```")[1]
            if retry_raw.startswith("json"):
                retry_raw = retry_raw[4:]
        retry_raw = retry_raw.strip()
        data = json.loads(retry_raw)

    # Apply humanizer to reflection
    if "reflection" in data:
        data["reflection"] = _humanize_reflection(data["reflection"])

    # Ensure key_capabilities exists
    if "key_capabilities" not in data:
        data["key_capabilities"] = []

    return CBDData(**data)


async def extract_form_data(case_description: str, form_type: str) -> FormDraft:
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
    json_template = "{\n" + ",\n".join([f'  "{k}": "<extracted value>"' for k in field_keys]) + "\n}"

    system_prompt = f"""You are a medical portfolio assistant. Extract data for a {schema['name']} ({form_type}) WPBA entry.

Return ONLY a JSON object with these exact keys:
{json_template}

Field definitions:
{chr(10).join(field_defs)}

Rules:
- For dropdown fields: return ONLY one of the listed options. If unclear, use the first option.
- For multi_select fields: return a list of values from the listed options.
- For kc_tick fields: return a list of SLO strings e.g. ["SLO1", "SLO3"]
- For date fields: return YYYY-MM-DD. Use today if not mentioned: {date.today()}
- For text fields: extract directly from the case, be concise and clinical
- Do not fabricate details not present in the case
- Return ONLY the JSON object. No explanation.

Case description:
{case_description}"""

    response = await _gemini_call_with_retry(
        lambda: client.models.generate_content(model="gemini-3-flash-preview", contents=system_prompt)
    )
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
        retry_response = await _gemini_call_with_retry(
            lambda: client.models.generate_content(model="gemini-3-flash-preview", contents=retry_prompt)
        )
        retry_raw = retry_response.text.strip()
        if retry_raw.startswith("```"):
            retry_raw = retry_raw.split("```")[1]
            if retry_raw.startswith("json"):
                retry_raw = retry_raw[4:]
        retry_raw = retry_raw.strip()
        data = json.loads(retry_raw)

    # Apply humanizer to reflection if present
    if "reflection" in data and data["reflection"]:
        data["reflection"] = _humanize_reflection(data["reflection"])

    return FormDraft(
        form_type=form_type,
        fields=data,
        uuid=FORM_UUIDS.get(form_type)
    )
