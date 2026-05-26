# Telegram Bot Autonomous Testing

This is the Portfolio Guru implementation of the wider OpenClaw Telegram bot testing discipline.

## Tool Roles

- `pytest` + PTB `Application.process_update()` is the default CI lane. It proves handlers, states, callbacks, snapshots, and failure paths without touching Telegram.
- Telethon is the real-user lane. It drives the live bot over Telegram as a user client, captures transcripts, checks expected text/buttons, and catches workflow regressions that mocked PTB tests cannot see.
- AI transcript review is a second-pass judgement layer for UX and clinical sense. It must not replace deterministic assertions.
- TDLib / Telegram Desktop / OpenClaw QA Lab is the heavy proof lane. Use it only for visual evidence, bot-to-bot behaviour, screenshots, launch proof, or PR-grade audits.
- Telegram Bot API checks are bot-side smoke checks only. They do not simulate a real user journey.
- Browser/Kaizen automation is separate from Telegram QA and stays behind explicit launch or dogfood gates because it touches external clinical portfolio systems.

## Launch Gate

Before launching or widening testing of a Telegram bot:

1. Run the offline bot gate.
2. Run the Telethon live lane against the intended bot account.
3. Save transcript artefacts.
4. Review the transcript for sense, tone, missing buttons, loops, empty replies, and leaked internals.
5. Escalate to TDLib/visual proof only if the launch decision needs screenshots, Telegram Desktop state, or bot-to-bot evidence.

## Portfolio Guru Command

```bash
scripts/telegram_bot_qa.sh
```

Default behaviour:

- Collects live Telegram tests so missing/renamed tests are caught.
- Runs the focused offline bot gate.
- Runs Telethon live tests only when Telethon session/API credentials are present.
- Writes logs and transcript artefacts under `.artifacts/telegram-bot-qa/`.
- Skips cleanly when live credentials are absent unless live testing is explicitly required.

For a launch-blocking run:

```bash
REQUIRE_TELEGRAM_LIVE=1 scripts/telegram_bot_qa.sh
```

## Credential Discipline

Telethon session strings are credentials. Store them in the secrets manager or private environment only. Do not commit them, paste them into chat, or write them to test artefacts.

Required live variables:

- `TELETHON_SESSION`
- `TELETHON_API_ID` or `TELEGRAM_API_ID`
- `TELETHON_API_HASH` or `TELEGRAM_API_HASH`
- `TELEGRAM_BOT_USERNAME` when testing a non-default bot

## Automation Contract

Autonomous scheduled runs should be silent when clean. They should alert only when:

- offline gate fails
- live Telethon lane is required but not configured
- live Telegram workflow fails
- transcript contains internal errors, empty replies, broken buttons, or obvious nonsense

Do not run live Telegram QA in public groups or against production users. Use controlled private chats or test accounts.
