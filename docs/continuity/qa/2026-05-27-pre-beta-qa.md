# Pre-Beta QA Report — 2026-05-27

**Branch:** `chore/telegram-bot-qa-discipline`  
**Assessed by:** Claude Code + Codex + controlled live Telethon/Kaizen smoke
**Gate:** Full offline pytest + static analysis + diff review + approved controlled live smoke

---

## Executive Readiness State

**CONTROLLED LIVE SMOKE PASSED. READY FOR A SMALL PRIVATE BETA ONLY AFTER FINAL COMMIT/PUSH/RESTART CHECK.**

The deterministic product gate is green and the approved live gate has now
proven the core loop on a synthetic case: text case -> recommendation ->
draft preview -> save as Kaizen draft -> real saved-draft URL detected ->
Telegram confirmation with recovery/follow-up buttons. Future live testing
must stay narrow, scripted, and stopped on the first unexplained failure.

Resolved offline:
- Filer tests are re-enabled and passing against current filing internals.
- Same-case and stale-form recovery have deterministic callback coverage.
- Live Telethon harness has been updated to observe edited bot messages.
- Root-level logging token redaction is implemented and verified.
- Process lock guard prevents duplicate bot polling instances.
- Post-filing reports are plain-text/fallback-safe so generated observation
  copy cannot break Telegram Markdown parsing.
- Required Kaizen stage-of-training fields are filled from the user's saved
  profile stage instead of being left blank when the source case omits it.

Still required before beta:
- Commit/push/restart the current product-readiness slice so the beta cut is
  reproducible from git, not only from the dirty local checkout.
- One final post-restart read-back confirming the live bot is running that
  commit and only one polling instance is active.
- Keep the beta to 3-5 trusted users; do not market or widen until the first
  real-user cases are reviewed.

## Deterministic Workflow / Button Map

Launch gate order:

1. User input: text, voice, photo, thin case, long messy case, mixed details.
   Expected result: supported facts only; unsupported details are requested or
   marked missing.
2. Recommendation screen: `FORM|best`, direct `FORM|<type>`, `FORM|show_all`,
   `FORM|cat_*`, `FORM|search`, `FORM|back`, `FORM|disabled`, `CANCEL|form`.
   Expected result: every visible button either advances, returns, or explains
   expiry; no dead callback.
3. Draft review: `APPROVE|draft`, `IMPROVE|reflection`, `CANCEL|draft`,
   `ACTION|continue_thin`, `ACTION|back_to_missing`.
   Expected result: safe draft preview, missing details visible, no filing
   before explicit save-as-draft.
4. Filing result: `ACTION|retry_filing`, `ACTION|file`,
   `ACTION|same_case_another`, stale `ACTION|post_file_more|...`.
   Expected result: saved-draft success/partial/failure is clear, same-case
   reuses the original case text and excludes the filed WPBA, old drawer
   buttons re-render safely.
5. Recovery/settings: `ACTION|setup`, `ACTION|cancel`, `ACTION|reset`,
   `ACTION|voice`, `ACTION|change_level`, `ACTION|change_curriculum`,
   `ACTION|settings`, `ACTION|back_to_menu`, `ACTION|delete`.
   Expected result: setup or settings routes are explicit; stale buttons do
   not strand the user.
6. External side effect: Kaizen save-as-draft.
   Expected result: actual Kaizen draft exists with quality fields filled;
   never submit/sign/approve/send/delete.

### Controlled Live Smoke Addendum

After Moeed explicitly approved a controlled live run, a guarded Telegram smoke was run against
the allowlisted Portfolio Guru bot only. No Kaizen save, submit, sign, approve, send, reject,
delete, or draft creation was triggered.

Result:

- Existing repo live Telethon lane: **FAILED** — 7 passed, 1 skipped, 3 failed by timeout.
- Root finding: the live harness waits for fresh bot messages, while the current bot commonly
  edits an existing progress message into the recommendation/draft. The bot did reply; the
  harness missed edited-message state.
- Controlled history-aware smoke: **PASSED** for `/cancel` → synthetic text case → recommendation
  → click `Use best fit: CBD` → draft preview → `/cancel`.
- Controlled transcript artefact:
  `.artifacts/telegram-bot-qa/2026-05-27T14-54-47Z-controlled/controlled-live-transcript.json`

Live smoke quality note:

- The recommendation and draft flow worked, but the draft carried `Date: — needs your detail`
  for a synthetic case with no date. That is acceptable behaviour, but still a useful beta UX
  check: missing details should stay obvious without blocking a safe draft preview.
- Primary draft buttons were calm: `Save as draft`, `Quick improve`, `Cancel`.

### Controlled Kaizen Saved-Draft Addendum

After Moeed approved the next gate, a narrow synthetic live test was run against
the allowlisted bot and real Kaizen save-draft path. Broad exploratory Telethon
testing was not used.

Initial result:

- Kaizen save worked: deterministic filer clicked `Save as draft`, detected a
  saved draft URL, and mirrored usage/case records.
- Telegram loop did not close: the final saved-draft report failed with
  Telegram `Bad Request` because the report was still sent with Markdown
  parsing after generated observation content was appended.

Fix applied:

- Post-filing reports now send as plain text with inline buttons, not Markdown.
- Fallback send failures are logged with exception detail.
- Token redaction now covers non-string logging arguments.
- Stage-of-training is filled from saved user profile metadata for forms that
  require it.

Final controlled live result:

- Synthetic text case -> `Use best fit: CBD` -> draft preview -> `Save as draft`.
- Kaizen proof: stage set to Higher, header dates filled, SLO2 / SLO3 / SLO7 /
  SLO11 expanded and KCs ticked, saved-draft URL detected.
- Telegram proof: final confirmation displayed successfully with `Open saved
  draft`, `Amend this draft`, `Same case, new WPBA`, and `File another case`.
- No Telegram `Bad Request` on the final report.
- Operational note: Gemini free-tier quota/high-demand errors occurred, but the
  configured fallback providers recovered and the user loop still completed.

Resolved launch blockers from live/log inspection:

1. **Live harness mismatch:** `tests/test_e2e.py` / `tests/test_e2e_live.py` must be updated to
   observe edited messages or poll recent bot history before the live Telegram lane can be used
   as a launch gate. **Resolved for the controlled harness path** by polling recent bot history
   and observing edited messages; broad legacy live tests remain non-launch-gate until refreshed.
2. **Credential leakage in local logs:** `/tmp/portfolio-guru-bot.log` records Telegram Bot API
   URLs with the raw bot token embedded. Logs must redact bot tokens before beta, because launch
   triage artefacts and copied log snippets can otherwise leak credentials. **Resolved for new
   logs** by root-level token redaction, including non-string logging arguments.
3. **Recent duplicate polling conflicts:** `launchd.err.log` contains repeated Telegram 409
   conflicts indicating another bot polling instance. This needs cleanup before beta monitoring
   can be trusted. **Resolved for the current runtime** by disabling the duplicate launchd service
   and restarting the canonical service under the process lock.

---

## Commands Run

```bash
# 1. Full offline gate
cd /Users/moeedahmed/projects/portfolio-guru/backend
source venv/bin/activate
python -m pytest tests/ -q \
  --ignore=tests/test_e2e.py \
  --ignore=tests/test_e2e_live.py
# Latest result after controlled-live fixes: 613 passed, 13 deselected, 40 warnings, 3 snapshots passed

# 2. Safety-critical modules only
python -m pytest tests/test_form_type_wiring.py \
  tests/test_source_grounding.py tests/test_assessor_writeback.py -v
# Result: 52 passed

# 3. UX-flow + RPL quality + DOPS quality + extraction
python -m pytest tests/test_flow_walker.py tests/test_reflect_log_quality.py \
  tests/test_reflect_log_filing_dropdown.py tests/test_dops_filing_quality.py \
  tests/test_extraction.py -v
# Result: 206 passed

# 4. Filer + conversation
python -m pytest tests/test_kaizen_filer.py tests/test_filing_reliability.py \
  tests/test_conversation.py -q
# Result: 31 passed, 22 skipped

# 5. Supervisor safety + invite guard + offline e2e + snapshots
python -m pytest tests/test_supervisor_bot.py tests/test_assessor_invite_guard.py \
  tests/test_e2e_offline.py tests/test_snapshots.py -q
# Result: 37 passed, 3 snapshots passed

# 6. Static diff review
git diff -- backend/bot.py backend/kaizen_form_filer.py
git status
```

**No test failures. No errors. All snapshots pass.**

---

## Findings

### Critical Blockers — Must Fix Before Any Beta

#### C1 — Uncommitted filer changes block the launch gate

`backend/kaizen_form_filer.py` has unstaged changes that touch filing helper routing:

- `_fill_field_legacy` `stage_of_training` branch redirected from `_fill_stage_of_training`
  (now dead) to `_fill_stage`.
- `SELECT` tag branch redirected from `_fill_select_legacy` to `_fill_select`.
- `_fill_stage` extended with a regex ST-number fallback for non-QIAT stages.

`backend/bot.py` unstaged changes touch stale-callback recovery and post-filing keyboard
layout — also safety-adjacent.

Per `docs/PRIVATE_BETA_LAUNCH.md` § Hard No-Go Blocker #6:

> "Any uncommitted or unreviewed change to assessor_writeback.py, supervisor_bot.py,
> filer.py, browser_filer.py, or filer_router.py. Safety contracts live in these files;
> they ship via reviewed PR or not at all."

`kaizen_form_filer.py` carries the same safety contract as `filer.py`/`browser_filer.py`.
**These changes must be committed and reviewed before launch.**

#### C2 — `test_kaizen_filer.py` is fully skip-annotated

```python
# Line 11: pytestmark = pytest.mark.skip(reason="Tests need rewriting for new kaizen_form_filer internals")
```

This silently disables 22 tests including:

- `test_stage_defaults_to_higher_for_st5`
- `test_stage_maps_accs_for_st1`
- `test_stage_maps_intermediate_for_st3`
- `test_all_form_types_have_uuid`
- `test_no_duplicate_uuids_within_form`
- All login/field-fill integration scenarios

The stage-mapping tests are exactly the behaviour changed by C1 (the regex alias extension
in `_fill_stage`). There is currently no passing test that exercises the new ST-number regex
path against the Angular select filler. The RPL dropdown tests in
`test_reflect_log_filing_dropdown.py` cover `_fill_select` but not `_fill_stage` for
non-RPL forms.

**The 22 skipped tests must be rewritten or re-enabled before the offline gate can be
trusted for beta.**

---

### High Priority — Fix Before Public Testing

#### H1 — `_fill_stage_of_training` is dead code

After the C1 change, `_fill_stage_of_training` (line 2076 of `kaizen_form_filer.py`) is
no longer called from anywhere in the live code path. The archived version in
`_archived/kaizen_filer_pre_merge_20260401.py` shows the same function, confirming this
is a legacy remnant. Dead code in a safety-critical filer creates confusion about which
function is authoritative and what the active ST-level mapping logic is. Remove it or add
a deprecation note pointing to `_fill_stage`.

#### H2 — No offline test for `_resume_paused_flow` calling `_restore_last_filed_case_context`

The C1 diff adds a call to `_restore_last_filed_case_context(context)` at the top of
`_resume_paused_flow`. The helper itself is correct and the `handle_form_choice` stale path
is well-tested, but the `_resume_paused_flow` integration (stale button → paused-flow
recovery → case context restored) is not directly asserted. If the restore helper silently
returns `False` (e.g. `last_filed_case_text` is empty), the paused flow proceeds without
case context — this is recoverable but could show the user an unexpected "no active case"
state.

#### H3 — Live filing success has no offline coverage

All 22 skipped `test_kaizen_filer.py` tests are the closest offline proxies for "did the
filer actually reach Kaizen and save a draft?" Without them, the offline gate passes
regardless of whether the deterministic Playwright path is functional. The only real
coverage is via dogfood smoke (`scripts/dogfood_smoke.sh`) which requires a live CDP
session. This is acceptable for a controlled smoke, but should be noted as a gap before
widening to multiple beta users.

#### H4 — Voice/photo extraction not covered offline

`test_flow_walker.py` and `test_extraction.py` only exercise text input. Voice notes go
through `backend/whisper.py` → Gemini audio path → same extraction pipeline. Photo input
goes through `backend/vision.py` → Gemini Vision. Neither path has offline test coverage
beyond the extraction logic itself. Any whisper transcription quality regression would only
surface in live dogfood.

---

### Medium Issues

#### M1 — Regex fallback in `_fill_stage` overwrites key-lookup result unconditionally

The new regex block in `_fill_stage` runs regardless of whether the key-lookup loop above
already resolved `stage_key`. For normal Kaizen values this is benign (the regex confirms
the same result), but a composite label like `"Higher ST3 transition period"` would have
the key loop correctly identify `"Higher"` and then the regex override to `"Intermediate"`
(matches `\bst3\b`). This edge case is unlikely for real Kaizen-sourced labels but is
untested. A guard like `if stage_key == stage_label` (i.e., key lookup found nothing)
before the regex block would prevent the override.

#### M2 — `same_case_another` fallback chain picks up `chosen_form` as exclusion

The C1 bot.py diff extends the `filed_form` resolution to:

```python
filed_form = (
    context.user_data.get("last_filed_form_type")
    or context.user_data.get("excluded_form_type")
    or context.user_data.get("chosen_form")
    or ""
)
```

If `chosen_form` is used (i.e., the user never completed a filing), it will be treated as
the previously-filed form and excluded from the next recommendation. This is likely
harmless but could confuse a user who cancels mid-flow and then taps "Same case, new WPBA"
expecting all form types to be available.

#### M3 — Deprecated `datetime.utcnow()` warnings in supervisor/profile code

43 deprecation warnings appear in the test run, most involving `datetime.utcnow()` in
`profile_store.py:225` and the Pydantic UTC datetime fac. These are Python 3.14+
compatibility signals. Not blocking for beta but will fail eventually on newer Python
releases.

---

### Low Issues

#### L1 — `test_kaizen_filer.py` skip reason is stale

The skip reason says "Tests need rewriting for new kaizen_form_filer internals" — with no
date or linked issue. This has been the state for an unknown period. Each slice adds more
`kaizen_form_filer.py` changes that the disabled tests were meant to cover, growing the
gap silently.

#### L2 — `launchd.out.log` and `.err.log` rotation not verified

Offline QA cannot check log rotation state. The runbook requires these files for 2h/24h
monitoring. If the Mac Mini has not been restarted recently, `/tmp/portfolio-guru-bot.log`
may contain data; after a reboot it is empty and only the `~/Library/Logs/` files persist.
Confirm log age before the beta smoke.

#### L3 — TASK.md has 7 stacked addenda without a git commit

All 7 addenda in `TASK.md` describe committed or near-committed work but the file itself
is unstaged. This means the sprint log is accurate only if the reader knows to run
`git status`. Not a functional issue, but a hygiene note.

---

## Coverage Matrix

| Feature / Flow                          | Evidence Checked                                                      | Status                 | Gap                                 |
| --------------------------------------- | --------------------------------------------------------------------- | ---------------------- | ----------------------------------- |
| Text input intake                       | `test_flow_walker`, `test_conversation`                               | ✅ Pass                | —                                   |
| Voice input intake                      | `test_flow_walker` (mocked)                                           | ⚠️ Mocked only         | No offline voice path               |
| Photo input intake                      | `test_flow_walker` (mocked)                                           | ⚠️ Mocked only         | No offline vision path              |
| LLM extraction grounding                | `test_source_grounding` (12 tests)                                    | ✅ Pass                | Non-determinism; 1 run              |
| Form recommendation                     | `test_extraction`, `test_form_type_wiring`                            | ✅ Pass                | —                                   |
| Draft quality — RPL                     | `test_reflect_log_quality` (15 tests)                                 | ✅ Pass                | —                                   |
| Draft quality — DOPS                    | `test_dops_filing_quality` (28 tests)                                 | ✅ Pass                | —                                   |
| RPL event-type dropdown                 | `test_reflect_log_filing_dropdown`                                    | ✅ Pass                | —                                   |
| RPL header dates                        | `test_reflect_log_filing_dropdown`                                    | ✅ Pass                | —                                   |
| Stage of training fill                  | `_fill_stage` covered via RPL only; unit tests SKIPPED                | ⚠️ Partial             | 3 stage unit tests skipped          |
| UUID completeness                       | `test_form_type_wiring` (not skipped) + `test_kaizen_filer` (skipped) | ⚠️ Partial             | UUID dupe/format tests skipped      |
| Post-filing keyboard layout             | `test_flow_walker` (comprehensive)                                    | ✅ Pass                | —                                   |
| Stale callbacks (post-file)             | `test_flow_walker`                                                    | ✅ Pass                | —                                   |
| Stale form-choice recovery              | `test_flow_walker` (new tests in diff)                                | ✅ Pass                | Resume-paused-flow restore untested |
| Same-case reuse                         | `test_flow_walker`                                                    | ✅ Pass                | —                                   |
| Edit / amend flow                       | `test_conversation`                                                   | ✅ Pass                | —                                   |
| Cancel / reset                          | `test_flow_walker`                                                    | ✅ Pass                | —                                   |
| Error recovery copy                     | `test_flow_walker`                                                    | ✅ Pass                | —                                   |
| No submit/sign/approve                  | `test_assessor_writeback` + source scan                               | ✅ Pass                | —                                   |
| CBD save-draft (assessor)               | `test_assessor_writeback`                                             | ✅ Pass                | Requires live CDP to exercise       |
| Supervisor guardrails                   | `test_supervisor_bot`, `test_assessor_invite_guard`                   | ✅ Pass                | —                                   |
| Disabled features (bulk/unsigned/chase) | `test_flow_walker`                                                    | ✅ Pass                | —                                   |
| Live Kaizen draft save                  | dogfood smoke only                                                    | ❌ No offline coverage | Requires dogfood                    |
| filer_router safety                     | `test_form_type_wiring`, `test_filing_reliability`                    | ✅ Pass                | —                                   |
| Credential privacy                      | `test_source_grounding`, code inspection                              | ✅ Pass                | —                                   |

---

## Recommended Next QA Pass

### Before private beta cut (required)

1. **Commit the unstaged changes** on this branch. All 6 modified files should move from
   working-tree to a commit. The commit message should name the filing helper consistency
   audit explicitly so the reviewable history is clear.

2. **Rewrite or re-enable `test_kaizen_filer.py`** — at minimum the 5 structural tests:
   `test_all_form_types_have_uuid`, `test_no_duplicate_uuids_within_form`,
   `test_stage_defaults_to_higher_for_st5`, `test_stage_maps_accs_for_st1`,
   `test_stage_maps_intermediate_for_st3`. These are pure unit tests that should be fast
   to update for the current `kaizen_form_filer.py` internals.

3. **Fix M1 (regex guard)** — add `if stage_key == stage_label:` before the regex block in
   `_fill_stage` so the key-lookup result cannot be overwritten.

4. **Remove dead `_fill_stage_of_training`** (H1) or add a deprecation marker pointing to
   `_fill_stage`.

5. **Run the dogfood smoke checklist** (`scripts/dogfood_smoke.sh`) on the Mac Mini with the
   live bot. This requires: launchd running, CDP Chrome session at `localhost:18800`, BWS
   secrets loaded. **Explicit operator approval required for live Telegram/Kaizen interaction
   before this step.**

### Live gates requiring explicit approval

| Action                                                                              | Requires approval from                        |
| ----------------------------------------------------------------------------------- | --------------------------------------------- |
| `scripts/dogfood_smoke.sh` live run                                                 | Moeed (live Telegram + Kaizen draft creation) |
| `TELEGRAM_LIVE_APPROVED=portfolio-guru-live-qa-approved scripts/telegram_bot_qa.sh` | Moeed (Telethon real-user session)            |
| `KAIZEN_LIVE_TESTS=1 pytest tests/test_kaizen_integration.py -v -m kaizen -s`       | Moeed (live Kaizen artefacts)                 |

Do not run any of the above gates without explicit per-run approval naming the target
bot and test scope. None were run in this QA pass.

---

## Safety Verification Summary

All source-scan invariants pass (52/52):

- `assessor_writeback.execute_write_plan` guards: ✅
- Submit/sign/approve/send/reject/delete blocked on all assessor surfaces: ✅
- CBD-only save-draft; all other assessor form types blocked: ✅
- `SUP|confirm-save-draft` two-step confirmation gate: ✅
- Trainee filing is draft-only on all form types: ✅

No credential exposure detected in any test output or diff. No tokens, session IDs, or
decrypted values appear in any inspected file.
