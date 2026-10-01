# PG2 — archived route audit recheck, 1 October 2026

Source: `technical-findings-2026-09-27.md`, main at `3b8b989`.
Rechecked against this worktree's `a0301fd` baseline and the local changes.
Evidence below is code inspection and synthetic offline tests, not live Kaizen proof.
“Reproducible → fixed” means a new regression failed before its minimal fix.
“Fixed” means the archived failure is already addressed in the baseline.
“Uncertain” means the remaining observation has no demonstrated failure in this run.

| Archived finding | Classification | Current evidence / action |
| --- | --- | --- |
| 1: clean partial save retried as a new form | Fixed | `_last_filing_retryable` excludes clean partial; existing `test_clean_partial_save_is_not_retryable` and stale-retry test. |
| 1: stale Retry approves another case | Reproducible → fixed | Retry carries the existing case token; bare legacy Retry cannot approve a stamped case. `test_old_retry_cannot_approve_a_newer_failed_case`; production registration test verifies stamped dispatch. |
| 1: typed save of failed draft opens another form | Reproducible → fixed | `_draft_url_to_reuse` recognises the current failed attempt even without a Retry flag. `test_typed_save_of_failed_attempt_reuses_its_url`. |
| 1: timeout / exception has no recoverable URL | Uncertain | Both already use `_FILING_UNCERTAIN_TEXT` and link to activities. An address never returned by a cancelled filer cannot be inferred safely. Actual remote autosave / duplicate outcome requires separately authorised live proof. |
| 1: editing clears retry status and loses attempt URL | Reproducible → fixed | `_store_draft` preserves an update target for the same form; new-case cleanup and changed-form regression prohibit reopening an unrelated draft. |
| 1: amend creates another form / names absent buttons | Fixed | `amend_draft_url`, `_draft_url_to_reuse` and Save/Cancel amend copy already exist; `test_amend_reopens_the_saved_draft_and_retry_its_own_attempt`. |
| 2: `/reset` wipes immediately / Keep clears case | Fixed | Both routes ask the same confirmation; Keep changes no state. Three existing reset tests in `test_route_audit_fixes.py`. |
| 2: Keep returns to Settings | Uncertain | Current Keep displays “Kept” instead of a Settings menu; no deletion or case loss remains. This is a navigation preference, outside the filing/input fix scope. |
| 3: voice replaces a case while form suggestions wait | Fixed | Existing voice gate uses `_show_open_case_new_case_gate`; `test_voice_note_while_forms_are_suggested_asks_before_replacing_case` in `test_gathering_mode.py`. Approval/template media have dedicated handlers. |
| 3: voice silently dropped while document/image choice waits | Fixed | `handle_pending_media_context` transcribes and retains `_pending_doc_context`; consent and processor guards remain intact. |
| 3: documents overwrite `_pending_doc` / lose the first file | Reproducible → fixed | Intake now uses `_queue_pending_media`; the queue seeds an existing legacy single item. `test_queue_keeps_a_legacy_single_document_before_new_files`; existing multi-file attachment tests cover application. |
| 3: document Cancel wipes the whole case | Fixed | `CANCEL|doc_intent` delegates to explicit ignore; `handle_document_intent` retains content state and removes queued files only. |
| 3: legacy bundle and gathering coexist | Uncertain | Both representations still exist for different intake paths. Gathering tests exercise combination; no additional synthetic loss isolated. No speculative rewrite. |
| 3: thin-case Add detail ends conversation | Fixed | `ACTION|add_detail` now returns `AWAIT_TEMPLATE_REVIEW`; current template/detail and gathering tests cover continuation. The empty-input refusal is intentionally retained. |
| 4: renewal welcome every month | Fixed | Webhook welcome requires `newly_upgraded`; tier transition is read atomically. Existing Stripe webhook/handler/reconciliation tests. No Stripe calls in this job. |
| 4: paid user opens another checkout / payment return wipes draft | Fixed | Existing paying-user checkout refusal and three payment-return preservation tests in `test_route_audit_fixes.py`. Payments remain default off. |
| 5: old FORM list replaces a newer case | Reproducible → fixed | `handle_form_choice` rejects a mismatched tracked prompt in the same chat, without changing the case. New stale-list test; legitimate current change-form test still passes with its actual source message id. |
| 5: Same case button wipes a newer case | Fixed | Existing newer-case guard resumes instead; `test_old_another_form_button_keeps_a_newer_open_case`. |
| 5: `form_choice_in_progress` redundant with per-user processor | Uncertain | Both guards remain. Redundancy alone is not an observed failure; removal would weaken protection. |
| 5: Retry recommendations loses input source / duplicate lists | Fixed / uncertain | Retirement and source preservation already tested by `test_retry_suggestions_retires_button_and_keeps_input_source`. Distinct queued taps producing duplicate lists were not isolated; no claim of exactly-once recommendation generation. |
| 6: upgrade, Draft Review, unsigned, Settings ignore beta | Fixed | Existing beta gates and `test_open_beta.py`; unsigned now shares Health and password-free access. Active trial also passes the shared unlimited-access check. |
| 6: Same case another bypasses filing limit | Reproducible → fixed | Checks `check_can_file` before cleanup/extraction. New refusal test asserts the case survives unchanged. |
| 6: password-free unsigned falls through to second reply | Fixed | Dedicated `_health_entry` / awaiting-queue route replaces the old branch; beta/password-free entry tests assert one requested view. |
| 7: level / curriculum mismatch, SAS toggle copy | Uncertain | `_effective_curriculum` deliberately pins SAS to 2021 and preserves trainee choices. Stored-choice/copy differences remain; changing profile policy or clinical copy is outside this job. |
| 7: setup curriculum “dead” / three pathway copies | Uncertain | `setup_curriculum` is still registered; auto-setup also stores the default curriculum. Several pathway entry points remain. No demonstrated filing/input loss. |
| 8: selected edit field ignored | Reproducible → fixed | Field label and key are included in regeneration feedback; no field value is invented. New selected-field regression. |
| 8: resume / legacy submit replaces amend controls | Reproducible → fixed | Both use `_active_draft_keyboard`; new resumed-amend Cancel test. Legacy submit still never submits to a supervisor. |
| 8: five pipelines / differing timeout copy | Uncertain | Multiple pipelines remain. Failure recovery is exercised offline; no additional failed synthetic journey isolated. No broad consolidation or new wording. |
| 8: legacy APPROVE/IMPROVE/REVIEW/EDIT handlers | Fixed | Compatibility handlers still exist with draft-only save and entitlement guards. Registration is not evidence of a submission capability. |
| 9: New case reset/file aliases and varied cancel/help/stale copy | Uncertain | Legacy aliases remain intentionally; current flow tests cover safe cancellation. The labels/copy have not been normalised without a product decision. |
| 9: `/help` and `/settings` end an active case | Reproducible → fixed | Navigation returns `None` for active case/media state so PTB retains the step. Two new regression variants; case data is unchanged. |
| 9: `/gather off` forgotten by flow clear | Reproducible → fixed | `_clear_conversation_data` retains only the explicit gathering preference; actual account erasure still clears everything. Another-form cleanup preserves it too. New cancel regression. |
| 9: five stale-input replies | Uncertain | Copy differs by state; no extra state loss demonstrated. Existing context-sensitive replies retained. |
| Low: repeated consent withdrawals append rows | Uncertain | Append-only withdrawal logging remains. This is a consent-record policy question, outside the filing/input fix scope; no consent guard altered. |

PG1 implementation: `PG_FREE_TRIAL_ENABLED=1` is a separate opt-in flag; unset is off.
Payments off wins regardless of the trial flag, so beta use stays unlimited and no trial starts.
When both flags are deliberately enabled after launch review, free users receive 14 days
from first eligible filing use, persisted once in an additive local `trial_started_at` column.
Expiry blocks both new intake and saving an open draft. Legacy subscribers retain their limits.
New expiry wording is **DRAFT FOR CLINICIAN REVIEW** and remains local / inactive.
Settings and upgrade marketing still have legacy five-case copy; review that before activation.

All new tests use synthetic cases, fake transport/provider boundaries and temporary storage.
No commit, push, deployment, live writes, paid calls, credential changes or Hermes access.

Final validation: `PATH=/Users/moeedahmed/.local/share/portfolio-guru/venv/bin:$PATH bash scripts/verify_release.sh`
exited 0: journey 940 passed; catalogue 963 passed; full suite 4294 passed,
6 existing skips and 18 deselected; 3 snapshots passed. Both `verify:changed PASSED.`
and `verify:release PASSED.` were printed. Receipt: `.artifacts/pg12-release-verification.log`.
Focused trial/recovery/message checks: 117 passed, 4 existing skips. Golden transcript: 3 passed.
Changed-module Python compilation and `git diff --check` passed; no dedicated lint/typecheck is configured.
