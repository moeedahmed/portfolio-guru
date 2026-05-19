# Active Task - Phase 2.8 Public UX Upgrade

## Objective

Make Portfolio Guru feel better than the Medic Portfolio topic for Kaizen filing while keeping the product safety contract:

- user can send rough context naturally
- bot guides toward the fastest sensible draft
- user can still override the form choice
- nothing is saved to Kaizen without explicit approval

## Current Slice

Build the smallest high-impact UX improvement from dogfood feedback:

- make draft review easier to scan on Telegram
- split long draft narrative blocks into short readable paragraphs in the preview
- preserve saved Kaizen field values and clinical facts exactly as extracted
- preserve existing form-choice, template-review, draft-review, and approval gates

## Guardrails

- No automatic Kaizen save.
- No supervisor submission.
- No clinical content in telemetry/log metadata.
- No removal of manual form selection.
- No draft-first automation until this one-tap recommendation path is verified.
- No live Kaizen/browser/service actions for this slice.

## Done

- Public UX upgrade plan logged from Moeed's feedback that Medic Portfolio feels smoother than Portfolio Guru.
- Implemented PHI-free funnel event labels for input, recommendation, form selection, best-fit selection, template gaps, draft display, refinement, save attempt, and cancel/reset.
- Added `Use best fit` as the primary recommendation action while keeping other suggested forms and `See all forms`.
- Added regression coverage for the best-fit path and updated recommendation copy/snapshot tests.
- Added a display-only draft preview readability guard that splits long narrative text into short paragraphs for Telegram review without mutating stored draft fields.
- Added regression coverage proving long preview paragraphs are split and blank required fields still show the missing-detail marker.

## Verification

- Focused flow/snapshot tests pass.
- Full offline suite passes before commit.
- Local bot restart required before reporting live; not done in this slice.

## Next

- Dogfood 5 anonymised cases in Medic Portfolio and Portfolio Guru.
- Score time-to-draft, taps/replies, correction burden, draft quality, and trust.
- Confirm whether shorter preview paragraphs reduce correction burden.
- If `Use best fit` and smoother review win, move to draft-first for high-confidence obvious cases.
