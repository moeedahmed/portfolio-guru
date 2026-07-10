# Portfolio Guru — Agent Workflow Reference

> Last updated: 2026-03-07
> Optimised for agent consumption — no diagrams, pure structured text.
> Human-readable Mermaid diagrams live in Notion (Portfolio Guru page).
> Update this file whenever a flow changes.

For the private-beta launch boundary, supervisor scope, rollback path,
and monitoring cadence, see `docs/PRIVATE_BETA_LAUNCH.md`. The dogfood
smoke checklist lives in `scripts/dogfood_smoke.sh`.

---

## Current Product Focus — Telegram Launch Proof

As of 2026-07-09, Portfolio Guru is a Telegram-first private beta. WhatsApp is
paused as a product priority, not deleted: keep the connector/code intact, but
do not extend WhatsApp parity, Cloud API, or web-workflow scope until Telegram
proves real repeat use.

Launch-proof work should improve the Telegram golden path only:

1. case capture
2. recommendation
3. draft preview
4. edit/approve
5. Kaizen draft save
6. retry/failure recovery
7. PHI-free funnel and filing reliability reporting

Use `/funnelreport` for journey proof and `/filingreport` for Kaizen reliability.
Do not infer adoption from bot logs alone.

---

## Channel Boundary — Dedicated Portfolio Guru WhatsApp Connector

Portfolio Guru is the deterministic product engine and the source of truth,
exactly like the Telegram beta bot. WhatsApp is **only a channel connector /
transport** for a **dedicated Portfolio Guru WhatsApp number/account** — not the
general EMGurus WhatsApp account, and not an EMGurus/Guru fan-out gateway.
Portfolio Guru is never a Hermes/EMGurus agent layer; Hermes may be used only as
an optional *thin transport wrapper*, never as the brain, classifier, drafter,
product identity, or a general EMGurus fan-out front door. The flows below are
channel-neutral — they describe the 1:1 portfolio conversation regardless of
whether the connector delivered it over Telegram, WhatsApp, or web.

Responsibility split:

| Concern                                                    | Owner                     |
| ---------------------------------------------------------- | ------------------------- |
| Dedicated PG WhatsApp number/account, Meta/WhatsApp plumbing | WhatsApp channel connector |
| DM-vs-group detection and routing                          | WhatsApp channel connector |
| Identity resolution (channel id → gateway user)            | WhatsApp channel connector |
| 1:1 portfolio extraction, drafting, draft-only Kaizen save | Portfolio Guru engine      |

The connector is deliberately thin: it moves messages to and from the dedicated
Portfolio Guru WhatsApp account and calls the engine's channel-neutral contract.
It carries no product logic. A Hermes profile, if used, is one possible thin
transport for that connector, not a required layer — the readiness guard
(`scripts/pg_whatsapp_readiness.py`) only enforces Hermes-profile gates when the
chosen connector is Hermes.

Boundary contract (code: `backend/channel_contract.py`, the inbound
counterpart to `backend/channel_actions.py`):

- A connector adapter hands in a channel-neutral `InboundMessage`
  (`SessionRef` channel/conversation/user, `ConversationScope` DIRECT|GROUP,
  `text`, `MediaRef` tuple, `private=True` default).
- `accept_inbound()` is the single entrypoint and has no side effects:
  `HANDLE` for DIRECT-with-content, `REFUSE_GROUP` (with a channel-neutral
  refusal that never echoes the inbound content) for group scope,
  `REFUSE_EMPTY` otherwise.
- Group/community scope is refused by design — it is the connector's problem,
  never Portfolio Guru's. Portfolio Guru refuses GROUP scope and never owns
  group mode.
- Portfolio evidence is private 1:1 state by default and must never be
  replayed into any group/community agent context.
- Contract + guard only: no Meta/WhatsApp connection, no credentials, and
  no live handler imports it yet. The Telegram path is unchanged. The module
  is import-clean of `python-telegram-bot` so it can run inside a connector
  process that never loads Telegram. Tests: `tests/test_channel_contract.py`.

### First-contact parity (WhatsApp opens like the Telegram bot)

The connector must feel like the Telegram beta bot at first contact: a first
message of `/start`, `start`, a bare greeting (`hi`/`hello`/`hey`), or a
capability question (`help`, `what can you do?`) is answered with WhatsApp-native
onboarding, and a real first message (a clinical case) routes straight into the
engine. No "magic sentence" is required — the user never has to type a full case
just to get a coherent reply.

This is a deterministic, LLM-free classifier (`backend/portfolio_first_contact.py`)
wired into the inbound bridge's HANDLE path (`webhook_server._select_inbound_reply`):

- `START_OR_GREETING` → the FIXED `welcome_disconnected` welcome (same copy the
  Telegram `/start` uses via `message_policy`).
- `CAPABILITY` → the FIXED `capability_overview` (what Portfolio Guru can do).
- `CASE` (default; also media-only turns and blank text) → existing routing:
  rich case (≥ `_RICH_CASE_WORD_THRESHOLD` words) → form-recommendation insight,
  otherwise the generic gathering prompt.

Matching is exact against normalised text (a leading `/` is stripped so `/start`
and `start` resolve identically on a channel with no command menu), so a real
case that merely *opens* with "hi" is never swallowed as a greeting. The module
owns no product logic and pulls all copy from `message_policy`, so the WhatsApp
first-touch copy can never drift from the Telegram surface or fabricate clinical
content. Tests: `tests/test_portfolio_first_contact.py` and the onboarding cases
in `tests/test_portfolio_inbound_bridge.py`.

> Historical: an earlier plan routed Portfolio Guru behind a single shared
> EMGurus WhatsApp gateway that fanned out to multiple Gurus. That shared
> front-door route is withdrawn for tester rollout — testers use the dedicated
> Portfolio Guru WhatsApp account only.

---

## Conversation States

| State constant      | Meaning                                                      |
| ------------------- | ------------------------------------------------------------ |
| `IDLE`              | No active conversation. Waiting for any input.               |
| `AWAIT_FORM_CHOICE` | Form type buttons shown. Waiting for user to select a form.  |
| `AWAIT_APPROVAL`    | Draft preview shown. Waiting for File / Edit / Cancel.       |
| `AWAIT_EDIT_FIELD`  | Edit mode. Waiting for user to select which field to change. |
| `AWAIT_EDIT_VALUE`  | Edit mode. Waiting for user to provide the new field value.  |
| `AWAIT_GATHERING`   | Opt-in collector. Waiting for more case detail or "done".    |
| `AWAIT_USERNAME`    | Setup flow. Waiting for Kaizen username.                     |
| `AWAIT_PASSWORD`    | Setup flow. Waiting for Kaizen password.                     |

**Invariant:** Every path to `ConversationHandler.END` must call `context.user_data.clear()` first.

## Telegram Callback And State Map

This map is the regression guard for Telegram button fixes. Keep it updated
whenever callback payloads or conversation states change.

| Callback family | Primary handler | Required active state/data | Safe stale-button behaviour |
| --- | --- | --- | --- |
| `ACTION\|setup` | `setup_start` | none, or setup flow anchor | If already waiting for username/password, answer the callback and do not send duplicate Step 1/2 prompts. |
| `ACTION\|file` | `handle_action_button` / case input path | none | Clear old flow state and prompt for a fresh case. |
| `ACTION\|retry_filing` | `handle_callback` -> approval save path | active `draft_data` or restorable last amend draft | Retry the current draft only; if draft state is missing, retire the old button and explain that the draft is no longer active. |
| `ACTION\|reset` / `CONFIRM\|reset` | `reset_data` / `handle_reset_confirm` | none | Clear user state and credentials only after explicit confirmation. |
| `FORM\|best` | `handle_form_choice` | `form_recommendations`, `case_text` | If the case is gone, remove the stale keyboard and ask for a new case. |
| `FORM\|<form>` | `handle_form_choice` | `case_text`; optional recommendations | File the selected form against the active case only; never use a form tap to start a synthetic case. |
| `APPROVE\|draft` | `handle_approval_approve` | `draft_data` plus credentials | One-shot external effect: set `filing_in_progress`, remove old markup, and never run duplicate Kaizen saves on double tap. |
| `EDIT\|draft` / `FIELD\|<field>` | `handle_approval_edit` / `handle_edit_field` | active draft | If draft state is missing, recover to the latest active draft message or end with a clear stale-draft explanation. |
| `CANCEL\|draft` / `CANCEL\|form` | `handle_callback` | optional active state | Clear active flow state and return to idle; do not leave old buttons live. |
| `SET_CURRICULUM\|*`, `LEVEL\|*` | settings/setup handlers | setup or settings flow anchor | Respect the active flow; setup callbacks must not be stolen by global settings handlers. |
| `UPGRADE\|*` | `handle_upgrade_button` | current user tier | Start checkout only after explicit tap; failures stay in Telegram and do not affect filing state. |

Callback invariants:

- Every external side effect callback must be idempotent before the first
  `await`.
- Every clicked keyboard that advances a flow should be retired or made
  harmless.
- A stale callback must never create a fake clinical case.
- A retry callback must only run against a current or deliberately restored
  draft.
- Funnel events must stay PHI-free and should be emitted at state transitions,
  not from raw message text.

### Controlled flexibility for free text

Portfolio Guru is a resilient assistant around a deterministic filing engine,
not a button-only machine and not an LLM-controlled state machine.

- Free text may ask questions, clarify, add genuine case context, or request a
  draft edit. Side questions are answered and return to the exact active step.
- Amend, save, cancel/reset/abandon, and case-switch actions require explicit
  intent that is permitted in the current state, or an existing callback.
- Ambiguous destructive/case-switch language asks for confirmation and does
  not mutate the active case or draft.
- A classifier failure fails closed: preserve the active state and ask for a
  clearer instruction instead of guessing a state-changing action.
- After successful completion, substantial clinical text starts a fresh case;
  thanks, questions, and vague edit requests do not do so silently.
- Command-sounding clinical wording (for example, documentation being
  incomplete or a clinical case being cancelled) remains case evidence unless
  the user explicitly refers to the Portfolio Guru draft/form/ticket.

The pure decision contract lives in `backend/workflow_turn_policy.py`; Telegram
handlers execute the returned decision but remain owners of I/O and the current
deterministic filing flow.

---

## Flow 1 — First-Time User

```
/start
→ Welcome message + [What is this? | Connect Kaizen | File a case]

[What is this?]
→ Explain message
→ Return to idle (no state change)

[Connect Kaizen]
→ has_credentials(user_id)?
  YES → "Kaizen already connected ✅" + [File a case] button
  NO  → Ask for Kaizen username → AWAIT_USERNAME
        → Ask for Kaizen password → AWAIT_PASSWORD
        → Encrypt + store in SQLite
        → "Connected ✅" + [File a case] button

[File a case]
→ has_credentials(user_id)?
  NO  → "Please connect Kaizen first" + [Connect Kaizen] button
  YES → "Send me your case" prompt → waiting for text/voice/photo
```

---

## Flow 2 — Core Filing (Happy Path)

```
User sends input (text / voice / photo)
→ classify_intent(text)
  CHITCHAT → friendly reply, return to idle
  QUESTION → answer_question(text), return to idle
  CASE     → proceed

If `PG_GATHERING_MODE=1` and the user has enabled `/gather on`:
  → Store this input as part of one open case
  → State → AWAIT_GATHERING
  → Subsequent case detail is appended
  → Simple side questions are answered as chat, not case content
  → User says "done" / "file this" / "preview"
  → Combined case text continues through the normal recommendation flow

Detect input type:
  text  → use as-is
  voice → whisper.transcribe() → text
  photo → vision.extract_from_image() → text

→ recommend_form_types(text)
  Returns list of FormTypeRecommendation objects (max 3)
  Each has: form_type, uuid, reason

→ Show buttons: one per recommended form + [❌ Cancel]
  State → AWAIT_FORM_CHOICE

User taps [❌ Cancel]
→ "Cancelled." context.user_data.clear() → END

User taps [form type]
→ extract_cbd_data(text, form_type)
  Returns CBDData with all fields:
  date, setting, presentation, clinical_reasoning,
  reflection (humanized), slos, key_capabilities
→ Store in context.user_data['draft']
→ Show draft preview message
→ Show [✅ File this draft | ✏️ Edit | ❌ Cancel]
   State → AWAIT_APPROVAL

[❌ Cancel]
→ context.user_data.clear() → END

[✏️ Edit]
→ Flow 3 (Edit Before Filing)

[✅ File this draft]
→ filer.file_form(draft, credentials)
  browser-use opens Kaizen
  navigates to: https://kaizenep.com/events/new-section/<UUID>
  fills all fields
  clicks "Save as Draft" ONLY — never submits to supervisor
  SUCCESS → "✅ Saved as draft in Kaizen" + [✅ Done | 📤 File another]
  FAILURE → "❌ Filing failed: <error>" + [🔁 Retry | ❌ Cancel]

[✅ Done]
→ context.user_data.clear() → END

[📤 File another]
→ context.user_data.clear() → "Send me your next case" → idle
```

---

## Flow 2A — Assess Ticket (Read-Only Mapping / Planned)

This is the assessor-side equivalent of Flow 2. It is not a persistent "mode";
it is a second entry point into the same review-and-approve engine.

```
Ticket appears in assessor portfolio
→ Portfolio Guru notification: "You have a ticket awaiting assessment"
→ User opens ticket from bot or Kaizen link
→ Bot extracts ticket content read-only
→ Bot shows:
   - ticket type
   - trainee/doctor details visible on the ticket
   - ticket fields and attachments metadata
   - Kaizen link as backup
→ Assessor gives intent:
   "Looks fine, sign it"
   "Ask them to add more reflection"
   "Mention clearer clinical reasoning"
   "Approve but add X"
→ Bot drafts assessor feedback/sign-off text
→ Bot shows missing assessor fields or risk notes if present
→ Bot shows draft response
→ User requests a reviewed Kaizen action plan
→ Bot shows the mapped fields and blocked/safety status
→ If the plan is executable (unblocked CBD save_draft), bot offers
  📤 Save draft in Kaizen
→ User taps Save draft in Kaizen
→ Bot posts a fresh confirmation message naming the action and the
  safety boundary, with [✅ Yes, save as draft | ❌ Cancel]
→ User taps Yes, save as draft
→ Bot re-validates plan, attaches CDP, navigates to the ticket,
  clicks Fill in, fills the mapped fields, and clicks Save as draft.
  Submit, sign, approve, send, reject, and delete remain out of scope.
→ Bot reports save success or a user-facing failure reason
```

Current implementation status:

```
Read-only mapping scaffold exists:
  backend/assessor_mapper.py
  backend/assessor_reader.py
  backend/state_tracker.py
  backend/supervisor_poller.py
  backend/role_detector.py
  backend/supervisor_workflow.py    # orchestration seam

Allowed during mapping:
  - navigate to Assessments timeline
  - list visible assessment rows (now also records `fill_action`)
  - open ticket detail pages
  - extract read-only fields, tags, state, visible buttons
  - output PHI-free ticket shapes for mapping

Backend integration landed earlier in the branch:
  - Account role cached per Telegram user (profile_store.kaizen_role)
    via supervisor_workflow.set_role_if_better — refuses to demote a
    known-good "assessor"/"trainee" cache to "unknown" on a flaky probe.
  - setup_password persists the canonical role after a successful login
    (one new line; trainee setup behaviour unchanged).
  - supervisor_workflow.run_supervisor_poll is a callable, fully tested
    orchestrator: refreshes role, gates on role=="assessor", polls the
    queue via supervisor_poller, returns PHI-free
    SupervisorNotificationPayload objects.
  - supervisor_workflow.render_supervisor_notification_text /
    render_supervisor_ticket_detail_text are pure formatters reused by
    the live workflow.

Live read-only supervisor workflow landed in this slice:
  - supervisor_scheduler.supervisor_poll_tick — JobQueue tick every 5
    minutes (first fire +5 min). Inert unless at least one user has
    kaizen_role=="assessor" AND credentials AND a reachable CDP session
    at localhost:18800. Per-user state file under
    ~/.openclaw/data/portfolio-guru/supervisor/. Trainee-only deploys
    stay silent.
  - supervisor_bot.send_supervisor_notification — turns a PHI-free
    payload into a Telegram message with Open / Skip / Later buttons
    and stashes the payload in supervisor_notification_cache.
  - supervisor_bot.handle_supervisor_callback — Open delegates to
    assessor_reader.open_ticket_readonly (read-only); Skip / Later are
    pure UI acknowledgements and never navigate to Kaizen.
  - profile_store.list_users_by_kaizen_role — scheduler-facing query
    helper used to short-circuit when no assessor users exist.
  - Source scans assert the new modules never click Fill in / Save /
    Submit / Sign / Approve / Delete / Send (test_supervisor_scheduler,
    test_supervisor_bot, plus the existing supervisor_workflow scan).

Local assessor draft capture landed next:
  - backend/assessor_drafter.py — pure draft service. Given a free-text
    supervisor intent and an assessor schema, builds a structured
    AssessorDraft with values (intent → feedback, entrustment inferred
    from numeric/behavioural hints), missing_required fields, risk
    notes (brief feedback, missing recommendation phrasing, missing
    entrustment, missing assessor identity), and render_preview for a
    Markdown Telegram preview.
  - backend/assessor_session_store.py — per-supervisor file cache that
    records the active ticket UUID, form type, ticket URL, the trainee
    section, and any in-progress intent/draft. Lives in the same
    supervisor data dir as the notification cache. Missing or corrupt
    files behave like "no session" rather than raising.
  - supervisor_bot.handle_supervisor_callback now also routes
    SUP|review (re-render preview), SUP|recapture (clear draft, prompt
    again), and SUP|cancel-draft (end session). None of these touch
    Kaizen.
  - supervisor_bot.handle_assessor_intent_capture — high-priority
    MessageHandler (group=-1) wired in bot.build_application. Inert
    when no active session for the user; otherwise transcribes voice
    via whisper.transcribe_voice, drafts via assessor_drafter, and
    replies with the preview + review keyboard. Raises
    ApplicationHandlerStop on success so the trainee flow in group 0
    does not double-process the message. Commands (/cancel, /start,
    etc.) are excluded from the filter so trainee fallbacks still
    reach the default group untouched.

Guarded write-back planning landed next:
  - backend/assessor_writeback.py — adapter that maps a reviewed local
    assessor draft to the Kaizen assessor completion surface for CBD only.
  - The adapter distinguishes fill_fields, save_draft, submit, sign,
    approve, and cancel. It requires ticket UUID, form type, explicit
    action, and reviewed draft hash for every Kaizen-touching action.
  - Mismatched ticket identity, mismatched draft hash, unsupported form
    type, missing required fields, and final actions produce blocked
    plans. Plan rendering is still descriptor-only.
  - supervisor_bot adds a review-only button: "Prepare Kaizen action
    plan (no write)". That callback renders the guarded plan from the
    current local draft and never connects to CDP or opens Kaizen.

Guarded save-draft live runner landed in this slice:
  - assessor_writeback.execute_write_plan now runs against a real CDP-
    attached Playwright page when — and only when — the plan is an
    unblocked CBD save_draft, the draft hash still matches, the ticket
    URL contains the planned ticket UUID, and every browser step kind
    is on the live allow-list ({open_completion_surface, fill_field,
    save_draft}). Any other condition raises
    AssessorWriteBackUnavailable before navigation. Recoverable runner
    failures (Fill in / field input / Save button missing, navigation
    error, missing Kaizen confirmation marker) return an
    AssessorWriteResult(status="failed", error=…) instead of raising.
  - The runner clicks "Fill in" once, fills the mapped CBD assessor
    fields by label, and clicks "Save as draft" — and nothing else.
    Source-scan refuses click/locator targets for Submit / Sign /
    Approve / Send / Reject / Delete in assessor_writeback.
  - supervisor_bot exposes the explicit confirmation step. After the
    reviewed plan is shown, an executable plan surfaces
    "📤 Save draft in Kaizen" (SUP|request-save-draft). Tapping it
    posts a separate confirmation message that names the action and
    safety boundary and offers "✅ Yes, save as draft" / "❌ Cancel".
    Only SUP|confirm-save-draft re-validates the plan, attaches CDP,
    calls execute_write_plan, and reports success or a user-facing
    failure reason. Session ends on success so future text/voice falls
    back to the trainee flow; session is preserved on failure so the
    supervisor can retry without re-recording. CDP-down, stale draft,
    blocked plan, missing ticket URL, runner failure, and unexpected
    exceptions all have distinct user-facing messages.
  - Ordinary Open / Skip / Later / Review / Recapture / Cancel /
    Prepare-writeback / Request-save-draft callbacks never invoke the
    live runner. A dedicated test sweeps every callback to assert that
    execute_write_plan is not awaited.

Not built yet:
  - LLM-assisted field extraction (current drafter is deterministic;
    feedback gets the raw intent, other assessor fields stay blank).
  - Submit / Sign / Approve / Send / Delete / Reject live actions —
    these remain blocked and tested. The save-draft runner is the
    only live assessor write in this product.
  - Save-draft for other assessor completion surfaces (DOPS, Mini-CEX,
    QIAT, ESLE). The runner refuses non-CBD plans until each
    completion surface is explicitly mapped, bound, and tested.
```

First mapped read-only ticket shape:

```
Ticket type: CBD - Case Based Discussion (2025 update)
Route: view-section
Visible read-only fields:
  - Date occurred on
  - End date
  - Case to be discussed
  - Attach files
Write-side controls detected but not clicked:
  - Fill in
  - Save
Implication:
  - The next mapping gap is the post-Fill in assessor form. Opening it may create
    or modify assessor-side state, so it stays blocked until an explicit
    one-ticket approval gate exists.
```

First mapped assessor completion shape after explicit approval:

```
Ticket type: CBD - Case Based Discussion (2025 update)
Route after Fill in: fillin
Assessor-side fields/inputs:
  - Assessor Registration Number
  - Job title
  - Entrustment Scale
  - Feedback
  - Recommendation for further learning or development
Write-side controls detected but not clicked:
  - Submit
  - Save as draft
Product implication:
  - Assessor action can use the same draft/review/approve engine as filing,
    but final submit must remain a one-ticket explicit approval action.
```

## Flow 2B — Portfolio Readiness / ARCP Health (Planned)

Canonical product spec: `docs/ARCP_HEALTH_DESIGN.md`.

This is a planning/readiness flow, not a Kaizen filing flow.

```
User opens Portfolio Readiness
→ confirm training stage, curriculum/requirement preset, and review date if known
→ user manually adds or updates evidence items
→ user maps evidence to domains/SLOs/KCs, or confirms assistant-suggested mappings
→ bot/web shows readiness status with concrete reasons and uncertainties
→ user can generate a draft review pack for supervisor discussion
```

MVP boundaries:

- no Kaizen login, scraping, import, browser automation, or submission
- no supervisor request
- no ARCP success claim
- manual/user-entered evidence first
- "uploaded", "reviewed", and "accepted" are separate user-controlled statuses
- readiness labels must always show the supporting gaps/reasons

---

## Flow 3 — Edit Before Filing

```
Entry: user tapped [✏️ Edit] from AWAIT_APPROVAL state

→ "Which field to edit?" + field buttons:
  [Date | Setting | Presentation | Clinical reasoning | Reflection | SLOs | ❌ Back]
  State → AWAIT_EDIT_FIELD

[❌ Back]
→ Re-show draft preview + approval buttons
  State → AWAIT_APPROVAL

[field selected]
→ "Send me the new value for <field>"
  State → AWAIT_EDIT_VALUE

User sends text
→ Update context.user_data['draft'][field] = new_value
→ Re-show updated draft preview
→ Re-show [✅ File this draft | ✏️ Edit | ❌ Cancel]
  State → AWAIT_APPROVAL
```

---

## Flow 4 — Edit Previously Filed Draft (v2.1 — NOT YET BUILT)

```
User says "edit my last case" or similar
→ Query case_history table in SQLite by user_id
  FOUND     → Show summary + [Yes, this one | No, different one]
  NOT FOUND → "No matching case found. Describe it more."

[Yes, this one]
→ browser-use opens Kaizen draft URL
→ Show current field values
→ Enter Flow 3 (Edit)

[No, different one]
→ Prompt again
```

---

## Flow 5 — Reset / Recovery

Two distinct surfaces — keep them separate:

```
SOFT recovery (return to idle, keep the account):
  /cancel  OR  the 🆕 Start fresh button (ACTION|reset)
→ context.user_data.clear()
→ "❌ Cancelled." / next-step keyboard
→ END (returns to idle; credentials and profile untouched)

FULL reset (wipe local state, reconnect Kaizen):
  /reset   (and the hidden /delete backwards-compat alias)
→ comprehensive local purge: bot draft/flow state, Kaizen credentials,
  saved profile, usage/KC evidence, Kaizen index, Portfolio Health,
  session cache
→ "✅ Your Portfolio Guru data is clear …" + Connect Kaizen keyboard
→ END. Cases already saved in Kaizen are NOT touched.

User sends a new case while mid-state (stuck)
→ classify_intent → CASE
→ If AWAIT_APPROVAL or AWAIT_EDIT_*:
    "Looks like a new case — start fresh or fold it into the current one?"
    (offers Start fresh / fold-in buttons — never points at /reset)
→ If IDLE:
    proceed normally
```

---

## Form Type Decision Rules

Applied inside `recommend_form_types()` in `extractor.py`.

```
CBD — always include if any clinical case was managed by the trainee.

DOPS — include ONLY IF:
  - Trainee directly performed a procedure with their own hands
  - Procedure is invasive: LP, intubation, central line, chest drain, cardioversion, etc.
  - Do NOT include if trainee only observed or assisted

LAT — include ONLY IF:
  - Trainee explicitly led a team, coordinated a resus, or managed a major incident
  - Must be active leadership, not just being present as senior
  - Do NOT include for routine senior clinical decision-making

ACAT — include ONLY IF:
  - Description covers a full shift assessment or multiple patients across a shift
  - Not for a single case

ACAF — include ONLY IF:
  - Acute care assessment in a non-traditional setting explicitly described

Max 3 forms recommended per case.
Adjacent ≠ demonstrated. When in doubt, exclude.
```

---

## Key Capabilities Selection Rules

Applied inside `extract_cbd_data()` in `extractor.py`.

```
SELECT a KC ONLY IF:
  - The trainee directly demonstrated it in this specific case narrative
  - There is explicit textual evidence (e.g. "I intubated" → SLO3 KC1)

DO NOT SELECT if:
  - It could plausibly apply but isn't mentioned
  - The patient needed it but the trainee didn't personally do it
  - It's adjacent to what was described

Over-selection is a bug. Under-selection is safer. Max ~4 KCs per case.
```

---

## Data Flow

```
User input (text/voice/photo)
  ↓
bot.py — conversation state machine, Telegram handler
  ↓
extractor.py — Gemini API calls (classify, recommend, extract, humanize)
  ↓
context.user_data['draft'] — CBDData object in memory
  ↓ (after ✅ File this draft)
filer.py — browser-use + Chromium → Kaizen ePortfolio (draft saved)

SQLite DB (~/.openclaw/data/portfolio-guru/portfolio_guru.db)
  ↔ bot.py (read credentials, write case history in v2.1)

BWS secrets (at startup only):
  - Telegram bot token
  - Google API key (Gemini)
  - Fernet encryption key (for credentials)
```

---

## Hard Constraints (never violate)

- NEVER handle GROUP scope — portfolio filing is 1:1 only; `accept_inbound()` refuses group/community turns and never echoes their content (gateway's job, not ours)
- NEVER submit a form to supervisor — draft save only, every time
- NEVER submit/sign an assessor ticket without explicit approval for that one ticket and reviewed response
- NEVER create drafts, sign, submit, delete, approve, reject, or send feedback while running assessor mapping
- NEVER store credentials in plaintext — always Fernet-encrypt before writing to DB
- NEVER open Kaizen before the user taps [✅ File this draft]
- NEVER select a KC unless the trainee directly demonstrated it in this case
- Date format for Kaizen input fields: `d/m/yyyy` (not ISO 8601)
- KC over-selection is a correctness bug — err on the side of fewer

---

## User-Facing Message Standard

End-to-end reference for every bot bubble in `backend/bot.py`.
Safety-critical templates live in `backend/message_policy.py` and must not
drift from there — see [Safety-critical templates](#safety-critical-templates)
below.

### Message classes

Every user-facing string falls into exactly one class. The class determines
where the text lives, who can edit it, and what may flex by context.

| Class            | Lives in                           | Examples                                                                                                               | Allowed to vary?                                          |
| ---------------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `FIXED`          | `message_policy.MESSAGE_TEMPLATES` | welcome, captured ack, privacy nudge, AI unavailable, thin-case detail request, draft reply hint, file-case prompt     | No — copy is locked here and pulled via `render_message`. |
| `TEMPLATED`      | `message_policy.MESSAGE_TEMPLATES` | form recommendation (composed from rationales)                                                                         | Variable slots only.                                      |
| `LLM_ASSISTED`   | LLM output, post-filtered          | `answer_question`, `compose_filing_recovery_copy`, post-file `summarise_recent_activity`                               | Yes — bounded to low-risk explanation/recovery paths.     |
| `CONVERSATIONAL` | Inline in `bot.py`                 | Stage acks, slow-progress edits, mode-aware errors, filing outcomes, gate messages, button replies, recovery scaffolds | Yes — must follow the [Shape](#shape) and tables below.   |

If a `CONVERSATIONAL` line repeats verbatim in more than one handler, prefer
adding it to `message_policy.MESSAGE_TEMPLATES` rather than duplicating it.

### Shape

- One bot bubble per long async action. The first ack is edited in place as
  the action progresses — never followed by a separate "still working" bubble.
- One leading emoji per message, present-tense verb, en/em dashes for inline
  asides (`—`), ellipsis (`…`) for in-flight actions.
- Telegram's typing indicator (`_typing_until`) is the ambient progress
  signal for long work. Text reassurance is opt-in, sparing, and replaces
  the ack — it never adds a second line.
- Markdown only when `parse_mode="Markdown"` is also passed. Raw asterisks
  in plain-text bubbles are a bug — `plain_text_policy_violations()` in
  `message_policy.py` is the test surface for `FIXED` templates.

### Vocabulary by stage (new-case flow)

These are the canonical strings for the primary path: user sends a fresh
clinical case → bot extracts text → bot recommends forms. Other flows
reuse the acks and adjust the success/error recovery clauses (see
[Mode-aware error recovery](#mode-aware-error-recovery)).

| Stage                        | Photo                                                                       | Voice                                                                        | Video                                                                                  | Document                                                                                    | Kaizen save                                                                                                                                                                         |
| ---------------------------- | --------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ack                          | `📷 Reading image…` / `📷 Reading images…`                                  | `🎙️ Transcribing voice note…`                                                | `🎬 Extracting audio from video…`                                                      | `📄 Reading *{file_name}*…`                                                                 | `📤 Saving {form_name} as a Kaizen draft…`                                                                                                                                          |
| Slow-progress (replaces ack) | `📷 Still reading…` (after 8 s)                                             | —                                                                            | —                                                                                      | —                                                                                           | `📤 Still saving {form_name} — Kaizen is loading the form…` then `📤 Filling fields in {form_name} — almost there…` then `📤 Verifying the save on Kaizen — this is the last step…` |
| Success                      | `📷 Image read. Finding matching forms…`                                    | `🎙️ Voice note read. Finding matching forms…`                                | n/a (video transcribes via `transcribe_voice`)                                         | `📄 *{file_name}* read. Finding matching forms…`                                            | `✅ *{form_name} saved.*` (with a SLO/date summary line)                                                                                                                            |
| Error                        | `⚠️ Couldn't read image. Try a clearer photo or describe the case in text.` | `⚠️ Couldn't transcribe voice note. Try again or describe the case in text.` | `⚠️ Couldn't extract audio from video. Try a voice note or describe the case in text.` | `⚠️ Couldn't read *{file_name}*. Try a different file format or describe the case in text.` | `❌ Filing failed. Try again or start fresh.` (with retry / start-fresh buttons)                                                                                                    |

`NOT_CLINICAL` extraction (photo doesn't look clinical) gets its own
recovery line — `This image doesn't look like a clinical case. Send a text
description or a photo of clinical notes/findings.` — and ends the
conversation rather than retrying.

Media routing per state (from `build_application`):

- **New case** (`AWAIT_CASE_INPUT`): accepts text, voice, photo, document.
  Video is **not** routed here — the Video column above is the canonical
  Ack/Error wording for when video lands in a later state, not a new-case
  surface.
- **Template review** (`AWAIT_TEMPLATE_REVIEW`): accepts text, voice,
  photo, video, document.
- **Approval** (`AWAIT_APPROVAL`): accepts text, voice, photo, video,
  document.
- **Edit value** (`AWAIT_EDIT_VALUE`): accepts text plus a catch-all for
  voice/photo/document via `handle_edit_value`.

### Mode-aware error recovery

The cause clause stays the same regardless of flow; only the recovery
clause changes. Three flow modes:

| Flow mode                                              | Where it lives                                                 | Recovery clause                         | Why                                                                  |
| ------------------------------------------------------ | -------------------------------------------------------------- | --------------------------------------- | -------------------------------------------------------------------- |
| **New case**                                           | `handle_case_input`                                            | `describe the case in text` (per media) | User hasn't picked a form yet — keep guiding toward providing case.  |
| **Template review** (form chosen, accumulating detail) | `handle_template_review_media` / `handle_template_review_text` | `Try again or send text.`               | We already have a partial template; text is the simplest next input. |
| **Existing draft** (preview shown, refining)           | `handle_approval_media_feedback` / `handle_edit_value`         | `Type your feedback instead.`           | User is editing a draft, not starting fresh.                         |
| **Voice profile setup**                                | `voice_collect_example`                                        | `Try pasting text instead.`             | Examples flow accepts paste; "case" framing doesn't apply.           |

Example: voice failure across the three primary modes:

```
new case        →  ⚠️ Couldn't transcribe voice note. Try again or describe the case in text.
template review →  ⚠️ Couldn't transcribe voice note. Try again or send text.
existing draft  →  ⚠️ Couldn't transcribe voice note. Type your feedback instead.
```

Apply the same pattern to image / video / document errors.

### Refining-existing-draft success vocabulary

When media is uploaded into an already-active flow, the success edit uses
"Got it — updating …":

| Flow                    | Success edit (replaces the ack)             | Where it lives                   |
| ----------------------- | ------------------------------------------- | -------------------------------- |
| Template review         | `Got it — updating template…` (per media)   | `handle_template_review_media`   |
| Existing draft feedback | `Got it — updating draft…` (per media)      | `handle_approval_media_feedback` |
| Edit value (legacy)     | `✏️ Regenerating draft with your feedback…` | `handle_edit_value`              |

Both `template` and `draft` framings are correct — they describe different
nouns. Keep them separate, but never have an ack say `updating template`
in a flow that's actually editing a draft.

### Slow-progress contract

For OCR, transcription, or filing that may take longer than the user's
patience window, follow the `_run_image_progress` pattern in `backend/bot.py`:

1. Send the initial ack once.
2. Schedule one replacement edit after a delay (`asyncio.Event` + `wait_for`).
3. Set the event before the success/error edit so the reassurance is
   suppressed when work finishes in time.
4. Cancel the task on the way out and swallow the resulting `CancelledError`.

Never concatenate `"\n"`-joined status lines onto the ack — that reads as
the bot repeating itself.

Two long-running surfaces use a richer progress sequence rather than a
single replacement, because the work has discrete phases:

- **Kaizen filing (`_filing_progress`):** three sequential edits at
  20 s / 60 s / 120 s — `Still saving…` → `Filling fields…` → `Verifying
the save…`. Each edit replaces the previous one. Catches any
  edit-throws-after-success and swallows them.
- **Kaizen login (`_progress_updates` in `setup_password`):** two
  sequential edits at 15 s / 35 s — `Still checking — Kaizen can be slow
to respond…` → `Almost there — finalising the login check…`.

### Errors (general rules)

- One ⚠️ line per error. State the cause in present tense (`Couldn't read
image`), then a one-clause recovery (see [Mode-aware error recovery]
  (#mode-aware-error-recovery)). No stack traces, no apologies, no
  "please".
- Hard failures that lose the draft use ❌ (e.g. `❌ Filing failed.`,
  `❌ Login failed — please check your username and password.`); recoverable
  warnings use ⚠️.
- Timeout uses ⏱ (`⏱ Filing took too long.`, `⏱ Kaizen took too long to
respond.`); button/state expiry uses ⏳ (`⏳ Quick improve timed out.`,
  `⏳ Template review timed out. Please try again.`).
- After an error that keeps the draft alive, the recovery message must be
  paired with a keyboard the user can act on — usually `🔄 Try Again` plus
  either `🆕 Start fresh` or `❌ Cancel`. A dead-end ⚠️ with no buttons is a
  bug.

### Pre-save gate (Save as draft)

When the user taps **Save as draft** the bot checks the draft against the
DOPS quality gate before calling Kaizen. The gate has two tiers:

- **Blocking (genuinely unsafe / near-empty draft).** The procedure name is
  absent, or the narrative slot has no clinical substance at all (thin
  `case_observed` AND both `indication` and `trainee_performance` empty).
  The bot sends a fresh `🟡 *{form_name} needs a bit more detail before I
file it.*` message with the missing fields and the Save / Quick improve /
  Cancel keyboard. The reviewed draft preview stays visible.
- **Warning (recoverable gap, save proceeds).** Missing date, missing
  stage, one missing semantic block, or rough reflection wording. The bot
  sends a fresh `🟡 *Saving {form_name} despite some gaps.* Add these in
Kaizen after the save:` message and then calls the filer normally. The
  reviewed draft preview stays visible; the user remains in control of
  their explicit Save.

Both tiers always arrive as **new messages**, never by editing the
reviewed draft preview — the user just approved that preview and should
not lose the context they were looking at.

### Filing outcomes (after Kaizen save returns)

The progress message edits one last time with the final outcome — the
reviewed draft preview always stays visible above. Outcome shapes:

| Status                 | Headline                                                                                  | Body                                                                                                                                                                             | Keyboard (flat — no More-options drawer)                                                                                                                                       |
| ---------------------- | ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `success`              | `✅ *Case filed*` + `{form_name} saved as a Kaizen draft.` subhead                        | SLO/date summary, field count, usage line, optional one-line `💡 {observation}`                                                                                                  | `🔗 Open saved draft` (or `🔗 Open Kaizen` fallback) / `🔁 Same case, another WPBA` (when applicable) / `📋 File another case` (+ `✏️ Amend this draft` appended to first row) |
| `partial` (with error) | `⚠️ *Filing had issues — check Kaizen*` + `{form_name}` subhead                           | `{n} fields filled.` + LLM-composed recovery clause + `[Find this draft in Kaizen]({saved_url})` or `[Check your Kaizen drafts](https://kaizenep.com/activities)` + proof report | `🔗 Open saved draft` (or `🔗 Open Kaizen` fallback) / `🔄 Try again` / `📋 File another case` / `❌ Cancel`                                                                   |
| `partial` (no error)   | `📥 *Draft saved in Kaizen*` + `{form_name}` subhead, then `⚠️ *Needs your review*` block | Filled + skipped count + `Open the saved draft to fill the missing detail` (or `Open Kaizen and find your saved draft`)                                                          | `🔗 Open saved draft` (or `🔗 Open Kaizen` fallback) / `🔁 Same case, another WPBA` (when applicable) / `📋 File another case`                                                 |
| `failed`               | `❌ *Filing didn't complete*` + `{form_name}` subhead                                     | LLM recovery clause + `[Open blank {form_name} in Kaizen to fill manually]({url})` + proof report                                                                                | `🔄 Try again` / `📋 File another case` / `❌ Cancel`                                                                                                                          |
| Timeout                | `⏱ Filing took too long.`                                                                 | `The draft might be in your activities list already — [open Kaizen]({url}) to check before retrying.`                                                                            | Stays on `AWAIT_APPROVAL` so the user can retry                                                                                                                                |

Settings and a Main-menu reset are deliberately absent from every post-filed
keyboard. Nothing about a just-saved draft makes a settings change immediately
relevant, and the welcome-style "Portfolio Guru is ready" message reads like a
context wipe right after a successful save. Stale `ACTION|post_file_more|...`
callbacks from older chat history fall through to `handle_action_button`,
which re-renders the same flat keyboard — never the Settings / Main-menu /
"Something missing?" drawer that briefly existed during dogfood.

The proof report at the bottom of partial/failed states is generated by
`_format_proof_report` and lists status, source, fields completed, skipped
fields, and "Not done: no supervisor request sent, no final submission
made" — it is the trust layer and should never be dropped.

### Callback / recovery / control messages

| Surface                                 | Helper / function                                                       | Copy                                                                                                                                                                                                                                                  |
| --------------------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/cancel` (connected user)              | `_cancelled_next_step_text` → `cancel_command` / `ACTION\|cancel`       | `✅ Cancelled. Just send your next case when ready.`                                                                                                                                                                                                  |
| `/cancel` (disconnected user)           | `_cancelled_next_step_text`                                             | `❌ Cancelled. Connect Kaizen to start filing.`                                                                                                                                                                                                       |
| Stale callback (button expired ~30 s)   | `error_handler` → `_resume_paused_flow`                                 | `That earlier button is no longer active.` (+ rebuild paused draft / form choice / start-fresh path)                                                                                                                                                  |
| Setup-flow stale button                 | `_expired_prompt_text`                                                  | `⏳ That button has expired. Finish setup from the latest message and I'll pick it up from there.`                                                                                                                                                    |
| Generic stale button (no setup pending) | `_expired_prompt_text`                                                  | `⏳ That button has expired. Start a new case from the latest message and I'll pick it up from there.`                                                                                                                                                |
| Unknown error with draft alive          | `error_handler`                                                         | `Something went wrong while filing. Try again or start fresh.` + retry/start-fresh keyboard                                                                                                                                                           |
| Unknown error with no draft             | `error_handler`                                                         | `Something went wrong. Use the latest message to start again.` + next-step keyboard                                                                                                                                                                   |
| Stale "earlier draft" recovery          | `_resume_paused_flow(reason="That earlier draft is no longer active.")` | `That earlier draft is no longer active.` (+ paused-flow rebuild)                                                                                                                                                                                     |
| Live submit attempt (legacy submit btn) | `handle_approval_submit`                                                | `Portfolio Guru only saves Kaizen entries as drafts. Use Save as draft when you're ready.`                                                                                                                                                            |
| Reuse same case after success           | `ACTION\|same_case_another`                                             | `🔁 Reusing the same case. I'll suggest a different WPBA type — not the one you already filed.`                                                                                                                                                       |
| Amend after a filed draft               | `handle_amend_draft`                                                    | Re-shows the draft preview with the amend keyboard (`📤 Save updated draft` / `❌ Cancel amend`).                                                                                                                                                     |
| `/health` empty portfolio               | `ACTION\|health`                                                        | `📊 No Portfolio Guru cases filed yet — start filing and come back to check your ARCP readiness. Your existing Kaizen cases aren't affected.`                                                                                                         |
| `/reset` confirmation (inline)          | `ACTION\|delete` → `CONFIRM\|reset`                                     | `⚠️ This resets Portfolio Guru — it clears your saved Kaizen login, portfolio, pathway and curriculum choice, voice profile, and local filing history and Portfolio Health evidence. It does not affect cases already saved in Kaizen. Are you sure?` |

Conversation-state invariant: every path to `ConversationHandler.END` must
call `context.user_data.clear()` first. The cancel/recovery surfaces above
all enforce this.

### Button / control vocabulary

Used across keyboards in `bot.py`. Keep these exact — the emoji and label
together carry meaning and downstream copy refers to them by name.

| Button label                      | Callback                              | Where                                                                                                                                                                                                                              |
| --------------------------------- | ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `🔗 Connect Kaizen`               | `ACTION\|setup`                       | Welcome, missing-credentials surfaces                                                                                                                                                                                              |
| `❌ Cancel`                       | `ACTION\|cancel` (and others)         | Universal cancel                                                                                                                                                                                                                   |
| `📤 Save as draft`                | `APPROVE\|draft`                      | Approval keyboard                                                                                                                                                                                                                  |
| `✨ Quick improve`                | `IMPROVE\|reflection`                 | Approval keyboard (single-use per draft)                                                                                                                                                                                           |
| `✏️ Edit` / `✏️ Amend this draft` | `APPROVE\|edit` / `AMEND\|amend`      | Approval / post-file keyboards                                                                                                                                                                                                     |
| `📋 File another case`            | `ACTION\|file`                        | Post-file keyboard                                                                                                                                                                                                                 |
| `🔁 Same case, another WPBA`      | `ACTION\|same_case_another`           | Post-success and clean-partial keyboards. Reuses the original case text from `last_filed_case_text` (NOT the saved draft body or any bot-generated text) and excludes the previously filed form type from the new recommendations. |
| `🔗 Open saved draft`             | `url=saved_url`                       | Post-file partial/uncertain — only when the deterministic filer captured the post-save Kaizen URL (`/events/fillin/<doc-id>?autosave=...`).                                                                                        |
| `🔗 Open Kaizen`                  | `url=https://kaizenep.com/activities` | Post-file partial/uncertain fallback when no captured URL — links to the Kaizen activities list, NEVER `/events/new-section/...` (that opens a blank form and reads like a fresh entry).                                           |
| `🚩 Flag a missed field`          | `FILING\|feedback\|{form_type}`       | Retired from primary post-file keyboards. Records pushback telemetry via `filing_coverage.record_pushback` if reached from older buttons or a future feedback surface.                                                             |
| `📝 Review draft` (Unlimited)     | `REVIEW\|draft`                       | Approval keyboard (gated tier)                                                                                                                                                                                                     |
| `🔄 Try again`                    | `ACTION\|retry_filing`                | Filing error / partial keyboards                                                                                                                                                                                                   |
| `🆕 Start fresh`                  | `ACTION\|reset`                       | Filing error keyboards                                                                                                                                                                                                             |
| `🔙 Back`                         | `ACTION\|back_to_menu`                | Sub-views (settings, health, help, info)                                                                                                                                                                                           |

### Safety-critical templates

These stay fixed in `backend/message_policy.py` (`MessageClass.FIXED`),
behind `render_message(key)`:

- `welcome_disconnected`, `welcome_connected` — first-touch framing.
- `what_is_this` — opt-in explanation.
- `file_case_prompt` — the "send what happened" prompt.
- `captured_ack` — the bridging ack after a case is received.
- `thin_case_detail_request` — anti-fabrication guard for empty inputs.
- `ai_temporarily_unavailable` — LLM-down fallback.
- `photo_privacy_nudge` — appended to recommendations after a photo input.
- `draft_reply_hint` — appended to draft previews (`_REPLY_HINT_SUFFIX`).
- `form_recommendation` (TEMPLATED) — the recommendation message body.

LLM-assisted prose is allowed only on explicitly low-risk explanation/
recovery paths: `answer_question`, `compose_filing_recovery_copy`,
`summarise_recent_activity`. Never let the LLM author safety-critical
copy (welcome, privacy, captured ack, thin-case detail).

### Intentional exceptions / non-drift cases

These look like drift but are correct as-is — don't normalise without
talking to Moeed first.

- `Got it — updating template…` vs `Got it — updating draft…` — different
  flow nouns (template review vs existing draft). Both are correct.
- `📷 Reading image…` vs `📷 Reading images…` — the plural is intentional
  during bundle mode (`pending_case_bundle`), where multiple photos arrive
  in one window.
- `🎙️ Transcribing…` inside `voice_collect_example` may stay generic if
  the surrounding context already establishes "voice example" framing;
  prefer `🎙️ Transcribing voice note…` otherwise.
- Setup-login error `Try again — or send the case anyway, you can connect
later.` deliberately offers two recovery paths because the user might
  want to file before fixing credentials.

---

## FORM_UUIDS (in extractor.py)

```python
FORM_UUIDS = {
    "CBD":      "3ce5989a-b61c-4c24-ab12-711bf928b181",  # CBD 2025 update
    "DOPS":     "159831f9-6d22-4e77-851b-87e30aee37a2",  # DOPS ST3-ST6 2025
    "LAT":      "eb1c7547-0f41-49e7-95de-8adffd849924",  # LAT 2025 v9
    "ACAT":     "6577ab06-8340-47e3-952a-708a5f800dcc",  # ACAT ACCS 2025
    "ACAF":     "15e67ae8-868b-4358-9b96-30a4a272f02c",
    "STAT":     "41ff54b8-35a7-414b-9bd6-97fb1c3eb189",
    "MSF":      "5f71ac04-ff45-44d2-b7a1-f8b921a8a4c8",
    "MINI_CEX": "647665f4-a992-4541-9e17-33ba6fd1d347",
    "JCF":      "3daa9559-3c31-4ab4-883c-9a991632a9ca",
    "QIAT":     "a0aa5cfc-57be-4622-b974-51d334268d57",
}
```

---

## Pending scope

See Notion → Portfolio Guru → Current State → v2.1 scope section.
