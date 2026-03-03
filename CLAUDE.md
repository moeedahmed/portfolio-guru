# Portfolio Guru — CLAUDE.md

## Project
Portfolio Guru automates e-portfolio filing for doctors in training.
Doctor describes a clinical case → LLM extracts structured WPBA data → browser-use logs into Kaizen → fills CBD form → saves as draft.

## Stack
- Backend: FastAPI + Python 3.12
- Browser automation: browser-use + Playwright (Chromium)
- LLM extraction: Anthropic Claude (claude-haiku-4-5 for extraction)
- Credentials: Bitwarden Secrets Manager (BWS)
- Target platform: Kaizen ePortfolio (eportfolio.rcem.ac.uk)
- Deployment: Railway (Docker container)
- Screenshot storage: Supabase (shared instance: eqljsghnuiysgruwztxs)

## Phase 1 Scope
Text input only. CBD form type only. Draft save only (no submit, no supervisor request).
No frontend for MVP — validate the filing engine end-to-end via API.

## Credentials Pattern
```python
import subprocess, json

def get_secret(secret_id: str) -> str:
    bws_token = open(os.path.expanduser("~/.openclaw/.bws-token")).read().strip()
    result = subprocess.run(
        ["/usr/local/bin/bws", "secret", "get", secret_id, "--output", "json"],
        env={**os.environ, "BWS_ACCESS_TOKEN": bws_token},
        capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)["value"]

KAIZEN_USERNAME_ID = "6e14d32b-6fff-480d-87b0-b3f300ee30f6"
KAIZEN_PASSWORD_ID = "f311d41a-fa77-44f8-be42-b3f300ee3e08"
```

## Key Constraints
- NEVER log credentials
- NEVER submit the CBD — draft save only
- NEVER send to supervisor — that's the doctor's action
- Always capture screenshot before returning

## File Structure
```
portfolio-guru/
├── backend/
│   ├── main.py        # FastAPI app, /api/file endpoint
│   ├── extractor.py   # LLM extraction → CBDData
│   ├── filer.py       # browser-use agent → Kaizen
│   ├── models.py      # Pydantic schemas
│   ├── config.py      # BWS credential loading
│   ├── Dockerfile
│   └── requirements.txt
└── CLAUDE.md
```
