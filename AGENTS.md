# Portfolio Guru — AGENTS.md (Claude Code Project Context)

## Identity

Portfolio Guru automates e-portfolio filing for UK EM trainees. A doctor sends a clinical case via Telegram (text, voice, photo, document); the bot extracts structured WPBA data, recommends/accepts a form type, previews a draft, then saves a Kaizen draft on approval. Supervisor submission is never automatic.

## Current State

- Phase: local/private beta on Mac Mini. Deploy: GitHub Actions self-hosted runner, push to `main`.
- Stack: python-telegram-bot v21+ polling, Gemini fast extraction, Playwright/CDP for DOM-mapped Kaizen forms, Fernet-encrypted SQLite, PicklePersistence.
- Target: Kaizen ePortfolio (`eportfolio.rcem.ac.uk` → `kaizenep.com`).
- Inputs: text, voice, audio, photos, documents.
- Output: Kaizen draft save only. No supervisor submission.
- Disabled code: `/bulk`, `/unsigned`, `/chase` return early with "coming soon" — code below the return is not live.

## Dev / Test Commands

- Install/runtime: use the existing backend virtualenvs (`backend/.venv` or `backend/venv`). Do not create a new dependency manager unless the repo is deliberately migrated.
- Local bot: `bash start-bot.sh` from the repo root. This calls `backend/run_local.sh`, loads secrets from BWS, starts the Stripe webhook server on port `8099`, ensures CDP Chrome is available, then runs `backend/bot.py`.
- Preflight before commit or handoff: `bash scripts/preflight.sh`.
- Main offline gate: `cd backend && venv/bin/python3 -m pytest tests/ -v --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py`.
- Offline E2E only: `cd backend && venv/bin/python3 -m pytest tests/ -v -m e2e`.
- Live Telegram smoke: `cd backend && venv/bin/python3 -m pytest tests/ -v -m live` only when explicitly approved and `TELETHON_SESSION` is set. Never run live Telegram tests as routine CI or autonomous loops.
- Snapshot updates: `cd backend && venv/bin/python3 -m pytest tests/ -v --snapshot-update` only after intentional bot-message changes.
- CI/deploy: pushes to `main` run GitHub Actions tests and the Mac Mini deploy workflow; local feature branches do not automatically deploy.

## Filing Routing Discipline

Single source: `backend/filer_router.py` selects the method per form type.

- **DOM-mapped forms** → deterministic Playwright via CDP (`localhost:18800`). No browser-use. If partial, log gap and fix — never credentials in LLM prompts.
- **Unknown form types on supported platform** → browser-use via CDP as emergency bridge. Auth in persistent Chrome session, never in prompt.
- **Unknown platforms** → browser-harness + domain skills first. User connects their Chrome, CDP navigates, persists helpers.
- browser-use is NEVER a substitute for DOM mapping.

## Key Known Failure Modes

- Disabled features have code paths below `return` — never treat as live.
- Kaizen date format: `d/m/yyyy`, not US `m/d/yyyy`.
- Two separate filer implementations: `filer.py` (browser-use) and `browser_filer.py` (Playwright). Shared logic, different failure modes.
- LLM extraction is non-deterministic — test with multiple runs.
- Playwright selectors break on Kaizen UI updates (third-party, no notice).
- Gemini fallback ordering in `model_config.py` — adding a model means updating all callers.

## Safety

- Never log credentials, decrypted values, or tokens.
- Never submit forms to supervisors. Draft-only saves.
- If docs disagree with git/tests/runtime, runtime evidence wins and docs must be corrected.

## Supported Forms

Full form catalogue and DOM coverage status: `docs/form-coverage.md`. The coverage doc is the source for which forms are deterministic, which are UUID-known but hidden, and which are admin/utility surfaces rather than fileable portfolio evidence.
