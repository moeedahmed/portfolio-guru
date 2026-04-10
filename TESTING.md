# Testing Contract

Portfolio Guru uses five testing layers, and every change must preserve all of them.

## Layer 1 — Smoke Tests

`backend/tests/test_smoke.py` covers startup, credentials, and environment-sensitive storage paths. It is the guardrail for "bot cannot boot" regressions, including Fernet setup and profile persistence.

## Layer 2 — Unit Tests

`backend/tests/test_forms.py`, `backend/tests/test_extraction.py`, and `backend/tests/test_conversation.py` cover labels, schema coverage, extraction logic, imports, and keyboard layout.

## Layer 3 — Flow Walker Tests

`backend/tests/test_flow_walker.py` walks full conversation paths through `BotSimulator`. It catches dead ends, missing buttons, and broken state transitions.

## Layer 4 — End-To-End Tests

`backend/tests/test_e2e.py` drives the live Telegram bot via Telethon. These tests are marked `@pytest.mark.e2e` and are credential-gated so they skip cleanly until Telethon credentials are configured.

## Layer 5 — Offline E2E

`backend/tests/test_e2e_offline.py` runs full conversation paths through PTB's real `Application.process_update()`. Uses `OfflineRequest` to block any accidental network calls — if the bot tries to reach Telegram, the test fails immediately. All Gemini and store interactions are monkeypatched. Runs in CI without API keys.

## Layer 6 — Live Telegram

`backend/tests/test_e2e_live.py` sends real messages to the real bot via Telethon personal account. Marked `@pytest.mark.live` and skipped unless `TELETHON_SESSION` is set. Manual trigger only, never in CI: `pytest -m live`.

## Layer 7 — API Mocks And Snapshots

`backend/tests/test_gemini_mocks.py` exercises Gemini edge cases against the SDK HTTP layer with `respx`. `backend/tests/test_snapshots.py` uses `syrupy` to lock key outgoing bot messages so formatting regressions fail fast.

## When To Run

Run `cd backend && venv/bin/python3 -m pytest tests/ -v --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py` before every commit and before any Claude Code session reports completion.

Run `cd backend && venv/bin/python3 -m pytest tests/ -v -m e2e` for offline E2E tests only.

Run `cd backend && venv/bin/python3 -m pytest tests/ -v -m live` for live Telegram tests (manual only, requires `TELETHON_SESSION`).

Run `cd backend && venv/bin/python3 -m pytest tests/ -v --snapshot-update` after intentional message-format changes.

## When To Add Tests

Add or extend tests for any new conversation state, button, callback, credential or profile storage change, new form type, Gemini failure mode, or user-facing message that should remain stable.

## Test Naming

Use `test_<what>_<expected_behaviour>`, for example `test_fernet_roundtrip_succeeds` or `test_missing_fernet_key_raises`.
