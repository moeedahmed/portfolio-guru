# Active Task — Private Beta Launch Cut

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
