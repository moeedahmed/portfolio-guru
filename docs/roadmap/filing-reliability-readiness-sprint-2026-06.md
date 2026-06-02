# Filing Reliability Readiness Sprint — 2026-06-02

**Status:** Plan landed; P0 evidence review complete; P1 fixture/dry-run
slices (P1.a → P1.d) landed offline; P4 concurrency / idempotency offline
proofs landed; P2/P3 Sana credential recovery + read-only smoke is the next
live/credential-gated step (foreground operator); P5/P6 remain gated.
**Owner:** Foreground orchestrator owns all live, push, deploy, restart phases.
Workers (Claude / Codex) own P0 review, P1 fixture/dry-run code, P4 offline
concurrency proofs, instrumentation code, and doc updates.
**Branch:** `main` (local commits ahead of `origin/main`; orchestrator decides
push cadence per `docs/PRIVATE_BETA_LAUNCH.md`).

Related plans:
`docs/roadmap/three-account-filing-validation-2026-06.md` (P0/P3 input),
`docs/PRIVATE_BETA_LAUNCH.md` (P5/P6 deploy gate),
`backend/filer_router.py` (single filing entry point).

---

## Executive summary (for the orchestrator to relay to Moeed)

Filing is the USP. Before promotion, the sprint must prove — with evidence,
not vibes — that the bot files cleanly across the portfolio types our
trusted-tester pool covers (HST, ACCS, Intermediate, SAS / CESR Portfolio
Pathway, plus the ACCS + Intermediate dual-access edge case that Harris
exercises), recovers credibly from the failure modes we already know about
(login lapse, partial save, Kaizen DOM drift), and does not corrupt state
when two trusted-tester accounts file at the same time.

> **Portfolio-type terminology — read this before reading the rest of the plan.**
> ACCS and Intermediate are **separate portfolio types** on Kaizen, not a
> single collapsed shape. Harris is the dual-access edge case: one trainee
> with access to both ACCS _and_ the Intermediate Portfolio. The bot's
> current storage collapses dual access into a single `accs_intermediate`
> Kaizen role / `INTERMEDIATE` `training_level` bucket — that is an
> implementation/storage behaviour that must be tested, **not** a product
> truth. HST (Moeed) and SAS / CESR Portfolio Pathway (Sana) are further
> distinct types. Several Kaizen differences between these types are still
> unconfirmed; the plan and its tests must avoid asserting more than the
> evidence proves.

Today the offline gate is green (933 tests passing on the last full run), the
three-account matrix is codified, and the live read-only smoke is green for
Moeed/HST and for Harris's ACCS + Intermediate dual-access account.
Sana / SAS-CESR is blocked at `auth_required` — that is the single biggest
unproven branch and is the critical-path blocker for the promotion gate.

The promotion gate is **draft-only by policy** for trainees. Real submission
is explicitly out of scope. Promotion means the bot is safe enough to widen
the trusted-tester pool, not that the trainee-side filing path is allowed to
submit on a doctor's behalf.

Phases run in order, each with an explicit stop/go gate. P0 evidence review
runs first (cheap, repeatable, can be redone by any worker). P3 live smoke
and P5 deploy/restart smoke require Moeed's per-phase approval.

---

## 1. The confidence bar — exact promotion criteria

Filing may be promoted to "USP-grade, widen the trusted-tester pool" when
**every line** below is true, with evidence linked.

1. **Offline gate is green.** Last run: `cd backend && venv/bin/python3 -m
pytest tests/ -v --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py`
   on the candidate commit returns exit 0 with at least the count we had at
   the start of the sprint (933 passed, 13 deselected on 2026-06-02). No new
   `xfail` covering a regression on a promotion-critical path.
2. **Three-account offline matrix is green.**
   `cd backend && venv/bin/python3 -m pytest
tests/test_three_account_filing_matrix.py
tests/test_profile_store_kaizen_role.py
tests/test_kaizen_login_reliability.py -v` returns exit 0 on the candidate
   commit. Any `xfail` here must be a deliberately pinned gap with a TASK.md
   entry, not silent rot.
3. **P1 fixture/dry-run slice has landed and is green** (see §4 for the four
   slices). The slice covers: stage defaulter → filer lookup → form catalogue
   round-trip per shape, login classification per detected role, `kaizen_role`
   ↔ `training_level` round-trip, and the recommended-form fallback for SAS.
4. **Per-account read-only live smoke is green.** Moeed (HST) and Harris
   (ACCS + Intermediate dual access) are already recorded as `ok`
   (2026-06-02 TASK.md addendum). Sana (SAS / CESR Portfolio Pathway) must
   also be `ok` — `auth_required` is not acceptable for promotion. Recovery
   path documented in §6.
5. **Per-account controlled draft-only live smoke is green** for the three
   accounts. One synthetic case per account, one form per account, one draft
   saved, draft visible in Kaizen, draft deleted by hand. No submission.
   Recorded as a TASK.md addendum naming the account shape, form type, draft
   URL prefix (no full UUID), and the pass gate. See §7.
6. **Concurrency / idempotency proofs land in offline form** (see §8). At
   minimum: two-user simultaneous draft creation does not cross-contaminate
   filing logs, profile store, or filing-coverage records. Telegram
   per-`user_id` `PicklePersistence` slots stay isolated.
7. **Instrumentation is present and used** (see §9). `filing_attempt_log`
   records every attempt with `user_id`, `form_type`, outcome category, and
   skipped/filled fields. `/admin filing_report` (or the existing equivalent
   in `backend/filing_attempt_log.py`) is reachable and returns a non-empty
   report within the smoke window.
8. **Deploy / restart smoke gate is green** after the candidate commit lands
   on `origin/main` (see §10). `launchctl print system/com.portfolioguru.bot`
   shows the new PID and start time, `/tmp/portfolio-guru-bot.log` shows a
   clean boot, and `scripts/dogfood_smoke.sh` returns exit 0 against the live
   bot before any beta-user message is sent.
9. **No carried-forward known critical bug** on the filing path that fires on
   trainee usage of the three account shapes. "Known and pinned with a
   visible test" is allowed; "known and we have not decided what to do" is
   not.

If any one line is missing or red, the sprint stays in the phase that
produced the gap. Workers cannot self-clear the bar; the orchestrator is the
only one who flips P6.

---

## 2. Proven vs not proven (evidence snapshot as of 2026-06-02)

### Proven

- **Full offline backend gate:** 933 passed, 13 deselected
  (`tests/ -q --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py`).
  Recorded in the 2026-06-02 TASK.md addendum.
- **Three-account offline matrix:** `test_three_account_filing_matrix.py`
  ships 22 pins covering stage defaulter on grouped-band WPBAs
  (CBD/DOPS/MINI_CEX/LAT), QIAT individual-year select, filer-side
  `STAGE_SELECT_VALUES` alignment, `TRAINING_LEVEL_FORMS` per shape, and
  the `TRAINING_LEVEL_LABELS` distinctness guard.
- **Filing routing discipline test surface exists.**
  `tests/test_filing_reliability.py` pins: normal filing does not reuse old
  drafts; explicit retry does; failure recovery exits exist; DOM-mapped forms
  never escalate to browser-use; tracked artefacts (`filing_coverage.json`,
  `dom_learning_log.json`, `kaizen_form_filer.py`) are not mutated by
  ordinary tests; alias routing keeps ESLE / Mini-CEX on the deterministic
  path.
- **Filing attempt logging exists.** `backend/filing_attempt_log.py` records
  attempts with outcome categories, separates synthetic user 99999999 from
  real-user counts, and exposes an admin report. Backed by
  `tests/test_filing_attempt_log.py`.
- **Read-only live smoke landed for two of three accounts.** Moeed (HST) and
  Harris (ACCS + Intermediate dual access): `ok`, indexed rows in a `/tmp`
  evidence DB (overwritten and unlinked after the run). Recorded in TASK.md
  2026-06-02.
- **Portfolio Health pathway-aware** (orthogonal but adjacent): trainee
  header now correctly frames ARCP as a checkpoint inside Training (CCT),
  not a pathway. Four new guards reject regression to "Training (ARCP)" /
  "ARCP pathway" / "ARCP Health" framings.

### Not proven

- **Sana/SAS-CESR live read-only smoke:** `auth_required` — the bot's CDP
  session did not land on a portfolio page. We do not yet know whether the
  blocker is wrong saved credentials, expired Kaizen session, MFA prompt, or
  a SAS-specific landing-page divergence. Until this is resolved, the SAS
  branch of `filer_router` and `kaizen_form_filer` is only proven by offline
  fixtures, not by a real Kaizen response.
- **Per-account controlled draft-only live smoke** for any of the three
  accounts. The smoke that ran on 2026-06-02 was read-only by design.
- **P1 dry-run / fixture coverage for the four slices in §4.** Scoped in
  `docs/roadmap/three-account-filing-validation-2026-06.md` Phase 2, queued
  here.
- **Concurrent two-user filing.** No offline proof yet that two
  trusted-tester accounts filing simultaneously do not cross-contaminate
  filing logs, profile state, or filing coverage. The existing tests cover
  single-user happy paths plus retry semantics.
- **Idempotent retry semantics under real Kaizen DOM drift.**
  `test_filing_reliability.py` pins the policy; live drift has not been
  re-tested since the last Kaizen UI change.
- **Deploy-time smoke against a freshly restarted bot.** `dogfood_smoke.sh`
  exists; the 2026-06-02 evidence is local-only and predates any restart.
- **Instrumentation visibility under live load.** The admin report path is
  tested; its content under real beta usage is not yet seen end-to-end.

---

## 3. Phases — order, gates, and ownership

Each phase has a stop/go gate. A phase does not start until the gate of the
previous phase is green, with evidence linked in TASK.md.

| Phase | What                                              | Who runs it          | Stop/go gate                                                                                                                          | Live?      |
| ----- | ------------------------------------------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| P0    | Evidence review — re-prove the offline gate       | Worker               | Offline gate exit 0; three-account matrix exit 0; no `git diff` against tracked artefacts; instrumentation paths exist                | Safe       |
| P1    | Fixture / dry-run coverage for the 4 slices in §4 | Worker               | New tests added and green; existing tests stay green; `git diff --check` clean                                                        | Safe       |
| P2    | Sana credential / session recovery (offline-only) | Foreground operator  | Sana login classification has a reproducible offline test; recovery procedure documented; no BWS read from worker context             | Foreground |
| P3    | Per-account read-only live Kaizen smoke           | Foreground operator  | All three accounts return `ok`, indexed rows ≥ 1, no draft created, no Kaizen save, no Telegram traffic, no production `usage.db`     | Live       |
| P4    | Concurrency / idempotency offline proofs          | Worker               | New offline tests cover two-user simultaneous draft creation, profile isolation, filing-log isolation, retry-after-DOM-drift recovery | Safe       |
| P5    | Per-account controlled draft-only live smoke      | Foreground operator  | One synthetic draft per account saved, visible in Kaizen, deleted by hand. No submission. Recorded in TASK.md                         | Live       |
| P6    | Deploy / restart / production smoke               | Foreground operator  | Push lands on `origin/main`; launchd restart shows new PID; `scripts/dogfood_smoke.sh` exit 0; no error spike in 30 min               | Live       |
| —     | Promotion gate                                    | Moeed (orchestrator) | All §1 criteria green; no carried-forward critical filing bug; pool can be widened                                                    | Decision   |

The promotion gate is a decision, not a phase. The orchestrator flips it
based on the evidence trail; a worker cannot.

---

## 4. P1 — what Claude / Codex should build first

This is the next executable slice. It is offline-only, fixture-driven, and
should land in one PR-sized commit on a task branch (per `preflight.sh`).

The four slices below are scoped from
`docs/roadmap/three-account-filing-validation-2026-06.md` Phase 2.

### Slice P1.a — per-shape login classification round-trip

**Goal.** Prove that login outcome classification (`credential failure` vs
`infra failure` vs `success`) does not silently degrade based on the
detected Kaizen role.

**Files.**

- Create: `backend/tests/test_login_classification_per_shape.py`
- Reuse fixtures from: `backend/tests/test_kaizen_login_reliability.py`,
  `backend/tests/test_three_account_filing_matrix.py`
- Stub provider: same style as `test_kaizen_login_reliability.py` (stubbed
  `KaizenProvider`, fake `_run_file` URLs, no CDP, no Kaizen).

**Acceptance criteria.**

- For each of the following portfolio shapes — kept **separate** so a
  silent collapse is loud, not silent:
  - `hst` (Moeed)
  - `accs` (ACCS-only Kaizen role)
  - `intermediate` (Intermediate-only Kaizen role)
  - `accs_intermediate_dual_access` (Harris; one trainee whose Kaizen
    profile carries **both** ACCS and Intermediate access — stored today
    as the single `accs_intermediate` role / `INTERMEDIATE` `training_level`
    bucket; the test pins that storage behaviour, it does **not** assert
    `accs_intermediate` is a standalone portfolio type)
  - `sas_cesr` (Sana; SAS / CESR Portfolio Pathway — provider returns the
    `sas` portfolio_type today)
- For each shape:
  - A simulated 401-on-login (provider `connect()` returns `False`) is
    classified as `credential_failure` (the bot wrapper returns `False`),
    not `infra_failure`.
  - A simulated browser-harness / CDP failure (provider raises
    `KaizenInfrastructureError`) propagates as the same error type, not
    silently downgraded to a `False` return.
  - A simulated landing on a non-portfolio page (the Sana shape we hit on
    2026-06-02; bootstrap `_login_kaizen_page` returns `False` after a
    valid-looking attempt) is classified as `auth_required` by
    `sync_kaizen_portfolio_index_for_user`, not `ok` and not `failed`.
  - A simulated dashboard landing classifies as `success` and the
    provider's `portfolio_type` binds to the role string that shape produces
    (`hst`, `accs`, `intermediate`, `accs_intermediate`, `sas`).
- Each assertion names the shape in the test ID (via `pytest.mark.parametrize`
  `ids=...`) so a future regression surfaces the shape, not just a generic
  failure.
- The test file uses the same offline-stub style as
  `test_kaizen_login_reliability.py` and the bootstrap stub style used in
  `test_kaizen_sync.py` (`_open_kaizen_session_page`,
  `_restore_cached_session`, `_load_user_credentials`, `_login_kaizen_page`).
  No live Kaizen, CDP, BWS, Telegram, or network.

**Run.**

```bash
cd backend && venv/bin/python3 -m pytest \
  tests/test_login_classification_per_shape.py -v
```

### Slice P1.b — detected-role → `training_level` mapping per shape

**Goal.** Lock in that detected Kaizen roles map to the right local
portfolio-profile bucket without pretending that the bucket is the portfolio
truth. `store_kaizen_role(...)` stores the raw detected role only; the
Telegram setup/login path applies the detected-role → `training_level` map.
That distinction matters because ACCS and Intermediate are separate Kaizen
portfolio types, while Harris's dual-access account currently surfaces as
the `accs_intermediate` role and is stored in the `INTERMEDIATE` bucket.

**Files.**

- Create: `backend/tests/test_detected_role_training_level_mapping.py`
- Reuse: `backend/tests/test_profile_store_kaizen_role.py` helpers and the
  setup/login-path role-map surface in `backend/bot.py`.

**Acceptance criteria.**

- `store_kaizen_role(user_id, role)` preserves raw roles for `hst`, `accs`,
  `intermediate`, `accs_intermediate`, and `sas` without mutating
  `training_level`; this keeps role detection and portfolio profile storage
  separate.
- The setup/login role map keeps ACCS-only and Intermediate-only distinct:
  `accs -> ACCS`, `intermediate -> INTERMEDIATE`, `hst -> HIGHER`,
  `sas -> SAS`.
- Harris's dual-access role is explicitly pinned as current implementation
  behaviour: `accs_intermediate -> INTERMEDIATE`, with a test/doc comment
  saying this is the dual-access storage alias, not a standalone portfolio
  type.
- A round-trip across all shapes in a single test database does not
  cross-contaminate (each `user_id` keeps its own raw role and profile
  level).

### Slice P1.c — recommended-form fallback does not leak HST-only forms to SAS

**Goal.** Catch the regression where the form catalogue falls through to the
legacy `ST5` superset when the shape is SAS, silently offering forms the SAS
shape cannot stage.

**Files.**

- Create: `backend/tests/test_form_recommender_per_shape.py`
- Use the same form-recommender entry point as `_run_form_recommender` in
  `backend/bot.py`. If the entry point is awkward to test directly, expose
  a thin pure helper for the SAS branch — do not refactor the bot more than
  necessary.

**Acceptance criteria.**

- The recommended-form output for a SAS profile, on a clinical case that
  would naturally route to CBD, does **not** include any
  `*_2021_legacy_ST5_only` form ids.
- The output explicitly includes the CESR core WPBAs we expect SAS to use
  (confirm list against `TRAINING_LEVEL_FORMS["SAS"]` and the unknown-default
  union; if `TRAINING_LEVEL_FORMS["SAS"]` is empty, this test pins the
  fallback union behaviour as the contract).
- The output for HST, ACCS-only, Intermediate-only, and Harris's
  `accs_intermediate` storage bucket stays as the existing pinned contract
  (`tests/test_three_account_filing_matrix.py` is the canonical pin; this
  test cross-checks the recommender, not the catalogue itself).

### Slice P1.d — partial-save outcome categorisation per shape

**Goal.** Prove that the `filing_attempt_log` outcome categories
(`SAVE_SUCCESS`, `PARTIAL_SAVE`, the failure shapes) bucket correctly for
each shape's typical partial pattern: SAS / CESR has no stage option,
ACCS-only auto-populates the ACCS band, Intermediate-only auto-populates the
Intermediate band, Harris's dual-access storage alias auto-populates the
Intermediate band today, and HST has the full superset.

**Files.**

- Extend: `backend/tests/test_filing_attempt_log.py`
- Or, if the file grows past readable: add a focused
  `backend/tests/test_filing_outcome_categories_per_shape.py`.

**Acceptance criteria.**

- A SAS-shape filing where the stage field was deliberately skipped is
  categorised `PARTIAL_SAVE` with `skipped == ["stage"]`, not
  `SAVE_SUCCESS`.
- An ACCS-only filing where the stage field auto-populated to
  `ACCS ST1-ST2/CT1-CT2` is categorised `SAVE_SUCCESS` with `skipped == []`.
- An Intermediate-only filing where the stage field auto-populated to
  `Intermediate` is categorised `SAVE_SUCCESS` with `skipped == []`.
- An `accs_intermediate` dual-access filing where the stage field
  auto-populated to `Intermediate` is categorised `SAVE_SUCCESS` with
  `skipped == []`.
- An HST filing with the full superset filled is `SAVE_SUCCESS`.
- The admin report formatter (`format_admin_report`) surfaces the per-shape
  outcomes in a way that lets the operator see "SAS is partial-saving stage"
  at a glance.

### P1 run + commit checklist

```bash
bash scripts/preflight.sh   # branch + clean tree gate
cd backend && venv/bin/python3 -m pytest tests/ -v \
  --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py
git diff --check
```

P1 lands only when all four slices are green, the full offline gate is still
green at the new test count, and `git diff --check` returns clean.

---

## 5. Live read-only and draft-only smokes — exactly what is allowed

The boundary in `docs/roadmap/three-account-filing-validation-2026-06.md` is
canonical. Repeated here for the promotion-gate reader.

### Allowed in a worker session (safe, no approval needed)

- Reading repo source and tests.
- Running the offline gate, the three-account matrix, the P1 slices, and the
  P4 concurrency tests.
- Updating docs that do not change runtime behaviour.

### Requires explicit Moeed approval, foreground operator only

- **P3 read-only Kaizen smoke per account.** Foreground operator exports
  the BWS secret into the managed Chrome session on `localhost:18800`,
  runs `sync_kaizen_portfolio_index_for_user` against a temporary
  `PORTFOLIO_GURU_USAGE_DB=/tmp/...` SQLite DB (never the production
  `~/.openclaw/data/portfolio-guru/usage.db`), asserts ≥ 1 indexed row,
  no draft creation, no save, no Telegram traffic, then overwrites and
  unlinks the `/tmp` DB. Result recorded as a TASK.md addendum naming the
  account shape and the verification gate that passed.
- **P5 controlled draft-only Kaizen smoke per account.** One synthetic case
  per account, one form per account, one draft saved, draft URL prefix
  recorded (no full UUID), draft deleted by hand from Kaizen. No
  submission. No supervisor write-back.
- **P6 deploy / restart / production smoke.** Push to `origin/main` (or PR
  merge), launchd restart, `scripts/dogfood_smoke.sh` against the live bot
  before any beta-user message.

### Forbidden everywhere in this sprint

- Worker reading BWS secrets for Kaizen / Stripe / Supabase.
- Worker initiating a live Telegram or live Kaizen test run.
- Any submission, sign, approve, send, reject, delete on Kaizen.
- Any write to the production `usage.db` from a smoke run.
- Restarting launchd, pushing to `origin/main`, or merging from a worker
  context. (Mirrors `AGENTS.md` § Rules.)

---

## 6. Sana / SAS-CESR — handling the login blocker without weakening the bar

The 2026-06-02 read-only smoke returned `auth_required` for Sana. The
promotion bar (§1.4) requires `ok` for all three accounts. We do not lower
the bar; we recover the account.

### Step 1 — narrow the root cause (offline)

Worker work, no live Kaizen. Add the P1.a slice
(`test_login_classification_per_shape.py`) so we can tell `auth_required`
from `credential_failure` from `infra_failure` in tests. Confirm the
2026-06-02 outcome is exactly `auth_required`, not a silently miscategorised
credential failure.

### Step 2 — confirm credential source (foreground)

Foreground operator only. Does BWS still hold a Sana credential? Was it
rotated? Is the saved Kaizen session for Sana stale? This is a `bws secret
list` sweep on the Mac Mini (per the user's standing instruction to use BWS
directly), not a re-auth flow.

### Step 3 — managed Chrome re-auth (foreground)

If the credential is current, foreground operator logs into Kaizen as Sana
inside the managed CDP Chrome on `localhost:18800` interactively, lets the
session persist, then re-runs the read-only smoke.

### Step 4 — confirm the landing-page shape (foreground + worker review)

If Sana's portfolio landing page differs from HST or from Harris's
ACCS + Intermediate dual-access account (possible — SAS / CESR can land on
a different default tab), capture the
URL pattern and add a fixture-only test that the read-only indexer accepts
that pattern. Do not change the live read-only indexer until the fixture
test is green.

### Step 5 — re-run P3 for Sana

Once `ok` lands for Sana, record the result in TASK.md alongside the
existing Moeed/Harris 2026-06-02 addendum. Only after Sana is `ok` does the
P3 gate flip green and P5 becomes runnable.

**The bar does not move.** If Sana's account is genuinely unrecoverable,
the sprint pauses at P3 — we do not promote filing with the SAS branch
unproven on real Kaizen. The alternative is to drop SAS from the
trusted-tester pool for this promotion cycle and re-run the bar with that
scope reduction made explicit in TASK.md.

---

## 7. Multi-user / concurrency / idempotency — what to prove

Trusted testers will file in parallel. The bot must not cross-contaminate.

### P4.a — Telegram per-user persistence isolation (offline)

**Goal.** Two `user_id`s with active conversations in
`PicklePersistence` do not see each other's draft state.

**Files.**

- Create: `backend/tests/test_concurrent_user_isolation.py`
- Reuse: `backend/tests/conftest.py` fixtures + the bot test harness used
  in `test_conversation.py`.

**Acceptance criteria.**

- A draft created under `user_id=A` is not visible when the harness queries
  the bot as `user_id=B` (no leakage through `chat_data` /
  `user_data` / `last_filed_case_text`).
- Sequenced calls — A creates a draft, B creates a draft, A approves — do
  not file B's draft under A's session, and vice versa.

### P4.b — filing-log isolation (offline)

**Goal.** Two simultaneous attempts append two distinct rows to
`filing_attempt_log`, with the correct `user_id` per row.

**Files.**

- Extend: `backend/tests/test_filing_attempt_log.py`

**Acceptance criteria.**

- Two `log_attempt(...)` calls with different `user_id` and overlapping
  timestamps each produce one row, and the report formatter does not double-count.
- The synthetic-user exclusion (`99999999`) still applies; the second user
  must not be silently treated as synthetic.

### P4.c — usage.db / profile_store isolation (offline)

**Goal.** Two users' profile writes do not collide on the same SQLite
connection / row.

**Files.**

- Extend: `backend/tests/test_profile_store_kaizen_role.py`, or add
  `backend/tests/test_profile_store_concurrency.py` if the existing file
  grows past readable.

**Acceptance criteria.**

- A `store_kaizen_role(A, "accs_intermediate")` interleaved with a
  `store_kaizen_role(B, "hst")` against the same SQLite path produces two
  distinct profile rows, no last-write-wins corruption, no foreign-key
  errors.
- The Fernet-encrypted credential blob for A is not decryptable from B's
  session (sanity guard against a key-reuse regression).

### P4.d — retry idempotency under simulated DOM drift (offline)

**Goal.** A retry after a partial save against a stub Kaizen DOM that has
shifted (extra field, missing field, renamed selector) does not create a
duplicate draft.

**Files.**

- Extend: `backend/tests/test_filing_reliability.py`

**Acceptance criteria.**

- Normal filing path: no draft reuse (existing pin).
- Explicit retry path: draft reused (existing pin).
- New: explicit retry against a stub DOM that has changed (one new field
  appended, one renamed) returns a partial-save outcome with the changed
  field surfaced in `skipped`, and the original draft URL is reused, not
  duplicated.

**P4 run.**

```bash
cd backend && venv/bin/python3 -m pytest \
  tests/test_concurrent_user_isolation.py \
  tests/test_filing_attempt_log.py \
  tests/test_profile_store_kaizen_role.py \
  tests/test_filing_reliability.py -v
```

P4 has no live component. The concurrency proof is offline and structural.
Real multi-user load against a deployed bot is the P6 dogfood smoke, not P4.

---

## 8. Instrumentation, monitoring, reporting — what must exist before promotion

The reader of last resort during beta is `filing_attempt_log` and
`/tmp/portfolio-guru-bot.log`. Both must be honest.

### Required

- **Filing attempt log records every attempt.** `filing_attempt_log.log_attempt`
  is called from every filer entry / exit (`filer.py`, `browser_filer.py`,
  `kaizen_form_filer.py` save points). Verify by grep before P6:

  ```bash
  rg -n "log_attempt\(" backend/filer*.py backend/browser_filer.py \
    backend/kaizen_form_filer.py
  ```

  Each call site has `user_id`, `form_type`, `outcome`, and the
  `skipped`/`filled` lists.

- **Admin report surface is reachable.** `format_admin_report` (or whatever
  the current admin handler in `bot.py` calls) returns attempts, saved /
  success / partial / failure counts, top failure categories, recent
  failures, and the synthetic-exclusion footer. Pinned by
  `tests/test_filing_attempt_log.py::test_format_admin_report*`.

- **Filing-log path is redirected away from tracked files.** Confirmed by
  `tests/test_filing_reliability.py::test_filing_coverage_path_is_redirected_to_tmp`
  and the autouse conftest fixture.

- **Bot log shows clean boot and a heartbeat.** `/tmp/portfolio-guru-bot.log`
  shows the launchd start line, no `ERROR` in the first 60 s, and at least
  one heartbeat tick.

### Nice-to-have for this sprint (do not block promotion)

- A per-shape rolling success rate in the admin report (HST / ACCS /
  Intermediate / `accs_intermediate` dual-access / SAS columns). Useful but
  not load-bearing for promotion.
- A separate counter for "filing succeeded but user immediately retried" —
  signals a UI confusion bug that the success rate alone hides.

---

## 9. Launch / deploy / restart smoke gate

P6 is owned by the foreground orchestrator. The worker's job is to leave the
candidate commit in a state where P6 is a clean run, not a debug session.

### Pre-push checks (worker can stage)

```bash
bash scripts/preflight.sh
cd backend && venv/bin/python3 -m pytest tests/ -v \
  --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py
git diff --check
git log --oneline origin/main..HEAD
```

### Push + deploy (orchestrator only)

Follows `docs/PRIVATE_BETA_LAUNCH.md`. Push to `origin/main` triggers the
self-hosted Mac Mini runner. Verify deploy with:

```bash
launchctl print system/com.portfolioguru.bot | head
tail -n 200 /tmp/portfolio-guru-bot.log
```

PID and start time must match the new boot. No `ERROR` lines in the first
60 s of the new log.

### Live dogfood smoke (orchestrator only)

```bash
bash scripts/dogfood_smoke.sh
```

Exit 0. The smoke runs the trainee golden path (`/start` → case → recommend
→ approve → draft saved) against the live bot with a synthetic case. The
synthetic-user exclusion in `filing_attempt_log` keeps this out of the
real-user metric.

### Beta-user message gate

Only after the dogfood smoke is green and `filing_attempt_log` shows the
synthetic dogfood attempt does the orchestrator send the beta-user message
in `docs/PRIVATE_BETA_LAUNCH.md`.

---

## 10. Explicitly out of scope

These are not part of the promotion gate and must not be quietly added.

- **Real submission of any WPBA, on any user's behalf, on any platform.**
  Trainee filing is draft-only by policy (`AGENTS.md` § Filing Routing
  Discipline, `CLAUDE.md` § Safety, `filer_router.py`). The only live
  save-draft path that touches Kaizen for an assessor is CBD, behind the
  `SUP|confirm-save-draft` confirmation, and only when every step is on the
  live allow-list — see `docs/PRIVATE_BETA_LAUNCH.md` § Controlled
  Supervisor Scope. That path is not extended in this sprint.
- **Supervisor write-back extensions.** No new assessor surface (DOPS,
  Mini-CEX, ESLE, QIAT, LAT, STAT, MSF, JCF, ACAF, ACAT) is mapped or wired
  in this sprint.
- **Bulk / unsigned / chase reactivation.** `/bulk`, `/unsigned`, `/chase`
  stay early-return "coming soon".
- **New platform onboarding.** Horus, SOAR, and any other non-Kaizen
  platform stay browser-use-only and out of beta scope.
- **Worker-initiated push, deploy, restart, BWS read, live Kaizen,
  live Telegram.** All foreground-owned.
- **DOM mapping changes to Kaizen filers** beyond what a P4.d retry-drift
  fixture pins. Live Kaizen UI drift fixes belong in their own slice with
  before/after evidence, not bundled into this sprint.

---

## 11. Status snapshot (this commit)

| #   | Phase                                                          | Status                                                                                                                                            |
| --- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| P0  | Evidence review — offline gate + three-account matrix re-green | done; prior gate 933 passed, doc slice verified references                                                                                        |
| P1  | Fixture / dry-run slices (a–d)                                 | **done (offline)**; P1.a landed in `42e3f4d`, P1.b in `ec67d84`, P1.c in `9ea35d6`, P1.d in `c59fd21`; see TASK.md addenda for per-slice evidence |
| P2  | Sana credential / session recovery                             | blocked on foreground operator + BWS sweep                                                                                                        |
| P3  | Per-account read-only live smoke                               | 2/3 ok (Moeed, Harris); Sana `auth_required`                                                                                                      |
| P4  | Concurrency / idempotency offline proofs                       | **done (offline)**; landed in `b6ff9b0` (draft-state isolation, filing-log isolation, profile/credential isolation, retry-after-DOM-drift)        |
| P5  | Per-account controlled draft-only live smoke                   | gated on P3 fully green (Sana recovery still blocking)                                                                                            |
| P6  | Deploy / restart / production smoke                            | gated on P5 fully green                                                                                                                           |
| —   | Promotion gate                                                 | held: §1.4 (Sana), §1.5, §1.8                                                                                                                     |

**Next executable slice:** §6 Sana / SAS-CESR recovery — foreground operator
work only. Worker P1.a–d and P4 offline coverage are landed; the bar will
not move further without the live/credential-gated Sana read-only smoke
returning `ok`. Once P3 is green for all three accounts, P5 (controlled
draft-only live smoke) becomes runnable.

---

## 12. Verification for this plan artefact

This document does not change runtime behaviour. Verification is doc-only.

```bash
# Confirm cross-references resolve.
test -f docs/roadmap/three-account-filing-validation-2026-06.md
test -f docs/PRIVATE_BETA_LAUNCH.md
test -f backend/filer_router.py
test -f scripts/preflight.sh
test -f scripts/dogfood_smoke.sh
test -f backend/tests/test_three_account_filing_matrix.py
test -f backend/tests/test_filing_reliability.py
test -f backend/tests/test_filing_attempt_log.py
test -f backend/tests/test_kaizen_login_reliability.py
test -f backend/tests/test_profile_store_kaizen_role.py

# Confirm the offline gate command in §1 still parses.
cd backend && venv/bin/python3 -m pytest \
  tests/test_three_account_filing_matrix.py \
  tests/test_profile_store_kaizen_role.py \
  tests/test_kaizen_login_reliability.py --collect-only -q

# Sanity on tracked-file hygiene.
git diff --check
```
