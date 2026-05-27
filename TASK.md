# Active Task — Private Beta Launch Cut

> **2026-05-27 addendum — user-visible form-name audit.**
> Moeed flagged that acronyms such as DOPS are acceptable, but internal form
> keys such as `PROC_LOG` must not appear in doctor-facing Telegram messages.
> The current slice adds a shared display-name/sanitisation layer for form
> names, routes recommendation rationale, question answers, recent-activity
> nudges, and filing failure details through it, and adds regression tests for
> internal-code leakage. Verification: full offline pytest gate green at
> 618 passed, 24 deselected, 40 warnings.

> **2026-05-27 addendum — public WPBA names and draft-divider spacing.**
> Moeed's beta screenshots showed two presentation issues: the post-filing
> portfolio nudge leaked internal form codes (`CBD`, `DOPS`, `PROC_LOG`)
> instead of public WPBA names, and the draft-preview divider was too tight
> against surrounding text. The current slice feeds public assessment names
> into the recent-activity LLM prompt, post-sanitises any leaked internal codes
> back to display names, and adds blank space above and below the draft-only
> divider. Verification: focused extraction + flow-walker gate green at
> 2 passed, 1 warning.

> **2026-05-27 addendum — saved-draft confirmation divider removed.**
> Moeed's live screenshot showed the post-save confirmation still carried the
> heavy draft-preview divider before usage/portfolio guidance. That divider now
> stays only in draft previews where it separates the user's draft from bot
> rationale/instructions; successful and clean-partial post-filing outcome
> messages render without it. Verification: focused flow-walker gate green at
> 4 passed, 134 deselected, 3 warnings.

> **2026-05-27 addendum — controlled live smoke passed.**
> Moeed approved the narrow live gate. The first Kaizen save proved the external
> side effect but exposed a Telegram confirmation blocker: the saved-draft
> report failed when sent with Markdown parsing. The current slice makes
> post-filing reports plain-text/fallback-safe, tightens token redaction for
> non-string log arguments, and fills required Kaizen `stage_of_training` from
> the user's saved training profile instead of leaving it for manual review.
> Controlled live smoke after restart passed end-to-end: synthetic text case →
> `Use best fit: CBD` → draft preview → `Save as draft` → real Kaizen draft
> URL detected → Telegram confirmation displayed with `Open saved draft`,
> `Amend this draft`, `Same case, new WPBA`, and `File another case`.
> Live filing proof included stage set to Higher, header dates filled, SLO2 /
> SLO3 / SLO7 / SLO11 expanded and KCs ticked, Supabase usage/case mirror
> created, and no Telegram `Bad Request` on the final report. Remaining
> operational risk: Gemini free-tier quota/high-demand fallbacks are noisy but
> recovered through configured fallback providers in the live run.

> **2026-05-26 addendum — UX polish batch (post-filed buttons).**
> This branch (`chore/telegram-bot-qa-discipline`) carries an uncommitted
> UX polish slice that responds to Moeed's latest beta-feedback evidence on
> the post-filed keyboard. See `## UX Polish Slice — Post-Filed Buttons`
> below. Offline pytest gate (`tests/` minus the e2e/live ignores) is
> green: 539 passed, 22 skipped, 3 snapshots passed. No deploy, no
> launchd restart, no push — orchestrator delivers.

> **2026-05-27 addendum — draft preview quality/layout polish.**
> Moeed's voice-test draft showed two beta-readiness issues: the draft body was
> visually sandwiched between a heavy "Why this form" block and a loud
> "Needs review" warning, and Reflective Practice Log action fields could
> repeat the same handover-improvement sentence. The current uncommitted slice
> makes draft previews output-first (compact rationale → draft body → compact
> missing-details/help), removes divider sandwiching from draft previews, and
> adds a Reflective Practice Log guard that rewrites repetitive focussing-on
> copy into a specific action plan when safely supported. Verification:
> full offline gate green at 555 passed, 22 skipped, 13 deselected, 3 snapshots
> passed. No live restart recorded in this file yet.

> **2026-05-27 addendum — saved-draft quality/button correction.**
> Moeed's Kaizen saved-draft screenshot showed the lean follow-up action was
> still wrong: `Flag a missed field` was surfaced as a primary button, while
> `Same case, another WPBA` was missing from clean partial saves. The current
> uncommitted slice removes the missed-field feedback button from primary
> post-file keyboards, shows `Same case, another WPBA` after successful and
> clean-partial saves when the original case text is available, and hardens
> Reflective Practice Log polish to fill safely supported title/date/why/
> different-outcome/focus fields for sepsis and surgical-referral reflections.
> Verification: focused Reflective Log + flow-walker tests green at 144 passed,
> 3 warnings; full offline gate green at 570 passed, 22 skipped,
> 13 deselected, 3 snapshots passed. No live restart recorded in this file yet.

> **2026-05-27 addendum — RPL dogfood UX/content polish.**
> Three changes from Moeed's latest dogfood screenshots: (1) Draft previews
> now show the actual portfolio draft first; the ℹ️ form-choice rationale moves
> to a footer after the draft body, separated by a `━━━━━━━━━━━━━━` divider,
> so users see Kaizen content before bot instruction. (2) RPL `different_outcome`
> field now guards against the absolute "No, the clinical outcome would remain the
> same" pattern for STEMI/ACS and communication-quality cases, replacing it with
> the softer framing "The clinical escalation was appropriate, but clearer
> communication may have improved patient understanding and reduced anxiety." (3)
> Post-filing success keyboard removes `👍 It worked` / `👎 Didn't work` from the
> primary keyboard; stale-callback handler retained for old messages.
> Verification: full offline gate green at 575 passed, 22 skipped,
> 13 deselected, 3 snapshots passed. No live restart recorded in this file yet.

> **2026-05-27 addendum — RPL field-specific quality regression.**
> Moeed's RUQ pain / sepsis-features voice note exposed that Reflective Practice
> Log filing still captured the clinical narrative but left safe reflective
> fields blank or repetitive in Kaizen. The current slice adds a regression for
> that exact beta case, adds an ED event-type schema option for RPL, and hardens
> RPL polishing for dual sepsis + surgical-referral reflections so title,
> event type, why, outcome/feelings, learning, and action-plan fields are
> filled where source-supported without inventing clinical facts. Verification:
> focused RPL quality test green at 15 passed; full offline gate green at
> 571 passed, 22 skipped, 13 deselected, 3 snapshots passed. No live restart
> recorded in this file yet.

> **2026-05-27 addendum — RPL event-circumstances dropdown.**
> Moeed's STEMI dogfood filing showed Kaizen's `Type of event/circumstances`
> dropdown was left blank. The current slice expands the Reflective Practice
> Log schema to the real Kaizen dropdown labels, treats source-supported acute
> EM pathways such as STEMI/cath-lab activation as `ED patient`, and adds a
> filing-layer regression that confirms the RPL event-type UUID is selected by
> label. Verification: focused RPL/dropdown tests green at 21 passed; full
> offline gate green at 579 passed, 22 skipped, 13 deselected, 3 snapshots
> passed. No live Kaizen test, launchd restart, deploy, or push.

> **2026-05-27 addendum — Kaizen header date fill regression.**
> Moeed's saved STEMI RPL screenshot showed Kaizen's required `Date occurred
> on` and `End date` header fields were still blank. The current slice routes
> the legacy filing path's date fields through the verified Angular-aware date
> filler used by the deterministic path, so header dates are clicked, selected,
> typed as `d/m/yyyy`, tabbed to trigger Kaizen watchers, and read back before
> being counted as filled. It also verifies `end_date` in the post-fill check.
> Verification: focused RPL/date tests green at 22 passed; full offline gate
> green at 580 passed, 22 skipped, 13 deselected, 3 snapshots passed. No live
> Kaizen test, launchd restart, deploy, or push.

> **2026-05-27 addendum — filing helper consistency audit.**
> Moeed asked whether other live filing fields still bypassed verified helpers.
> The current slice routes legacy-compatible select/dropdown fields through the
> verified select helper and routes legacy stage-of-training selection through
> the verified stage helper while preserving ST1/ST3/ST4-ST6 aliases. Audit
> finding: live mapped date fields now use the verified date helper in both
> deterministic and legacy-compatible paths. Remaining inline date code exists
> only in a dormant Kaizen domain-skill provider path, not the live bot route.
> Verification: focused filing tests green at 39 passed, 22 skipped; full
> offline gate green at 581 passed, 22 skipped, 13 deselected, 3 snapshots
> passed. No live Kaizen test, deploy, or push.

> **2026-05-27 addendum — same-case stale-button recovery.**
> Moeed's beta run showed old `Same case` / `See all forms` buttons could be
> tapped after the visible chat had moved on, leaving either no response or the
> blunt `filed case is no longer available here` copy. The current slice treats
> visible stale buttons as recoverable UX: if the last filed case is still in
> bot state, stale form-list callbacks restore it and keep the filed form
> excluded; if the case has genuinely expired, form selection and same-case
> shortcuts give a calm restart path. The transitional `Reusing the same case`
> message is now tracked and edited into the `Forms that fit your case` list,
> so it does not sit above the real next step. Post-save copy now says
> `Kaizen draft saved`, and the post-filing keyboard puts `Same case, new WPBA`
> beside `File another case` when both actions are available. Verification:
> focused stale-callback/post-filing tests green at 23 passed; full offline
> gate green at 585 passed, 22 skipped, 13 deselected, 3 snapshots passed. No
> live Kaizen test, launchd restart, deploy, or push.

> **2026-05-27 addendum — pre-beta QA hardening.**
> Resolved the offline pre-beta blockers identified in the latest QA pass. (1) Re-enabled and updated all 22 mock tests in `test_kaizen_filer.py` to match current filing internals, restoring coverage for legacy filer paths. (2) Cleaned `kaizen_form_filer.py` by removing legacy dead code (`_fill_stage_of_training`, `_fill_select_legacy`) and adding a safety guard in `_fill_stage` to prevent the regex fallback from unconditionally overriding a successful key/label lookup. (3) Fixed the same-case fallback edge case in `bot.py` by ignoring `chosen_form` when no successful filing has occurred. (4) Resolved the live Telethon harness mismatch by introducing a robust, polling-based `wait_for_matching_message` shared helper that correctly watches for message edits and updates in real-time. (5) Added root-level token redaction to logging to guarantee raw bot tokens are never printed or saved to local log files, and verified this behavior with a dedicated unit test in `test_smoke.py`. (6) Incorporated a non-blocking process lock in `bot.py`'s `main()` to gracefully prevent multiple concurrent polling instances. Verification: full offline pytest gate is green with 612 passed, 0 failed, 13 deselected, and 43 warnings. No live external actions.

> **2026-05-27 addendum — deterministic QA gate correction.**
> The launch call is corrected: Portfolio Guru is ready for a controlled live
> smoke, not private beta. The QA report now carries a deterministic workflow /
> button map, explicit live-smoke limits, and the remaining beta gates:
> controlled Telegram smoke, controlled Kaizen saved-draft verification, and a
> reviewed commit of this product-readiness slice. Added offline coverage for
> paused-flow recovery restoring the last filed case before rebuilding form
> recommendations, so stale callbacks cannot strand a user between same-case
> and form-selection flows. Verification: full offline gate green at
> 612 passed, 13 deselected, 43 warnings; focused flow/filer/harness/smoke gate
> green at 179 passed, 6 warnings. No live Telegram, Kaizen, deploy, push, or restart.

## Objective

Cut a private-beta-ready slice of Portfolio Guru for 3–5 trusted UK EM
trainees. No public launch, no marketing, no new supervisor surface
features. The work here is launch discipline: a written runbook, a
dogfood smoke checklist, and the carried-over supervisor guardrails the
last few slices established. The next operator should be able to push,
deploy, and dogfood without re-discovering the release path.

## Current Slice

1. `docs/PRIVATE_BETA_LAUNCH.md` is the launch runbook. It defines the
   beta boundary (3–5 trusted EM trainees, no promotion), the supported
   trainee flows (text/voice/photo → recommendation → draft → edit /
   cancel / recover → Kaizen save draft), the controlled supervisor
   scope (read-only notifications and local draft prep always safe; CBD
   save-draft only behind explicit confirmation against a disposable
   unfilled CBD ticket), the hard no-go blockers, the rollback /
   disable path for launchd and the GitHub Mac-Mini runner, the
   monitoring cadence at 30 min / 2 h / 24 h, and the verbatim
   message to send beta users.
2. `scripts/dogfood_smoke.sh` is a manual checklist. It does not touch
   Telegram, Kaizen, the LLM, or the filer. It walks the operator
   through 12 checks (service health, logs, /start, text / voice /
   photo case → draft, edit, cancel / reset, stale-button recovery,
   trainee save-as-draft, supervisor save-draft confirmation boundary,
   and a final no-submit Kaizen audit) and records pass / fail / skip
   plus a free-text note to a timestamped artefact under
   `docs/continuity/dogfood/`. `--no-record` prints the checklist
   without prompting, for review.
3. `WORKFLOWS.md` gets a single pointer up top to the launch runbook so
   the agent context surfaces the launch source-of-truth without
   wholesale reformatting.

## Done

- Launch runbook written and committed to the branch.
- Dogfood smoke script committed, `chmod +x`, `bash -n` clean, and
  `--no-record` dry-run prints the full checklist.
- `TASK.md` updated to reflect the active Private Beta Launch Cut sprint
  with carried supervisor guardrails.
- `WORKFLOWS.md` gets a single launch pointer; no broad reformatting.

## Verification

```bash
bash -n scripts/dogfood_smoke.sh
bash scripts/dogfood_smoke.sh --no-record   # prints checklist, no I/O
cd backend && source venv/bin/activate
python -m pytest tests/ -q \
  --ignore=tests/test_e2e.py \
  --ignore=tests/test_e2e_live.py
```

The pytest gate above is the same gate the launch runbook references as
the cut-line; only run it on the laptop before push, not from this
slice's documentation work.

No live Kaizen tests run in this slice. No deployment, no launchd
restart, no push, no Telegram traffic. This branch is documentation and
operator tooling only.

## Guardrails (Carried Forward)

These were established by the prior supervisor slices and must not
regress as part of the launch cut:

- `backend/assessor_writeback.execute_write_plan` runs against the live
  CDP page only when the plan is an unblocked CBD save_draft, the draft
  hash still matches, the ticket URL contains the planned ticket UUID,
  and every browser step kind is on the live allow-list
  (`{open_completion_surface, fill_field, save_draft}`). Any other
  condition raises `AssessorWriteBackUnavailable` before navigation.
- The runner clicks `Fill in` once, fills the mapped CBD assessor
  fields by label, and clicks `Save as draft` — and nothing else.
  Source-scan tests refuse Submit / Sign / Approve / Send / Reject /
  Delete locator targets in `assessor_writeback`.
- `backend/supervisor_bot.py` exposes the live runner only via
  `SUP|confirm-save-draft`, after a separate `SUP|request-save-draft`
  confirmation step that names the action and safety boundary. Open /
  Skip / Later / Review / Recapture / Cancel / Prepare-writeback /
  Request-save-draft never invoke the live runner.
- Save-draft remains CBD-only. DOPS, Mini-CEX, ESLE, QIAT, LAT, STAT,
  MSF, JCF, ACAF, ACAT assessor completion surfaces stay blocked until
  each is mapped, bound, and tested.
- Trainee filing is draft-only (`filer.py`, `browser_filer.py`,
  `filer_router.py`). No submit / sign / approve / send / reject /
  delete on any surface, for any user, in any flow.

## UX Polish Slice — Post-Filed Buttons (2026-05-26)

Uncommitted on `chore/telegram-bot-qa-discipline`. Responds to Moeed's
latest beta-feedback evidence on the keyboard the user sees after a
filing attempt.

Acceptance criteria → resolution:

1. _Return-to-primary after More options, or remove the split entirely._
   `_build_post_filing_keyboard` is now flat — there is no More-options
   drawer. Every useful follow-up sits on one keyboard. Stale
   `ACTION|post_file_more|...` callbacks from older chat history fall
   through to `handle_action_button`, which re-renders the same flat
   keyboard (no Settings, no Main-menu, no "Something missing?").
2. _Remove duplicated `📋 File another case`._ Asserted by
   `test_post_filing_keyboard_has_no_duplicate_file_another_case`: the
   button appears at most once across every (status, kwargs) combo.
3. _Drop Settings and the generic Main-menu reset from post-filed
   surfaces._ `⚙️ Settings` and `🏠 Main menu` no longer appear after
   a filing attempt. Settings remains reachable from `/settings`, the
   welcome keyboard, and `/start` — just not from the post-file follow-up,
   which used to drop the user into a "Portfolio Guru is ready" reset.
4. _Clarify or remove "Something missing?"._ Superseded on 2026-05-27:
   the missed-field feedback path is no longer a primary post-file action.
   The handler remains for stale buttons or a future feedback surface, but
   the lean saved-draft flow now prioritises opening the draft, filing the
   same case as another WPBA, or filing a new case.
5. _Reuse same case for a different WPBA._ Wired in
   `handle_action_button("same_case_another")` — it reads
   `last_filed_case_text` (the original user-submitted case text,
   set in `handle_approval_approve` before any draft mutation), excludes
   the previously filed form type, and routes through `_process_case_text`
   back to the assessment-type recommendation step. As of 2026-05-27 it is
   offered after clean partial saves as well as success. Tests lock in that
   the recommender receives the original case text — never the bot-generated
   draft body or `last_draft_preview`.

Files touched:

- `backend/bot.py` — `_build_post_filing_keyboard` rewritten flat; the
  `post_file_more` callback retained as a stale-button fallback that just
  re-renders the flat keyboard.
- `backend/tests/test_flow_walker.py` — new tests for the renamed
  pushback label, the no-duplicate invariant, the failure-path button
  absence, and the same-case-another reuse contract. Pre-existing
  assertions for the More-options drawer / Settings / Main-menu / old
  "Something missing?" label are now `not in` checks.
- `WORKFLOWS.md` — post-filing-outcome table and button-vocabulary table
  updated to match the flat keyboard, including the
  `🚩 Flag a missed field`, `🔗 Open saved draft`, and `🔗 Open Kaizen`
  entries. The "no More-options, no Settings, no Main-menu reset" rule
  is now documented under the outcome table.
- `TASK.md` — this slice.

Verification run:

```bash
cd backend && source venv/bin/activate
python -m pytest tests/ -q \
  --ignore=tests/test_e2e.py \
  --ignore=tests/test_e2e_live.py
# 539 passed, 22 skipped, 13 deselected, 3 snapshots passed
```

No live Kaizen tests, no deploy, no launchd restart, no push. Out of
scope for this slice: Kaizen/supervisor safety changes beyond honest
button labelling (carried-forward guardrails above stay intact).

## Orchestrator Hand-Off

This branch is `launch/private-beta-cut`. Local `main` is currently
**ahead of `origin/main` by 3 commits**, none of them pushed or
deployed yet:

- `8e28832 fix: restore Kaizen CDP attach for Chrome 148`
- `cd2aae0 feat: add guarded CBD save-draft live runner`
- `269446b feat: add guarded assessor writeback planning`

Plus the launch-cut docs/script added on this branch.

The orchestrator owns:

- Pushing (or PR-merging) `launch/private-beta-cut` plus the three
  prior commits to `origin/main`.
- Letting the self-hosted Mac-Mini runner deploy, then verifying via
  `launchctl print` and `/tmp/portfolio-guru-bot.log`.
- Running the dogfood smoke (`scripts/dogfood_smoke.sh`) against the
  live bot before sending the beta-user message.
- Sending the beta-user message in `docs/PRIVATE_BETA_LAUNCH.md`.
- Deciding whether to hide or keep coming-soon responses for `/bulk`,
  `/unsigned`, `/chase` during the beta window.

Until the orchestrator pushes and deploys, nothing this branch added is
live on the Mac Mini bot.
