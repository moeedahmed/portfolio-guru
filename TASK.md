# Active Task — Kaizen Filing Reliability Cleanup

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

## Verification

- Default offline gate: `python -m pytest tests/ --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py`
  passes (249 passed, 22 skipped, 13 deselected).
- `git status` after the run shows no mutations to `backend/filing_coverage.json`,
  `backend/dom_learning_log.json`, or `backend/kaizen_form_filer.py`.
- New tests in `tests/test_filing_reliability.py` all pass.

## Next

- Codex review of the diff (orchestrator owns the handoff).
- Once reviewed and merged, restart the launchd bot via the standard deploy
  path. No live Kaizen verification is required as part of this slice.

## Carried Context — Portfolio Readiness / ARCP Health

The previously active "Portfolio Readiness / ARCP Health Spec" remains paused.
Its plan in `docs/ARCP_HEALTH_DESIGN.md` is still the source of truth when it
resumes. Do not start that work until this filing cleanup is landed.
