import google.generativeai as genai
import json
import os
from datetime import date
from models import CBDData

genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))


def extract_cbd_data(case_description: str) -> CBDData:
    """Extract structured CBD data from free-text case description."""
    model = genai.GenerativeModel("gemini-1.5-flash")

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
  "reflection": "what was learned from this case / learning points",
  "level_of_supervision": "Direct" or "Indirect" or "Distant",
  "supervisor_name": null or "Name if mentioned",
  "curriculum_links": ["SLO3"]
}}

Stage of Training mapping:
- FY1/FY2/CT1/CT2 → "Intermediate/ST3"
- ST3/ST4/ST5/ST6/SpR/registrar → "Higher/ST4-ST6"
- Paediatric EM trainee → "PEM"
- ACCS trainee → "ACCS"
- If unclear, default to "Higher/ST4-ST6"

Curriculum Links — select the most relevant SLOs from this list (pick 1-3):
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

SLO inference rules:
- Resus/arrest/critical care → SLO3
- Paediatric case → SLO5
- Procedure performed → SLO6
- Trauma/injury → SLO4
- Teaching/supervision → SLO9
- Quality improvement/audit → SLO11
- Management/leadership → SLO8 or SLO12
- Diagnostic uncertainty / stable presentations → SLO1 or SLO2
Return SLO labels only (e.g. ["SLO3", "SLO4"]) — max 3.

Rules:
- Extract only what is stated or clearly implied. Do not fabricate clinical details.
- For unspecified fields, use a reasonable placeholder like "Not specified in description".
- Today's date: {date.today()}
- Return ONLY the JSON. No explanation."""

    prompt = f"{system_prompt}\n\nCase description:\n{case_description}"
    response = model.generate_content(prompt)
    raw = response.text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        data = json.loads(raw)
        return CBDData(**data)
    except (json.JSONDecodeError, ValueError) as e:
        # Retry once with explicit instruction
        retry_prompt = f"Fix the JSON and return ONLY valid JSON. No explanation.\n\nParse error: {e}\n\nOriginal output:\n{raw}"
        retry_response = model.generate_content(retry_prompt)
        retry_raw = retry_response.text.strip()
        if retry_raw.startswith("```"):
            retry_raw = retry_raw.split("```")[1]
            if retry_raw.startswith("json"):
                retry_raw = retry_raw[4:]
        retry_raw = retry_raw.strip()
        data = json.loads(retry_raw)
        return CBDData(**data)
