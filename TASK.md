# ✅ DONE: Collapse setup flows into single live messages

**Status:** Implemented and tested. All 86 offline tests pass.

## What changed

Login setup, voice profile setup, and pro upgrade flows now use a single "live" message that updates in place as each step progresses, instead of sending a new message for every prompt. This keeps the chat clean — no more stack of stale prompts (username, password, voice setup, upgrade) cluttering the conversation.

## How it works

New helpers in `backend/bot.py`:

- `_flow_msg(update, context, text, ..., flow_key=...)` — sends a new flow anchor on first call; subsequent calls with the same `flow_key` edit that anchor in place. Falls back to sending a fresh message if the anchor is gone or too old to edit.
- `_flow_done(context, flow_key=...)` — clears the anchor when the flow ends (success/cancel/error).

Three flow keys in use:

- `setup` — credentials (username → password → testing → connected)
- `voice` — voice profile (intro → examples → analyse → preview → activate)
- `upgrade` — pro upgrade (plan listing → tier selection → payment prompt)

## Also fixed during sweep

- Two stale internal docstrings still said "Pro/Pro+" — updated to "Pro/Unlimited" (`extractor.py:115`, `usage.py:112`).

## Verification

- All 86 offline tests pass (`pytest tests/ --ignore=test_e2e.py --ignore=test_e2e_live.py`).
- `test_bot_imports`, `test_setup_password_skips_training_level_and_goes_to_file_first_case`, and all snapshot tests still pass.
- Existing dynamic edits in the case-filing flow are unchanged.
