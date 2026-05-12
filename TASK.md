# TASK: Portfolio Guru recovery sprint — docs, hygiene, and filing confidence

## Goal

Make Portfolio Guru restartable from repo state, not memory.

## Scope

1. Refresh `CLAUDE.md` to match current product:
   - all-form routing
   - deterministic Playwright + browser-use fallback
   - real log paths
   - current disabled/coming-soon commands
2. Clean repo hygiene:
   - classify untracked backups, tickets, docs
   - keep needed tickets under a clear tracked or ignored home
   - archive/remove stale backup files safely
3. Resolve command inconsistencies:
   - decide whether `/unsigned` and `/chase` are disabled or enabled
   - remove unreachable code or re-enable intentionally
4. Restore Kaizen filing verification:
   - rewrite or replace skipped Kaizen filer tests
   - add at least one non-live deterministic smoke test for `route_filing`
5. Run test suite and record current known-live limitations.

## Verification

- `git status --short`
- `python -m pytest tests/ -q --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py --ignore=tests/test_kaizen_integration.py`
- grep confirms docs no longer claim CBD-only filing

## Current evidence

- Offline tests passed during recovery audit: 51 passed, 22 skipped.
- Skipped tests include Kaizen filer/integration/live tests, so real browser filing confidence is not fully verified.
- Bot is running locally and polling.
- Gemini standardisation in Portfolio Guru backend appears done.

## Out of scope

- New portfolio features
- Live Kaizen filing without explicit approval
- Public/external sends
- EMGurus model cleanup, which belongs to a separate EMGurus sprint
