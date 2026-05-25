# Active Task — Clinical Supervisor Guarded Write-Back Planning

## Objective

Extend Clinical Supervisor mode from local draft capture into a guarded
Kaizen write-back planning slice. A supervisor can open a ticket, dictate or
type feedback, review the local assessor draft, and request a reviewed Kaizen
action plan. The bot still does not open the assessor completion surface, fill
fields, save a draft, submit, sign, approve, delete, or send anything in live
Kaizen.

## Current Slice

1. `backend/assessor_writeback.py` — new guarded adapter. It maps reviewed
   local assessor draft values onto the mapped CBD assessor completion surface
   and distinguishes `fill_fields`, `save_draft`, `submit`, `sign`, `approve`,
   and `cancel`.
2. The adapter requires a ticket UUID, form type, explicit action, and reviewed
   draft hash for every Kaizen-touching action. Mismatched ticket identity,
   mismatched draft hash, unsupported form type, missing required fields, and
   final actions all produce blocked plans.
3. Live execution is intentionally unavailable. `execute_write_plan` raises
   `AssessorWriteBackUnavailable`; browser steps are descriptors only, not
   Playwright calls.
4. `backend/supervisor_bot.py` adds one review-only affordance:
   **Prepare Kaizen action plan (no write)**. It renders the guarded plan from
   the already reviewed draft and never connects to CDP or opens Kaizen.
5. Tests cover write-back mapping, action separation, ticket/draft binding,
   missing-required blocking, final-action blocking, local cancel, Telegram
   callback safety, and source-scan boundaries for ordinary supervisor flows.

## Done

- CBD assessor write-back field mapping added for:
  - Assessor Registration Number
  - Job title
  - If other, please specify
  - Entrustment Scale
  - Feedback
  - Recommendation for further learning or development
- New write action model distinguishes:
  - local cancel
  - fill fields
  - save draft
  - final submit/sign/approve actions
- Supervisor Telegram review keyboard now has a plan-only button whose label
  states the safety boundary. Open / review / recapture / cancel remain
  read-only/local and do not navigate to Kaizen.
- Focused tests pass:
  `python -m pytest tests/test_assessor_writeback.py tests/test_supervisor_bot.py tests/test_assessor_drafter.py tests/test_assessor_session_store.py tests/test_assessor_reader.py tests/test_assessor_mapper.py tests/test_assessor_form_schemas.py tests/test_supervisor_workflow.py -q`
  → 154 passed.

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

## Guardrails

- No live Kaizen write execution exists in this slice.
- The Telegram bot cannot save, submit, sign, approve, delete, send, or fill
  Kaizen from ordinary Open / review / recapture / cancel / plan flows.
- Save-draft planning is CBD-only until another assessor completion surface is
  explicitly mapped and tested.
- Final actions (`submit`, `sign`, `approve`) are represented only so the code
  can block and test them distinctly.
- No live browser/CDP tests, no deployment, no launchd restart, no push.

## Carried Context — Local Assessor Draft Capture

The previous slice landed local assessor draft capture. `assessor_drafter`,
`assessor_session_store`, and the high-priority supervisor intent handler remain
the baseline. Voice/text capture, review, recapture, cancel, skip, later, and
open callbacks must stay preserved.

## Carried Context — Kaizen Filing Reliability Cleanup

Deterministic trainee filing, browser-use fallback rules, and launchd deploy
state are part of the baseline. Do not reopen unless a regression appears.
