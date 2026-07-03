# Hermes Capability Map — Portfolio Guru

Date: 2026-06-17
Owner: Founder / Portfolio Guru
Scope: A judge-facing map from the capabilities Hermes is built around to
the real, tested implementation in this codebase. Every row cites a file
or test so a judge can verify the claim instead of taking it on trust.

Companion documents:

- [`DEMO_SCRIPT_90S.md`](DEMO_SCRIPT_90S.md) — the 90-second narration.
- [`REHEARSAL_RUNBOOK.md`](REHEARSAL_RUNBOOK.md) — deterministic rehearsal path.
- [`HERO_CASE_2026-06-30.md`](HERO_CASE_2026-06-30.md) — the synthetic case used.

## What this document is — and is not

Portfolio Guru is an autonomous business agent for one painful, narrow
workflow: turning a UK Emergency Medicine trainee's shift note into a
structured RCEM Kaizen portfolio draft, with a human approval gate before
anything is written. It is built on its own stack — `python-telegram-bot`
polling, Gemini extraction, Playwright/CDP browser automation, and a
Fernet-encrypted SQLite store.

This map shows that Portfolio Guru already demonstrates the same agent
capabilities Hermes is built around, with working code behind each one.

Honesty boundary, stated up front so no row below can be misread:

- Portfolio Guru does **not** currently run on the Hermes runtime. This is
  an architectural mapping, not a claim of a Hermes dependency.
- The live extraction engine today is **Gemini** (see
  [`backend/model_config.py`](../../backend/model_config.py)). Hermes and
  NVIDIA Nemotron are **target model slots** the model-agnostic config
  layer is designed to accept — they are not wired into the live path, and
  the ledger labels any such row `Demo / Test`.
- Scheduled-automation surfaces (`/chase`, `/unsigned`, `/bulk`) are
  **disabled** — their code returns early with "coming soon". They are
  listed below only as a roadmap row, never as a live capability.

## Honesty key

Each row carries a status label, the same vocabulary the runbook uses:

- `[live]` — implemented and exercised on the real product path.
- `[test]` — backed by a deterministic in-process test.
- `[roadmap]` — designed-for but not live; never demoed as if live.

## Capability map

### 1. One agent across many input surfaces

Hermes framing: one agent reachable across many channels.

Portfolio Guru: a single Telegram agent accepts text, voice note, audio,
photo, and document for the same case-capture flow.

- Evidence `[live]`: `backend/bot.py:10197-10201` registers
  `MessageHandler`s for `TEXT`, `VOICE`, `AUDIO`, `PHOTO`, and
  `Document.ALL`, all routed into one `handle_case_input` entry point.
- Honesty: WhatsApp and other surfaces are later routed convenience, not a
  promise in this cut. The web app (EMGurus Hub) is the public front door;
  Telegram is the daily action engine.

### 2. One persistent memory

Hermes framing: persistent memory across sessions.

Portfolio Guru: per-user state survives restarts and personalises output.

- Evidence `[live]`: `python-telegram-bot` `PicklePersistence` keeps
  conversation state; `backend/profile_store.py` and `backend/usage.py`
  hold a Fernet-encrypted SQLite profile and usage ledger;
  `backend/voice_profile.py` learns the trainee's writing tone so drafts
  read in their own voice over time.
- Honesty: credentials are encrypted at rest and never logged; "learns
  your tone" means a per-user style profile, not a self-evolving skill set.

### 3. Tool use, vision, and browser automation

Hermes framing: web, search, vision, tool use.

Portfolio Guru: vision reads a photographed note; deterministic browser
automation drives the third-party Kaizen form.

- Evidence `[live]`: photo/document capture feeds the same extraction
  path (`backend/bot.py:10200-10201`); `backend/browser_filer.py` fills
  the Kaizen form via Playwright over CDP; `backend/filer_router.py`
  selects the filing method per form type.
- Honesty: deterministic DOM mapping is preferred over generic browser
  agents; the browser-use bridge is an emergency path for unmapped forms,
  never a substitute for DOM mapping.

### 4. Model-agnostic, multi-slot model configuration

Hermes framing: model slots for the main model plus auxiliary roles.

Portfolio Guru: a central config layer makes the model pluggable with an
ordered fallback chain rather than a single hard-coded model.

- Evidence `[live]`: `backend/model_config.py:35` builds the Gemini
  fallback list (fast → premium → stable), with `openai_fallback_model()`
  and `browser_fallback_model()` as further slots.
- Honesty: today the live engine is Gemini. Adding a Hermes-hosted or
  NVIDIA Nemotron slot is a config change, not a rewrite — but it is
  `[roadmap]`, not live, and is labelled `Demo / Test` wherever shown.

### 5. Approval-gated, safe writes

Hermes framing: write approval and read-safety as first-class reliability
concepts.

Portfolio Guru: no external write happens without an explicit human tap,
and the agent never submits on a supervisor's behalf.

- Evidence `[live]`: `backend/bot.py:9868` `handle_approval_approve` is
  the only path that proceeds to a Kaizen draft save, and it saves a draft
  only. The agent does not submit, sign, send, approve, reject, or delete
  on a supervisor's behalf — that boundary is the hard line.
- Honesty: two gates exist — the trainee approves in Telegram, then Kaizen
  holds it as a draft for their own final sign-off.

### 6. Active orchestration / routed sub-tasks

Hermes framing: isolated subagents and active orchestration.

Portfolio Guru: distinct routed roles handle filing, supervisor workflow,
and assessor drafting rather than one monolithic prompt.

- Evidence `[live]`: `backend/filer_router.py` routes per form type;
  `backend/supervisor_workflow.py` and `backend/assessor_drafter.py` are
  separate role surfaces with their own tests.
- Honesty: this is a routing/role layer in our own runtime, not literally
  Hermes subagents.

### 7. Reliability and self-improvement by design

Hermes framing: reliability by design and self-improvement.

Portfolio Guru: deterministic test coverage, friction telemetry, and a
reset-state rehearsal path keep the demo and the product honest.

- Evidence `[test]`: the Stripe checkout → webhook → tier-flip path is
  covered by `backend/tests/test_stripe_webhook_e2e.py`; the demo assets
  are copy-scanned by `backend/tests/test_demo_assets.py`.
- Evidence `[live]`: `backend/bot.py:2853` `_track_funnel_event` emits
  PHI-free funnel events (`draft_shown`, `checkout_started`,
  `checkout_completed`, `bot_linked`, `credentials_connected`) so beta
  friction is measurable; `voice_profile.py` improves drafts per user
  from their own accepted edits.
- Honesty: "self-improvement" here is per-user style learning and
  measured-funnel iteration, not an autonomous self-rewriting loop.

### 8. Earn / spend / operate as a business agent

Hermes framing: a useful agent that runs a real workflow end to end.

Portfolio Guru: a Stripe subscription is the earn path; inference is the
spend; the operating surfaces are tracked on a demo ledger.

- Evidence `[test]`: `backend/stripe_handler.py:131` logs the
  `checkout_completed` funnel event; the tier flip is proven by
  `backend/tests/test_stripe_webhook_e2e.py`; the Hub surface is
  `src/modules/portfolio/pages/BusinessAgentLedger.tsx` (every row badged
  `Demo / Test`).
- Honesty: there are no real-money assessor payouts in this product, and
  no live Stripe production charge is part of the demo. The proven path is
  test-mode and in-process; the live Stripe-CLI proof is a manual,
  foreground step in [`../STRIPE_LOCAL_PROOF.md`](../STRIPE_LOCAL_PROOF.md).

### 9. Scheduled automations

Hermes framing: scheduled, always-on automations.

Portfolio Guru: supervisor polling/scheduling code exists, but the
trainee-facing automation commands are not switched on in this cut.

- Evidence `[roadmap]`: `backend/supervisor_poller.py` and
  `backend/supervisor_scheduler.py` exist with tests, but `/chase`,
  `/unsigned`, and `/bulk` return early with "coming soon" — the code
  below those returns is not live and must not be demoed as live.
- Honesty: this row is roadmap, included for completeness, not a claim.

## What we deliberately do not claim

- We do not claim Portfolio Guru runs on the Hermes runtime.
- We do not claim RCEM endorsement; Portfolio Guru is independent of the
  Royal College and Portfolio Health is a directional planning aid, not an
  official ARCP or CESR outcome.
- We do not claim live NVIDIA Nemotron or Hermes-hosted inference; the
  live engine is Gemini and any other slot is a `Demo / Test` row.
- We do not claim real-money assessor payouts; there are none.
- We do not claim a public WhatsApp launch; WhatsApp is later routing.
- The agent never auto-submits, auto-signs, auto-sends, auto-approves,
  auto-rejects, or auto-deletes on a supervisor's behalf.
- **No shared Telegram token.** The Hermes profile is wired to the
  separate test bot (`@portfolio_guru_test_bot`) using its own token
  (BWS secret name: `TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST`; OpenClaw/runtime
  alias: `PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN`). The live Portfolio
  Guru beta bot uses a completely separate token
  (`PORTFOLIO_GURU_TELEGRAM_BOT_TOKEN`). These two tokens are never
  co-polled, swapped, or shared. The live beta is unaffected by any
  Hermes test-bot activity.

## How a judge can verify in two minutes

1. Multi-surface and approval gate: open `backend/bot.py`, jump to lines
   10197 and 8594.
2. Model-agnostic slots: open `backend/model_config.py`, line 35.
3. Earn path proof: run
   `cd backend && venv/bin/python3 -m pytest tests/test_stripe_webhook_e2e.py -v`.
4. Honesty of the demo assets: run
   `cd backend && venv/bin/python3 -m pytest tests/test_demo_assets.py -v`.
