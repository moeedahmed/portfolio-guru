# Active Task - Phase 2.8 Public UX Upgrade

## Objective

Make Portfolio Guru feel better than the Medic Portfolio topic for Kaizen filing while keeping the product safety contract:

- user can send rough context naturally
- bot guides toward the fastest sensible draft
- user can still override the form choice
- nothing is saved to Kaizen without explicit approval

## Current Slice

Build the smallest high-impact UX improvement:

- PHI-free friction telemetry for the core funnel
- primary `Use best fit` route from recommendations
- keep `See all forms` as the manual escape hatch
- preserve existing form-choice, template-review, draft-review, and approval gates

## Guardrails

- No automatic Kaizen save.
- No supervisor submission.
- No clinical content in telemetry/log metadata.
- No removal of manual form selection.
- No draft-first automation until this one-tap recommendation path is verified.

## Done

- Public UX upgrade plan logged from Moeed's feedback that Medic Portfolio feels smoother than Portfolio Guru.
- Implemented PHI-free funnel event labels for input, recommendation, form selection, best-fit selection, template gaps, draft display, refinement, save attempt, and cancel/reset.
- Added `Use best fit` as the primary recommendation action while keeping other suggested forms and `See all forms`.
- Added regression coverage for the best-fit path and updated recommendation copy/snapshot tests.

## Verification

- Focused flow/conversation/snapshot tests must pass.
- Full offline suite must pass before commit.
- Local bot restart required before reporting live.

## Next

- Dogfood 5 anonymised cases in Medic Portfolio and Portfolio Guru.
- Score time-to-draft, taps/replies, correction burden, draft quality, and trust.
- If `Use best fit` wins, move to draft-first for high-confidence obvious cases.
