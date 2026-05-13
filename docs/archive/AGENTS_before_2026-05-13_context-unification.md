# Portfolio Guru — AGENTS.md

## Project

Portfolio Guru automates e-portfolio filing for UK EM trainees.
Doctor sends a clinical case via Telegram (text, voice, or photo) → Gemini extracts structured WPBA data → shows draft for approval → routes the form through deterministic Playwright filing with browser-use fallback where supported. Live supervisor submission is never automatic.

## Stack

- Telegram bot: python-telegram-bot v21+ (polling mode)
- LLM extraction/vision/voice: Google Gemini 3 Flash Preview (`gemini-3-flash-preview`)
- Browser automation: deterministic Playwright + browser-use fallback (Chromium)
- Credential store: Fernet-encrypted SQLite (via SQLModel)
- State persistence: PicklePersistence (survives restarts)
- Target platform: Kaizen ePortfolio (eportfolio.rcem.ac.uk → kaizenep.com)
- Deployment: background process on Mac Mini M4 (macOS). No launchd plist yet — started manually. Logs at /tmp/portfolio-guru-bot.log

## Key Constraints

- NEVER log credentials (username, password, or decrypted values)
- NEVER submit any form — draft save only
- NEVER send to supervisor — that's the doctor's action
- Bot token in TELEGRAM_BOT_TOKEN env var
- Google API key in GOOGLE_API_KEY env var
- Fernet key in FERNET_SECRET_KEY env var
- macOS host (Mac Mini M4) — NO systemd, NO systemctl. Use launchd or manual process management.

## Supported Forms

`FORM_SCHEMAS` currently covers 50 forms and `FORM_UUIDS` covers 72 form routes. The router supports deterministic Playwright filing plus browser-use fallback. Treat real Kaizen filing reliability as partially verified until the skipped Kaizen filer tests are rewritten and at least one non-live deterministic filing smoke test exists.

## Kaizen Form UUIDs

Form UUIDs are in `backend/extractor.py` → `FORM_UUIDS` dict.
Kaizen URL pattern: `https://kaizenep.com/events/new-section/<UUID>`
Date format: Kaizen expects d/m/yyyy (e.g. 6/3/2026), not ISO.

## File Structure

```
portfolio-guru/
├── backend/
│   ├── bot.py           # Telegram bot — conversation handler, draft preview, approval flow
│   ├── extractor.py     # Gemini extraction — CBD, generic forms, recommendations, intent classification
│   ├── form_schemas.py  # Ground-truth Kaizen form schemas (19 forms)
│   ├── models.py        # Pydantic models — CBDData, FormDraft, FormTypeRecommendation
│   ├── filer.py         # browser-use CBD auto-filer (Playwright + Gemini)
│   ├── vision.py        # Image extraction via Gemini Vision
│   ├── whisper.py       # Voice transcription via Gemini native audio
│   ├── credentials.py   # Fernet-encrypted credential store (SQLite)
│   ├── profile_store.py # Training level store (SQLite)
│   ├── store.py         # Unified store — picks SQLite or Render backend
│   ├── config.py        # BWS credential loading (dev)
│   ├── render_store.py  # Render.com env var store (production)
│   ├── main.py          # FastAPI app (legacy, not used in polling mode)
│   ├── run_local.sh     # Local dev startup script (loads BWS secrets)
│   ├── venv/            # Python virtualenv — activate with: source venv/bin/activate
│   └── requirements.txt
├── AGENTS.md            # This file
└── WORKFLOWS.md         # Agent-readable workflow definitions
```

## Conversation States

AWAIT_USERNAME=0, AWAIT_PASSWORD=1, AWAIT_FORM_CHOICE=2, AWAIT_APPROVAL=3,
AWAIT_EDIT_FIELD=4, AWAIT_EDIT_VALUE=5, AWAIT_CASE_INPUT=6, AWAIT_TRAINING_LEVEL=7

## Key Design Decisions

- **Local development only until well-tested** — Decision 2026-03-11: Keep bot running locally via systemd until fully functional. No Render/Railway deploy until core features stable and tested.
- `allow_reentry=False` on case_conv — prevents voice/photo from restarting conversation mid-edit
- PicklePersistence with `_store_draft`/`_load_draft` helpers — Pydantic objects stored as plain dicts
- KC-first curriculum selection — KCs picked directly from case context, SLOs derived
- `curriculum_links` = SLO codes only; `key_capabilities` = full KC strings
- All Gemini calls wrapped in `_gemini_call_with_retry` with `run_in_executor` (never blocks event loop)
- Stale button guard on `handle_form_choice` — expired buttons show clean message

## Session Continuity

Builder uses persistent Codex sessions for this project via cc-sessions.json.
Sessions are resumed via `resumeSessionId` with `mode: "session"`.

## Current Recovery Focus

Use `TASK.md` for the active recovery sprint. Current priority is docs/hygiene/filing confidence before adding features. `/bulk`, `/unsigned`, and `/chase` have inconsistent disabled vs legacy code paths and must be resolved deliberately.

## TASK-HISTORY.md

TASK-HISTORY.md is for human reading only — never reference or import it as agent context.

## Testing

- Bot token BWS ID: af553b7d-5c05-418a-b80e-b405015708ed
- Google API key BWS ID: af6579a0-2cbe-4cef-94b3-b405017b48fe
- Fernet key BWS ID: 9e653679-9a33-4c23-a15c-b405015713de
- Test layers: smoke, unit, flow walker, offline E2E (process_update), Telethon E2E, live Telegram, Gemini API mocks, message snapshots
- Testing contract: see `TESTING.md`
- Test account: Create via /setup in bot
- Restart: `pkill -f "bot.py" && sleep 2 && cd /Users/moeedahmed/projects/portfolio-guru/backend && nohup venv/bin/python3 bot.py >> /tmp/portfolio-guru-bot.log 2>&1 &`
- Logs: `tail -f /tmp/portfolio-guru-bot.log`
- Reinstall git hook: `cp .githooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`

## Running Tests

Every Codex session must run the test suite before declaring done:

```bash
cd /Users/moeedahmed/projects/portfolio-guru/backend
source venv/bin/activate
python -m pytest tests/ -v --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py 2>&1 | tail -30
```

All tests must pass before reporting completion to Moeed. If tests fail, fix them in the same session before reporting.

Additional test commands:

```bash
cd /Users/moeedahmed/projects/portfolio-guru/backend
source venv/bin/activate
pytest tests/ -v -m e2e            # offline E2E only (process_update)
pytest tests/ -v -m live           # live Telegram tests (manual only, needs TELETHON_SESSION)
pytest tests/ -v --snapshot-update
pytest tests/test_kaizen_integration.py -v -m kaizen -s  # Filer integration (manual only — runs real Playwright against Kaizen, leaves draft entries to delete)
```

## Autonomous Test Suite

Every Codex session MUST run the full test suite before reporting done:

```bash
cd /Users/moeedahmed/projects/portfolio-guru/backend
source venv/bin/activate
python -m pytest tests/ -v --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py 2>&1 | tail -40
```

If any test fails:

1. Fix the bug in the bot code
2. Re-run the tests
3. Repeat until all pass
4. Only then report completion

Test categories:

- test_forms.py — labels, grades, curriculum filter
- test_extraction.py — form detection, schema coverage
- test_conversation.py — imports, state definitions, keyboard layout
- test_flow_walker.py — full conversation paths, button coverage, dead ends, guardrails
- test_gemini_mocks.py — Gemini API edge cases via mocked HTTP
- test_snapshots.py — message snapshots for key bot outputs
- test_e2e.py — live Telegram E2E checks via Telethon

If a test failure requires a design decision, mark it `pytest.mark.skip(reason="...")` and flag it in the completion message.
