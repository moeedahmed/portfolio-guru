# Kaizen Filing — End-to-End Test Plan (2026-06-02)

**Status:** Plan landed. Phases P0–P4 already green offline. P3 read-only
live smoke green for Moeed/HST, Harris/ACCS+Intermediate dual access, and
two locally saved SAS-profile candidates. P5 controlled draft-only live
smoke is the next approval-gated phase.
**Owner:** Worker owns offline phases, plan-side checklists, doc upkeep.
Foreground orchestrator owns every live (Kaizen / Telegram / deploy /
restart / push / BWS) action.
**Branch:** `main` (local commits ahead of `origin/main`).

Related plans (canonical sources — read alongside this doc):

- `docs/roadmap/filing-reliability-readiness-sprint-2026-06.md` — promotion
  gate, per-phase ownership, P1.a–d offline slices, P4 concurrency proofs.
- `docs/roadmap/three-account-filing-validation-2026-06.md` — original
  per-shape matrix and Phase 1/2/3 boundary.
- `docs/PRIVATE_BETA_LAUNCH.md` — P6 deploy gate + beta-user message.
- `scripts/dogfood_smoke.sh` — Moeed manual checklist (already wired).

---

## 1. Why this plan exists

The orchestrator commissioned a single, restartable end-to-end testing
plan that:

1. Reconciles what is already proven (offline + live read-only) against
   what is still unproven.
2. Sequences offline → read-only → draft-preview → controlled live →
   manual judgement → fix-loop → promotion.
3. Names exactly what each phase touches, what its stop-go gate is, and
   who owns the action.
4. Stays inside the existing safety boundary: no live Kaizen writes, no
   Telegram automation, no submission, no deploy/restart/push from a
   worker session.

This doc consolidates the four-account fixture matrix (Moeed / Haris /
Sana / Ahmed) into one plan. It does **not** replace the Filing
Reliability Readiness Sprint doc; it complements it by laying out the
ordered test phases plus the Moeed manual checklist that fires only
after the next live gate.

---

## 2. Approved fixture matrix

Live credential IDs live only in the private OpenClaw/BWS registry as
Portfolio Guru aliases; never in repo docs, prompts, or tickets.

| #   | Doctor         | Portfolio shape                                                              | Local bucket(s)                                                       | Why it matters for filing tests                                                                                                                  |
| --- | -------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | Moeed          | HST (CCT pathway, ST4–ST6)                                                   | `training_level=HIGHER`, role `hst`                                   | Default development shape; ST6 superset; stage defaults to `Higher/ST4-ST6` on grouped-band WPBAs.                                               |
| 2   | Haris / Harris | DREAM Pathway junior with dual access to ACCS **and** Intermediate Portfolio | `training_level` ∈ {`ACCS`, `INTERMEDIATE`}; role `accs_intermediate` | Only fixture exercising both junior portfolios. ACCS/Intermediate are separate types; `accs_intermediate` is Harris's dual-access storage alias. |
| 3   | Sana           | SAS doctor planning CESR / Portfolio Pathway                                 | `training_level=SAS`, role `sas`                                      | Only non-training fixture. Empty-stage path on every WPBA; no `TRAINING_LEVEL_FORMS["SAS"]` key, so recommender unions the fallback catalogue.   |
| 4   | Ahmed          | Consultant / supervisor portfolio access                                     | role `assessor`; UX fallback `training_level=HIGHER`                  | Only fixture exercising assessor read paths + supervisor write-back. Trainee filing is not a normal action on this surface.                      |

> **Portfolio-type vocabulary — do not collapse.** ACCS and Intermediate
> are separate portfolio types. Harris is the dual-access edge case. SAS
> is non-training. Ahmed is non-trainee (assessor barrier). The bot's
> storage may collapse some of these for convenience (`accs_intermediate`
> → `INTERMEDIATE`, `assessor` → `HIGHER` for UX continuity), but tests
> must keep the shapes separate so a future split is loud, not silent.

---

## 3. Safety boundary (binds every phase below)

Forbidden in **any** phase of this plan, in **any** session, until the
orchestrator opens a specific gate:

- Kaizen submit / sign / approve / send / reject / delete.
- Modifying or deleting an existing live Kaizen draft we did not just
  create as part of an approved smoke.
- Telegram automation, live bot messaging, beta-user pings.
- Deploy, restart launchd, push to `origin/main`, BWS read from a worker
  context.
- Saving anything to the production `usage.db` from a smoke. All live
  smokes use a `/tmp` SQLite DB that is unlinked after the run.
- Exposing raw BWS IDs, credentials, full draft UUIDs, or real evidence
  rows in logs, tickets, or chat.

Only the foreground operator may take a live action, and only after
explicit Moeed approval **per phase, per account**.

---

## 4. Phase map

Phases run in order. A later phase does not start until the earlier one
is green, with evidence in `TASK.md`.

| Phase | What                                           | Owner                 | Live?       | Status at 2026-06-02                                                   |
| ----- | ---------------------------------------------- | --------------------- | ----------- | ---------------------------------------------------------------------- |
| P0    | Offline / unit matrix coverage                 | Worker                | Safe        | done (greenest gate)                                                   |
| P1    | Read-only portfolio mapping per fixture        | Foreground            | Live        | done for Moeed, Harris, two saved SAS-profile candidates; Ahmed scoped |
| P2    | Draft-preview bot flow without live save       | Worker + foreground   | Mostly safe | scoped here                                                            |
| P3    | Controlled draft-only live smoke per portfolio | Foreground            | Live        | **next approval gate**                                                 |
| P4    | Moeed manual product-judgement checklist       | Moeed                 | Live        | gated on P3 green                                                      |
| P5    | Post-smoke fix loop and promotion criteria     | Orchestrator + worker | Mixed       | held until P3/P4 done                                                  |

> **Phase numbering note.** The Filing Reliability Readiness Sprint uses
> P0–P6. This E2E plan reuses the conceptual ordering but compresses
> live smoke + manual judgement into P3/P4 here. The promotion gate is
> still the §1 gate from the sprint doc.

---

## 5. Phase 0 — Offline / unit matrix coverage

**Goal.** Prove every shape's contract in pure helpers before any live
Kaizen action runs.

**Scope (already landed; this doc lists them so any operator can re-run
without rediscovery):**

- `backend/tests/test_three_account_filing_matrix.py` — 22 pins:
  stage defaulter on grouped-band WPBAs (CBD/DOPS/MINI_CEX/LAT), QIAT's
  individual-year select, filer-side `STAGE_SELECT_VALUES` alignment,
  `TRAINING_LEVEL_FORMS` catalogue per shape, `TRAINING_LEVEL_LABELS`
  distinctness.
- `backend/tests/test_login_classification_per_shape.py` (P1.a) — 20
  parametrised pins covering credential-failure / infra-failure /
  auth-required / success classification across `hst`, `accs`,
  `intermediate`, `accs_intermediate_dual_access`, `sas_cesr`.
- `backend/tests/test_detected_role_training_level_mapping.py` (P1.b)
  — per-shape bucket map + raw-role storage isolation. **As of this
  plan**, also pins `assessor` → `HIGHER` UX fallback (Ahmed) and the
  raw-role / bucket decoupling that the supervisor workflow depends on.
- `backend/tests/test_form_recommender_per_shape.py` (P1.c) — SAS
  fallback union, HST superset, ACCS/Intermediate share ST3 today,
  Harris dual-access alias.
- `backend/tests/test_filing_attempt_log.py` (P1.d + P4.b) — outcome
  categorisation per shape + filing-log isolation across two users.
- `backend/tests/test_concurrent_user_isolation.py` (P4.a) — draft
  state, last-filed-case state isolation across Telegram users.
- `backend/tests/test_profile_store_kaizen_role.py` (P4.c) —
  interleaved per-user writes do not collide; Fernet credential
  isolation.
- `backend/tests/test_filing_reliability.py` (P4.d) — retry after
  simulated DOM drift reuses original saved-draft URL, surfaces
  drifted field as skipped.
- `backend/tests/test_kaizen_login_reliability.py`,
  `backend/tests/test_kaizen_sync.py`,
  `backend/tests/test_kaizen_save_confirmation.py` — bootstrap, sync,
  and save-confirmation invariants.

**Run.**

```bash
cd backend && venv/bin/python3 -m pytest tests/ -v \
  --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py
```

**Stop-go.** Exit 0 at or above the last green count
(993 passed, 3 snapshots passed at 2026-06-02). Any new `xfail` on a
promotion-critical path is a stop signal.

**Why this is enough offline.** These pins catch silent regressions on
every shape's stage default, form catalogue, login classification, and
isolation surface. Anything that flips one of these shows up in CI
before a live smoke can mask the change.

---

## 6. Phase 1 — Read-only portfolio mapping per fixture

**Goal.** Verify the bot can authenticate as each fixture in the managed
CDP Chrome and read at least one indexed evidence row, without writing
or filing anything.

**Owner.** Foreground operator. Worker prepares the script invocation
text and recovery procedure (this section); worker does not run it.

**Procedure (per account, repeated for all four fixtures).**

1. Foreground exports the Portfolio Guru BWS credential for the fixture
   into the managed Chrome session on `localhost:18800`. Worker does
   **not** read BWS.
2. Run the existing read-only sync helper against a temporary SQLite
   DB. Worker leaves the command here so the operator does not have to
   reconstruct it:

   ```bash
   PORTFOLIO_GURU_USAGE_DB=$(mktemp -t pg-readonly-XXXXXX.db) \
   venv/bin/python -c "
   import asyncio, os
   from kaizen_sync import sync_kaizen_portfolio_index_for_user
   user_id = int(os.environ['PG_FIXTURE_USER_ID'])
   asyncio.run(sync_kaizen_portfolio_index_for_user(user_id))
   "
   ```

   `PG_FIXTURE_USER_ID` is the Portfolio Guru Telegram user id for the
   saved-credentials profile (recorded in the private secret registry,
   not in this doc).

3. Foreground records the outcome:
   - Rows seen (raw count from the managed page walk).
   - Rows indexed in the `/tmp` DB.
   - `index_runs` status — must be `ok`, never `failed`, never silently
     `auth_required` left untreated.
   - Visible title of one sample row (no PHI; redact the patient
     identifier if any).

4. Foreground **unlinks the `/tmp` DB** after recording the outcome so
   no real evidence row content is retained on disk.

**Status today.**

| Fixture            | Latest read-only outcome                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Moeed / HST        | `ok`, 22 rows seen, 21 indexed (2026-06-02).                                                                                                                                                                                                                                                                                                                                                                      |
| Harris / dual      | `ok`, 21 rows seen, 21 indexed (2026-06-02).                                                                                                                                                                                                                                                                                                                                                                      |
| Sana / SAS-CESR    | Two locally saved SAS-profile candidates `ok` (15/13, 29/29). If "Sana" refers to a Telegram identity outside those candidates, that identity needs its own mapping pass.                                                                                                                                                                                                                                         |
| Ahmed / consultant | **not yet run.** Expected outcome: role-detector returns `assessor`, sync writes an `index_runs` row but the read walk will be minimal because pure-supervisor accounts cannot create events. Sample sanity gate: classify body text "You cannot create any events!" as assessor in `test_role_detector.py` already pins the path; the live smoke just confirms the live Kaizen surface matches that expectation. |

**Stop-go.** All four fixtures return `ok`. Sana's "different Telegram
identity" gap is either resolved (the saved candidates ARE the correct
account) or explicitly scoped out before P3.

---

## 7. Phase 2 — Draft-preview bot flow without live save

**Goal.** Prove the in-bot flow from raw case input → form recommendation
→ draft preview is clean for every shape, **without** saving a Kaizen
draft.

This phase is mostly offline. It can be done by the worker against the
fixture matrix using the existing flow-walker tests and the
`bot_simulator` harness; it can also be done live (foreground) against
the dev bot if Moeed wants to eyeball the wording per shape.

### Worker side (safe; can run now)

- Confirm `backend/tests/test_flow_walker.py` is green at the current
  count.
- Confirm `backend/tests/test_vnext_draft_preview.py` is green.
- Confirm draft preview for each fixture's `training_level` does not
  expose internal codes (`PROC_LOG`, etc.) to the user surface — pinned
  by `backend/tests/test_user_visible_form_names.py`.
- Confirm the post-filing keyboard regression suite is green
  (`test_flow_walker.py::test_post_filing_keyboard_*`).
- Confirm draft footer divider rules + RPL polish guards are green
  (`test_reflect_log_quality.py`).

### Live (foreground, optional dev-bot dogfood)

For each fixture, against the dev bot **only**:

1. `/start` → pick the matching profile from the picker (or let
   detection set it).
2. Send a synthetic text case (no PHI; e.g. "60M chest pain → STEMI,
   activated cath lab, learning about door-to-balloon").
3. Confirm the recommended forms list contains only the shapes' allowed
   forms (HST superset, ACCS/Intermediate ST3 catalogue, SAS fallback
   union, consultant: trainee filing is not the intended path here).
4. Open the draft preview.
5. **Do not tap Save as draft.** Use `/reset` or `Cancel` and capture a
   screenshot of the preview for Moeed's manual review later.

**Stop-go.** Each fixture's draft preview renders without:

- Mid-word truncation in the description summary.
- Internal form codes (`PROC_LOG`, `REFLECT_LOG`) in user-facing copy.
- A fabricated training-year string for Sana on grouped-band WPBAs.
- Duplicate `File another case` buttons on post-filing keyboards.

---

## 8. Phase 3 — Controlled draft-only live smoke per portfolio

**Goal.** Save exactly one synthetic Kaizen draft per fixture, verify it
is visible in Kaizen, then delete the draft by hand. No submission.

**Owner.** Foreground operator. Worker prepares the checklist and the
acceptance evidence template; worker does not execute.

**Approval required.** Moeed explicit per-fixture approval. P3 is the
next live gate; do not run more than one fixture at a time.

### Per-fixture checklist (foreground)

For each fixture in order (Moeed → Harris/ACCS → Harris/Intermediate →
Sana → Ahmed):

1. **Pre-flight.**
   - `git status` clean; on `main`; `bash scripts/preflight.sh` exit 0.
   - Offline gate at or above the last green count.
   - `launchctl print system/com.portfolioguru.bot | head -25` shows
     the production bot is the **current** code or a **frozen-known-good**
     build; if the bot is mid-deploy, wait.
   - Confirm Telegram bot identity (production vs dev) before sending
     anything — the smoke uses the dev bot unless Moeed explicitly
     names production.

2. **Authenticate the fixture in managed Chrome on `localhost:18800`.**
   - Foreground operator exports the BWS credential into the session.
   - For Ahmed: the post-login landing is the assessor surface; trainee
     filing is **not** the action under test. For Ahmed, P3 instead
     exercises the supervisor save-draft confirmation boundary against
     a disposable / unfilled CBD ticket the supervisor controls (see
     `scripts/dogfood_smoke.sh` ask 11). Stop without invoking the
     live runner — tap Cancel on the confirmation step.

3. **Trainee fixtures (Moeed, Harris-ACCS, Harris-Intermediate, Sana):**
   - From the dev bot, send a synthetic text case with no PHI.
   - Pick the form Moeed names (default: CBD).
   - Open the draft preview, then tap `Save as draft`.
   - Wait for `✅ … saved.` confirmation with `Open saved draft`.
   - Open Kaizen in the browser. Confirm the draft exists. Record:
     - Account shape.
     - Form type used.
     - Draft URL **prefix only** (host + path up to the UUID; do not
       record the full UUID in repo docs).
     - Timestamp.
   - **Delete the draft by hand** from Kaizen. Do **not** submit, sign,
     send, approve, reject, or assess.

4. **Ahmed (supervisor fixture):**
   - Foreground sends a synthetic CBD-ticket notification to the
     supervisor surface (or uses an existing disposable unfilled CBD
     ticket).
   - Tap `Open` in Telegram, capture a synthetic feedback intent,
     tap `Prepare Kaizen action plan (no write)`, then
     `📤 Save draft in Kaizen`. The separate confirmation must appear.
   - **Tap Cancel.** No Kaizen write. Confirm the supervisor session is
     preserved.
   - Only if Moeed explicitly approves the live `Yes` path on this
     fixture: tap `Yes`, confirm a CBD draft is saved on the supervisor
     ticket, then delete it by hand.

5. **Record outcome as a TASK.md addendum** naming:
   - Fixture, form type, outcome, draft URL prefix, no PHI.
   - The deletion confirmation step.
   - Filing log row (`filing_attempt_log.ndjson`) for the synthetic
     attempt — the `synthetic` flag should be `False` (this is a real
     user id) but the attempt must still show `SAVE_SUCCESS` or the
     correct partial bucket.

### Stop-go for P3

All four fixtures green: one synthetic draft saved, verified in
Kaizen, deleted. No submission. Outcome rows recorded in TASK.md.

If a fixture fails, **do not** retry the same fixture in the same
session. Land the fix loop (Phase 5) first, then re-approve P3 for that
fixture only.

---

## 9. Phase 4 — Moeed manual product-judgement checklist

**Goal.** Moeed eyeballs the bot end-to-end for product quality once P3
is green. This is the human gate that the offline tests cannot replace.

**Trigger.** P3 green for at least the fixtures Moeed wants to ship.
Run-only-after-P3 — never before, because P4 involves saving real
drafts on Moeed's own account and testing across multiple flows.

**Surface.** Telegram against the **dev bot** for everything except the
production smoke noted in step 9 below.

> **Detailed checklist intentionally lives below P3, not at the top of
> the doc.** This is the manual gate Moeed runs **after** the worker
> finishes prep. Listing it earlier would invite running it before P3.

### Checklist

1. `/start` shows the welcome bubble + the expected keyboard within
   a few seconds. No raw engine state names leak ("AWAIT_GATHERING",
   etc.).
2. Chat-only side traffic (greetings, "how are you", "what can you
   do") gets sensible chat replies. No case-collection nudge for
   non-clinical text.
3. Text case → recommendation → draft preview. Preview is
   output-first: draft body comes before the rationale footer, no
   heavy divider sandwiching, no internal codes.
4. Voice note case → ack → transcript → recommendation → preview.
   Transcript is grounded; no fabricated clinical details.
5. Photo of clinical notes → recommendation → preview. NOT_CLINICAL
   on placeholder images is acceptable.
6. Document (DOCX/PDF) attachment → cached → handed to the filer →
   the attachment is uploaded to Kaizen as part of save-draft (live
   only; do not run unless P3 includes attachment coverage).
7. `Edit` flow updates a field cleanly; preview redraws once;
   original keyboard reappears.
8. `Cancel` / `/reset` cleanly returns to idle; no orphan keyboards.
9. Stale callback recovery — wait ~45 s, tap an old button; the bot
   recovers with the correct flat keyboard.
10. `Save as draft` on a synthetic CBD case for Moeed's HST profile.
    Kaizen draft saves, the post-save keyboard shows
    `Open saved draft`, `Amend this draft`, `Same case, new WPBA`,
    `File another case`. **Delete the draft by hand.**
11. Settings → Portfolio Health is the primary CTA; sync row reads
    "syncing now" while a real sync is running, "sync timed out"
    after 30 minutes, never lies about state.
12. `/filingreport` (admin-only) returns a non-empty report within
    the smoke window and excludes the synthetic test traffic by
    default.

### What Moeed is judging that tests cannot

- Does the wording sound like the user's own voice (no AI slop)?
- Does the preview show the right level of clinical specificity for
  the case?
- Does the form recommendation feel obvious in hindsight, not
  random?
- Does the post-save copy invite the right next action without
  pushing a wrong default?
- Does the supervisor confirmation boundary feel safe to tap on a
  real consultant account?

**Stop-go for P4.** Moeed approves each item or files a fix ticket.
Failed items go into Phase 5; the rest of the gate stays held.

---

## 10. Phase 5 — Post-smoke fix loop and promotion criteria

**Goal.** Close any gap surfaced by P3 / P4 without weakening the
promotion bar.

### Fix-loop policy

- Each gap gets its own task branch, its own offline pin, and a
  TASK.md addendum naming the fixture and the failure mode it
  prevents.
- A fix that touches the filer (`filer.py`, `browser_filer.py`,
  `filer_router.py`, `kaizen_form_filer.py`) must come paired with
  the offline test that would have caught the regression, and the
  full offline gate must re-green before the fix lands.
- A fix that touches the assessor surface (`assessor_writeback.py`,
  `supervisor_bot.py`, `supervisor_workflow.py`, related schemas)
  must keep the existing `SUP|request-save-draft` →
  `SUP|confirm-save-draft` two-step confirmation and the
  `AssessorWriteBackUnavailable` refusal path; pinned by
  `backend/tests/test_assessor_writeback.py`.

### Promotion criteria (rolls forward from the sprint doc §1)

Promotion to widen the trusted-tester pool requires **every** line:

1. Full offline gate exit 0 at or above the prior green count.
2. Three-account offline matrix exit 0.
3. P1 fixture/dry-run slices a–d landed and green (done).
4. P4 concurrency/idempotency offline proofs landed and green (done).
5. Per-fixture read-only live smoke `ok` for all four fixtures.
6. Per-fixture controlled draft-only live smoke `ok` for the
   trainee fixtures, plus the supervisor confirmation-boundary smoke
   `ok` for Ahmed.
7. Moeed manual checklist (Phase 4) passes.
8. `filing_attempt_log` shows the smoke attempts and no untreated
   `EXCEPTION` / `TIMEOUT` rows on the relevant accounts.
9. Deploy smoke green per `docs/PRIVATE_BETA_LAUNCH.md`.

If any line is missing or red, the plan stays in the phase that
produced the gap. Workers do not self-clear promotion; the
orchestrator flips the gate.

---

## 11. Manual checklist (for Moeed, fires only after P3 green)

This is the short-form version of §9, sized for a phone or printout.
**Do not run before P3 has saved-and-deleted a draft for each
fixture.**

- [ ] `/start` welcomes me; keyboard is the expected one.
- [ ] Greetings and feature questions get chat replies, not case
      prompts.
- [ ] Text case → recommendation → draft preview, output-first,
      no internal codes.
- [ ] Voice note case → grounded transcript → preview.
- [ ] Photo case → preview (or clean NOT_CLINICAL for placeholders).
- [ ] Document attachment is acknowledged and queued for upload.
- [ ] `Edit` updates one field cleanly; preview redraws once.
- [ ] `Cancel` and `/reset` return to idle cleanly.
- [ ] Stale-button recovery works after ~45 s.
- [ ] Save-as-draft on my HST CBD case lands in Kaizen; I delete it
      by hand.
- [ ] Settings → Portfolio Health is the primary action; sync row
      tells the truth about state.
- [ ] `/filingreport` returns a non-empty report and excludes
      synthetic traffic.
- [ ] Supervisor save-draft confirmation appears, `Cancel` leaves
      Kaizen untouched, `Yes` (if I choose to exercise it) writes
      one CBD draft to a disposable ticket and I delete it.
- [ ] No submit / sign / send / approve / reject / delete happened on
      Kaizen.

---

## 12. Verification (for this plan artefact)

This document does not change runtime behaviour beyond the small new
offline pin in
`backend/tests/test_detected_role_training_level_mapping.py` (the
assessor → `HIGHER` UX fallback + raw-role/bucket decoupling pin for
Ahmed's consultant fixture).

```bash
# Confirm cross-references resolve.
test -f docs/roadmap/three-account-filing-validation-2026-06.md
test -f docs/roadmap/filing-reliability-readiness-sprint-2026-06.md
test -f docs/PRIVATE_BETA_LAUNCH.md
test -f scripts/dogfood_smoke.sh
test -f backend/tests/test_three_account_filing_matrix.py
test -f backend/tests/test_login_classification_per_shape.py
test -f backend/tests/test_detected_role_training_level_mapping.py
test -f backend/tests/test_form_recommender_per_shape.py
test -f backend/tests/test_filing_attempt_log.py
test -f backend/tests/test_concurrent_user_isolation.py
test -f backend/tests/test_profile_store_kaizen_role.py
test -f backend/tests/test_filing_reliability.py

# Re-run the focused matrix the orchestrator pinned at promotion §1.
cd backend && venv/bin/python3 -m pytest \
  tests/test_three_account_filing_matrix.py \
  tests/test_login_classification_per_shape.py \
  tests/test_detected_role_training_level_mapping.py \
  tests/test_form_recommender_per_shape.py \
  tests/test_profile_store_kaizen_role.py \
  tests/test_kaizen_login_reliability.py -v

# Sanity on tracked-file hygiene.
git diff --check
```

No live Kaizen, no Telegram, no BWS, no deploy, no restart, no push
in this slice.

---

## 13. Status snapshot (this commit)

| #   | Item                                                              | Status                                               |
| --- | ----------------------------------------------------------------- | ---------------------------------------------------- |
| 1   | This consolidated E2E plan exists and links the three sprint docs | done (this commit)                                   |
| 2   | Offline gate + three-account matrix green                         | done (993 passed, 3 snapshots at last full run)      |
| 3   | P1.a–d slices landed and green                                    | done                                                 |
| 4   | P4.a–d concurrency/idempotency proofs green                       | done                                                 |
| 5   | Read-only live smoke green: Moeed, Harris dual, two saved SAS     | done                                                 |
| 6   | Ahmed read-only mapping pass                                      | **scoped here**, foreground-owned, not yet run       |
| 7   | Assessor → `HIGHER` UX fallback pin + raw role decoupling         | done (this commit)                                   |
| 8   | P3 controlled draft-only live smoke                               | **next approval gate**                               |
| 9   | P4 Moeed manual checklist                                         | gated on P3                                          |
| 10  | Promotion criteria documented in one place                        | done (this commit; rolls forward from sprint doc §1) |

**Next executable gate (recommended next human approval):** P3
controlled draft-only live smoke against Moeed/HST first, then
Harris/ACCS, then Harris/Intermediate, then Sana/SAS, then Ahmed's
supervisor confirmation-boundary smoke. One fixture at a time, fix-loop
in between, no submission.
