# Telegram Bot Autonomous Testing

This is the Portfolio Guru implementation of the wider OpenClaw Telegram bot testing discipline.

## Tool Roles

- `pytest` + PTB `Application.process_update()` is the default CI lane. It proves handlers, states, callbacks, snapshots, and failure paths without touching Telegram.
- Telethon is the real-user lane. It drives the live bot over Telegram as a user client, captures transcripts, checks expected text/buttons, and catches workflow regressions that mocked PTB tests cannot see.
- The Telethon harness must exercise inline buttons by label, not only send text. A live workflow is incomplete unless it sends realistic user input, waits through acknowledgement messages, clicks the intended button, waits for the next screen, and records the resulting text/buttons.
- AI transcript review is a second-pass judgement layer for UX and clinical sense. It must not replace deterministic assertions.
- TDLib / Telegram Desktop / OpenClaw QA Lab is the heavy proof lane. Use it only for visual evidence, bot-to-bot behaviour, screenshots, launch proof, or PR-grade audits.
- Telegram Bot API checks are bot-side smoke checks only. They do not simulate a real user journey.
- Browser/Kaizen automation is separate from Telegram QA and stays behind explicit launch or dogfood gates because it touches external clinical portfolio systems.

## Launch Gate

Before launching or widening testing of a Telegram bot:

1. Run the offline bot gate.
2. Ask Moeed before any Telethon live run, name the target bot, and wait for explicit approval for that exact run.
3. Run the Telethon live lane against the intended bot account only.
4. Save transcript artefacts.
5. Review the transcript for sense, tone, missing buttons, loops, empty replies, and leaked internals.
6. Escalate to TDLib/visual proof only if the launch decision needs screenshots, Telegram Desktop state, or bot-to-bot evidence.

## Live Telethon Guardrails

Live Telethon QA uses a real user session, so the harness treats it as a controlled external action:

- Require explicit approval for the exact run with `TELEGRAM_LIVE_APPROVED=portfolio-guru-live-qa-approved`.
- Require a single named target bot via `TELEGRAM_BOT_USERNAME`; default is `portfolio_guru_bot`.
- Refuse runtime target mismatches. The script cannot be pointed at one bot and then send to another.
- Keep an allowlist in `TELEGRAM_LIVE_ALLOWED_BOTS`; default is only `portfolio_guru_bot`.
- Open conversations only with the allowlisted target bot.
- Click only inline buttons returned by that bot conversation, selected by expected label text.
- Treat empty replies, missing expected buttons, forbidden text, internal errors, and leaked tracebacks as failures.
- Never run while Moeed is manually testing unless he explicitly approves that specific overlap.

The live test path should never browse Telegram chats, message groups, test unrelated bots, or use Telethon as a general-purpose Telegram client.

## Offline Transcript Lane

For workflow review without a live Telethon session, the offline transcript
runner drives the real PTB handler stack through `OfflineRequest` (any
outbound network call fails the test immediately) and writes a structured
JSON + Markdown transcript covering bot messages, inline buttons, observed
form recommendations, captured draft state, and per-step pass/fail flags.

```bash
cd backend && venv/bin/python3 -m pytest tests/test_telegram_qa_offline_transcript.py -v
# or, with a chosen output dir:
TELEGRAM_QA_TRANSCRIPT_DIR=/tmp/pg-qa venv/bin/python3 -m pytest tests/test_telegram_qa_offline_transcript.py -v
```

Default output: `.artifacts/telegram-qa-transcript/<utc-stamp>/transcript.{json,md}`.
Cases live in `backend/tests/fixtures/telegram_qa_cases.py` — six anonymised
Haris (ACCS/Intermediate) and Sana (SAS/CESR) golden cases. This lane never
calls Telegram and does not need `TELEGRAM_LIVE_APPROVED`.

## Portfolio Guru Command

```bash
scripts/telegram_bot_qa.sh
```

Default behaviour:

- Collects live Telegram tests so missing/renamed tests are caught.
- Runs the focused offline bot gate.
- Runs Telethon live tests only when Telethon session/API credentials are present.
- Uses the live guardrail gate before any Telethon send/click.
- Writes logs and transcript artefacts under `.artifacts/telegram-bot-qa/`.
- Skips cleanly when live credentials are absent unless live testing is explicitly required.

For a launch-blocking run:

```bash
TELEGRAM_LIVE_APPROVED=portfolio-guru-live-qa-approved REQUIRE_TELEGRAM_LIVE=1 scripts/telegram_bot_qa.sh
```

Only set `TELEGRAM_LIVE_APPROVED` after Moeed has approved that specific live run. Never run Telethon live QA silently while Moeed is manually testing the bot.

## Credential Discipline

Telethon session strings are credentials. Store them in the secrets manager or private environment only. Do not commit them, paste them into chat, or write them to test artefacts.

## Telethon Session Setup

Use the one-click Desktop helper or run `backend/tests/generate_session.py` from the backend virtualenv. The generator reads `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from environment variables first, then falls back to the OpenClaw BWS credential map.

Setup guardrails learned from the first Portfolio Guru login:

- Do not source the full app `.env` just to generate a session; unrelated shell syntax in `.env` can break the login flow before Telethon starts.
- Load only the Telegram API ID/hash, and strip surrounding quotes before passing `api_id` to Telethon.
- Enter the phone number in international format, preferably without spaces.
- Keep Telegram login codes, 2FA passwords, and the printed `StringSession` out of Telegram chat.
- After the session is stored in BWS, add or update the `TELETHON_SESSION` entry in the OpenClaw secrets map before running live QA.

Required live variables:

- `TELETHON_SESSION`
- `TELETHON_API_ID` or `TELEGRAM_API_ID`
- `TELETHON_API_HASH` or `TELEGRAM_API_HASH`
- `TELEGRAM_LIVE_APPROVED=portfolio-guru-live-qa-approved` after explicit approval for that exact run
- `TELEGRAM_BOT_USERNAME` when testing a non-default bot
- `TELEGRAM_LIVE_ALLOWED_BOTS` if widening beyond the default `portfolio_guru_bot`

The harness refuses to run live messages unless approval is present and the target bot is allowlisted. The default allowlist is `portfolio_guru_bot`.

## Automation Contract

Autonomous scheduled runs should be silent when clean. They should alert only when:

- offline gate fails
- live Telethon lane is required but not configured
- live Telegram workflow fails
- transcript contains internal errors, empty replies, broken buttons, or obvious nonsense

Do not run live Telegram QA in public groups or against production users. Use controlled private chats or test accounts.
