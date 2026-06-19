# Hermes Optimisation Analysis - 2026-06-19

Status: analysis-only. No live beta bot, token, runtime, Kaizen, deployment, or Hermes profile changes were made.

## Bot Surfaces

- Live beta bot: `@portfolio_guru_bot`
- Hermes test bot: `@portfolio_guru_test_bot`
- Live beta runtime: `backend/bot.py`, polling with `PORTFOLIO_GURU_TELEGRAM_BOT_TOKEN`
- Hermes test runtime: Hermes profile at `~/.hermes/profiles/portfolio-guru`, polling with `PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN` / `TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST`

The live beta bot and Hermes test bot must stay separate until Hermes proves it can outperform the beta workflow in dogfood and beta-like testing.

## What Makes The Beta Bot Mature

The live beta bot is not just a Telegram wrapper. Its strength is the layered workflow:

1. A persistent `ConversationHandler` with explicit states for setup, form choice, approval, edit flow, case input, template review, curriculum, gathering, pathway, and document intent. See `backend/bot.py` lines 1272-1280 and 10141-10266.
2. Multi-input capture through text, voice, audio, photos, and documents at entry and mid-flow points. See `backend/bot.py` lines 10151-10155 and 10176-10237.
3. A hard approval boundary before Kaizen write actions. Approval callbacks are separated under `AWAIT_APPROVAL`; save-as-draft and submit/supervisor-style controls are not free-text side effects. See `backend/bot.py` lines 10222-10237.
4. A duplicate-process lock and webhook clearing before polling, which protects against Telegram token conflicts. See `backend/bot.py` lines 10479-10504.
5. A single filing router for Kaizen form writes, where DOM-mapped forms use deterministic Playwright via CDP and browser-use is only an emergency bridge for unsupported paths. See `backend/filer_router.py` lines 1-18 and 91-158.
6. Channel-neutral contracts already exist for non-Telegram fronts. `channel_contract.accept_inbound()` accepts only direct/private portfolio turns and refuses group or empty content without touching credentials or Telegram. See `backend/channel_contract.py` lines 1-35 and 182-197.
7. A vNext case engine already models the "conversational outside, deterministic inside" rule. It keeps chat separate from source-backed case facts, marks image/document inputs stricter, and emits deterministic actions. See `backend/conversational_case_engine.py` lines 1-27, 37-84, 191-243, and 393-472.

## Existing Hermes Contract In The Repo

The repo already defines the intended Hermes wiring:

1. `docs/hermes/INTEGRATION_GUIDE.md` defines the architecture: Telegram test bot -> Hermes profile -> `hermes_bridge_contract.inbound_from_payload()` -> `channel_contract.accept_inbound()` -> `telegram_vnext_adapter.event_from_telegram_message()` -> `conversational_case_engine.apply_event()` -> rendered reply. See lines 12-32.
2. The integration guide explicitly says the live beta bot and Hermes test bot are separate processes with separate tokens and must never poll the same token, share state, or share webhook/polling loops. See lines 34-45 and 49-65.
3. Shadow mode is already the intended first validation step: call the bridge and engine, log dispositions/actions, and only enable live replies after at least 10 message types are clean. See lines 121-143.
4. Stop conditions are already defined: 409 token conflict, live token in Hermes config, Kaizen draft save without approval, or clinical content appearing in logs. See lines 165-190.
5. `docs/hermes/PROFILE_PROMPT.md` already says the Hermes profile owns conversation only, must not simulate the deterministic engine, must not paraphrase clinical content before handing it to the engine, and must never override engine dispositions. See lines 31-82.

## Current Hermes Profile Gap

The Hermes profile does not yet match the repo contract.

Observed profile files:

- `~/.hermes/profiles/portfolio-guru/SOUL.md`
- `~/.hermes/profiles/portfolio-guru/USER.md`
- `~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/pg`
- `~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/recommend.py`
- `~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/draft.py`
- `~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/save.py`
- `~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/health.py`

Key mismatches:

1. The profile currently uses a local toy `pg` CLI rather than the repo's bridge and vNext engine.
2. `recommend.py` uses simple keyword scoring and simplified form codes, including `CDD`; the repo and Hermes docs use real Portfolio Guru conventions such as `CBD`.
3. `draft.py` creates empty-section local JSON drafts under the Hermes profile rather than invoking the real extraction/draft/preview path.
4. `save.py` archives local JSON and describes Kaizen submission as simulated. It must not be treated as the real filing engine.
5. `health.py` reads only local pending drafts and says full Portfolio Health requires a Kaizen API connection. It is not connected to the repo's `health_engine.py`, `health_models.py`, or `health_profile_store.py`.
6. The profile prompt concept is correct, but the profile implementation currently violates its own boundary by simulating engine behaviour instead of calling the deterministic repo-owned contract.

## Product Interpretation

The beta bot's "rigid" workflow is the asset, not the problem.

Hermes should add:

- natural-language intake;
- better case-vs-question judgement;
- short adaptive clarification;
- conversational continuity;
- calmer explanation and recovery.

Hermes should not replace:

- source-grounded extraction;
- form routing;
- draft eligibility;
- approval gates;
- Kaizen credential handling;
- filing router;
- token isolation;
- group refusal;
- shadow-mode proof.

The correct target is not "Hermes as a smarter independent Portfolio Guru". The correct target is "Hermes as an intelligent front door to the mature Portfolio Guru engine".

## Recommended Optimisation Plan

### Phase 0 - Freeze The Live Beta Bot

No runtime changes to `@portfolio_guru_bot`.

Do not restart the live beta process, touch its token, change its branch, run live Telegram tests, run Kaizen writes, or deploy. All optimisation happens against `@portfolio_guru_test_bot`.

### Phase 1 - Replace The Hermes Mock Engine

Retire or quarantine the Hermes-profile local mock `pg` path as the source of truth.

Build a thin test-bot adapter that imports the repo engine from `/Users/moeedahmed/projects/portfolio-guru/backend` and calls:

- `hermes_bridge_contract.inbound_from_payload()`
- `channel_contract.accept_inbound()`
- `telegram_vnext_adapter.event_from_telegram_message()`
- `conversational_case_engine.apply_event()`
- `hermes_bridge_contract.serialise_reply()` / `serialise_decision()`
- `channel_actions.to_telegram_keyboard()` or `render_numbered()` as needed

The adapter should pass the user's source text unchanged into the engine. Hermes may decide how to speak around the result, but not rewrite clinical content before extraction.

### Phase 2 - Shadow Mode

Run the Hermes test bot in shadow mode first:

- receive test messages;
- call the repo bridge and engine;
- log only disposition/action metadata and non-clinical diagnostics;
- send no user-facing replies from the new adapter until the shadow log is clean.

Minimum shadow set:

- clinical case text;
- "what forms would this support?";
- "file this as a CBD";
- empty message;
- medical advice question;
- image/document placeholder;
- group context refusal if a group surface is ever wired.

### Phase 3 - Live Replies On Test Bot Only

Once shadow mode is clean, enable replies on `@portfolio_guru_test_bot` only.

The first live reply version should render the engine's `NextAction` outcomes as mobile-first Telegram messages:

- `ACK_CASE_DETAILS`: short captured acknowledgement;
- `REQUEST_CASE_CONFIRMATION`: ask whether this is a case to draft;
- `REQUEST_CLARIFICATION`: ask one missing-detail question;
- `REQUEST_FACT_CONFIRMATION`: ask the user to confirm image/document-derived facts;
- `OFFER_DRAFT`: show draft preview with approve/edit/cancel;
- `DRAFT_NOT_READY`: explain what is missing;
- `SAVE_DRAFT`: only dispatch after explicit approval.

### Phase 4 - Bring In The Mature Beta UX Patterns

Carry these patterns from the live bot into Hermes:

- one action per message;
- inline buttons for irreversible or state-changing steps;
- "Use best fit" primary action with other forms as overrides;
- draft preview before save;
- missing-field transparency;
- post-failure retry path without losing the draft;
- privacy nudge before drafting;
- medical advice redirect;
- group refusal;
- no supervisor submission language.

### Phase 5 - Compare Against Beta Dogfood

Run the same anonymised cases through:

- live beta bot, observation only;
- Hermes test bot;
- offline vNext adapter tests.

Hermes graduates only when it beats beta on:

- messy intake;
- fewer unnecessary buttons;
- better clarification;
- no loss of safety gates;
- no invented clinical facts;
- no token/runtime coupling;
- no confusing local mock state.

## No-Touch Boundaries

- Do not touch `@portfolio_guru_bot` runtime while beta testers are using it.
- Do not co-poll live and test Telegram tokens.
- Do not put live beta token values into Hermes config, prompts, logs, or LLM context.
- Do not let Hermes call Kaizen directly.
- Do not let Hermes save drafts unless the repo engine returns an approved save action after an explicit user confirmation.
- Do not use the Hermes local `pending.json` as product truth.
- Do not treat the Hermes toy `pg` CLI as the Portfolio Guru engine.

## Next Work Brief

Objective: build a Hermes test-bot adapter that replaces the local mock `pg` behaviour with the repo-owned bridge/engine contract, initially in shadow mode only.

Scope:

- profile/test bot only;
- no live beta changes;
- no Kaizen writes;
- no deployments;
- no live Telegram spam beyond controlled test messages;
- no token changes.

Proof:

- offline tests for bridge payloads and engine actions;
- shadow-mode transcript for at least 10 message types;
- no live token references in Hermes profile;
- no clinical content in logs;
- manual test on `@portfolio_guru_test_bot` after shadow proof.
