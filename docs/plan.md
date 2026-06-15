# Portfolio Guru - Conversational Router Plan

## Goal

Make Portfolio Guru feel like a natural portfolio assistant while preserving the deterministic Kaizen workflows that already work.

The product should accept messy doctor-language, understand intent, ask one useful clarifying question when needed, then route into safe structured execution. Filing, billing, credential handling, and Kaizen submission remain deterministic and confirm-before-action.

## Product Decision

Build Portfolio Guru vNext as a separate private conversational test bot first, not a replacement for the current public bot. The public Portfolio Guru bot stays stable on its existing deterministic engine. Once vNext beats the current bot in messy dogfooding, the orchestrator may later decide whether to point the public bot identity at the new engine.

This supersedes the earlier "keep one bot" decision recorded in this file. The reason for the change: dogfooding a conversational layer directly on the live public bot would couple the experiment to billing, Kaizen credentials, supervisor flows, and user memory at the moment we most need to iterate on intent classification, source grounding, and case-vs-chat separation. Running vNext on a dedicated private token isolates that risk. Duplication of auth/persistence/Kaizen wiring is accepted as a temporary cost in exchange for safe iteration.

Approved on 2026-05-28 by Moeed. Migration of the public bot identity to the new engine remains gated on dogfood proof, not a calendar date.

## Channel Architecture Decision

Portfolio Guru is a separate product engine, not a sub-prompt inside the EMGurus/Guru agent.

Current channel plan:

- Telegram can keep the separate Portfolio Bot identity.
- WhatsApp should initially route through the single EMGurus/Guru WhatsApp front door because the active WhatsApp number is registered to EMGurus/Guru.
- The EMGurus/Guru WhatsApp agent owns channel routing, DM-vs-group gating, and handoff.
- Portfolio Guru owns portfolio intake, extraction, drafting, confirmation, and draft-only Kaizen filing.
- The WhatsApp bridge must connect to Portfolio Guru through channel-neutral contracts, so the same engine can later be used by a dedicated Portfolio Guru WhatsApp number, web app, API, or another channel.

Do not move portfolio workflow logic into OpenClaw agent memory or the generic EMGurus/Guru prompt. The engine must remain portable and repo-owned.

Canonical umbrella note: `../../emgurus-hub/docs/okf/emgurus-product-knowledge/channel-architecture.md`.

## Architecture

### Layer 1 - Natural conversation intake

Accept ordinary messages, not just command/button flows.

Examples:

- "Had a difficult airway case, can this go in Kaizen?"
- "What forms would this support?"
- "File this as CBD and reflection."
- "Actually make it more concise."
- "Why is this asking me to pay?"

### Layer 2 - Intent router

Classify every non-command message into one intent:

- `new_case` - user is describing portfolio evidence
- `portfolio_question` - user asks advice about forms, curriculum, ARCP, or Kaizen
- `edit_draft` - user wants to revise an existing draft
- `file_to_kaizen` - user wants a draft filed
- `account_or_billing` - user asks about limits, tiers, payments, or access
- `setup_or_credentials` - user needs Kaizen connection help
- `unknown` - unclear message; ask one clarifying question

The router must return structured JSON only. No direct user-facing prose from the router.

### Layer 3 - Existing deterministic workflows

Do not rewrite the working machinery. Route into existing handlers:

- case extraction and recommendation
- draft generation
- improve/rewrite flows
- Kaizen credential setup
- Kaizen filing
- payment and usage-limit flows
- voice profile flows

Buttons remain available at confirmation points, but they stop being the only way to operate the bot.

### Layer 4 - Safety and recovery

Hard rules:

- Never file to Kaizen without explicit user confirmation.
- Never change billing or credentials without explicit confirmation.
- Never hallucinate portfolio facts; ask if the case lacks required detail.
- Never go silent on unknown input. Ask one useful clarifying question.
- If routing confidence is low, explain what the bot can do next in one short message.

## Implementation Phases

### Phase 0 - Checkpoint and branch

Done:

- DeepSeek model-pathway work committed.
- Main pushed to GitHub.
- New branch created for this work.

### Phase 1 - Router contract and tests

Add a small router module with:

- intent enum
- structured router result
- confidence score
- extracted signals, such as form type, action, and target draft
- fallback clarification text

Add tests for the highest-value messages:

- case description routes to `new_case`
- form advice routes to `portfolio_question`
- "file this" routes to `file_to_kaizen`
- "make it shorter" routes to `edit_draft`
- billing/access messages route to `account_or_billing`
- nonsense or underspecified messages route to `unknown`

No production routing changes in this phase.

### Phase 2 - Passive shadow mode

Run router in the background for ordinary text messages and log the intended route without changing user behaviour.

Purpose: prove the router understands real messages before handing it control.

Verification:

- existing tests pass
- shadow logs show correct intent on at least 20 representative prompts
- no user-facing workflow changes

Status:

- Implemented for ordinary text in `handle_case_input` and `handle_mid_conversation_text`.
- Current implementation is log-only. It does not change routing, replies, buttons, filing, billing, or credentials.
- Tests prove existing decisions are preserved even when the shadow router returns a conflicting intent.

### Phase 2.5 - Source-grounding before more conversational routing

Status:

- Added after a real photo-derived draft fabricated a CPR/ALS/ROSC CBD from screenshots that only supported rib fractures, regional anaesthesia, imaging findings and follow-up.
- Image extraction now uses a facts-only prompt and explicitly forbids extrapolating visible findings into management narrative.
- Photo/image-derived recommendations and drafts now receive source-grounding guards.
- Draft fields from image sources are sanitised to strip unsupported high-risk resuscitation/cardiac narrative while preserving doctor-authored text/voice resuscitation cases.
- Phase 3 remains paused until this survives real image/photo usage.

### Phase 2.6 - Message and workflow hardening

Status:

- Added before Phase 3 to avoid brittle fixed copy and unsafe free-form bot wording.
- Deterministic workflow states remain the source of truth.
- Safety-critical messages stay fixed: filing, billing, credentials, confirmations, privacy warnings, and blockers.
- High-value user-facing surfaces now route through a small message policy/template layer: welcome, about/help, case prompt, captured acknowledgement, recommendation copy, privacy nudge, thin-case blocker, AI unavailable, and draft reply hint.
- LLM-assisted wording remains limited to low-risk explanation/recovery paths already designed for it; it does not control filing or safety actions.
- Phase 3 remains paused until the message policy/tests are green.

### Phase 2.7 - Assessor workflow mapping

Product direction:

- One engine, two entry points.
- `file_evidence`: user sends their own case → bot drafts evidence → user approves → bot saves a Kaizen draft.
- `assess_ticket`: ticket arrives for review → bot shows ticket content → assessor gives intent → bot drafts feedback/sign-off text → assessor approves → bot submits the assessor action.
- No persistent user-facing modes unless task routing proves confusing.

Safety contract:

- Assessor mapping starts read-only.
- Browser harness may navigate, list assessment tickets, open tickets, and extract field/button metadata.
- Browser harness must not sign, submit, save, delete, approve, reject, send feedback, or create drafts during mapping.
- Any future assessor submit/sign action needs a separate explicit approval gate for one named ticket and one reviewed response.
- Colleague/consultant credentials are treated as high-risk: no noisy test artefacts, no spam drafts, and no destructive actions.

Status:

- Read-only assessor mapper scaffold added in `backend/assessor_mapper.py`.
- It can list visible assessment timeline rows and optionally extract ticket detail fields/tags/buttons for mapping.
- It can output a PHI-free ticket shape (`--shape-only`) so mapping can record field labels/control labels without storing patient narrative.
- First read-only live shape mapped a CBD assessor ticket with visible fields: date occurred on, end date, case to be discussed, attach files.
- The same live shape exposed write-side controls: Fill in and Save. These are detected but not clicked.
- After explicit approval, the mapper opened Fill in once and captured the CBD assessor completion shape without saving or submitting.
- CBD assessor completion requires assessor registration number, job title, entrustment scale, feedback, and recommendation for further learning/development.
- The completion surface exposes Submit and Save as draft; these are detected but not clicked.
- It does not click write controls and has tests guarding the read-only boundary.
- Full assessor feedback/sign-off field mapping is now mapped for CBD only; other ticket types remain unmapped.
- Live read-only mapping can use an existing authenticated browser session; supplied credentials are only needed if login is required, and the mapper must stop at 2FA/captcha.
- Guarded write-back planning slice added in `backend/assessor_writeback.py`.
- The write-back adapter maps reviewed local CBD assessor draft values to the mapped Kaizen assessor completion labels.
- Actions are explicitly separated as `fill_fields`, `save_draft`, `submit`, `sign`, `approve`, and `cancel`.
- Every Kaizen-touching action requires ticket UUID, form type, explicit action, and reviewed draft hash. Mismatches, unsupported form types, missing required fields, and final actions produce blocked plans.
- `supervisor_bot` exposes a review-safe button: `Prepare Kaizen action plan (no write)`. The callback renders the guarded plan and never connects to CDP or opens Kaizen.
- Guarded save-draft live runner added next: `assessor_writeback.execute_write_plan` runs against the existing CDP-attached Playwright page only for CBD `save_draft`, only when the plan is unblocked, hash-bound, ticket-URL-bound, and limited to the {open_completion_surface, fill_field, save_draft} step kinds. It clicks `Fill in` once, fills the mapped CBD assessor fields, and clicks `Save as draft` — nothing else. Submit, sign, approve, send, reject, and delete remain blocked and tested. Source-scan on `assessor_writeback` refuses click/locator targets for the forbidden controls.
- The Telegram surface adds an explicit confirmation step: tapping `Prepare Kaizen action plan` shows the plan and, when executable, a `📤 Save draft in Kaizen` button. That posts a separate confirmation message naming the action and safety boundary with `✅ Yes, save as draft` / `❌ Cancel`. Only the explicit confirm tap reaches the runner. CDP-down, stale draft, blocked plan, missing ticket URL, and runner failure each produce distinct user-facing messages. Session ends on success and is preserved on failure so the supervisor can retry.

### Phase 2.8 - Public UX upgrade

Status:

- Added after real usage feedback that Medic Portfolio feels smoother than the public Portfolio Guru bot.
- Product diagnosis: Portfolio Guru was exposing the state machine too early: choose form, review missing fields, then draft. That is safe, but it feels brittle compared with an assistant that accepts context and chooses the next sensible action.
- Current slice keeps the deterministic safety contract but makes the recommendation step more assistant-like.
- Added PHI-free funnel event labels for input received, recommendation shown, best-fit/form chosen, template gaps shown, draft shown, refinement replies, save attempt, and cancel/reset.
- Added `Use best fit` as the primary recommendation action. Other suggested forms and `See all forms` remain available as overrides.
- Added a display-only draft preview formatting guard so long clinical narrative/reflection fields are split into readable short paragraphs in Telegram review while saved Kaizen field values remain unchanged.
- Refined cancel/new-case handling: after Cancel the active conversation state ends cleanly, and extra clinical chunks sent while choosing a form are treated as more detail for the current fresh case rather than a second new-case warning.
- Next UX move: dogfood the same anonymised cases in Medic Portfolio and Portfolio Guru, then move to draft-first behaviour only for high-confidence obvious cases.

### Phase 2.9 - Portfolio Readiness / ARCP Health spec

Status:

- Approved product direction: build Portfolio Readiness as a generic Portfolio Guru feature, not as a Moeed-only tracker or Medic-internal automation.
- Canonical spec lives in `docs/ARCP_HEALTH_DESIGN.md`.
- MVP is manual/user-entered first: no Kaizen login, scraping, import, browser automation, supervisor request, or automated submission.
- Safety contract: readiness is a planning aid only; it must not claim ARCP success or invent requirements, dates, evidence, supervisors, or clinical details.
- Next build slice is data contracts plus a pure readiness engine with offline tests only. Leave live Telegram filing, Kaizen flows, browser actions, deployment, and service runtime unchanged.

### Phase 2.10 - Kaizen Mapping Sprint (read-only adapter foundation)

Status:

- Added 2026-06-01 to formalise the Kaizen platform map as a reusable adapter
  rather than another stack of per-form, per-user scrapes.
- Plan and scorecard live in `docs/roadmap/kaizen-mapping-sprint-2026-06.md`.
- The adapter defines routes, entity shapes, source priority, extraction
  methods, the page-render contract, gotchas, and a versioning + drift signal
  shared across all users.
- The first build slice is **Kaizen Portfolio Index v1**: a read-only refresh
  that walks the timeline, event detail, activities/drafts, files, profile,
  and goals surfaces for the logged-in user, de-duplicates by event UUID, and
  writes a normalised `evidence_items` + `index_runs` schema (local SQLite
  first; Supabase mirror is a follow-up).
- Current implementation status: the storage substrate, offline sync driver,
  trusted login/session bootstrap wrapper, and guarded user-facing refresh
  workflow have landed.
  `backend/kaizen_sync.py` consumes an already-authenticated page/session,
  walks timeline categories plus `/activities`, opens detail views read-only,
  writes through `kaizen_index`, and records `index_runs` drift/auth/failure
  status. A new high-level helper `sync_kaizen_portfolio_index_for_user`
  opens an isolated CDP page via the existing form-filer
  `connect_cdp_browser`, tries `use_cached_session`, and falls back to
  `store.get_credentials` plus the existing `_login` helper when the cache is
  stale; it persists the fresh session via `save_session_state` and then
  hands the page to the read-only sync. Bootstrap-stage failures still write
  an `index_runs` row so `/settings` can surface the outcome. None of this
  is exposed to users through `/settings -> Refresh portfolio -> Refresh now`.
  The confirmation screen states that this is read-only and does not save,
  submit, sign, delete, edit Kaizen, create drafts, or send supervisor
  requests.
- First live read-only smoke status: the initial 2026-06-01 attempt against
  the managed CDP browser stopped at Kaizen auth and read no rows. The follow-
  up smoke used `sync_kaizen_portfolio_index_for_user` with the same trusted
  login/session bootstrap used by the form filer, against a temporary local
  database only. It got past sign-in, read one real Kaizen assessment row
  (`DOPS - (ST3-ST6 - 2025 update)`), wrote one temporary `evidence_items`
  row, and recorded the run as `ok`. Production `usage.db` stayed untouched.
- The Index becomes the primary auto-populate source for
  `docs/PORTFOLIO_HEALTH_SPEC.md` Phase 2; existing PG filing records remain
  the fallback when no index is present yet.
- Safety: no write codepath, no Telegram traffic, no production index write,
  no restart, no deploy, no push. The login wrapper may use the existing saved
  credential path to restore an authenticated session, then hands the page to
  the read-only adapter, which refuses to act on `auth.kaizenep.com`. No
  supervisor surfaces, no assessor surfaces, no `/inbox` in v1.
- The conversational engine (Phases 2.x) and filing routes (Phase 5) are
  unaffected by this sprint — the adapter is a read-only foundation under
  them, not a replacement.
- Next step: manually test the visible Telegram workflow in Portfolio Guru:
  `/settings -> Refresh portfolio -> Refresh now`. Moeed should check the
  wording, button path, progress/result screen, and whether the refreshed
  Portfolio Health entry point feels like the right product flow before more
  Portfolio Health behaviour builds on top.

### Phase 2.11 - Browser automation architecture and control-plane UX

Product direction:

- Portfolio Guru stays **API/document-first and deterministic-first**. Use
  direct APIs, local documents, structured extraction, and DOM-mapped
  Playwright/CDP workflows whenever they can solve the job reliably.
- Browser automation is not the core product identity. It is the adapter used
  when portfolio systems force logged-in browser work.
- For Kaizen and other mapped systems, prefer the existing deterministic
  browser adapter: known routes, known fields, verified selectors, and
  draft-only save boundaries.
- For logged-in hard workflows that are not yet mapped, use Browser Harness as
  the local/cheap baseline before considering hosted browser services.
- Browser Use Cloud is a later scale/stealth/proxy/captcha option, not an MVP
  dependency.
- Browser Use Terminal is not the engine, but its control-plane pattern is
  useful: watch, steer, stop, resume, review artefacts, inspect history, and
  approve risky steps.

UX contract:

- Avoid the bad black-box version: `Portfolio Guru updated your portfolio`.
- Show what the agent opened, read, changed, skipped, and could not verify.
- Preserve proof artefacts where useful: screenshots, field summaries,
  before/after status, run history, and assumptions.
- Stop before risky actions: supervisor submission, signing, approval, deletion,
  credential changes, payment changes, or anything that alters the doctor or
  assessor's professional record beyond a reviewed draft.
- When browser automation fails, report the exact failure class in product
  language: auth needed, selector drift, page unavailable, missing field,
  ambiguous match, unsupported form, or user approval required.

Status:

- This is a product architecture decision, not a tooling install request.
- No Operator browser-tooling change is needed now.
- The next build implication is to make read-only sync and filing flows expose
  clearer proof/history/assumption surfaces before expanding to more portfolio
  systems.

### Phase 3 - Safe activation for low-risk intents

Activate router for:

- `portfolio_question`
- `unknown`
- `account_or_billing`
- `setup_or_credentials`

Keep case creation and filing on existing paths until the router proves stable.

Verification:

- ordinary "what can you do?" and "why am I blocked?" messages receive useful replies
- no disruption to existing button workflows

### Phase 4 - Case-intake activation

Route natural case descriptions into the existing case extraction flow.

The output should be the same structured draft/recommendation flow users already know, but the input can be natural text.

Verification:

- existing case flows still pass
- natural case prompts create drafts without requiring command/button setup first
- unclear cases ask one clarifying question instead of failing silently

### Phase 4.5 - Conversational engine history (updated 2026-06-01)

The conversational engine work that started as a separate private dogfood bot
has been merged into the main Telegram bot behind `PG_GATHERING_MODE`. The
live code path is now the in-bot gathering mode and `/gather` toggle; the
separate polling runner, private-token scaffold, and local vNext runner script
have been retired.

### Phase 5 - Filing command activation

Allow natural filing instructions only when a draft already exists and the action is clear.

Examples:

- "file this as a CBD"
- "send this to Kaizen"
- "also make a reflection log"

Verification:

- filing still requires explicit confirmation
- wrong/ambiguous form requests ask a clarification
- no duplicate Kaizen drafts

## Non-Goals

- No rewrite of Kaizen filing.
- No removal of buttons from the public bot.
- No free-form agent with permission to file autonomously.
- No migration of the public bot identity onto the vNext engine until dogfood beats the current bot.
- No live wiring of the vNext private test bot into launchd, the Mac Mini GitHub runner, or the public bot's Telegram token.

## Success Criteria

The change is successful when:

- a doctor can use the bot naturally without knowing commands
- existing deterministic workflows still pass tests
- unknown messages get helpful recovery responses
- Kaizen filing remains confirm-first and auditable
- fewer users hit dead-end silence or "dumb bot" behaviour

## First Build Slice

Build Phase 1 only:

1. Add the router contract and tests.
2. Do not wire it into live message handling yet.
3. Run the test suite.
4. Commit the branch checkpoint.

## 2026-06-05 — Conversation-supervisor slice (channel-agnostic)

Branch `feature/conversation-supervisor-20260605`. Smallest coherent
architecture slice that consolidates gathering-mode decisioning and makes the
Telegram implementation portable to WhatsApp.

What changed:

- **`backend/channel_actions.py`** — channel-agnostic reply model. A reply is
  defined once as a `ChannelReply` (body, optional continuation, actions) and
  renders losslessly two ways: Telegram inline buttons (`callback_data ==
  action_id`) via `to_telegram_keyboard`, and a WhatsApp-friendly numbered
  block via `render_numbered`. `resolve_numbered_choice` maps a numbered/plain
  reply back to the `action_id`. Telegram button text is no longer the source
  of truth.
- **`backend/conversation_supervisor.py`** — the single gathering-turn control
  loop. `classify_gathering_turn` separates canonical intent (from
  `conversational_router`) from turn kind (`FINISH_CASE`, `ANSWER_CAPABILITY`,
  `ANSWER_SIDE_QUESTION`, `CONTINUE_GATHERING`). `decide_gathering_turn`
  returns a channel-agnostic `GatheringDecision`. Genuine
  portfolio/account/setup questions are answered through an injected grounded
  `answer_question` callable and always carry a continuation line back to the
  case, so a side question never strands the user outside filling. Capability/
  greeting copy is templated and deterministic. The supervisor owns no I/O.
- **`backend/message_policy.py`** — capability, greeting, gathering-captured,
  and gathering-continuation copy now live here as auditable FIXED templates.
- **`backend/vnext_dialogue_policy.py` / `backend/bot.py`** — the old
  "private vNext test bot / dogfood" side-chat copy is removed from the live
  gathering path. `bot.handle_gathering_input` now delegates to
  `decide_gathering_turn`. For live copy this supersedes the earlier "separate
  private dogfood bot" decision recorded above: dogfood/test-bot wording can no
  longer reach a real user, and tests enforce it.

Out of scope (unchanged): Kaizen filing, `filer_router`, `browser_filer`,
credentials, billing/Stripe, supervisor submission, and every
confirm-before-file gate.

Tests: `test_channel_actions.py`, `test_conversation_supervisor.py`, and
updated `test_gathering_mode.py` prove dogfood copy cannot leak, grounded side
questions use the injected answer path plus a continuation, and actions render
as both Telegram buttons and numbered replies without losing labels/context.
Full offline gate green (1140 passed). Not live until the Mac Mini bot is
redeployed.

## 2026-06-14 — EMGurus WhatsApp Gateway boundary (contract only)

**Architecture decision (locked).** There is one EMGurus WhatsApp business
number and one external EMGurus gateway/router. Portfolio Guru is a separate
_internal service_ behind that gateway for 1:1 ARCP/Kaizen portfolio workflows
— **not** a direct WhatsApp bot. Group, community, and exam behaviour belong to
the other Gurus (Career / Exam) behind the same gateway, never to this repo.
This repo does not connect to Meta/WhatsApp and holds no WhatsApp credentials.

**Responsibility split (gateway-owned vs Portfolio Guru-owned):**

| Concern                                                    | Owner           |
| ---------------------------------------------------------- | --------------- |
| WhatsApp business number, Meta/WhatsApp API plumbing       | EMGurus Gateway |
| DM-vs-group detection and routing                          | EMGurus Gateway |
| Identity resolution (channel id → EMGurus user)            | EMGurus Gateway |
| Fan-out to the right Guru (Career / Exam / Portfolio)      | EMGurus Gateway |
| 1:1 portfolio extraction, drafting, draft-only Kaizen save | Portfolio Guru  |
| Refusing group scope for portfolio evidence                | Portfolio Guru  |
| Keeping portfolio evidence private to the 1:1 session      | Portfolio Guru  |

**Privacy (locked).** Portfolio evidence is private 1:1 state by default and is
never shared into group/community agent context. The inbound contract marks
content `private=True` by default and refuses any non-DIRECT scope.

What changed (contract + guard only — no live wiring, Telegram path untouched):

- **`backend/channel_contract.py`** — the _inbound_ counterpart to
  `channel_actions.py`. A gateway hands in a channel-neutral `InboundMessage`
  envelope: `SessionRef` (channel + conversation id + optional resolved gateway
  user id), `ConversationScope` (`DIRECT` | `GROUP`), `text`, `MediaRef` tuple,
  and `private` (default `True`). `accept_inbound()` is the single entrypoint a
  gateway adapter calls before any portfolio workflow: it returns `HANDLE` for
  a DIRECT turn with content, `REFUSE_GROUP` (with a channel-neutral
  `ChannelReply` refusal that never echoes the inbound content) for group scope,
  and `REFUSE_EMPTY` for a contentless turn. The module is import-clean of
  `python-telegram-bot` so it can run inside a gateway process. It touches no
  credential, no network, and no Kaizen save gate.

How this composes with the existing channel-neutral stack: inbound
`InboundMessage` → `accept_inbound` (this slice) → `conversational_router`
(intent) → `conversation_supervisor` (decision, returns a `ChannelReply`) →
`channel_actions` renderers (Telegram keyboard _or_ WhatsApp numbered block).
The boundary is now symmetric: channel-neutral in, channel-neutral out.

Out of scope (unchanged): all Meta/WhatsApp connection work, the WhatsApp
number, credentials, Kaizen filing, `filer_router`, `browser_filer`,
billing/Stripe, supervisor submission, and every confirm-before-file gate. No
live handler imports `channel_contract` yet (same posture as the Phase 1 router
checkpoint).

Tests: `test_channel_contract.py` (10 tests) prove the envelope is channel-
neutral across WhatsApp/Telegram/Web, portfolio content is private by default,
group scope is refused as a gateway responsibility without leaking content, the
refusal renders without Telegram, and the module imports clean of Telegram.

**Next build slice (for the next agent).** When the gateway is ready to call in:

1. Write a thin gateway adapter that maps the gateway's inbound payload onto
   `InboundMessage` and `accept_inbound`, then bridges a `HANDLE` turn into the
   existing `telegram_vnext_adapter` → `conversational_case_engine` path
   (channel-neutral `IngestEvent`s), and renders the resulting `ChannelReply`
   via `render_numbered`.
2. Define how the gateway authenticates to Portfolio Guru and how it fetches
   media bytes for a `MediaRef.uri` — credentials stay server-side, never in a
   prompt or in this contract.
3. Keep the Kaizen save draft-only and confirm-first regardless of channel; no
   new path may file/save without the existing confirmation gates.

## 2026-06-15 — EMGurus WhatsApp Gateway bridge: outbound reply path (slice 2)

**Architecture decision (locked).** The portfolio bridge is now end-to-end for
a single DIRECT handled turn: Portfolio Guru replies back to the WhatsApp user
via a new authenticated outbound endpoint on the OpenClaw gateway, so the
user receives a real Portfolio Guru workflow question (not just the gateway ACK)
in their DM.

**What changed:**

_OpenClaw worktree (`local/portfolio-bridge-v2026.6.6`):_

- **`extensions/whatsapp/src/inbound/portfolio-outbound-route.ts`** (new) —
  `registerPortfolioOutboundRoute` registers `POST /api/channels/whatsapp/:accountId/send`
  via `registerPluginHttpRoute` (`auth: "plugin"`). Verifies `X-Portfolio-Secret`
  header against `PORTFOLIO_BRIDGE_SECRET` env var (same secret used by the
  inbound bridge, both directions). Calls `sendApi.sendMessage(to, text)` from
  the existing `createWebSendApi` instance. Inert and unregistered when the
  secret is not set — generic OpenClaw installs unaffected.

- **`extensions/whatsapp/src/inbound/monitor.ts`** (modified) — `attachWebInboxToSocket`
  calls `registerPortfolioOutboundRoute` after `createWebSendApi`, passes the
  live `sendApi`, and unregisters on `close()`.

_Portfolio Guru (`backend/webhook_server.py`):_

- **Added outbound helpers** — `_OutboundConfig` (private class),
  `_resolve_outbound_config` (reads `PORTFOLIO_OUTBOUND_URL` /
  `PORTFOLIO_OUTBOUND_ACCOUNT_ID` / `PORTFOLIO_OUTBOUND_SECRET` from env;
  returns `None` when incomplete — feature inert), `_make_initial_gathering_reply`
  (returns a channel-neutral `ChannelReply` opening question rendered via
  `render_numbered`), `_send_portfolio_turn_reply` (async `httpx` POST to the
  gateway outbound route).

- **Modified `portfolio_inbound` handler** — on `InboundDisposition.HANDLE`:
  resolves outbound config, renders the gathering reply, calls
  `_send_portfolio_turn_reply`; outbound failures are logged as warnings and
  never propagate (handler still returns `disposition: handle`). GROUP and EMPTY
  dispositions preserve their existing refusal/no-side-effect behaviour.

**Env vars added (Portfolio Guru, all optional — feature inert if absent):**

| Var | Purpose |
|---|---|
| `PORTFOLIO_OUTBOUND_URL` | Base URL of the OpenClaw gateway |
| `PORTFOLIO_OUTBOUND_ACCOUNT_ID` | WhatsApp account id to route through |
| `PORTFOLIO_OUTBOUND_SECRET` | Shared secret for X-Portfolio-Secret header |

**Responsibility split update:** the gateway's `PORTFOLIO_BRIDGE_SECRET` is now
used for both directions — it gates the gateway→PG inbound call (via
`PORTFOLIO_INBOUND_SECRET` on the PG side) and the PG→gateway outbound call
(via `PORTFOLIO_BRIDGE_SECRET` on the OpenClaw side, echoed as
`PORTFOLIO_OUTBOUND_SECRET` in the PG env).

**Out of scope (unchanged):** full conversation supervisor / case engine wiring,
Kaizen filing, Telegram path, live WhatsApp, credentials, push, restart, deploy.

**Tests:**

- OpenClaw: `portfolio-outbound-route.test.ts` — 11 passed (inert with no
  secret; route registration path/auth/accountId; handler 401 on missing/wrong
  secret; 200 + sendMessage call on valid request; 400 on missing to/text or
  invalid JSON; array-header handling).
- Portfolio Guru: `test_portfolio_inbound_bridge.py` — 15 passed (6 carried +
  9 new: outbound invoked on HANDLE with rendered WhatsApp text; GROUP/EMPTY do
  not trigger outbound; auth still rejects on wrong secret; outbound failure
  reported safely; HANDLE succeeds without outbound configured). Full offline
  gate: **1408 passed, 0 failed**.

**Release classification:** local/build-complete, proof-pending.

**Remaining live/manual gates (orchestrator/foreground only):**
1. Set `PORTFOLIO_OUTBOUND_URL` / `PORTFOLIO_OUTBOUND_ACCOUNT_ID` /
   `PORTFOLIO_OUTBOUND_SECRET` in the Mac Mini Portfolio Guru env (BWS).
2. Set `PORTFOLIO_BRIDGE_SECRET` in the OpenClaw gateway env (BWS).
3. Live WhatsApp DM end-to-end smoke (user sends "portfolio" DM → gateway ACK →
   Portfolio Guru gathering question received in same DM).
4. Push both repos once live gates pass.
