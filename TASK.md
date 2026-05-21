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
- Fixed image-bundle state so stale "waiting for images" sessions cannot absorb a later new case, and bundle status edits are scoped to the active bundle message instead of mutating an old case/status message.
- Changed Kaizen save progress UX so approving a draft leaves the reviewed draft message visible; filing progress and final result now appear as separate messages underneath.
- Added lightweight repeated Telegram typing indicators during long image-reading and Kaizen-filing work, plus a delayed "still reading" image status for slower clinical screenshots.
- Added amend mode after filed drafts: the reopened draft now stays locked to the current case until Save updated draft or Cancel amend, extra text/media refines the existing draft, and explicit new-case wording asks the user to choose update-vs-new instead of guessing.
- Refined the post-cancel/new-case boundary: Cancel now fully ends the active conversation state, and extra clinical text sent while choosing a form is folded into the current fresh case instead of producing a second "start new case" warning.
- Strengthened the DOPS quality gate: it now blocks save when the case_observed narrative is a label-only stub, when the Indication or Trainee Performance semantic blocks are missing both as fields and as labelled narrative sections, and when the reflection is an incoherent fragment.
- `_pre_file_missing_fields` in bot.py now delegates the semantic checks to `dops_quality_gate`, so the bot and `file_to_kaizen` apply the same rules and the user is returned to draft approval (not to a half-saved Kaizen state) when the gate blocks.
- `file_to_kaizen` now emits `quality_gate_failed: True` and `missing_for_quality: [...]` alongside the partial-status error, giving the bot a structural signal independent of the English error string.
- Added a configurable Gemini 3.5 Flash extraction route (`PORTFOLIO_GURU_EXTRACTOR_PROVIDER=gemini-3.5-flash`, model name `gemini-3.5-flash`, env override `GEMINI_3_5_FLASH_MODEL`) without changing the DeepSeek production default.
- Added a focused DOPS bake-off (`backend/eval_dops_bakeoff.py`) that scores provider extractions on procedure / indication / trainee performance / reflection / KC links / grammar; the deterministic scorer is unit-tested offline.
- Changed the normal "File another case" path to clear prior case/draft/recommendation state, while keeping explicit same-case and amend/update paths context-aware.
- Cleaned normal form recommendation UX so default-2025 suggestions hide curriculum suffixes and use complete short one-line rationales instead of chopped descriptions with ellipses.

## Verification

- Focused extraction/source-grounding tests pass.
- Full backend offline suite passes when run from the backend pytest config.
- Local bot restart required before reporting live.
- 20 May 2026: focused DOPS/save/assessor tests passed: 24 passed.
- 20 May 2026: full backend offline suite passed: 160 passed, 22 skipped, 13 deselected.
- 20 May 2026: live bot restarted via launchd and confirmed running.
- 20 May 2026: image-bundle regression tests passed; full backend offline suite passed: 162 passed, 22 skipped, 13 deselected.
- 20 May 2026: amend-mode regression tests passed; full backend offline suite passed: 165 passed, 22 skipped, 13 deselected; live bot restarted and confirmed on the amend-mode commit.
- 20 May 2026: cancel/new-case boundary regression tests passed; full backend offline suite passed: 167 passed, 22 skipped, 13 deselected.
- 20 May 2026: strengthened DOPS gate + Gemini 3.5 Flash route + DOPS bake-off scorer landed; focused suites (test_dops_filing_quality, test_model_config, test_eval_dops_bakeoff, test_flow_walker) passed: 98 passed; full backend offline suite passed: 187 passed, 22 skipped, 13 deselected.
- 21 May 2026: context-boundary and recommendation-copy fixes verified; focused conversation/flow/snapshot suite passed: 80 passed; full backend offline suite passed: 198 passed, 22 skipped, 13 deselected; live bot restarted via launchd and confirmed running.
- 21 May 2026 closeout review: py_compile passed for bot.py, dops_filing.py, kaizen_form_filer.py; focused flow/conversation/snapshot/DOPS suite passed: 108 passed. No live Kaizen/browser actions, commit, deploy, or restart were performed.

## Next

- Dogfood 5 anonymised cases in Medic Portfolio and Portfolio Guru.
- Score time-to-draft, taps/replies, correction burden, draft quality, and trust.
- Dogfood this pleural-effusion/possible-heart-failure case again and compare draft quality.
- If the quality rubric holds across 3-5 cases, move to draft-first behaviour only for high-confidence obvious cases.
