# TASK.md — Portfolio Guru Phase 1

## Objective
Build the Portfolio Guru filing engine. Text description of a clinical case → LLM extracts structured CBD data → browser-use logs into Kaizen → fills CBD form → saves as draft → returns screenshot proof.

Phase 1 is backend only. No frontend. Validate the engine works end-to-end.

## Read CLAUDE.md first for project context and credentials pattern.

---

## Step 1: models.py

Create `backend/models.py` with these Pydantic models:

```python
from pydantic import BaseModel
from typing import Optional, List, Any

class CBDData(BaseModel):
    patient_age: str
    patient_presentation: str   # presenting complaint / chief complaint
    clinical_setting: str       # e.g. "Emergency Department - Resus"
    trainee_role: str           # e.g. "Primary clinician with indirect supervision"
    clinical_reasoning: str     # what the trainee thought/did/why
    learning_points: str        # what was learned from this case
    level_of_supervision: str   # "Direct" | "Indirect" | "Distant"
    supervisor_name: Optional[str] = None
    date_of_encounter: str      # ISO date string YYYY-MM-DD

class FileRequest(BaseModel):
    case_description: str
    dry_run: bool = False       # if True: extract only, no browser

class ActionStep(BaseModel):
    step: int
    action: str
    success: bool
    detail: Optional[str] = None

class FileResponse(BaseModel):
    status: str                 # "success" | "partial" | "failed" | "dry_run"
    extracted_data: Optional[CBDData] = None
    action_log: List[ActionStep] = []
    screenshot_url: Optional[str] = None
    error: Optional[str] = None
```

---

## Step 2: config.py

Create `backend/config.py`:

```python
import os
import subprocess
import json

KAIZEN_USERNAME_ID = "6e14d32b-6fff-480d-87b0-b3f300ee30f6"
KAIZEN_PASSWORD_ID = "f311d41a-fa77-44f8-be42-b3f300ee3e08"

def get_bws_secret(secret_id: str) -> str:
    """Fetch a secret from Bitwarden Secrets Manager."""
    bws_token_path = os.path.expanduser("~/.openclaw/.bws-token")
    
    # In production (Railway), BWS_ACCESS_TOKEN is set as env var
    bws_token = os.environ.get("BWS_ACCESS_TOKEN")
    if not bws_token and os.path.exists(bws_token_path):
        with open(bws_token_path) as f:
            bws_token = f.read().strip()
    
    if not bws_token:
        raise ValueError("BWS_ACCESS_TOKEN not available")
    
    result = subprocess.run(
        ["/usr/local/bin/bws", "secret", "get", secret_id, "--output", "json"],
        env={**os.environ, "BWS_ACCESS_TOKEN": bws_token},
        capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)["value"]

def get_kaizen_credentials() -> tuple[str, str]:
    """Returns (username, password) for Kaizen."""
    username = os.environ.get("KAIZEN_USERNAME") or get_bws_secret(KAIZEN_USERNAME_ID)
    password = os.environ.get("KAIZEN_PASSWORD") or get_bws_secret(KAIZEN_PASSWORD_ID)
    return username, password
```

---

## Step 3: extractor.py

Create `backend/extractor.py`:

Use the Anthropic SDK (anthropic package) with claude-haiku-4-5.
Make a structured extraction call that takes the raw case description and returns a CBDData object.

The system prompt should instruct the LLM to:
- Extract structured data from a doctor's free-text clinical case description
- Return ONLY valid JSON matching the CBDData schema
- For any field not mentioned in the text, make a reasonable inference or use "Not specified"
- For date_of_encounter: if not mentioned, use today's date (ISO format)
- For level_of_supervision: infer from context (junior doctor = likely Direct; senior = Indirect/Distant)
- Never fabricate clinical details — only extract or note as unspecified

Use instructor or json_repair for robust JSON parsing from LLM output.
If instructor is not available, use raw JSON parsing with a fallback retry.

Return CBDData on success. Raise a clear ValueError with missing fields on failure.

```python
import anthropic
import json
import os
from datetime import date
from models import CBDData

def extract_cbd_data(case_description: str) -> CBDData:
    """Extract structured CBD data from free-text case description."""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    
    system_prompt = """You are a medical portfolio assistant. Extract structured data from a doctor's clinical case description for a Case-Based Discussion (CBD) WPBA entry.

Return ONLY a JSON object with these exact fields:
{
  "patient_age": "age as string e.g. '45-year-old'",
  "patient_presentation": "presenting complaint / chief complaint",
  "clinical_setting": "e.g. 'Emergency Department - Resus', 'Majors', 'Minors'",
  "trainee_role": "e.g. 'Primary clinician with indirect supervision'",
  "clinical_reasoning": "what the trainee thought, investigated, and did — and why",
  "learning_points": "what was learned from this case",
  "level_of_supervision": "Direct" or "Indirect" or "Distant",
  "supervisor_name": null or "Name if mentioned",
  "date_of_encounter": "YYYY-MM-DD — today if not mentioned"
}

Rules:
- Extract only what is stated or clearly implied. Do not fabricate clinical details.
- For unspecified fields, use a reasonable placeholder like "Not specified in description".
- Today's date: """ + str(date.today()) + """
- Return ONLY the JSON. No explanation."""

    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": case_description}]
    )
    
    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    
    data = json.loads(raw)
    return CBDData(**data)
```

---

## Step 4: filer.py

This is the critical module. Use browser-use to automate Kaizen.

Install: `pip install browser-use`
browser-use uses Playwright and an LLM to navigate the browser.

```python
import asyncio
import os
import base64
from datetime import datetime
from typing import List, Optional
from models import CBDData, ActionStep
from config import get_kaizen_credentials

async def file_cbd_to_kaizen(cbd_data: CBDData) -> tuple[str, List[ActionStep], Optional[str]]:
    """
    File a CBD entry to Kaizen using browser-use.
    
    Returns: (status, action_log, screenshot_base64)
    status: "success" | "partial" | "failed"
    """
    from browser_use import Agent, Browser, BrowserConfig
    from langchain_anthropic import ChatAnthropic
    
    username, password = get_kaizen_credentials()
    action_log: List[ActionStep] = []
    screenshot_b64: Optional[str] = None
    
    # Build the task description for the browser-use agent
    task = f"""
    Complete the following steps on the Kaizen ePortfolio website (https://eportfolio.rcem.ac.uk):
    
    1. Go to https://eportfolio.rcem.ac.uk and log in with:
       - Username: {username}
       - Password: {password}
    
    2. Navigate to create a new Case-Based Discussion (CBD) assessment/entry.
       Look for "New Entry", "Add Entry", "CBD", or similar in the navigation.
    
    3. Fill in the CBD form with these values:
       - Patient age / details: {cbd_data.patient_age}
       - Presenting complaint: {cbd_data.patient_presentation}
       - Clinical setting: {cbd_data.clinical_setting}
       - Your role: {cbd_data.trainee_role}
       - Clinical reasoning / discussion: {cbd_data.clinical_reasoning}
       - Learning points: {cbd_data.learning_points}
       - Level of supervision: {cbd_data.level_of_supervision}
       - Date of encounter: {cbd_data.date_of_encounter}
       {f"- Supervisor name: {cbd_data.supervisor_name}" if cbd_data.supervisor_name else "- Leave supervisor field blank or as 'TBC'"}
    
    4. IMPORTANT: Save as DRAFT only. Do NOT submit. Do NOT send to supervisor.
       Look for a "Save as Draft", "Save Draft", or "Save" button (not "Submit" or "Send").
    
    5. After saving, take a screenshot of the saved draft confirmation page.
    
    CRITICAL RULES:
    - Never click Submit or Send to Supervisor
    - If you cannot find the CBD form, report exactly what you see on the navigation menu
    - If a field doesn't exist exactly as described, find the closest matching field
    - If you see a CAPTCHA or MFA challenge, stop and report it
    """
    
    browser_config = BrowserConfig(headless=True)
    browser = Browser(config=browser_config)
    
    llm = ChatAnthropic(
        model="claude-opus-4-5",  # Use Opus for reliable browser navigation
        api_key=os.environ.get("ANTHROPIC_API_KEY")
    )
    
    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
    )
    
    try:
        result = await agent.run(max_steps=30)
        
        # Extract action history from result
        # browser-use result contains action history
        if hasattr(result, 'history'):
            for i, step in enumerate(result.history):
                action_log.append(ActionStep(
                    step=i+1,
                    action=str(step.model_output) if hasattr(step, 'model_output') else str(step),
                    success=True
                ))
        
        # Take final screenshot
        try:
            page = await browser.get_current_page()
            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
        except Exception as ss_err:
            pass
        
        # Check if draft was saved (look for success indicators in result)
        result_str = str(result).lower()
        if any(word in result_str for word in ['draft', 'saved', 'success', 'created']):
            status = "success"
        else:
            status = "partial"
            
    except Exception as e:
        action_log.append(ActionStep(
            step=len(action_log)+1,
            action="agent_error",
            success=False,
            detail=str(e)
        ))
        status = "failed"
        # Still try to get screenshot
        try:
            page = await browser.get_current_page()
            screenshot_bytes = await page.screenshot()
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
        except:
            pass
    finally:
        await browser.close()
    
    return status, action_log, screenshot_b64
```

---

## Step 5: main.py

Create `backend/main.py`:

```python
import asyncio
import os
import base64
import tempfile
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from models import FileRequest, FileResponse, ActionStep
from extractor import extract_cbd_data
from filer import file_cbd_to_kaizen

app = FastAPI(title="Portfolio Guru API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok", "service": "portfolio-guru"}

@app.post("/api/file", response_model=FileResponse)
async def file_entry(request: FileRequest):
    # Step 1: Extract structured data
    try:
        cbd_data = extract_cbd_data(request.case_description)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM error: {str(e)}")
    
    # Dry run: return extracted data only
    if request.dry_run:
        return FileResponse(
            status="dry_run",
            extracted_data=cbd_data,
        )
    
    # Step 2: File to Kaizen via browser-use
    try:
        status, action_log, screenshot_b64 = await file_cbd_to_kaizen(cbd_data)
    except Exception as e:
        return FileResponse(
            status="failed",
            extracted_data=cbd_data,
            error=str(e)
        )
    
    # Step 3: Store screenshot (save locally for MVP, Supabase in V2)
    screenshot_url = None
    if screenshot_b64:
        screenshot_dir = "/tmp/portfolio-guru-screenshots"
        os.makedirs(screenshot_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        screenshot_path = f"{screenshot_dir}/cbd-draft-{timestamp}.png"
        with open(screenshot_path, "wb") as f:
            f.write(base64.b64decode(screenshot_b64))
        screenshot_url = f"file://{screenshot_path}"  # local for MVP
    
    return FileResponse(
        status=status,
        extracted_data=cbd_data,
        action_log=action_log,
        screenshot_url=screenshot_url,
    )
```

---

## Step 6: requirements.txt

Create `backend/requirements.txt`:
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
anthropic>=0.40.0
langchain-anthropic>=0.3.0
browser-use>=0.1.40
playwright>=1.49.0
pydantic>=2.0.0
httpx>=0.27.0
```

---

## Step 7: Dockerfile

Create `backend/Dockerfile`:

```dockerfile
FROM python:3.12-slim

# System deps for Playwright/Chromium
RUN apt-get update && apt-get install -y \
    wget gnupg curl \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libasound2 fonts-liberation libx11-6 libxext6 libxfixes3 \
    && rm -rf /var/lib/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Step 8: Test script

Create `backend/test_extraction.py` — a standalone script to test the extraction without browser:

```python
#!/usr/bin/env python3
"""Test CBD extraction without browser. Run: python test_extraction.py"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))

from extractor import extract_cbd_data
import json

TEST_CASE = """
I saw a 67 year old man in resus last night. He came in with sudden onset chest pain, 
8/10, radiating to his left arm, started about 2 hours before arrival. 
He was pale and diaphoretic. I was the FY2 on shift, worked with the SpR who was 
in the department but supervising from a distance. I took the history, examined him,
requested ECG and troponin, spotted the STEMI on ECG and immediately called the SpR
who activated the cath lab. I stayed with the patient while we waited for the team.
The learning point for me was the importance of quick ECG interpretation in chest pain —
I need to be faster at spotting STEMI patterns. The consultant Dr. Ahmed was on call.
"""

if __name__ == "__main__":
    print("Testing CBD extraction...")
    result = extract_cbd_data(TEST_CASE)
    print(json.dumps(result.model_dump(), indent=2))
    print("\n✅ Extraction successful")
```

---

## Step 9: .env.example

Create `backend/.env.example`:
```
ANTHROPIC_API_KEY=your_key_here
# In production: BWS_ACCESS_TOKEN set as Railway env var
# Or set directly:
# KAIZEN_USERNAME=your_kaizen_email
# KAIZEN_PASSWORD=your_kaizen_password
```

---

## Verification Steps (run in order)

1. `cd /home/moeed/projects/portfolio-guru/backend`
2. `pip install -r requirements.txt`
3. `playwright install chromium`
4. `python test_extraction.py` — should print extracted CBDData as JSON
5. `uvicorn main:app --port 8001 --reload`
6. In another terminal: `curl -X POST http://localhost:8001/api/file -H "Content-Type: application/json" -d '{"case_description": "67yo male STEMI in resus, FY2 role, SpR supervision, spotted ECG changes and activated cath lab, learning: ECG interpretation speed", "dry_run": true}'`
7. Verify dry_run returns extracted CBDData correctly
8. Then test without dry_run (requires real Kaizen credentials)

## Important Notes

- ANTHROPIC_API_KEY must be set in env. Check ~/.openclaw/.bws-token or ask Moeed for the key.
- Kaizen credentials fetched from BWS using IDs in config.py
- For the real Kaizen test, browser-use will navigate to eportfolio.rcem.ac.uk — this is a live NHS system, so the draft save is real. Test carefully.
- If browser-use fails to find the CBD form, capture the screenshot and action log — that's valuable debug info for the next iteration.
- Do not worry about Supabase screenshot upload for Phase 1 — local file storage is fine. Add URL to response.

## Done Criteria
- [ ] test_extraction.py runs and returns valid CBDData
- [ ] /api/file with dry_run=true returns 200 with CBDData
- [ ] /api/file with dry_run=false navigates Kaizen and saves CBD draft
- [ ] Screenshot saved and URL returned in response
- [ ] Report back with screenshot of the draft
