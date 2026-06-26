# Portfolio Guru — AGENTS.md (Claude Code Project Context)

## Identity

Portfolio Guru automates e-portfolio filing for UK EM trainees. A doctor sends a clinical case via Telegram (text, voice, photo, document); the bot extracts structured WPBA data, recommends/accepts a form type, previews a draft, then saves a Kaizen draft on approval. Supervisor submission is never automatic.

## Current State

- Phase: beta-ready (private/invite-only paid beta) on Mac Mini. Deploy: GitHub Actions self-hosted runner, push to `main`, **gated on CI tests passing** with post-deploy smoke + auto-rollback (`deploy_mac.sh`).
- Stack: python-telegram-bot v21+ polling, **Vertex AI (EU, London `europe-west2`) `gemini-3.5-flash`** extraction (via `gemini_client.make_client()`, flag `PG_USE_VERTEX`; dedicated GCP project `portfolio-guru-eu`), Playwright/CDP for DOM-mapped Kaizen forms, Fernet-encrypted SQLite, PicklePersistence, best-effort Supabase (EU) mirror.
- Compliance/ops live: EU data residency for clinical AI, `extracted_fields` encrypted before Supabase, GDPR `/reset` erasure (`delete_user_data`), operator alerting + heartbeat (`ops_alert.py`), daily DB backup (launchd). Legal drafts in `docs/legal/` (solicitor review gates _public_ launch).
- Billing: Stripe **live** (proven end-to-end: real £9.99 → upgrade). £9.99/mo Unlimited + free (5/mo). Reconciliation + `invoice.paid` + mode guard in `stripe_handler.py`.
- Target: Kaizen ePortfolio (`eportfolio.rcem.ac.uk` → `kaizenep.com`). Multi-platform-ready via `filer_router.PLATFORM_REGISTRY` (kaizen built, horus stubbed).
- Inputs: text, voice, audio, photos, documents.
- Output: Kaizen draft save only. No supervisor submission.
- Disabled commands: `/bulk` and `/chase` return early with "coming soon" (their dead implementation code has been removed). `/unsigned` is NOT disabled — it is a live, tier-gated (`pro_plus`) feature registered in `build_application`.

## Dev / Test Commands

- Install/runtime: use the existing backend virtualenvs (`backend/.venv` or `backend/venv`). Do not create a new dependency manager unless the repo is deliberately migrated.
- Local bot: `bash start-bot.sh` from the repo root. This calls `backend/run_local.sh`, loads secrets from BWS, starts the Stripe webhook server on port `8099`, ensures CDP Chrome is available, then runs `backend/bot.py`.
- Preflight before commit or handoff: `bash scripts/preflight.sh`.
- Release closure: `scripts/release_loop.sh --surface telegram --mode prepare|ship` is the deterministic closure entrypoint. `prepare` reports READY/BLOCKED and is always side-effect free; `ship` is gated (refuses without `RELEASE_APPROVED=telegram-YYYYMMDD` or `--approved`, and refuses on a dirty/non-fast-forwardable tree) and reuses preflight + telegram offline QA + the push→`deploy-mac.yml`→`deploy_mac.sh` CI deploy + `dogfood_smoke.sh`. `ship` must end with `FINAL_RELEASE_STATE=live|release-ready|proof-pending|blocked`; printed proof commands are only a next gate, not live proof. Do not run `ship` with approval autonomously.
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

- `/bulk` and `/chase` are disabled (early `return`, "coming soon"); `/unsigned` IS live (tier-gated). Don't assume a command is disabled from docs alone — check its handler body.
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
