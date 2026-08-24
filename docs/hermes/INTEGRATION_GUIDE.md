# Hermes + Portfolio Guru — Integration Guide

How to wire the Hermes conversational layer to the Portfolio Guru
deterministic engine using the `@portfolio_guru_test_bot` test bot.

This guide covers architecture, token isolation, the channel-contract
seam, shadow-mode validation, fallback strategy, and the conditions under
which you must stop and investigate before proceeding.

---

## Architecture overview

Telegram is a **hybrid** surface: Hermes owns the conversation, Python owns
the product facts and every irreversible boundary.

```
Telegram test bot (@portfolio_guru_test_bot)
  ↓  receives message
portfolio-guru-engine-dispatch  pre_gateway_dispatch
  ↓  observes raw text for an outstanding approval phrase, returns no directive
Hermes agent turn (normal conversation, session memory, clarification)
  ↓  calls a Portfolio Guru tool when it needs a fact, a draft, or an action
portfolio_case_analyze / portfolio_draft_preview / portfolio_handoff_create
  ↓  identity from gateway.session_context, never from the model
`pg <case-analyze|draft-preview|handoff-create>`  (profile shim)
  ↓
backend/hermes_case_tools.py
  ↓  hermes_shadow_adapter.process_payload
hermes_bridge_contract.inbound_from_payload → channel_contract.accept_inbound
  ↓  disposition == HANDLE
telegram_vnext_adapter.event_from_telegram_message → IngestEvent
  ↓
conversational_case_engine.apply_event  →  vnext_form_recommender.recommend
  ↓                                          vnext_draft_preview.build_draft_preview
facts / form / preview + binding receipt back to the agent
  ↓
Hermes writes the reply in its own words
```

The plugin never sends a Telegram message and never returns `skip` for
Telegram. It previously did both, which made the test bot a Python state
machine: a multi-patient resus shift was flattened to a setting, the same
diagnosis prompt replayed, and `/new` only reset Python. The archived
implementation is at
`backend/_archived/20260824T041500Z-hermes-telegram-turn-state-machine/`.

The three components and their responsibilities:

| Component                           | Who owns it                       | Token it uses                                                                                               |
| ----------------------------------- | --------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Hermes agent profile                | Hermes / OpenClaw                 | BWS secret: `TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST` (OpenClaw alias: `PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN`) |
| Portfolio Guru deterministic engine | Portfolio Guru Python process     | None (stateless, called in-process or via IPC)                                                              |
| Live beta bot                       | Python process (`backend/bot.py`) | Live token (BWS: `PORTFOLIO_GURU_TELEGRAM_BOT_TOKEN`)                                                       |

The live beta bot and the Hermes test bot are **entirely separate
processes** with **separate tokens**. They must never poll the same
token, share state, or be wired to the same Telegram webhook or polling
loop.

The `portfolio-guru-engine-dispatch` plugin is profile-local and patches no
Hermes core. It registers one narrow toolset (`portfolio_guru`) and one
`pre_gateway_dispatch` hook. On Telegram the hook takes no dispatch decision at
all; it only records that an approval phrase appeared in an authorised
trainee's own words, so the handoff tool can prove approval. On WhatsApp it
still renders and sends the deterministic reply, unchanged. Unsupported media
and production-only surfaces remain explicit parity gaps.

The optional mobile Kaizen handoff does not change that ownership. Hermes
remains the only test-bot poller; the handoff service owns no Telegram token.
It accepts an approved CBD payload only through a protected loopback endpoint,
returns a one-time mobile link, and resumes the existing deterministic filer
only after the clinician signs into an isolated Kaizen browser themselves.

---

## Token isolation (hard rule)

**The test bot token (BWS secret name: `TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST`;
OpenClaw/runtime alias: `PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN`) is owned
by the Hermes profile. The live beta token (`PORTFOLIO_GURU_TELEGRAM_BOT_TOKEN`)
is owned by `backend/bot.py`. These tokens must never be co-polled, swapped,
or shared.**

Telegram rejects a second `getUpdates` long-poll for the same token
with a 409 Conflict error. If you see a 409 on the test bot, something
else is polling the test token. Stop immediately — see
[Stop conditions](#stop-conditions) below.

Neither token is ever passed to the Hermes agent's LLM context, logged
to a file, written to a prompt, or stored anywhere except BWS. The
bridge contract (`hermes_bridge_contract.py`) never reads, accepts, or
touches any bot token.

---

## Channel-contract seam

The correct entry point for inbound messages is:

```python
from hermes_bridge_contract import inbound_from_payload, serialise_decision
from channel_contract import InboundDisposition

# Build a payload from the Hermes adapter's inbound event.
payload = {
    "channel": "telegram",
    "conversation_id": f"tg:{chat_id}",
    "gateway_user_id": str(user_id),
    "scope": "direct",   # always "direct" for test bot DMs
    "text": message_text,
    "media": [],          # extend for voice/photo/document
    "private": True,
}

decision = inbound_from_payload(payload)

if decision.disposition is InboundDisposition.HANDLE:
    # decision.message is an InboundMessage ready for the engine.
    # For Telegram-shaped messages, convert further with:
    # event = telegram_vnext_adapter.event_from_telegram_message(tg_msg)
    ...
elif decision.disposition is InboundDisposition.REFUSE_GROUP:
    reply = serialise_decision(decision)["refusal"]
    # render reply["body"] and reply["continuation"] to the user
    ...
elif decision.disposition is InboundDisposition.REFUSE_EMPTY:
    # ask the user to send their case notes
    ...
```

For the reply path, the engine returns a `ChannelReply` that can be
serialised:

```python
from hermes_bridge_contract import serialise_reply

reply_dict = serialise_reply(channel_reply)
# reply_dict has: body (str), continuation (str|None), actions (list of dicts)
```

The `channel_actions` module also provides `to_telegram_keyboard` and
`render_numbered` for rendering replies as Telegram inline keyboards or
plain-text numbered lists — use these when the Hermes adapter is
producing Telegram messages directly.

### The Telegram tool seam

The agent never builds these payloads itself. The plugin builds them from the
gateway session identity and passes only the trainee's own case wording:

```bash
pg case-analyze   --payload-file -   # facts, recommended form, open questions
pg draft-preview  --payload-file -   # preview_text + preview_hash/id + approval_phrase
pg handoff-create --payload-file -   # requires preview_hash + confirmation_phrase
```

`pg shadow` remains metadata-only and is for shadow logs, not for the
conversational path. `pg preview` is retained for the WhatsApp/legacy preview
call; `case-analyze` and `draft-preview` are the Telegram surface. All of them
perform no Telegram send, Kaizen write, Stripe call, BWS read, or network
operation beyond the loopback handoff broker.

#### Approval binding

`draft-preview` hashes the exact reviewed draft (`PREVIEW_HASH_VERSION`, form
type, normalised case text, rendered preview) and derives an approval phrase
from it. `handoff-create` recomputes that hash from the supplied case text and
refuses unless it matches and the phrase matches.

That alone proves *which* draft was approved, not *who* approved it. The
second half is the hook: it watches raw inbound Telegram text — a channel the
model cannot write to — for the outstanding phrase, and only a receipt with an
observed confirmation can be spent, once, within 15 minutes.

Both halves must hold. A model asserting approval in tool arguments is not
evidence and is refused.

**Known limitation.** The supported plugin APIs give a tool no way to read the
result of Hermes' native clarify/confirmation surface as trusted evidence —
`clarify` returns through the model. The typed phrase is therefore the
approval channel, and it fails closed rather than accepting a model-mediated
"the user said yes".

---

## Shadow mode first

Before processing any real trainee messages through Hermes, run in
**shadow mode**:

1. Wire the Hermes adapter so it calls `inbound_from_payload` and
   `apply_event` but **does not send any Telegram message** to the user.
2. Log the `InboundDecision.disposition` and the `EngineSnapshot.actions`
   to a local file for every test message.
3. Drive a set of test messages (see below) through the bot and confirm
   the decisions and actions match expectations.
4. Only after the shadow log is clean for at least 10 distinct message
   types should you enable live replies.

Suggested shadow test messages:

| Input                            | Expected disposition | Expected first action                         |
| -------------------------------- | -------------------- | --------------------------------------------- |
| Clinical case text               | HANDLE               | ACK_CASE_DETAILS or REQUEST_CASE_CONFIRMATION |
| "What forms would this support?" | HANDLE               | ANSWER_CHAT                                   |
| "File this as a CBD"             | HANDLE               | SAVE_DRAFT or DRAFT_NOT_READY                 |
| Empty message                    | REFUSE_EMPTY         | —                                             |
| Medical advice question          | HANDLE               | ANSWER_CHAT (safety redirect)                 |

---

## Fallback to the existing bot

If the Hermes layer crashes, fails to start, or produces unexpected
output:

1. **Stop the Hermes process** polling the test bot token.
2. **Do not fall back to the live beta bot** — the test bot and the live
   bot are separate. Trainees on the live beta are unaffected.
3. Investigate the failure using the shadow log before restarting.
4. The existing `backend/bot.py` process continues serving live beta
   users; do not restart or modify it.

The `backend/bot.py` process is the canonical deterministic engine.
Hermes adds a conversational layer on top of the test bot; it does not
replace or modify the engine.

---

## Stop conditions

Stop and investigate before proceeding if you observe any of the
following:

1. **409 Conflict from Telegram on the test bot token.** Something else
   is polling `PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN`. Identify and
   stop the other polling process before restarting Hermes. Running two
   pollers on the same token drops messages unpredictably.

2. **Any reference to the live beta token in Hermes configuration.**
   The live token (`PORTFOLIO_GURU_TELEGRAM_BOT_TOKEN`) must never appear
   in a Hermes config, profile, or prompt. If you see it, stop and audit
   the configuration.

3. **A Kaizen draft save completing without a user Approve action.**
   The `SAVE_DRAFT` action from the engine must only be dispatched after
   an explicit user confirmation in the current conversation turn. If a
   draft is saved without one, there is a logic error in the Hermes
   action handler. Stop, audit the action dispatch, and fix before
   continuing.

4. **Clinical content appearing in logs.** The bridge contract, the
   channel contract, and the engine are all designed to never log
   clinical content. If you see patient-identifiable information in any
   log file, stop, rotate the token, and audit the log pipeline.

5. **A mobile handoff for anything other than a private, approved CBD.**
   The initial proof is deliberately narrow. Stop if a group conversation,
   unapproved preview, unsupported form, reused link, expired link, or payload
   containing credential fields reaches the browser service.

6. **A handoff created without the trainee typing the approval phrase.**
   The phrase is the only approval channel the gate can verify. If a link is
   ever produced from a model-asserted approval, stop and audit both halves of
   the binding before continuing.

7. **The plugin answering a Telegram message.** The hook must never send text
   or return `skip` on Telegram. A templated Portfolio Guru reply appearing
   alongside the agent's own means the state machine has been reintroduced.

---

## Test-only mobile Kaizen handoff

The handoff is disabled when its local broker is not running. With the broker
active, the profile uses this sequence:

1. The agent calls `portfolio_draft_preview` with the trainee's own wording.
2. It shows the source-tied CBD preview and the approval phrase.
3. The trainee types that exact phrase in the same private conversation; the
   plugin hook records it from the raw inbound text.
4. The agent calls `portfolio_handoff_create` with the `preview_id`; the tool
   verifies the binding and the observed confirmation, then runs
   `pg handoff-create`, which re-verifies both before reaching the broker.
5. The agent sends the returned one-time URL without rewriting it.
6. The clinician signs into Kaizen through the temporary mobile browser.
7. The service reuses that authenticated session for one deterministic CBD
   draft save, reports the outcome on the mobile page, and destroys the
   browser/session state.

`pg handoff-create` creates a handoff, not a success receipt. It remains
blocked when the broker is absent, the preview hash no longer binds, the
approval phrase does not match, the confirmation was never observed on the raw
inbound channel, the receipt was already spent or has expired, the
conversation is not a private Telegram DM, the engine cannot recommend CBD, or
the draft lacks a source-tied narrative. Passwords are never included in the CLI payload or stored by the
service. The live beta process, token, database, and polling loop are untouched.

---

## Deployment sequence

This sequence applies when all shadow-mode and preflight checks are
clean. No step should be skipped.

1. Confirm BWS secret `TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST` is the test
   bot token (not the live beta token). The OpenClaw/runtime alias is
   `PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN`.
2. Confirm no existing process is polling the test bot token
   (`getUpdates` or webhook). Resolve any 409 before proceeding.
3. Start the Hermes agent with the profile from
   [`PROFILE_PROMPT.md`](PROFILE_PROMPT.md).
4. Run shadow mode for at least 10 message types (see above). Verify
   the shadow log shows correct dispositions and actions.
5. Enable live replies.
6. Monitor for the stop conditions above during the first live session.
7. The live beta bot (`backend/bot.py`) continues running unchanged
   throughout. Do not restart it.

---

## Repo-owned profile shim (test bot only)

The Hermes test bot used to ship its own local `recommend.py`, `draft.py`,
`health.py`, and `save.py` inside
`~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/`. Those
scripts hosted a small keyword-scoring heuristic that drifted away from
the deterministic engine. They have been archived under
`_archived/<timestamp>/` in the profile folder and replaced by a single
thin shim that delegates every command to the repo-owned CLI.

| Layer                              | Owner                     | Source                                                                                                      |
| ---------------------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Hermes profile shim (`pg`)         | Hermes profile (test bot) | `scripts/hermes-profile/pg` (repo-tracked)                                                                  |
| Repo-owned CLI (`hermes_pg_cli`)   | Portfolio Guru repo       | `backend/hermes_pg_cli.py`                                                                                  |
| Shadow-mode adapter                | Portfolio Guru repo       | `backend/hermes_shadow_adapter.py`                                                                          |
| Bridge / channel contract / engine | Portfolio Guru repo       | `backend/hermes_bridge_contract.py`, `backend/channel_contract.py`, `backend/conversational_case_engine.py` |

The shim resolves the repo at `$PORTFOLIO_GURU_REPO`
(default `~/projects/portfolio-guru`), picks `backend/venv/bin/python3`,
and runs `python -m hermes_pg_cli ...`. No product logic ever lives in
the profile folder; reinstall the shim from
`scripts/hermes-profile/README.md` after a profile rebuild.

### Offline smoke command

Run this from anywhere — it exercises both the repo CLI and the
installed profile shim without touching Telegram, Kaizen, or BWS:

```bash
# Repo CLI directly (uses the backend venv)
cd ~/projects/portfolio-guru/backend && venv/bin/python3 -m hermes_pg_cli status

# Through the installed profile shim
~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/pg status

# Push a Hermes-shaped payload through the engine
~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/pg shadow \
  --payload '{"channel":"telegram","conversation_id":"tg:chat:smoke","scope":"direct","text":"62M chest pain in resus","private":true}'
```

Each invocation prints one JSON object with a top-level `status` of
`ok | blocked | error`. `shadow` returns the engine's JSON-safe
metadata (no clinical text). `recommend`, `draft`, `health` return
`blocked` with `route_via: "shadow"`. `save` always returns `blocked`
— Kaizen writes happen only in the live `backend/bot.py` process after
an explicit user Approve.

### Focused offline test suite

```bash
cd ~/projects/portfolio-guru/backend && venv/bin/python3 -m pytest \
  tests/test_hermes_pg_cli.py \
  tests/test_hermes_hybrid_telegram_seam.py \
  tests/test_hermes_profile_telegram_dispatch.py \
  tests/test_hermes_shadow_adapter.py \
  tests/test_hermes_integration.py \
  tests/test_channel_contract.py \
  tests/test_channel_actions.py \
  tests/test_portfolio_inbound_bridge.py \
  tests/test_telegram_vnext_adapter.py \
  tests/test_conversational_case_engine.py \
  tests/test_vnext_text_extractor.py \
  tests/test_vnext_draft_preview.py \
  tests/test_vnext_form_recommender.py \
  tests/test_vnext_dialogue_policy.py -v
```

---

## What this integration does not claim

- The Hermes layer does not make Portfolio Guru run on the Hermes
  runtime. The deterministic engine is still a Python process; Hermes
  adds a conversational front door via the test bot.
- This integration does not graduate the test bot to production status.
  The live beta remains the production surface.
- No Kaizen data flows through the Hermes layer. Kaizen writes occur
  inside the deterministic engine process, after user approval, via the
  existing Playwright filer.
