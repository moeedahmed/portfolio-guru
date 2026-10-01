# Codex result — 1 October 2026

Local worktree only: no commit, push, deploy, external message, paid call, production write, credential change or Hermes access.
All proposed bot wording and legal documents remain **DRAFT FOR CLINICIAN REVIEW**; legal drafts also require legal review and are NOT IN FORCE.

## PG1 — done (offline implementation)
- Persisted 14-day first-use trial behind `PG_FREE_TRIAL_ENABLED` (default off); beta remains unlimited with payments off.
- Files: `backend/usage.py`, `backend/bot.py`, `backend/tests/test_open_beta.py`, `backend/tests/test_message_standard.py`.
- Prior focused checks: 117 passed, 4 existing skips; final verification below covers the combined worktree.
- Review: expiry wording and legacy Settings/upgrade copy need launch review before flag activation; no live proof.

## PG2 — done (offline recheck)
- Route fixes and classified archived findings: `docs/route-audit-recheck-2026-10-01.md`.
- Files: `backend/bot.py`, `backend/tests/test_route_audit_fixes.py`; helpers, qa_transcript, test_e2e_offline, test_essential_first_gate, test_flow_walker and whole_bot_catalogue/whole_bot_coverage under `backend/tests/`.
- Prior full offline gate: 4294 passed, 6 existing skips, 18 deselected; receipt `.artifacts/pg12-release-verification.log`.
- Review: uncertain remote autosave outcomes and structural/profile/copy observations remain open; no live proof.

## PG3 — partial (review drafts complete; legal/operational decisions open)
- Files: `docs/legal/drafts-2026-10-01/{processors-ropa,dpia,privacy-notice}.md`; existing legal/consent content unchanged.
- Evidence: 270 source anchors pinned to `a0301fd5f87badb103738ea54abeef31bb6297e3`; 13 activities and OPEN O1–O13.
- Prior privacy/retention/consent checks: 132 passed; source-anchor, draft-label and whitespace checks passed.
- Earlier combined-suite failures were superseded by the PG2 green gate; they were not evidence of legal sufficiency.
- Review: clinician/privacy lead/solicitor must settle O1–O13, including controller identity, lawful bases, recipients/transfers, plaintext/assessor stores and erasure/retention.
- Runtime, contracts and user records were not inspected. Reconcile subsequent code changes under O12 before publication; no compliance approval claimed.

## Review fixes
The duplicated review contains three unique findings; all accepted, none rejected.
- R1 — done: tracked actual messages from alternative-form recommendations and search (including no-results Back); new buttons work and older lists remain rejected.
- R2 — done: documents reuse one combined media-intent prompt; superseded controls are retired where possible and cannot act on the current queue. Single-document/certificate choices are preserved.
- R3 — done: Save tokens rotate only after entitlement succeeds; expiry and entitlement errors preserve the controls through payment return. The in-progress guard still precedes the entitlement await.
- Code: `backend/bot.py`; regression coverage: `backend/tests/test_route_audit_fixes.py`.
- Supporting tests: `backend/tests/test_action_label_emoji_policy.py` now returns a realistic message; `backend/tests/whole_bot_catalogue.py` refreshes the reviewed producer fingerprint without weakening its guard.
- Reproduced all three defects before fixing: 10 targeted cases failed; final coverage also checks all four queued-document actions and prompt re-anchoring.
- Focused command from `backend/`: `/Users/moeedahmed/.local/share/portfolio-guru/venv/bin/python3 -m pytest tests/test_route_audit_fixes.py tests/test_action_label_emoji_policy.py tests/test_open_beta.py tests/test_attachment_handoff.py -q --tb=short` — PASS, 140 passed.
- Full command: `PATH=/Users/moeedahmed/.local/share/portfolio-guru/venv/bin:$PATH PYTHON_DOTENV_DISABLED=1 bash scripts/verify_release.sh` — PASS; receipt `.artifacts/review-fixes-verification.log`.
- Actual green output: `verify:changed PASSED.`; `4308 passed, 6 skipped, 18 deselected, 1726 warnings in 255.87s (0:04:15)`; `verify:release PASSED.`; 3 snapshots passed.
- Full runs caught stale test assumptions: fake-message/fingerprint updates, then invented media callback IDs in `backend/tests/qa_transcript.py`. The transcript now targets the displayed prompt; its 3 tests PASS, and the socket guard blocked the unintended provider route before any connection.
- `git diff --check` — PASS; Python syntax compilation — PASS. No standalone Python lint/typecheck configured; no dependency changes.
- `scripts/preflight.sh` omitted because its fetch writes shared Git refs; its offline consent/full-test checks are covered by the release gate.
- Review: combined document-choice wording and the stale-file notice are DRAFT FOR CLINICIAN REVIEW; no live content changed. Live Telegram/Kaizen verification was excluded by this job.
