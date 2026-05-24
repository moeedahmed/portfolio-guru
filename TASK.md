# Active Task — Clinical Supervisor Local Draft Capture

## Objective

Extend the Clinical Supervisor workflow from read-only ticket viewing into
local draft capture: after the supervisor taps **Open**, the bot accepts a
text or voice note as assessor intent, drafts the assessor section locally,
and lets the supervisor review/re-record/cancel — without ever clicking
Fill in, Save, Submit, or Sign in Kaizen.

## Current Slice

1. `backend/assessor_drafter.py` — pure draft service. `draft_from_intent`
   takes a supervisor utterance + form_type and returns an `AssessorDraft`
   with `values`, `missing_required`, `risk_notes`, and `source_intent`.
   Deterministic for now (LLM extraction is a follow-up). `render_preview`
   produces a Markdown preview safe for Telegram.
2. `backend/assessor_session_store.py` — per-supervisor JSON cache for the
   active ticket UUID, form type, trainee section, in-progress intent, and
   latest draft. Idempotent `start` / `get` / `update_intent` /
   `update_draft` / `end`. Missing or corrupt files behave like
   "no session".
3. `backend/supervisor_bot.py` — Open now also starts a session and prompts
   the supervisor for an assessor utterance. New callbacks
   `SUP|review|<uuid>`, `SUP|recapture|<uuid>`, `SUP|cancel-draft|<uuid>`
   manage the local draft. None click any Kaizen write control.
4. `handle_assessor_intent_capture` — text/voice MessageHandler wired in
   `bot.build_application` at group=-1. Returns silently when no session is
   active (trainee handlers in group 0 keep working). Raises
   `ApplicationHandlerStop` after a successful capture so the same update
   never reaches the trainee filing flow.
5. Tests cover the new modules and the new callbacks:
   - `tests/test_assessor_drafter.py` — extraction, validation, risk
     notes, preview rendering, source-scan safety.
   - `tests/test_assessor_session_store.py` — lifecycle, isolation,
     corruption handling, source-scan safety.
   - Extended `tests/test_supervisor_bot.py` — Open creates session +
     prompts; review re-renders; recapture clears + reprompts; cancel
     ends session; text and voice intent capture happy paths; missing
     session no-op; transcription failure path; unknown-form-type
     short-circuit; source-scan continues to forbid Fill in / Save /
     Submit / Sign / Approve / Delete / Send.

## Done

- New modules `backend/assessor_drafter.py` and
  `backend/assessor_session_store.py` implemented and unit-tested.
- `backend/supervisor_bot.py` extended with three new callback actions and
  a high-priority intent capture handler. Existing `send_supervisor_notification`,
  `connect_cdp_page`, `handle_supervisor_callback` open/skip/later behaviours
  preserved verbatim. Source scan still passes — no Kaizen write paths
  reachable from this module.
- `backend/bot.py` `build_application` adds one MessageHandler at group=-1
  for `handle_assessor_intent_capture`. The existing trainee
  ConversationHandlers in the default group are untouched.
- `WORKFLOWS.md` Flow 2A status panel updated to reflect the new draft
  capture surface and what remains out of scope.
- Focused tests pass: `pytest tests/test_supervisor_bot.py
tests/test_assessor_reader.py tests/test_assessor_form_schemas.py
tests/test_supervisor_workflow.py tests/test_assessor_drafter.py
tests/test_assessor_session_store.py` → 131 passed.
- Offline gate passes: `pytest tests/ --ignore=tests/test_e2e.py
--ignore=tests/test_e2e_live.py` → 489 passed, 22 skipped, 13 deselected.

## Verification

```bash
cd /Users/moeedahmed/projects/portfolio-guru/backend
source venv/bin/activate
python -m pytest tests/test_supervisor_bot.py \
  tests/test_assessor_reader.py \
  tests/test_assessor_form_schemas.py \
  tests/test_supervisor_workflow.py \
  tests/test_assessor_drafter.py \
  tests/test_assessor_session_store.py -q
python -m pytest tests/ -q \
  --ignore=tests/test_e2e.py \
  --ignore=tests/test_e2e_live.py
```

## Guardrails

- **No Kaizen write action is added in this slice.** The Review keyboard
  intentionally omits Save / Submit / Sign. `handle_assessor_intent_capture`
  never opens a Playwright page, never calls `fill_and_save_draft`, and
  never escalates to any browser write path.
- Source-scan tests on the three new/extended modules continue to forbid
  `click('text=Save`, `.fill(`, `extract_assessor_completion_shape`, and
  the rest of the existing write-action list.
- The existing trainee filing workflows remain untouched. The new
  MessageHandler at group=-1 is inert for any user without an active
  assessor session; commands are filtered out so `/cancel`, `/start`,
  etc. always reach the trainee fallbacks.
- No credential changes, no deletes/creates/submits, no `launchd`
  restart, no deploy or push from this slice.

## Carried Context — Voice Profile Two-Path Setup

The previous voice-profile sprint landed (see commits 907aaf8..72f159e)
and is part of the baseline. Read-only Kaizen voice sampling, the
two-path choice screen, immediate profile activation, and Kaizen-path
gate enforcement are all live. Do not reopen unless a regression appears.

## Carried Context — Kaizen Filing Reliability Cleanup

Filing reliability cleanup (deterministic Playwright for DOM-mapped
forms, browser-use disabled by default, legacy `/api/file` deprecated,
login-failure taxonomy) is part of the baseline. The plan in the prior
TASK.md slice is preserved in git history; do not reopen unless a
regression appears.
