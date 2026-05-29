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

Full form catalogue and DOM coverage status: `docs/form-coverage.md`. Currently 46 verified forms with DOM mappings, covering CBD, DOPS, Mini-CEX, ACAT, reflections, teaching, procedurals, management, US cases, and more.
