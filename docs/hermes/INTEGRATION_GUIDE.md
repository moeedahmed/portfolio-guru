# Hermes + Portfolio Guru — Integration Guide

How to wire the Hermes conversational layer to the Portfolio Guru
deterministic engine using the `@portfolio_guru_test_bot` test bot.

This guide covers architecture, token isolation, the channel-contract
seam, shadow-mode validation, fallback strategy, and the conditions under
which you must stop and investigate before proceeding.

---

## Architecture overview

```
Telegram test bot (@portfolio_guru_test_bot)
  ↓  receives message
Hermes agent  ←— PROFILE_PROMPT.md (this profile, not the live beta)
  ↓  calls bridge
hermes_bridge_contract.inbound_from_payload(payload)
  ↓  returns InboundDecision
channel_contract.accept_inbound(message)
  ↓  disposition == HANDLE
Hermes passes InboundMessage to the next engine layer
  ↓
telegram_vnext_adapter.event_from_telegram_message(msg)   ← Telegram-specific
  ↓  IngestEvent
conversational_case_engine.apply_event(workspace, event)
  ↓  EngineSnapshot + NextAction list
Hermes renders the appropriate ChannelReply
  ↓
Test bot sends reply to the trainee
```

The three components and their responsibilities:

| Component | Who owns it | Token it uses |
|---|---|---|
| Hermes agent profile | Hermes / OpenClaw | BWS secret: `TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST` (OpenClaw alias: `PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN`) |
| Portfolio Guru deterministic engine | Portfolio Guru Python process | None (stateless, called in-process or via IPC) |
| Live beta bot | Python process (`backend/bot.py`) | Live token (BWS: `PORTFOLIO_GURU_TELEGRAM_BOT_TOKEN`) |

The live beta bot and the Hermes test bot are **entirely separate
processes** with **separate tokens**. They must never poll the same
token, share state, or be wired to the same Telegram webhook or polling
loop.

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

| Input | Expected disposition | Expected first action |
|---|---|---|
| Clinical case text | HANDLE | ACK_CASE_DETAILS or REQUEST_CASE_CONFIRMATION |
| "What forms would this support?" | HANDLE | ANSWER_CHAT |
| "File this as a CBD" | HANDLE | SAVE_DRAFT or DRAFT_NOT_READY |
| Empty message | REFUSE_EMPTY | — |
| Medical advice question | HANDLE | ANSWER_CHAT (safety redirect) |

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

## What this integration does not claim

- The Hermes layer does not make Portfolio Guru run on the Hermes
  runtime. The deterministic engine is still a Python process; Hermes
  adds a conversational front door via the test bot.
- This integration does not graduate the test bot to production status.
  The live beta remains the production surface.
- No Kaizen data flows through the Hermes layer. Kaizen writes occur
  inside the deterministic engine process, after user approval, via the
  existing Playwright filer.
