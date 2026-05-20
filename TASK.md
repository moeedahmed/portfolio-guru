# Active Task - Phase 2.8 Public UX Upgrade

## Objective

Make Portfolio Guru feel better than the Medic Portfolio topic for Kaizen filing while keeping the product safety contract:

- user can send rough context naturally
- bot guides toward the fastest sensible draft
- user can still override the form choice
- nothing is saved to Kaizen without explicit approval

## Current Slice

Build the next draft-quality improvement from dogfood feedback:

- import reusable Claude/Medic portfolio skill standards into Portfolio Guru's own draft engine
- improve form choice, field-specific drafting, assessor-safe wording, privacy/de-identification, and KC/SLO discipline
- use text + image/OCR notes as one evidence bundle
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
- Imported a product-owned Portfolio Skill Quality Rubric from Claude Code portfolio and Medic Portfolio standards, covering form choice, actual Kaizen field purpose, Driscoll-style reflection, de-identification, KC-first mapping, and pre-preview quality checks.
- Added deterministic preview cleanup for blunt judgement wording, transcription artefacts, overconfident septation/transudate phrasing, confusing ITU wording, third-party names, named tertiary centres, and historic surgery years.
- Added regression coverage for the rubric appearing in form recommendation and extraction prompts, and for deterministic de-identification/wording cleanup.
- Added DOPS-specific Kaizen filing normalisation and quality gate after dogfood showed most DOPS fields were being left blank.
- DOPS filing now maps indication, trainee performance, clinical reasoning, procedure, placement and dates into the actual Kaizen DOPS fields before save.
- DOPS filing now blocks underfilled DOPS saves before browser filing instead of claiming success for a near-empty draft.
- DOPS KC selection now supplements unstable AF/shock/sedation/cardioversion cases with supported SLO3 and SLO6 key capabilities.
- Added focused offline DOPS filing quality coverage for the unstable AF with RVR, ketamine sedation, refractory cardioversion, amiodarone/magnesium, echo and ITU/medical escalation case.

## Verification

- Focused extraction/source-grounding tests pass.
- Full backend offline suite passes when run from the backend pytest config.
- Local bot restart required before reporting live.
- 20 May 2026: focused DOPS/save/assessor tests passed: 24 passed.
- 20 May 2026: full backend offline suite passed: 160 passed, 22 skipped, 13 deselected.
- 20 May 2026: live bot restarted via launchd and confirmed running.

## Next

- Dogfood 5 anonymised cases in Medic Portfolio and Portfolio Guru.
- Score time-to-draft, taps/replies, correction burden, draft quality, and trust.
- Dogfood this pleural-effusion/possible-heart-failure case again and compare draft quality.
- If the quality rubric holds across 3-5 cases, move to draft-first behaviour only for high-confidence obvious cases.
