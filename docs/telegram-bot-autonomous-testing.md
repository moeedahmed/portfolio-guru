# Telegram Bot Autonomous Testing

This is the Portfolio Guru implementation of the wider OpenClaw Telegram bot testing discipline.

## Tool Roles

- `pytest` + PTB `Application.process_update()` is the default CI lane. It proves handlers, states, callbacks, snapshots, and failure paths without touching Telegram.
- Telethon is the real-user lane. It drives the live bot over Telegram as a user client, captures transcripts, checks expected text/buttons, and catches workflow regressions that mocked PTB tests cannot see.
- The Telethon harness must exercise inline controls by their reviewed payload/effect policy and record their labels, not only send text. A live workflow is incomplete unless it sends realistic user input, waits through acknowledgement messages, clicks the intended button, waits for the next screen, and records the resulting text/buttons.
- AI transcript review is a second-pass judgement layer for UX and clinical sense. It must not replace deterministic assertions.
- TDLib / Telegram Desktop / OpenClaw QA Lab is the heavy proof lane. Use it only for visual evidence, bot-to-bot behaviour, screenshots, launch proof, or PR-grade audits.
- Telegram Bot API checks are bot-side smoke checks only. They do not simulate a real user journey.
- Browser/Kaizen automation is separate from Telegram QA and stays behind explicit launch or dogfood gates because it touches external clinical portfolio systems.

## Launch Gate

Before launching or widening testing of a Telegram bot:

1. Run the offline bot gate.
2. Task-bound autonomy, named: an already-approved product task, or an exact release approval (`--approved <sha>:<card-digest>`, see `docs/release-standard.md`), for the same allowlisted bot and an unchanged recipient/effect covers that task's or card's required live verification — run it without asking again, gated by the `TELEGRAM_LIVE_APPROVED` guard variable being set to `portfolio-guru-live-qa-approved` for that run. A standalone, ad-hoc live run outside an approved task or card (exploring the bot live for its own sake, a new target, a new recipient, or a broader effect) still needs Moeed's exact approval before `TELEGRAM_LIVE_APPROVED` is set — name the target bot and wait for that explicit approval first.
3. Run the Telethon live lane against the intended bot account only.
4. Save transcript artefacts.
5. Review the transcript for sense, tone, missing buttons, loops, empty replies, and leaked internals.
6. Escalate to TDLib/visual proof only if the launch decision needs screenshots, Telegram Desktop state, or bot-to-bot evidence.

## Live Telethon Guardrails

Live Telethon QA uses a real user session, so the harness treats it as a controlled external action:

- Require the guard variable `TELEGRAM_LIVE_APPROVED=portfolio-guru-live-qa-approved` before any send. It is set for a run that an already-approved product task or exact release approval already covers (same allowlisted bot, unchanged recipient/effect — see the Launch Gate above); a standalone ad-hoc run still needs Moeed's exact approval before this variable is set.
- Require a single named target bot via `TELEGRAM_BOT_USERNAME`; default is `portfolio_guru_bot`.
- Refuse runtime target mismatches. The script cannot be pointed at one bot and then send to another.
- Keep an allowlist in `TELEGRAM_LIVE_ALLOWED_BOTS`; default is only `portfolio_guru_bot`.
- Open conversations only with the allowlisted target bot.
- Click only safe payloads returned by the allowlisted bot; labels never authorise an effect. Record protected controls without clicking.
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
Cases live in `backend/tests/fixtures/telegram_qa_cases.py` — ten anonymised
Haris (ACCS/Intermediate) and Sana (SAS/CESR) golden cases covering text,
synthetic handwritten-note photo, voice note, PDF/document evidence, and mixed
photo+text input. Media cases use local synthetic Telegram attachments plus
patched extractors, so this lane proves handler/source/draft behaviour without
testing real Telegram media transfer. It never calls Telegram and does not need
`TELEGRAM_LIVE_APPROVED`.

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
- Missing live approval, credentials or proof returns non-zero/pending. An explicit `RUN_LIVE_TELEGRAM=0 REQUIRE_TELEGRAM_LIVE=0` invocation is offline-only and cannot satisfy whole-bot or focused live proof.
- `--focused-release` is **fail-closed, not skip-cleanly**: it is the release gate's required live proof, so it forces the same behaviour as `REQUIRE_TELEGRAM_LIVE=1` regardless of the caller's environment. Missing approval or credentials, an incomplete allowlist, or an explicit `RUN_LIVE_TELEGRAM=0` all make it exit non-zero rather than silently reporting a clean skip.

For a launch-blocking run:

```bash
TELEGRAM_LIVE_APPROVED=portfolio-guru-live-qa-approved REQUIRE_TELEGRAM_LIVE=1 scripts/telegram_bot_qa.sh
```

Only set `TELEGRAM_LIVE_APPROVED` after an already-approved task/card covers this exact run, or after Moeed has approved a standalone ad-hoc run. Never run Telethon live QA silently while Moeed is manually testing the bot.

## Whole-bot completion

`bash scripts/telegram_bot_qa.sh --whole-bot` is the comprehensive entrypoint.
It runs the full offline release gate first, then, when authorised and bound to
the exact candidate, the bounded safe command/menu graph and verified `/cancel`
cleanup. Clinical generation and unstructured-input journeys remain protected
and offline-only until a future explicit synthetic-isolation envelope exists.
The clinical layer therefore remains pending; safe-graph success alone cannot
complete whole-bot proof. Routine testing inside the already-approved
bot/account/effect envelope is autonomous: no second Founder checklist or fresh
permission request is required. New access, recipients, spend or protected writes
remain outside that envelope.

The runtime registration objects provide the command/callback/state inventory.
A reviewed keyboard-producing module AST digest adds keyboard drift detection; it never earns
behavioural credit. The catalogue binds actual registered callback execution to
passing assertion-backed scenarios. `evidence_matches` in
`tests/whole_bot_catalogue.py` is the shared offline semantic-credit authority for
the audited catalogue and the manual `Coverage` API. State requirements are
separate for every registration slot, including repeated setup registrations.
`DispatchRecorder` observes the actual leaf selected inside
`Application.process_update()`, its source conversation state and the state
committed by `ConversationHandler`. Credit requires the declared input/payload,
source and destination plus an assertion bound to that event. Direct calls,
wrong-state fallbacks and global handlers cannot satisfy a state-owned route.
The per-slot probes exercise disconnected/stale guards and stubbed effects;
the wider clinical and callback-branch requirements remain independently required.

Protected commands have no boundary exemption. Their latest matching command
branch must have an explicitly asserted effect/guard; ordinary output alone earns
no protected credit. `command:link` is the no-argument instructions/zero-mutation
guard, while `command:link/args:token` requires token-branch evidence. Offline tests
assert the exact synthetic token/user passed to the stubbed account mutation.
Arguments themselves are never retained in observation receipts.

The manual ledger records scenario evidence; only the passing pytest audit and
aggregate establish suite and whole-bot completion. Its live annotations alone
cannot satisfy the Phase 5A provenance/runtime/transport gates. Dynamic IDs and
form values are represented by reviewed branch templates. Language/model/provider
permutations remain sampled, not exhaustive.

Whole-bot success means every current catalogue requirement and required layer
passes: registration/catalogue, offline behavioural regression, safe live graph,
clinical journeys, nonempty transcripts and safe cleanup. The single
`whole-bot-aggregate.json` contains `passed|pending|failed`, a run identity, layer
results and artifact hashes. Only `passed` exits 0; pending exits 20 and failed
exits 1. Unknown controls, unclassified units, skips, stale/missing/empty artifacts,
failed scenarios or incomplete cleanup cannot be promoted to success.

Before any Telegram client access, live proof requires a full
`PORTFOLIO_GURU_EXPECTED_SHA`, a clean candidate checkout (including untracked
files), and successful `scripts/verify_live_runtime.py --expected-sha <40hex>`.
The verifier uses its canonical Portfolio Guru checkout/service/identity defaults;
this gate accepts only `portfolio_guru_bot`. Missing/mismatched candidate identity
stays pending before Telegram access; attempted runtime-verifier failure fails.
The check runs again in the live fixture before connection and after the attempt.
Run ID, target, candidate SHA and verified runtime SHA bind every live receipt and
transcript envelope (`events` contains the redacted exchanges). The aggregate
validates these against the run context and runtime-process receipt. It always
checks attempted live process/JUnit failures, including collection/fixture errors,
even when the graph never starts.

Live also requires an explicit `TELEGRAM_QA_USER_ID` matching the synthetic
session account. That match does not establish synthetic isolation of a linked
portfolio. `/health`, `/unsigned`, unsigned lookback/custom controls, all health
views/queues/review routes, credential/portfolio access, generation and persistent
mutations are protected. The live roots are `/help`, `/bulk`, `/chase` and the
explicit `/cancel` reset/cleanup path; only static help/disabled-form toast
callbacks are traversable. Unknown controls still fail closed. It never sources dotenv, never sends `/reset` or
`/delete`, and never invokes payment, credential changes, admin mutations/reports,
Kaizen saves or supervisor submission. Offline final-effect guards/mutations use
stubs or isolated synthetic storage (`protected-boundary-covered`). Live controls
are observed only up to `protected-boundary-reached`. Protected commands are
explicitly classified as not invoked; that is not a claim that their live effect
was exercised. Actual saves and other protected release effects require their own
existing authorisation and proof.

Owned offline/live process groups have finite configurable deadlines:
`WHOLE_BOT_OFFLINE_TIMEOUT=1800`, `WHOLE_BOT_LIVE_TIMEOUT=1200`,
`WHOLE_BOT_RUNTIME_TIMEOUT=45` and reserved `WHOLE_BOT_CLEANUP_TIMEOUT=60`
(all seconds). Timeout/SIGINT/SIGTERM terminates the owned group, gives it a
three-second grace period, kills survivors and waits for the owned process.
Receipts are atomically replaced with failed/interrupted results, including
explicit termination errors when the OS denies cleanup; those errors cannot pass. A failed live
attempt reserves a separate bounded, identity/account-checked cancel-only client;
it cannot turn the failed attempt green. No live cleanup runs after offline failure.

`verify_changed.sh` includes the catalogue audit and fake-client/aggregate tests;
`verify_release.sh` inherits those checks and adds the full offline suite. Neither
contacts Telegram. Python socket/DNS guards turn missing provider stubs into local
failures. The whole-bot entrypoint always runs this offline gate before checking
live readiness. An offline failure leaves a failed aggregate and prevents sends.
The focused CBD release mode remains available for risk-scaled narrow proof, but
its success alone is not whole-bot completion.

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
- `TELEGRAM_LIVE_APPROVED=portfolio-guru-live-qa-approved` — set once an already-approved task/card covers this exact run, or after Moeed's explicit approval for a standalone ad-hoc run
- `TELEGRAM_BOT_USERNAME` when testing a non-default bot
- `TELEGRAM_LIVE_ALLOWED_BOTS` if widening beyond the default `portfolio_guru_bot`

The harness refuses to run live messages unless approval is present and the target bot is allowlisted. The default allowlist is `portfolio_guru_bot`.

## Automation Contract

Offline CI may run routinely. Live whole-bot proof is explicit/on-demand within an approved task, never scheduled monitoring. Report a non-pass when:

- offline gate fails
- live Telethon lane is required but not configured
- live Telegram workflow fails
- transcript contains internal errors, empty replies, broken buttons, or obvious nonsense

Do not run live Telegram QA in public groups or against production users. Use controlled private chats or test accounts.
