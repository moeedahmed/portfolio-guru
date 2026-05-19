# Active Task - Phase 2.7 Assessor Workflow Mapping

## Objective

Map the assessor-side Kaizen workflow safely so Portfolio Guru can support the second real-life entry point:

- File evidence: user provides their own case, bot drafts evidence, user approves, bot saves a Kaizen draft.
- Assess ticket: ticket arrives for review, bot shows ticket content, assessor gives intent, bot drafts feedback/sign-off, assessor approves, bot submits/signs.

No persistent user-facing modes for now. Route by task.

## Scope

Phase 2.7 only:

- Capture the assessor workflow in repo context.
- Add a read-only assessor mapper scaffold.
- Allow browser navigation and extraction only.
- List visible assessment tickets.
- Extract read-only ticket fields, tags, state, and visible button labels for mapping.
- Keep final assessor submit/sign disabled.

## Guardrails

- No signing.
- No submitting.
- No deleting.
- No approving/rejecting.
- No saving drafts.
- No feedback submission.
- No draft artefacts in a colleague/consultant portfolio.
- Stop at login, 2FA, captcha, or unclear side effect.
- Any future assessor write action needs explicit approval for one named ticket and one reviewed response.

## Done

- Product direction settled: one engine, two entry points.
- `docs/plan.md` updated with Phase 2.7 assessor mapping direction and safety contract.
- `WORKFLOWS.md` updated with the planned Assess Ticket flow and hard constraints.
- `backend/assessor_mapper.py` added as read-only mapping scaffold.
- `backend/tests/test_assessor_mapper.py` added for parser and read-only guard coverage.
- Mapper now classifies safe navigation controls separately from write-side controls.
- Mapper now supports PHI-free shape output for live mapping without storing patient narrative.
- First live read-only shape mapped a CBD assessor ticket and detected `Fill in` / `Save` as write-side controls without clicking either.
- After explicit approval, `Fill in` was opened once for a CBD assessor ticket.
- The CBD assessor completion shape was mapped without typing, saving, submitting, signing, approving, rejecting, or sending feedback.
- Completion fields detected: assessor registration number, job title, entrustment scale, feedback, recommendation for further learning/development.
- Completion write controls detected: `Submit`, `Save as draft`.

## Verification

- Assessor mapper unit tests pass.
- Read-only live shape smoke passed against an authenticated browser session.
- Explicitly approved completion-shape smoke passed and reported `saved_or_submitted=False`.
- Flow/snapshot tests still pass.
- Full offline pre-commit gate must pass before commit.

## Next

Run live read-only mapping only when an authenticated assessor session or approved credentials are available. Capture:

- Where pending assessment tickets appear.
- Ticket list row selectors and states.
- Detail page read-only field structure.
- Assessor-specific fields/buttons.
- Exact submit/sign button selectors for later approval-gated implementation once write-side mapping is explicitly approved.
- Remaining ticket types beyond CBD.

Do not perform any write action during mapping.
