# Active Task — Voice Profile Two-Path Setup

## Objective

Replace the old "send 3-5 examples" voice-profile entry with a production-shaped
two-path setup: **Learn from Kaizen entries** (read-only, action-gated) and
**Add examples manually** (existing 3-5 examples flow). The Kaizen path uses the
managed authenticated Chrome session through a read-only sampler: it can open
existing entries after the user chooses the Kaizen path and sample window, but
cannot create, edit, delete, submit, or send assessor actions.

## Current Slice

1. Two-path choice screen at `/voice` and `ACTION|voice` — Learn from Kaizen
   entries / Add examples manually / Back to settings. Same screen for fresh +
   rebuild, with Remove added when a profile already exists.
2. Manual path preserves the existing 3-5 examples flow verbatim.
3. Kaizen path opens the sample-size picker directly (Recent 10, Last 6 months,
   Last 12 months), with the read-only guarantees stated in the first screen and
   repeated lightly in the picker.
4. `backend/voice_sampler.py` is the only service boundary that can reach
   Kaizen for voice learning. It uses browser-harness/CDP in read-only mode and
   is fully mocked in normal tests, so tests never hit live Kaizen.
5. Stale sample-pick buttons can never bypass the Kaizen path gate.
6. A generated voice profile activates immediately; the sample draft is a
   reassurance/demo, not an approval gate.

## Done

- `backend/voice_sampler.py` introduced as the read-only sampler boundary
  (`SamplerStatus`, `SampleWindow`, `sample_kaizen_entries`). It resolves the
  managed Kaizen CDP session, opens timeline/detail pages read-only, extracts
  long portfolio writing fields, and returns typed fallback states when the
  browser session is unavailable or no usable samples are found.
- `backend/bot.py`:
  - `voice_start` + `ACTION|voice` both call `_voice_show_choice_screen` so
    Settings → Set up voice profile and `/voice` are identical entry points.
  - New `VOICE|path_manual`, `VOICE|path_kaizen`,
    `VOICE|kaizen_sample|<window>`, `VOICE|back_to_choice` handlers in
    `voice_collect_example`.
  - Kaizen path gate enforced in `_voice_run_kaizen_sample` — without
    `context.user_data["voice_kaizen_path_started"]`, the sampler is never
    awaited.
  - `voice_conv` adds `ACTION|voice` as an entry_point so the conv state is
    entered when users tap from Settings. Top-level `handle_action_button`
    pattern now excludes `voice$` so `voice_conv` claims it.
- `backend/tests/test_flow_walker.py` gains `TestVoiceProfileTwoPathFlow`
  covering: fresh + rebuild choice screens, manual path copy preserved,
  read-only guard copy, sample-size pick, path enforcement on stale callbacks,
  mocked sampler success, read-only browser script checks,
  callback acknowledgement, and `VOICE|back_to_choice`.
- Voice-profile generation now stores the profile immediately after analysis,
  removes the "Does this sound like you?" activation gate, and shows the sample
  as a demo built from combined writing patterns. Stale old preview buttons
  recover safely without re-entering fragile pending state.

## Verification

- Focused gate: `python -m pytest tests/test_flow_walker.py::TestVoiceProfileTwoPathFlow -v`
- Regression sweep on voice-adjacent flows: `python -m pytest tests/test_flow_walker.py::TestFlowWalker::test_callback_buttons_have_guardrails tests/test_flow_walker.py::TestTrainingStageGroups::test_settings_layout_prioritises_voice_profile -v`
- Default offline gate: `python -m pytest tests/ --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py`

## Guardrails

- No credential changes, no deletes/creates/submits, no assessor actions, no
  `launchd` restart, no deploy or push in this slice.
- The sampler browser script must remain read-only: no click/type/fill/save/
  submit/delete operations. It may only navigate and extract visible text.
- Do not regress the existing manual examples flow — the 3-5 examples brief
  must still appear verbatim once the user picks the manual path.

## Carried Context — Kaizen Filing Reliability Cleanup

The previous Kaizen filing reliability slice landed and is now part of the
baseline. Its plan is preserved below for reference; do not reopen unless a
regression appears.

# Carried — Kaizen Filing Reliability Cleanup

## Objective

Make Portfolio Guru's Kaizen filing path leaner and less fragile without a
rewrite. Preserve the core design: draft-only filing, deterministic Playwright
for DOM-mapped forms, explicit user approval, no live Kaizen tests in normal
CI.

## Current Slice

1. Preserve the recent live fixes for retry, failed-message copy, "File another
   case", and Cancel buttons.
2. Stop normal runtime/tests from dirtying tracked artefacts
   (`backend/filing_coverage.json`, `backend/dom_learning_log.json`,
   `backend/kaizen_form_filer.py` source via dom_learner).
3. Keep DOM-mapped Kaizen forms on deterministic Playwright only; browser-use
   auto-learning is feature-flagged off by default.
4. Document the legacy `backend/filer.py` / `main.py /api/file` path as
   deprecated so it cannot silently fire with credentials in the LLM prompt.
5. Pin alias routing (ESLE / Mini-CEX 2021) with focused offline tests so
   future regressions are caught.
6. Pin Kaizen login reliability so browser/CDP/session failures cannot be
   misclassified as bad credentials.

## Guardrails

- No live Kaizen filing, no final submission, no supervisor request, no
  deploy/push without explicit approval.
- Do not edit credentials or secrets.
- Do not broaden into Portfolio Readiness / ARCP Health work — that spec is
  paused while this cleanup ships.
- Do not revert unrelated edits.

## Done

- Existing live fixes for retry/cancel/failed-summary remain untouched in
  `backend/bot.py` and `backend/tests/test_flow_walker.py`.
- `backend/filing_coverage.py` resolves `COVERAGE_PATH` via
  `PORTFOLIO_GURU_FILING_COVERAGE_PATH`. The live default is
  `~/.openclaw/data/portfolio-guru/filing_coverage.json` (same runtime dir as
  the SQLite store and bot persistence) — the tracked
  `backend/filing_coverage.json` is no longer the live fallback. Tests
  redirect to per-test tmp paths via the conftest fixture.
- `backend/dom_learner.py` no-ops unless `PORTFOLIO_GURU_DOM_AUTOLEARN=1`. The
  learning log default is `~/.openclaw/data/portfolio-guru/dom_learning_log.json`;
  the patched filer path still resolves to the tracked `kaizen_form_filer.py`
  source because autolearn's whole purpose is to amend that mapping — the
  opt-in flag, not the path, is the safety boundary.
- `backend/filer_router.py` has a hard guard that refuses to escalate a
  DOM-mapped form to browser-use.
- `backend/filer.py` `file_cbd_to_kaizen` raises `NotImplementedError` unless
  `PORTFOLIO_GURU_ALLOW_LEGACY_FILER=1` is set, and `main.py`'s legacy
  `/api/file` route is now clearly documented as deprecated.
- `backend/tests/conftest.py` autouse fixture redirects all three tracked
  artefact paths to per-test tmp paths.
- `backend/tests/test_filing_reliability.py` adds 11 focused offline tests
  covering reuse-on-retry, no-reuse-on-normal, DOM isolation, alias routing,
  legacy deprecation, and tracked-artefact protection.
- `backend/engine/providers/kaizen/__init__.py` now raises a distinct
  `KaizenInfrastructureError` for browser-harness, CDP, subprocess, and
  timeout failures. A loaded-but-not-dashboard result remains the only
  credential rejection path.
- `backend/tests/test_kaizen_login_reliability.py` adds focused offline
  regression coverage for managed CDP resolution, provider failure taxonomy,
  and the setup flow's user-facing split between "couldn't reach Kaizen" and
  "Login failed".

## Verification

- Default offline gate: `python -m pytest tests/ --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py`
  passes (249 passed, 22 skipped, 13 deselected).
- `git status` after the run shows no mutations to `backend/filing_coverage.json`,
  `backend/dom_learning_log.json`, or `backend/kaizen_form_filer.py`.
- New tests in `tests/test_filing_reliability.py` all pass.
- Login reliability gate: `pytest tests/test_kaizen_login_reliability.py tests/test_flow_walker.py::TestOnboardingFrictionPatch tests/test_filing_reliability.py -v`
  passes (33 passed).
- Default offline gate now passes with the login reliability tests included
  (271 passed, 22 skipped, 13 deselected).

## Next

- Commit this reliability slice.
- Restart the launchd bot via the standard deploy path only after approval.
  No live Kaizen verification is required as part of this slice.

## Carried Context — Portfolio Readiness / ARCP Health

The previously active "Portfolio Readiness / ARCP Health Spec" remains paused.
Its plan in `docs/ARCP_HEALTH_DESIGN.md` is still the source of truth when it
resumes. Do not start that work until this filing cleanup is landed.
