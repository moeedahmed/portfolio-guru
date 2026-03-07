from google import genai
import json
import os
import re
import time
from datetime import date
from typing import List, Callable, Any
from models import CBDData, FormTypeRecommendation

_client = None


def _gemini_call_with_retry(fn: Callable[..., Any], *args, retries: int = 3, delay: int = 2) -> Any:
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
                    time.sleep(delay)
                continue
            raise  # Re-raise non-retryable errors immediately
    raise last_error  # All retries exhausted

FORM_UUIDS = {
    "CBD":  "3ce5989a-b61c-4c24-ab12-711bf928b181",
    "ACAT": None,  # TODO: verify UUID from Kaizen
    "DOPS": None,  # TODO: verify UUID from Kaizen
    "LAT":  None,  # TODO: verify UUID from Kaizen
    "STAT": None,  # TODO: verify UUID from Kaizen
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


def classify_intent(text: str) -> str:
    """Classify user message intent: 'chitchat', 'question', or 'case'."""
    client = _get_client()

    prompt = """Classify this message into exactly one category:

- chitchat: greetings, thanks, short social messages (hi, hello, thanks, bye, ok, great, etc.)
- question: asking about what the bot does, how it works, capabilities, help requests
- case: a clinical case description suitable for portfolio filing (contains patient details, symptoms, management, procedures, or clinical scenarios)

Message: """

    contents = f"{prompt}{text}\n\nRespond with ONLY one word: chitchat, question, or case"
    response = _gemini_call_with_retry(
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


def answer_question(text: str) -> str:
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
    response = _gemini_call_with_retry(
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


def recommend_form_types(case_description: str) -> List[FormTypeRecommendation]:
    """Recommend applicable WPBA form types based on case description."""
    client = _get_client()

    system_prompt = """Analyze this clinical case and recommend which WPBA forms apply.

Rules:
- CBD: Always include if trainee managed a clinical case (any case = CBD eligible)
- LAT: Add if resus leadership, leading the department, managing a major incident, coordinating a team
- DOPS: Add if trainee explicitly performed a procedure (LP, intubation, central line, chest drain, etc.)
- ACAT: Add if description covers a full shift or multiple patients
- Never recommend more than 3 forms

Return ONLY a JSON array:
[{"form_type": "CBD", "rationale": "one-line reason"}, ...]

Only include forms that clearly apply. CBD is almost always applicable."""

    prompt = f"{system_prompt}\n\nCase description:\n{case_description}"
    response = _gemini_call_with_retry(
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


def extract_cbd_data(case_description: str) -> CBDData:
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
  "curriculum_links": ["SLO1"],
  "key_capabilities": ["SLO1 KC1"]
}}

Stage of Training mapping:
- FY1/FY2/CT1/CT2 → "Intermediate/ST3"
- ST3/ST4/ST5/ST6/SpR/registrar → "Higher/ST4-ST6"
- Paediatric EM trainee → "PEM"
- ACCS trainee → "ACCS"
- If unclear, default to "Higher/ST4-ST6"

===== CURRICULUM LINKS — STRICT RULES =====

SLO List:
SLO1: Managing stable patients with undifferentiated presentations
SLO2: Formulating clinical questions and finding answers
SLO3: Resuscitating and stabilising patients
SLO4: Managing patients with injuries
SLO5: Managing children and young people
SLO6: Performing procedural skills
SLO7: Managing complex situations
SLO8: Leading a shift
SLO9: Supervising and educating others
SLO10: Conducting research and managing data
SLO11: Improving quality and patient safety
SLO12: Leading and managing the department

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

Return max 2 SLOs. Only include SLOs that are DIRECTLY demonstrated.

===== KEY CAPABILITIES — EVEN STRICTER =====

Key Capabilities are sub-competencies within each SLO. Format: "SLO1 KC1", "SLO6 KC2", etc.

Rules:
- Only select KCs if you selected the parent SLO
- Only select KCs that are DIRECTLY demonstrated by specific actions in the case
- Max 3 KCs total
- If unsure, select fewer KCs — it's better to underselect

Common KC mappings:
- SLO1 KC1: Undifferentiated patient assessment
- SLO3 KC1: Resuscitation/airway management (ONLY if patient actually resuscitated)
- SLO6 KC1-4: Specific procedural skills (ONLY if procedure performed)

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
    response = _gemini_call_with_retry(
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
        retry_response = _gemini_call_with_retry(
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
