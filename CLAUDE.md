# Portfolio Guru — CLAUDE.md

## Project

Portfolio Guru automates e-portfolio filing for UK EM trainees. A doctor sends a clinical case via Telegram (text, voice, or photo); the bot extracts structured WPBA data, recommends/accepts a form type, previews a draft, then saves a Kaizen draft after user approval. Live supervisor submission is never automatic.

## Current Bot State

- Phase: local/private beta on Moeed's Mac Mini.
- Runtime: launchd service `com.portfolioguru.bot`, working directory `/Users/moeedahmed/projects/portfolio-guru`.
- Logs: `/tmp/portfolio-guru-bot.log`, plus `~/Library/Logs/portfolio-guru/launchd.out.log` and `.err.log`.
- Deploy: GitHub Actions self-hosted Mac Mini runner runs `scripts/deploy_mac.sh` on pushes to `main`.
- Current branch: `main`; GitHub/main is the intended source of truth for deploys.
- Supported inputs: text, Telegram voice/audio, and images/photos.
- Supported output: Kaizen draft save only; user reviews/submits manually.
- Disabled/coming soon: `/bulk`, `/unsigned`, `/chase` return coming-soon messages; old code remains after early `return` and must not be treated as live.

## Stack

- Telegram bot: `python-telegram-bot` v21+ in polling mode.
- LLM extraction: Gemini fast model from `backend/model_config.py`, with DeepSeek/OpenAI fallback for eligible tiers.
- Vision/voice: Gemini Vision/audio paths, with configured fallback models where implemented.
- Browser automation: deterministic Playwright plus browser-use fallback where supported.
- Storage: Fernet-encrypted SQLite credentials/profile/state stores; PicklePersistence for Telegram conversation state.
- Target platform: Kaizen ePortfolio (`eportfolio.rcem.ac.uk` → `kaizenep.com`).

## Key Constraints

- Never log credentials, decrypted values, or bot/API tokens.
- Never submit forms or send supervisor requests; save drafts only.
- Use launchd on macOS; do not use systemd/systemctl.
- Treat live Kaizen filing as approval-sensitive; manual/live tests can create draft artefacts that may need cleanup.
- If docs disagree with git, tests, launchd status, or logs, runtime evidence wins and docs must be corrected before major work continues.

## Supported Forms

`FORM_SCHEMAS` covers the structured form fields; `FORM_UUIDS` in `backend/extractor.py` maps form routes to Kaizen UUIDs. Verify counts from code before quoting them. Current known doc gap: real Kaizen filing confidence is still only partially verified until skipped Kaizen filer tests are rewritten and a non-live deterministic filing smoke test exists.

Kaizen URL pattern: `https://kaizenep.com/events/new-section/<UUID>`.
Kaizen date format: `d/m/yyyy`.

## Key Files

```text
backend/bot.py              Telegram handlers, draft preview, approval/edit flow
backend/extractor.py        LLM extraction, form recommendation, form UUID routing
backend/form_schemas.py     Kaizen form schema definitions
backend/model_config.py     Model names and fallback ordering
backend/filer.py            browser-use CBD auto-filer path
backend/browser_filer.py    Deterministic/browser filing helpers
backend/vision.py           Image extraction
backend/whisper.py          Audio transcription
backend/credentials.py      Fernet-encrypted credential store
backend/profile_store.py    Training level/curriculum storage
backend/run_local.sh        Local startup with BWS-loaded secrets
start-bot.sh                launchd entrypoint
scripts/deploy_mac.sh       Mac Mini deploy script
docs/MAC_MINI_DEPLOYMENT.md Deployment/runbook truth
docs/continuity/RESUME_BRIEF.md Generated restart snapshot
TASK.md                     One active sprint only
WORKFLOWS.md                Bot flow reference
```

## Conversation States

`AWAIT_USERNAME=0`, `AWAIT_PASSWORD=1`, `AWAIT_FORM_CHOICE=2`, `AWAIT_APPROVAL=3`, `AWAIT_EDIT_FIELD=4`, `AWAIT_EDIT_VALUE=5`, `AWAIT_CASE_INPUT=6`, `AWAIT_TRAINING_LEVEL=7`.

## Key Design Decisions

- Draft-only filing: doctor must review and submit/sign-off manually.
- `allow_reentry=False` on case conversation prevents voice/photo from restarting flow mid-edit.
- Draft persistence stores Pydantic objects as plain dicts via `_store_draft`/`_load_draft`.
- KC-first curriculum selection: KCs picked from case context, SLOs derived.
- `curriculum_links` = SLO codes; `key_capabilities` = full KC strings.
- LLM calls must avoid blocking the event loop.
- Stale button guards should give clean user-facing recovery messages.

## Continuity Protocol

Before substantial work:

1. Read the Notion Portfolio Guru hub: Status, Architecture, Brief, Features, recent Log.
2. Run `check-product-continuity.py /Users/moeedahmed/projects/portfolio-guru --write-resume` and read `docs/continuity/RESUME_BRIEF.md`.
3. Read `TASK.md` and verify it is the single active sprint.
4. Inspect git status, recent commits, relevant tests, and live launchd/log state if runtime matters.
5. Refresh stale Notion/repo context before coding.

After meaningful changes: update Notion Status/Architecture/Features/Log as relevant, update this file only for permanent context changes, update/archive `TASK.md`, run tests, and commit coherent work.

## Testing

Default offline gate:

```bash
cd /Users/moeedahmed/projects/portfolio-guru/backend
source venv/bin/activate
python -m pytest tests/ -v --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py
```

Manual/live gates:

```bash
pytest tests/ -v -m live
pytest tests/test_kaizen_integration.py -v -m kaizen -s
```

Live gates need explicit approval because they can touch Telegram/Kaizen or leave draft artefacts.
