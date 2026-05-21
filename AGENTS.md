# Portfolio Guru — AGENTS.md

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
- Browser automation: deterministic Playwright via browser-harness CDP for DOM-mapped forms (46 verified Kaizen form types). browser-use (LLM agent) is an emergency bridge only — replaced by browser-harness domain skills for any new platform.
- Storage: Fernet-encrypted SQLite credentials/profile/state stores; PicklePersistence for Telegram conversation state.
- Target platform: Kaizen ePortfolio (`eportfolio.rcem.ac.uk` → `kaizenep.com`).

## Key Constraints

- Never log credentials, decrypted values, or bot/API tokens.
- Never submit forms or send supervisor requests; save drafts only.
- Use launchd on macOS; do not use systemd/systemctl.
- Treat live Kaizen filing as approval-sensitive; manual/live tests can create draft artefacts that may need cleanup.
- If docs disagree with git, tests, launchd status, or logs, runtime evidence wins and docs must be corrected before major work continues.

## Filing Routing Discipline

Single source of truth: `backend/filer_router.py` defines which filing method is used for each form type.

**DOM-mapped forms** → deterministic Playwright via browser-harness CDP (`localhost:18800`). Always. No escalation to browser-use. If Playwright returns partial, the DOM map gap is logged and returned for fixing. Never credentials in LLM prompts.

**Unknown form types** (no DOM mapping on a supported platform) → browser-use via CDP (`localhost:18800`). The persistent Chrome session handles auth; credentials never enter the LLM prompt. If the session is lost, report SESSION_EXPIRED — do not attempt login.

**Unknown platforms** (Horus, SOAR, etc.) → browser-harness + domain skills first. browser-use is an emergency bridge only, replaced by written domain skills. The pattern is: user connects their own Chrome → browser-harness navigates via CDP → learns the layout → persists helpers for next time. No credentials ever touch the system.

**What browser-use is NOT:** browser-use is NOT a substitute for DOM mapping or domain skills. It is a last-resort LLM agent for pages that CDP/Playwright cannot handle (rare). The `browser_use` Python package must never bypass the CDP auth profile with embedded credentials.

## Known Failure Modes

These are common mistakes in this codebase:

- **Disabled features still have code paths:** `/bulk`, `/unsigned`, `/chase` return "coming soon" with an early `return`. Never treat the code below that return as live or production-relevant.
- **Kaizen date format is `d/m/yyyy`:** Not US `m/d/yyyy`. Extracted dates from clinical text need conversion before filing. This is the most common Kaizen filing error.
- **Two separate filer implementations:** `filer.py` (browser-use / CBD) and `browser_filer.py` (deterministic Playwright). They share logic in places but have different failure modes. Tests for one don't cover the other.
- **Fernet-encrypted stores are read-once per process:** The decrypt-and-hold pattern means stale credentials persist in memory until restart. Don't add credential refresh logic — it's deliberate for stability.
- **LLM extraction is non-deterministic:** The same input can produce different form recommendations on different calls. Test extraction with multiple runs, not one-off passes.
- **Gemini fallback ordering:** `model_config.py` defines which models are tried in what order. Adding a new model there means updating all callers — not just the config array.
- **Playwright selectors break on Kaizen UI changes:** Kaizen is a third-party platform that updates without notice. Deterministic selectors (XPath, CSS) are brittle. When tests fail after a Kaizen deploy, check selectors first.
- **launchd logs are rotated:/tmp/ logs are lost on reboot.** Long-term debugging needs the `~/Library/Logs/` files.

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
KAIZEN_LIVE_TESTS=1 pytest tests/test_kaizen_integration.py -v -m kaizen -s
```

Live gates need explicit approval because they can touch Telegram/Kaizen or leave draft artefacts.
Kaizen integration tests require `KAIZEN_LIVE_TESTS=1` even when credentials are exported. Each live run writes a unique manifest and run token. Delete only drafts whose event ID is listed in that exact manifest and whose content contains that exact run token; never delete by form type, date, or generic test wording.


## Compatibility

`CLAUDE.md` is a symlink to this file for Claude Code compatibility. Do not maintain duplicate long-form agent context files.
