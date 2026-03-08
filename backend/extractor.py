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
    "SLO1 KC1: Competent in ECG, clinical image, and biochemical assay interpretation",
    "SLO1 KC3: Recognise and reduce cognitive error (metacognition, debiasing)",
    "SLO3 KC1: Provide airway management and ventilatory support to critically ill patients"
  ]
}}

Stage of Training mapping:
- FY1/FY2/CT1/CT2 → "Intermediate/ST3"
- ST3/ST4/ST5/ST6/SpR/registrar → "Higher/ST4-ST6"
- Paediatric EM trainee → "PEM"
- ACCS trainee → "ACCS"
- If unclear, default to "Higher/ST4-ST6"

===== CURRICULUM LINKS — STRICT RULES =====

RCEM 2021 EM Curriculum (2025 Update) — Full SLO and KC List:

SLO1: Care for physiologically stable adult patients presenting across the full range of complexity
  KC1: Competent in ECG, clinical image, and biochemical assay interpretation
  KC2: Understand diagnostic test methodology and decision rules
  KC3: Recognise and reduce cognitive error (metacognition, debiasing)
  KC4: Manage uncertainty and complexity in undifferentiated presentations

SLO2: Support the ED team by answering clinical questions and making safe decisions
  KC1: Formulate clinical questions and find best evidence
  KC2: Critically appraise and apply evidence to practice
  KC3: Support safe discharge decisions with appropriate advice

SLO3: Identify sick adult patients, resuscitate and stabilise, and know when to stop
  KC1: Provide airway management and ventilatory support to critically ill patients
  KC2: Expert in fluid management and circulatory support in critically ill patients
  KC3: Manage all life-threatening conditions including peri-arrest and cardiac arrest
  KC4: Expert in caring for patients and relatives at end of life in the ED
  KC5: Effectively lead and support resuscitation teams

SLO4: Care for acutely injured patients across the full range of complexity
  KC1: Assessment, investigation, and management of all injuries regardless of complexity
  KC2: Lead the Major Trauma Team and manage with no supervisor involvement

SLO5: Care for children of all ages in the ED, at all stages of development
  KC1: Assess and manage all paediatric presentations including complex needs
  KC2: Lead multidisciplinary paediatric resuscitation including trauma
  KC3: Identify the sick child and initiate appropriate management
  KC4: Manage safeguarding concerns and complex social situations in children

SLO6: Deliver key procedural skills
  KC1: Clinical knowledge to identify when procedural skills are indicated
  KC2: Knowledge and psychomotor skills to perform EM procedures safely and in a timely fashion
  KC3: Supervise and guide colleagues in delivering procedural skills
  KC4: Deliver effective feedback to colleagues on procedural performance

SLO7: Deal with complex and challenging situations in the workplace
  KC1: Manage patients with complex medical, social, or psychiatric needs
  KC2: Manage conflict and challenging behaviour in the ED
  KC3: Manage clinical uncertainty and make decisions under pressure

SLO8: Lead the ED shift
  KC1: Coordinate and prioritise patient flow across the department
  KC2: Lead the clinical team and delegate appropriately during a shift
  KC3: Manage department-level incidents and escalate appropriately

SLO9: Support, supervise and educate
  KC1: Prepare and deliver teaching sessions to colleagues and students
  KC2: Supervise and give feedback to junior colleagues
  KC3: Contribute to the learning environment of the ED

SLO10: Participate in research and manage data appropriately
  KC1: Understand research methodology and apply to clinical practice
  KC2: Manage patient data appropriately and ethically
  KC3: Participate in or lead clinical research activity

SLO11: Participate in and promote quality improvement and patient safety
  KC1: Identify and report patient safety incidents
  KC2: Lead or participate in a quality improvement project
  KC3: Apply QI methodology to improve clinical care

SLO12: Lead and manage
  KC1: Understand health service management and governance structures
  KC2: Demonstrate leadership behaviours and values in clinical practice
  KC3: Contribute to departmental management activities
  KC4: Develop a personal development plan and support others to do so

CRITICAL — Only select SLOs if the case DIRECTLY DEMONSTRATES that capability:

✅ SELECT SLO3 if: Patient was actually resuscitated, intubated, had cardiac arrest, airway emergency, or required immediate stabilisation
❌ DO NOT select SLO3 if: Patient was GCS 15, stable, or just "in resus" for observation

✅ SELECT SLO6 if: Trainee personally performed a procedure (LP, central line, chest drain, intubation, etc.)
❌ DO NOT select SLO6 if: Procedure was indicated but not performed, or performed by someone else

✅ SELECT SLO4 if: Patient had trauma/injury that trainee managed
❌ DO NOT select SLO4 if: Medical patient with incidental minor injury

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
