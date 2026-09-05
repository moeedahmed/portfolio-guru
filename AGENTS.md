# Portfolio Guru — AGENTS.md (Claude Code Project Context)

## Identity

Portfolio Guru automates e-portfolio filing for UK EM trainees. A doctor sends a clinical case via Telegram (text, voice, photo, document); the bot extracts structured WPBA data, recommends/accepts a form type, previews a draft, then saves a Kaizen draft on approval. Supervisor submission is never automatic.

Core product edge: this is not a generic AI writing tool. Doctors can already draft with ChatGPT; Portfolio Guru's wedge is reducing the whole portfolio filing load: capture evidence where the doctor already is, help select the right Kaizen ticket/form, draft simply in the doctor's voice when possible, attach the right evidence, and save a Kaizen draft after approval. Product/content positioning source: `docs/portfolio-guru-core-edge-2026-07-06.md`.

## Current State

- Phase: controlled dogfood / beta-hardening on Mac Mini. Core filing is built, but wider invite-only paid beta remains gated by the launch checklist, legal sign-off, and manual live smoke proof for new attachment flows. Deploy: GitHub Actions self-hosted runner, push to `main`, **gated on CI tests passing** with post-deploy smoke + auto-rollback (`deploy_mac.sh`).
- Stack: python-telegram-bot v21+ polling, **Vertex AI (EU, London `europe-west2`) `gemini-3.5-flash`** extraction (via `gemini_client.make_client()`, flag `PG_USE_VERTEX`; dedicated GCP project `portfolio-guru-eu`), Playwright/CDP for DOM-mapped Kaizen forms, Fernet-encrypted SQLite, PicklePersistence, best-effort Supabase (EU) mirror.
- Compliance/ops live: intended Vertex AI `europe-west2` routing for clinical AI when `PG_USE_VERTEX` is enabled, `extracted_fields` encrypted before Supabase, GDPR `/reset` erasure (`delete_user_data`), operator alerting + heartbeat (`ops_alert.py`), daily DB backup (launchd). Legal drafts in `docs/legal/` remain draft/not-in-force and gate wider paid beta/public launch.
- Billing: Stripe **live** (proven end-to-end: real £9.99 → upgrade). £9.99/mo Unlimited + free (5/mo). Reconciliation + `invoice.paid` + mode guard in `stripe_handler.py`.
- Target: Kaizen ePortfolio (`eportfolio.rcem.ac.uk` → `kaizenep.com`). Multi-platform-ready via `filer_router.PLATFORM_REGISTRY` (kaizen built, horus stubbed).
- Inputs: text, voice, audio, photos, documents.
- Output: Kaizen draft save only. No supervisor submission.
- Disabled commands: `/bulk` and `/chase` return early with "coming soon" (their dead implementation code has been removed). `/unsigned` is NOT disabled — it is a live, tier-gated (`pro_plus`) feature registered in `build_application`. The `/upgrade` upsell copy must never advertise `/bulk` or `/chase` as a paid perk while they are disabled.

## Dev / Test Commands

- Install/runtime: use the existing backend virtualenvs (`backend/.venv` or `backend/venv`). Do not create a new dependency manager unless the repo is deliberately migrated.
- Local bot: `bash start-bot.sh` from the repo root. This calls `backend/run_local.sh`, loads secrets from BWS, starts the Stripe webhook server on port `8099`, ensures CDP Chrome is available, then runs `backend/bot.py`.
- Preflight before commit or handoff: `bash scripts/preflight.sh`.
- Release closure: `scripts/release_loop.sh` is the sole deterministic closure entrypoint, and it runs on **one card, one approval**. Full standard: `docs/release-standard.md`.
  - `--mode prepare --risk internal|telegram|broad --effect "<one line>" [--live-target <bot_username>]` makes no remote, runtime or user-facing change; it refreshes local refs and writes a local card. It runs preflight + offline Telegram QA (with fixed throwaway credentials scoped to those children only), fetches current `origin/main`, verifies the live runtime equals it, then writes one immutable local card under the gitignored `.release/` keyed by the full HEAD SHA: schema version, SHA, surface, risk, effect, proof mode, exact live target, already-verified rollback SHA, `rollback_mode = operator-triggered`, fixed exclusions, timestamp. It prints the card, one exact ship command and one exact rollback command. `--effect` is required, and telegram risk requires an exact `--live-target`. If the tree or live baseline is not release-ready, no card is written.
  - **Approval names one SHA.** `--approved <40hex>` (or `RELEASE_APPROVED=<40hex>`) must equal the card SHA and current `HEAD`, and the CLI surface/risk must equal the card's. Dated or bare approvals are refused. A changed SHA, target, risk, surface, effect, proof mode, rollback target or exclusion needs a newly prepared card and a new approval; an unchanged resume reuses the original approval and must not prompt again.
  - `--mode ship` verifies the immutable card, requires a clean fast-forwardable feature branch, runs the offline gates, **re-fetches current `origin/main`, requires it still equals the rollback SHA already frozen on the card, and re-verifies the live runtime against it before main moves** (it blocks before mutation and requires a new card if any value moved), then pushes one exact full SHA to main. No separate feature-branch backup push is taken: the exact-SHA push already preserves the commit remotely. It then requires a successful `push` Tests run for that SHA, a later successful `workflow_run` Mac Mini deploy bound to the same SHA, exact checked-out/runtime identity, and risk-scaled proof.
  - Proof mode is frozen on the card. `internal` is automated (CI + deploy + runtime identity, no journey). `telegram` is automated only when Telethon session/API id/hash, `TELEGRAM_BOT_USERNAME` and the live allowlist all name the exact card target; otherwise the card is manual. A manual card never sends automatically, even if credentials appear later. An automated card whose readiness disappears reports pending and refuses the live child rather than downgrading itself. The live guard `TELEGRAM_LIVE_APPROVED` is set per-command on the live child only, never exported; `telegram_bot_qa.sh`'s own direct-call guard is unchanged. `broad` still needs all 15 interactive dogfood checks with no skips.
  - `--mode attest --approved <sha> --result pass|fail --note "<one line>"` closes only a card already frozen as manual. It re-verifies card, SHA, surface, risk, `HEAD == origin/main == SHA` and runtime identity, writes a local attestation, and mutates nothing external. It says `manual proof attested by operator` and never claims automated proof. It refuses internal risk and every automated card, even if automated readiness later disappears.
  - Missing/running/timeout/inaccessible proof exits 4 as `proof-pending` and, on a manual card, says manual proof is required rather than asking for approval again. Completed CI/deploy or journey failure is `blocked`; on live-proof failure the loop prints the verified prior known-good SHA and the exact `--mode rollback` command, and states plainly that the new SHA stays live until a targeted rollback is actually run. It never rewrites history or claims a rollback it did not perform.
  - `--mode rollback --risk <card risk> --approved <released 40hex>` is the bounded recovery the same approval already covers: the card names both the released SHA and the frozen known-good SHA, so no second approval is asked for. It is **operator-triggered, never silent** — `deploy_mac.sh`'s own post-deploy health rollback is separate and unchanged. Before mutation it requires a clean tracked tree, the card, `HEAD == origin/main ==` the released SHA, a known-good SHA that is a real ancestor, and a live runtime reconciled and reported as the released SHA. It then makes one normal forward commit whose parent is exactly the released SHA and whose tree is exactly the known-good SHA's; no force push, reset, merge, `main` checkout or untracked-file change, and the tracked preimage is restored if that tree cannot be produced exactly. Validated state keyed by the released SHA is written under `.release/` before the push, so rerunning the same command resumes a committed-but-unpushed rollback, skips a duplicate push when `origin/main` is already the rollback commit, runs proof only, and never makes a second commit. Proof is the same exact-SHA Tests/deploy/runtime pipeline keyed to the rollback commit, with **no live journey**. Success is `FINAL_RELEASE_STATE=rolled-back` only after the runtime proves that commit; if the deploy restores the released runtime the receipt says main is the rollback commit while the runtime is still the released SHA.
  - Resume only with the printed `--release-sha <40hex>` command, which requires the same approval, the card, and exact local/remote SHA, re-verifies runtime, and never pushes twice. PRs are optional and not part of the default flow. Printed proof commands are next gates, not live proof. Do not run approved `ship`/`attest`/`rollback` unless Moeed has authorised that exact release envelope; once authorised, do not invent another approval inside the unchanged envelope.
- Main offline gate: `cd backend && venv/bin/python3 -m pytest tests/ -v --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py`.
- **Every change**: `bash scripts/verify_changed.sh` — the consent static guardrail plus the critical-journey offline pytest files (case capture/extraction/recommendation, draft preview/approval/Kaizen save, Telegram channel contract, consent/beta gating, Stripe billing, attachment handoff, funnel telemetry). Run before calling any change done.
- **Before release/handoff**: `bash scripts/verify_release.sh` — `verify_changed.sh` plus the full offline pytest suite (matches the CI `Tests` job in `.github/workflows/test.yml`). No live Telegram/Vertex/Kaizen/Stripe network calls in either gate — see `docs/rollback.md` for what to do if a shipped change needs to be undone. This repo does not own database migrations, so there is no migration-safety gate here (Supabase is a best-effort mirror, not a schema owner).
- User-facing completion proof is risk-scaled and must follow `docs/verification-contract.md`. Meaningful changes require the real product path plus independent verification; visual changes require screenshots; video is reserved for multi-step interactions.
- **Do not claim a change is "done" or "release-ready" without pasting the actual green output of `verify_changed.sh` (done) or `verify_release.sh` (release-ready).** A described or assumed pass is not proof.
- Offline E2E only: `cd backend && venv/bin/python3 -m pytest tests/ -v -m e2e`.
- Live Telegram smoke: `cd backend && venv/bin/python3 -m pytest tests/ -v -m live` only when explicitly approved and `TELETHON_SESSION` is set. Never run live Telegram tests as routine CI or autonomous loops.
- Snapshot updates: `cd backend && venv/bin/python3 -m pytest tests/ -v --snapshot-update` only after intentional bot-message changes.
- CI/deploy: pushes to `main` run GitHub Actions tests and the Mac Mini deploy workflow; local feature branches do not automatically deploy.

## Filing Routing Discipline

Single source: `backend/filer_router.py` selects the method per form type.

- **Mapped forms** → deterministic Playwright via CDP (`localhost:18800`). New/updated maps use semantic-first selector plans: label/role/placeholder/name/data candidates first, DOM id/CSS/XPath as fallback, with repair hints and snapshot evidence when selectors drift. No browser-use. If partial, log gap and fix — never credentials in LLM prompts.
- **Unknown form types on supported platform** → browser-use via CDP as emergency bridge, **off by default in this beta**. `filer_router._browser_use_fallback_enabled()` gates every browser-use call; with `PG_ENABLE_BROWSER_USE_FALLBACK` unset, an unmapped form/platform fails cleanly (`method: "browser-use-disabled"`) instead of silently escalating. Set `PG_ENABLE_BROWSER_USE_FALLBACK=1` to opt back in explicitly. Auth in persistent Chrome session, never in prompt.
- **Unknown platforms** → same off-by-default gate applies; browser-harness + domain skills is the intended path once explicitly enabled. User connects their Chrome, CDP navigates, persists helpers.
- browser-use is NEVER a substitute for deterministic mapped forms.

## Key Known Failure Modes

- `/bulk` and `/chase` are disabled (early `return`, "coming soon"); `/unsigned` IS live (tier-gated). Don't assume a command is disabled from docs alone — check its handler body.
- Kaizen date format: `d/m/yyyy`, not US `m/d/yyyy`.
- Two separate filer implementations: `filer.py` (browser-use) and `browser_filer.py` (Playwright). Shared logic, different failure modes.
- LLM extraction is non-deterministic — test with multiple runs.
- Playwright selectors break on Kaizen UI updates (third-party, no notice).
- Gemini fallback ordering in `model_config.py` — adding a model means updating all callers.

## Telemetry Provenance

`backend/filing_attempt_log.py` and `backend/funnel_metrics.py` classify every
event/attempt into exactly one of: real beta user, synthetic test fixture
(`is_synthetic_user`, default id `99999999`), operator/dogfood traffic
(`is_operator_user`, default id `6912896590` — mirrors `bot.ADMIN_USER_ID`), or
legacy/unattributed (no `user_id`). `/filingreport` and `/funnelreport` default
to the real cohort only; `all`/`synthetic`/`full` includes synthetic +
operator traffic together. Unattributed records are never counted as
completed/repeat real users — they are reported as a separate count instead.
Both admin reports append a `Revision: <branch>@<commit>` line sourced from
the existing `runtime_identity` mechanism (admin-only; never shown to
ordinary users).

## Where To Work

This repo is worked on by several agents — Claude Code sessions and Hermes
profile agents — all acting for Moeed. On 2026-08-25/26 three of them shared one
working directory and produced three separate incidents and two failed deploys:
a feature branch pulled uncommitted work out from under the sync robot's
main-branch protection, a branch switch landed one session's commit on another's
branch, and commits made in the live deployment checkout diverged `main` and
blocked deploys twice. Nothing was caused by the agents being different tools.
Every one was concurrent writers on shared mutable state.

- **One writer per file set, one directory per writer.** Never work in
  `~/projects/portfolio-guru` itself and never in the live checkout. Get your
  own with `scripts/new_worktree.sh <short-name>` — it takes seconds and shares
  a prepared venv and `.env`.
- **`~/projects/portfolio-guru-live` is the deployment, not a workspace.** It is
  pinned to `main`, only `deploy_mac.sh` writes to it, and a pre-commit hook
  refuses commits there. A commit made there diverges `main` from origin and the
  next deploy fails closed with "expected SHA is not a safe fast-forward".
- **Product work belongs in an interactive Claude Code session**, where context
  compounds across a long investigation. Bounded, fully-briefable jobs —
  backups, healthchecks, digests, scheduled maintenance — belong in Hermes.
- **Do not run Hermes product work on this repo while a Claude Code session is
  open on it.** Not a technical limit: two agents will each be right about their
  own change and wrong about the other's.

## Safety

- Never log credentials, decrypted values, or tokens.
- Never submit forms to supervisors. Draft-only saves.
- If docs disagree with git/tests/runtime, runtime evidence wins and docs must be corrected.

## Supported Forms

Full form catalogue and DOM coverage status: `docs/form-coverage.md`. The coverage doc is the source for which forms are deterministic, which are UUID-known but hidden, and which are admin/utility surfaces rather than fileable portfolio evidence.
