# Three-Account Basic Filing Validation — 2026-06-02

**Status:** Plan + offline coverage landed; Phase 3 read-only smoke partially run.
**Owner:** Foreground (orchestrator-delivered work) for any live phase.
**Branch:** `main` (local commits ahead of `origin/main`).

---

## Why this exists

The last few sprints focused on Portfolio Health pathway-awareness and Kaizen
indexer reliability. During that work an earlier instruction was missed:
Portfolio Guru's _basic filing_ path must be validated against the three real
portfolio shapes our trusted-tester pool covers, not just the HST shape we
build against by default. Filing routing, stage defaulting, and form-catalogue
gating all branch on `profile_store.training_level` and the inferred Kaizen
role; if any branch silently degrades on a non-HST shape, real users feel it
on their first draft.

This document is the restartable record of the missed requirement, the
three-account matrix, the safe/live boundary, the offline gate that already
exists, and the live phases that still need explicit approval.

---

## The three-account matrix

Each account exercises a different portfolio shape. Live credentials live in
BWS and are **not** read by this validation work — see "Safe / live boundary"
below.

| #   | Doctor | Portfolio shape                                                   | `training_level` value(s)     | Why this account matters                                                                                                                  |
| --- | ------ | ----------------------------------------------------------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Moeed  | Senior / HST (CCT pathway, ST4–ST6)                               | `HIGHER` (legacy `ST4`–`ST6`) | Default development shape. Forms catalogue is the ST6 superset; stage defaults to `Higher/ST4-ST6` on every WPBA schema that has a stage. |
| 2   | Harris | DREAM Pathway junior — ACCS **and** Intermediate Portfolio access | `ACCS` and/or `INTERMEDIATE`  | Only account that can hit both junior stages. Exposes the `accs_intermediate` collapse where dual access is stored as a single profile.   |
| 3   | Sana   | SAS doctor planning CESR / Portfolio Pathway                      | `SAS`                         | Only non-training portfolio. Hits the empty-stage path on every WPBA stage select, and has no `TRAINING_LEVEL_FORMS["SAS"]` entry.        |

### What each shape touches in the codebase

- `backend/bot.py` `_stage_value_from_training_level` — Kaizen stage defaulter
  used by `_apply_profile_training_stage` before every draft preview.
- `backend/bot.py` `TRAINING_LEVEL_FORMS` — form catalogue gating in
  `handle_form_selection`, `_run_form_recommender`, and similar entry points.
- `backend/kaizen_form_filer.py` `STAGE_SELECT_VALUES` / `QIAT_STAGE_VALUES` —
  the deterministic Playwright stage UUIDs the filer types into Kaizen.
- `backend/profile_store.store_kaizen_role` and `kaizen_role` column — role
  detection ("trainee" / "assessor" / "accs_intermediate" → bucketed back into
  `training_level`).

If any of these regress on Harris's or Sana's shape, the user-facing failure
is silent (wrong default stage, missing form button, blank Kaizen stage
field). That class of regression is exactly what this validation sprint
exists to catch _before_ the live smoke.

---

## Safe / live boundary

This sprint is split into clearly delineated phases. Live phases are gated on
explicit Moeed approval and orchestrator delivery; nothing below is executed
inside a worker session unless the phase says so.

### Phase 1 — Offline portfolio-shape pinning (safe; landed in this commit)

- New offline test:
  `backend/tests/test_three_account_filing_matrix.py` (no Kaizen, no
  credentials, no Telegram, no Playwright).
- Pins, per account shape:
  - Stage defaulter returns the right Kaizen option string per shape.
  - Filer's `STAGE_SELECT_VALUES` lookup matches the stage defaulter.
  - Form catalogue (`TRAINING_LEVEL_FORMS`) contains the WPBAs we expect to
    offer that shape.
  - Known gaps are pinned with `xfail` or assertion comments so a future
    silent fix is visible, not invisible.
- Run with the existing offline gate:
  ```bash
  cd backend && venv/bin/python -m pytest tests/test_three_account_filing_matrix.py -v
  ```

### Phase 2 — Dry-run / fixture checks (safe; can be added incrementally)

- Use the same offline harness style as
  `backend/tests/test_kaizen_login_reliability.py` and
  `backend/tests/test_kaizen_save_confirmation.py`: stub `KaizenProvider`,
  fake `_run_file` URLs, no real CDP, no live Kaizen.
- Cover, per shape:
  - Login classification (credential failure vs infra failure) when the
    detected role is `accs_intermediate` vs `hst` vs `sas`.
  - `kaizen_role` round-trip (`store_kaizen_role(... "accs_intermediate")` →
    `_apply_kaizen_role_to_profile` sets `training_level` to `INTERMEDIATE`,
    not `HIGHER`).
  - The recommended-form fallback for `SAS` does not collapse to the legacy
    `ST5` superset and does not silently leak HST-only forms.
- These belong alongside the Phase 1 tests; this doc names them as the next
  offline slice so the orchestrator can schedule them without rediscovery.

### Phase 3 — Live Kaizen account smoke (gated; not run from a worker)

- Pre-requisite: explicit Moeed approval **per account**.
- Bitwarden Secrets Manager already holds the three account credentials.
  Workers must not read BWS for this purpose; the foreground operator
  exports the secret into the managed Chrome session on
  `localhost:18800` for the duration of the smoke.
- For each account, run a **read-only** smoke first:
  1. Connect to managed CDP (`localhost:18800`).
  2. `sync_kaizen_portfolio_index_for_user` against a **temporary** local
     SQLite DB (`PORTFOLIO_GURU_USAGE_DB=/tmp/...`), not the production
     `usage.db`.
  3. Assert at least one indexed evidence row, no auth-required outcome,
     no draft creation, no Kaizen save, no Telegram traffic.
- Only after read-only is green per account: do **one** controlled
  draft-only filing per account against a synthetic case, verify the draft
  saves and is visible in Kaizen, then delete the synthetic draft from
  Kaizen by hand. Still no submission.
- Live smoke output is recorded as a TASK.md addendum naming the account
  shape, draft URL prefix only (no full UUID), and the verification gate
  that passed.

### Phase 4 — Real submission

- **Not in scope** for this sprint. Portfolio Guru filing is draft-only by
  policy (`AGENTS.md` § Filing Routing Discipline; `CLAUDE.md` § Safety).
  Any real submission requires a separate decision and explicit approval
  outside this validation work.

---

## Out of scope

- Reading or writing BWS secrets, Kaizen credentials, or live Kaizen
  sessions from a worker context.
- Restarting launchd, deploying to the Mac Mini, or pushing to
  `origin/main`. Orchestrator owns commit and closure.
- Any change to filer source files (`filer.py`, `browser_filer.py`,
  `filer_router.py`, `kaizen_form_filer.py`), credential storage, the
  assessor write-back, or deployment plumbing.
- Pathway terminology cleanup beyond the smallest doc fixes that make the
  three-account matrix consistent with the existing
  `docs/roadmap/portfolio-pathways-research-2026-06.md` map.

---

## Verification (offline gate)

```bash
cd backend && venv/bin/python -m pytest \
  tests/test_three_account_filing_matrix.py \
  tests/test_profile_store_kaizen_role.py \
  tests/test_kaizen_login_reliability.py -v
```

The new file is the load-bearing addition. The other two are existing pins
on the same code paths; running them together confirms the new tests do not
break the `kaizen_role` or login-reliability invariants we already shipped.

For a full safety check before any live phase:

```bash
cd backend && venv/bin/python -m pytest tests/ -q \
  --ignore=tests/test_e2e.py \
  --ignore=tests/test_e2e_live.py
```

This is the same offline gate the launch runbook uses
(`docs/PRIVATE_BETA_LAUNCH.md`).

---

## Status snapshot

| #   | Deliverable                                                                    | Status                                  |
| --- | ------------------------------------------------------------------------------ | --------------------------------------- |
| 1   | Three-account matrix codified in this doc                                      | done (this commit)                      |
| 2   | Offline test pinning per-shape stage defaulter + filer lookup + form catalogue | done (this commit)                      |
| 3   | Known SAS / `accs_intermediate` gaps pinned with visible assertions            | done (this commit)                      |
| 4   | Phase 2 dry-run/fixture tests scoped                                           | scoped here; implementation queued      |
| 5   | Phase 3 live read-only smoke per account                                       | partial: Moeed + Harris ok; Sana auth_required |
| 6   | Phase 4 real submission                                                        | **out of scope** — draft-only is policy |
| 7   | TASK.md 2026-06-02 addendum                                                    | done (this commit)                      |
| 8   | No live Kaizen / credentials / browser / Telegram / deploy / push              | met                                     |
