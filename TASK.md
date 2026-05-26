# Active Task — Clinical Supervisor Guarded Save-Draft Write-Back

## Objective

Extend the Clinical Supervisor write-back from planning-only into a one-action
live runner. A supervisor can open a ticket, dictate or type feedback, review
the local assessor draft, request a reviewed Kaizen action plan, and — behind a
separate explicit Telegram confirmation — let the bot save the assessor section
as a Kaizen _draft_. The bot still does not submit, sign, approve, send, delete,
or reject anything in Kaizen.

## Current Slice

1. `backend/assessor_writeback.py` keeps the planning surface but now adds a
   guarded live runner. `execute_write_plan` opens the named ticket via the
   CDP-attached Playwright page, clicks `Fill in`, fills the mapped CBD
   assessor fields, and clicks `Save as draft` — and nothing else. Browser
   step kinds outside `{open_completion_surface, fill_field, save_draft}`
   are rejected.
2. `execute_write_plan` enforces the safety envelope before it touches the
   browser: action must be `SAVE_DRAFT`; `plan.blocked_reasons` must be
   empty; the draft hash must still match; the ticket URL must contain the
   plan's ticket UUID; at least one field write must be planned. Any mismatch
   raises `AssessorWriteBackUnavailable` before any navigation.
3. Recoverable runner failures (Fill in / field input / Save button missing,
   navigation error, no Kaizen confirmation marker) return an
   `AssessorWriteResult(status="failed", error=…)` instead of raising. The
   Telegram surface renders that error as a user-facing message and never
   claims partial success.
4. `backend/supervisor_bot.py` adds the explicit confirmation step. Reviewing
   the plan now exposes `📤 Save draft in Kaizen` (only when the plan is
   executable); tapping it shows a fresh confirmation message that names the
   action and safety boundary, with `✅ Yes, save as draft` and `❌ Cancel`.
   The live runner runs only after the confirmation tap. Ordinary
   Open / Skip / Later / Review / Recapture / Cancel / Prepare paths never
   reach the runner.
5. Tests cover write-back mapping, action separation, ticket/draft binding,
   missing-required blocking, final-action blocking, the new live happy
   path, every recoverable failure mode, hash drift, ticket-URL mismatch,
   the explicit-confirmation gate, blocked-plan rejection, CDP failure,
   the no-write source-scan boundary on `assessor_writeback`, and the
   ordinary-callback isolation in `supervisor_bot`.

## Done

- CBD assessor save-draft runner clicks only `Fill in` and `Save as draft`
  (verified by source-scan).
- Live execution is gated by:
  - Action must be `SAVE_DRAFT`; everything else raises
    `AssessorWriteBackUnavailable`.
  - Plan must be unblocked, draft-hash-bound, and have at least one field
    write.
  - Browser step kinds must be on the live allow-list.
  - Ticket URL must contain the planned ticket UUID.
- `render_write_plan` surfaces a distinct safety boundary for executable vs
  blocked plans. Executable plans advertise the explicit-confirmation gate;
  blocked plans say nothing was opened/filled/saved/submitted in Kaizen.
- Telegram surface adds two new callbacks:
  - `SUP|request-save-draft|<uuid>` — shows the explicit confirmation copy
    and the Yes / Cancel keyboard. No CDP attach, no runner call.
  - `SUP|confirm-save-draft|<uuid>` — re-validates the plan, attaches CDP,
    calls the runner, and reports success or a user-facing failure reason.
    Session ends on success so future text/voice falls back to the trainee
    flow; session is preserved on failure so the supervisor can retry.
- Focused tests pass:
  `python -m pytest tests/test_assessor_writeback.py tests/test_supervisor_bot.py tests/test_assessor_drafter.py tests/test_assessor_session_store.py tests/test_assessor_reader.py tests/test_assessor_mapper.py tests/test_assessor_form_schemas.py tests/test_supervisor_workflow.py -q`
  → 177 passed.
- Full offline suite passes:
  `python -m pytest tests/ -q --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py`
  → 525 passed, 22 skipped, 13 deselected.

## Verification

```bash
cd /Users/moeedahmed/projects/portfolio-guru/backend
source venv/bin/activate
python -m pytest tests/test_assessor_writeback.py \
  tests/test_supervisor_bot.py \
  tests/test_assessor_drafter.py \
  tests/test_assessor_session_store.py \
  tests/test_assessor_reader.py \
  tests/test_assessor_mapper.py \
  tests/test_assessor_form_schemas.py \
  tests/test_supervisor_workflow.py -q
python -m pytest tests/ -q \
  --ignore=tests/test_e2e.py \
  --ignore=tests/test_e2e_live.py
```

## Live Smoke — 2026-05-26

- Fixed the Chrome 148 / Playwright CDP attach failure by using
  `connect_over_cdp(..., no_defaults=True)` across the live Kaizen CDP entry
  points and requiring Playwright 1.60+.
- Verified the persistent Chrome session can attach via CDP, log in, and read
  the live Kaizen Assessments queue.
- Live save-draft could not be completed because every visible assessment row
  in the checked accounts was already filled; no unfilled CBD ticket exposed a
  `Fill in` control.
- Safety smoke against an existing filled CBD returned a clean failure before
  any field write: `Fill in` control not found, zero fields filled, still on the
  original ticket URL.

## Guardrails

- Save-draft is the _only_ live assessor action this slice enables. Submit,
  sign, approve, send, reject, and delete remain blocked and tested.
- The runner requires the matching draft hash, the matching ticket UUID in
  the URL, an unblocked plan, and at least one mapped field write. Any
  drift raises before any navigation.
- The Telegram bot has no path to the live runner from Open / Skip / Later /
  Review / Recapture / Cancel / Prepare-writeback / Request-save-draft.
  Only `confirm-save-draft` reaches the runner, and it does so only after
  the supervisor taps `Yes, save as draft`.
- Save-draft remains CBD-only until another assessor completion surface is
  mapped and tested.
- No live Kaizen tests are run in this slice. No deployment, no launchd
  restart, no push.

## Carried Context — Guarded Write-Back Planning

The previous slice added the planning surface in `assessor_writeback`,
`AssessorWriteAction` separation, and the supervisor's
`Prepare Kaizen action plan (no write)` button. That contract is preserved:
non-save-draft actions still produce planning-only results, `cancel` stays
local, and final actions block.

## Carried Context — Local Assessor Draft Capture

`assessor_drafter`, `assessor_session_store`, and the high-priority
supervisor intent handler remain the baseline. Voice/text capture, review,
recapture, cancel, skip, later, and open callbacks must stay preserved.

## Carried Context — Kaizen Filing Reliability Cleanup

Deterministic trainee filing, browser-use fallback rules, and launchd deploy
state are part of the baseline. Do not reopen unless a regression appears.
