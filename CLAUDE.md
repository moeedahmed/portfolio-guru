# Portfolio Guru — CLAUDE.md

## Project
Portfolio Guru automates e-portfolio filing for doctors in training.
Doctor sends a text description of a clinical case via Telegram → LLM extracts structured WPBA data → browser-use logs into Kaizen → fills CBD form → saves as draft → replies to Telegram.

## Stack
- Backend: FastAPI + Python 3.12
- Telegram bot: python-telegram-bot v21+
- Browser automation: browser-use + Playwright (Chromium)
- LLM extraction: Anthropic Claude (claude-haiku-4-5)
- LLM navigation: Anthropic Claude (claude-opus-4-5 via browser-use)
- Credential store: Fernet-encrypted SQLite (via SQLModel)
- Target platform: Kaizen ePortfolio (eportfolio.rcem.ac.uk → kaizenep.com)
- Deployment: Railway (Docker container)

## Key Constraints
- NEVER log credentials (username, password, or decrypted values)
- NEVER submit the CBD — draft save only
- NEVER send to supervisor — that's the doctor's action
- Always capture screenshot before returning
- Bot token in TELEGRAM_BOT_TOKEN env var
- Fernet key in FERNET_SECRET_KEY env var

## Kaizen CBD Form
- Login: https://eportfolio.rcem.ac.uk → redirects to kaizenep.com
- CBD URL (2025 Update): https://kaizenep.com/events/new-section/3ce5989a-b61c-4c24-ab12-711bf928b181
- ALWAYS navigate directly to the UUID URL — do not use menus
- Date format: Kaizen expects d/m/yyyy (e.g. 6/3/2026), not ISO

## User Credentials (for testing — dev only)
These are Moeed's Kaizen credentials in Bitwarden Secrets Manager:
- Username BWS ID: 6e14d32b-6fff-480d-87b0-b3f300ee30f6
- Password BWS ID: f311d41a-fa77-44f8-be42-b3f300ee3e08

In production, credentials come from the per-user encrypted SQLite store (credentials.py).

## File Structure
```
portfolio-guru/
├── backend/
│   ├── main.py          # FastAPI app, /api/file endpoint
│   ├── bot.py           # Telegram bot
│   ├── extractor.py     # LLM extraction → CBDData
│   ├── filer.py         # browser-use agent → Kaizen
│   ├── models.py        # Pydantic schemas
│   ├── credentials.py   # Fernet-encrypted credential store
│   ├── config.py        # BWS credential loading (dev fallback)
│   ├── Dockerfile
│   └── requirements.txt
└── CLAUDE.md
```
