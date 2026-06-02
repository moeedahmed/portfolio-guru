# Active Task — Kaizen Mapping Sprint

> **2026-06-02 addendum — Filing Reliability Readiness Sprint planned.**
> Filing is the USP and must clear a promotion-grade bar before the
> trusted-tester pool widens. Plan landed at
> `docs/roadmap/filing-reliability-readiness-sprint-2026-06.md` with six
> ordered phases (P0 evidence review → P1 fixture/dry-run → P2 Sana
> credential/session recovery → P3 per-account read-only live smoke → P4
> concurrency/idempotency offline proofs → P5 per-account controlled
> draft-only live smoke → P6 deploy/restart/production smoke) and an
> explicit §1 promotion gate.
>
> Snapshot at plan landing: full offline gate 933 passed; three-account
> matrix codified; P3 read-only smoke ok for Moeed/HST and
> Harris/ACCS+Intermediate; Sana/SAS-CESR blocked at `auth_required` and is
> the critical-path blocker for promotion. The plan is doc-only; no live
> Kaizen, no Telegram, no BWS read, no push, no deploy, no restart.
>
> **Next executable slice:** Plan §4 P1.a —
> `backend/tests/test_login_classification_per_shape.py` covering
> credential-failure / infra-failure / auth-required classification for
> each of `hst`, `accs_intermediate`, `sas`. Followed by P1.b, P1.c, P1.d
> in order. Each is offline, fixture-driven, lands on its own task branch
> after `bash scripts/preflight.sh`. The full offline gate must stay green
> at the new test count.
>
> No source files outside docs were touched in this slice. The plan
> cross-references `docs/roadmap/three-account-filing-validation-2026-06.md`
> (P0 / P3 input) and `docs/PRIVATE_BETA_LAUNCH.md` (P6 deploy gate).

> **2026-06-02 addendum — Portfolio Health pathway model correction.**
> Moeed corrected the product model: the two pathways are **Training / CCT**
> and **CESR / Portfolio Pathway**. ARCP is a _yearly review checkpoint_
> inside Training/CCT, not a pathway in its own right. Earlier copy and
> tests leaked "Training (ARCP)" / "ARCP path" framings that implied ARCP
> was a pathway label. Corrected user-visible surfaces:
>
> - `/health` trainee header is now `Portfolio Health — Training (CCT)
>   pathway · ARCP readiness check` (was `Training (CCT) ARCP readiness`).
> - `/pathway` selector copy now explicitly names the two pathways and
>   calls out ARCP as the yearly review checkpoint inside Training (CCT).
> - `/health` paywall now says "Training (CCT) pathway (ARCP readiness
>   check) or CESR / Portfolio Pathway view" instead of "training (ARCP)
>   or CESR".
> - Upgrade screen calls the feature "Portfolio Health" (pathway-aware),
>   not "ARCP Health"; BOT_COMMANDS description is no longer ARCP-only.
> - `_pathway_for_detected_role` docstring documents the trainee
>   destination as "Training (CCT)", with ARCP as a checkpoint inside it.
> - `docs/PORTFOLIO_HEALTH_SPEC.md` user-journey + Phase 2 checklist no
>   longer describe a "Training (ARCP)" pathway.
>
> Tests now pin the corrected model: header and pathway-divergence asserts
> use the new wording, plus four new guards
> (`test_pathway_command_describes_arcp_as_checkpoint_not_pathway`,
> `test_bot_commands_health_description_is_not_arcp_only`,
> `test_upgrade_copy_calls_feature_portfolio_health_not_arcp_health`,
> `test_pathway_for_detected_role_docstring_uses_training_cct_not_arcp`)
> reject any future drift back to "Training (ARCP)" / "ARCP pathway" /
> "ARCP Health" framings.
>
> Verification:
>
> - Focused: `cd backend && venv/bin/python -m pytest tests/test_health_bot.py tests/test_health_index_integration.py tests/test_health_engine.py -q`
>   → 76 passed.
> - Offline gate: `cd backend && venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py`
>   → 933 passed, 13 deselected.
>
> No live bot restart, deploy, push, Kaizen, or Telegram. Files changed:
> `backend/bot.py` (header, pathway selector copy, paywall, upgrade
> bullet, docstrings, two comments), `backend/tests/test_health_bot.py`
> (header asserts updated + 4 new guards), `docs/PORTFOLIO_HEALTH_SPEC.md`
> (user journey + Phase 2 checklist), `TASK.md` (this addendum).

> **2026-06-02 addendum — Phase 3 read-only Kaizen smoke run.**
> Moeed approved the gated live read-only smoke after the offline three-account
> matrix landed. Boundary held: no draft creation, no Kaizen save/submit, no
> Telegram automation, no production `usage.db` write, no deploy, no restart,
> no push. The temporary `/tmp` evidence DB was overwritten and unlinked after
> the run so real portfolio rows were not retained for debugging.
>
> Results:
>
> - **Moeed / senior-HST:** `ok`; 22 rows seen, 21 indexed in the temporary DB.
> - **Harris / ACCS+Intermediate:** `ok`; 21 rows seen, 21 indexed in the
>   temporary DB.
> - **Sana / SAS-CESR candidate:** `auth_required`; RCEM/Kaizen login did not
>   land on a portfolio page within the read-only smoke window. No rows indexed.
>
> Interpretation: the read-only indexer and CDP session bootstrap work for the
> senior-HST and junior/intermediate portfolio shapes. Sana's account still
> needs manual credential/session recovery or confirmation of the correct saved
> account before we can validate the SAS/CESR shape live.

> **2026-06-02 addendum — three-account basic-filing validation matrix codified (offline only).**
> Earlier instruction missed: basic filing must be validated against the three
> portfolio shapes our trusted-tester pool actually covers, not just the HST
> shape we build against by default. The three accounts (credentials live in
> BWS — **not read here**):
>
> 1. **Moeed** — senior / HST (CCT pathway, ST4–ST6). `training_level=HIGHER`.
> 2. **Harris** — DREAM Pathway junior with ACCS _and_ Intermediate Portfolio
>    access. `training_level` is stored as `ACCS` or `INTERMEDIATE`; the
>    `accs_intermediate` Kaizen role collapses to a single profile bucket.
> 3. **Sana** — SAS doctor planning CESR / Portfolio Pathway. `training_level=SAS`.
>    No matching grouped-band stage option on standard WPBAs.
>
> Safe / live boundary made explicit in
> `docs/roadmap/three-account-filing-validation-2026-06.md`:
> Phase 1 offline portfolio-shape pinning (this commit), Phase 2 dry-run /
> fixture checks (scoped, queued), Phase 3 live read-only Kaizen smoke per
> account (gated on explicit Moeed approval per account; foreground-owned
> per-account secret export to managed CDP), Phase 4 real submission (**out
> of scope** — draft-only is policy).
>
> Codified:
>
> - New plan / checklist: `docs/roadmap/three-account-filing-validation-2026-06.md`.
> - New offline test: `backend/tests/test_three_account_filing_matrix.py` — 22
>   pins covering, per shape: stage defaulter on grouped-band WPBAs
>   (CBD/DOPS/MINI_CEX/LAT), stage defaulter on QIAT's individual-year select,
>   filer-side `STAGE_SELECT_VALUES` alignment, the `TRAINING_LEVEL_FORMS`
>   catalogue (HST superset, ACCS/INTERMEDIATE ST3 collapse, SAS fall-through to
>   the unknown-default union including CESR core WPBAs), and the
>   `TRAINING_LEVEL_LABELS` distinctness guard so the three shapes never
>   collapse into the same UI label.
> - Known live-impact gaps pinned visibly rather than fixed in this slice:
>   - `training_level == "SAS"` returns an empty stage string on every WPBA;
>     intentional (we refuse to invent a training year for a non-training
>     doctor) but a future silent flip to `Higher/ST4-ST6` would be a real
>     trust break.
>   - `TRAINING_LEVEL_FORMS` has no `SAS` key; Sana hits
>     `_default_allowed_forms_for_unknown_training`. Pinned that the fallback
>     union must continue to offer CBD / DOPS / MINI_CEX / REFLECT_LOG (CESR
>     core evidence).
>   - QIAT exposes a `Portfolio pathway (CESR)` option that today's defaulter
>     does not use for `SAS`. Pinned so any future mapping is intentional and
>     paired with user-visible copy in the draft preview.
>   - `accs_intermediate` Kaizen role collapses to a single `INTERMEDIATE`
>     bucket. Pinned (`ACCS` and `INTERMEDIATE` aliases share `ST3`'s form
>     catalogue) so a refactor that breaks the alias is loud.
>
> Verification:
>
> - Focused new pins: `cd backend && venv/bin/python -m pytest tests/test_three_account_filing_matrix.py -v` → 22 passed.
> - Sibling pinning gate (same code paths):
>   `cd backend && venv/bin/python -m pytest tests/test_three_account_filing_matrix.py tests/test_profile_store_kaizen_role.py tests/test_kaizen_login_reliability.py -q` → 45 passed.
>
> Not run from this slice: full offline backend gate, live Kaizen, live
> Telegram, browser-harness, BWS reads, launchd restart, deploy, push. The
> three live accounts are documented for Phase 3 but require explicit
> per-account Moeed approval before any read-only smoke; foreground operator
> owns the secret hand-off into the managed CDP session, not the worker.
>
> Still requiring Moeed approval: Phase 3 read-only Kaizen smoke against each
> of the three accounts (Moeed/Harris/Sana). Phase 4 real submission stays out
> of scope by Portfolio Guru policy.

> **2026-06-02 addendum — pathway-aware Portfolio Health output.**
> Product decision: `/health` must clearly diverge by pathway. CCT/Training
> users see a Training (CCT) ARCP readiness brief with risk, why, and the
> next 3 urgent filing actions before ARCP. CESR / Portfolio Pathway users
> see a long-term evidence plan: 36-WPBA progress with DOPS/Mini-CEX/CBD
> breakdown, this year's 3–12 month evidence actions, domain balance,
> missing domains, and a 5-year evidence-window framing — no ARCP-deadline
> language. The chart image stays secondary. The /health paywall is now
> pathway-neutral instead of promising only ARCP analysis.
>
> Verification:
>
> - Focused: `cd backend && venv/bin/python -m pytest tests/test_health_bot.py tests/test_health_index_integration.py tests/test_health_engine.py -q`
>   → 72 passed.
> - Offline gate: `cd backend && venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py`
>   → 907 passed, 13 deselected.
>
> Files: `backend/bot.py` (ARCP/CESR formatters + paywall copy),
> `backend/health_engine.py` (CESR pathway_readiness now carries the
> DOPS/Mini-CEX/CBD breakdown; CESR next_actions reframed as a yearly /
> 3–12 month plan), `backend/tests/test_health_bot.py` (new
> ARCP-vs-CESR divergence tests + paywall copy guard), and
> `backend/tests/test_health_index_integration.py` (title and progress
> assertions updated for the new copy). No live bot restart, deploy, or
> push.

> **2026-06-01 addendum — settings wording and stale sync status.**
> Product decision: `/settings` should read like a product surface, not an
> internal maintenance panel. Updated the voice-profile wording to
> `Writing style` so it aligns with the other settings rows, and changed the
> Kaizen evidence row so a fresh `running` sync says `syncing now` while a
> stale run older than 30 minutes says `sync timed out` instead of pretending
> it is still running. Manual sync remains hidden from normal settings.
>
> Verification:
>
> - Focused settings/status gate:
>   `cd backend && venv/bin/python -m pytest tests/test_health_index_integration.py tests/test_health_bot.py tests/test_kaizen_index.py tests/test_kaizen_sync.py -q`
>   → 82 passed.

> **2026-06-01 addendum — filing reliability instrumentation + admin report.**
> Product decision: the next sprint is _not_ a polished user-facing health
> dashboard. Core filing is the product, so we first need to _know_ whether
> real users are filing and which failure path is biting. Built a durable
> per-attempt filing log (`backend/filing_attempt_log.py`) and an internal
> `/filingreport` admin command that summarises real-user reliability.
>
> Mechanics:
>
> - Every Kaizen filing attempt from `bot.py` (success, partial, save-failure,
>   timeout, exception) writes one PHI-free NDJSON record to
>   `~/.openclaw/data/portfolio-guru/filing-log.ndjson` via the new module.
>   Path is overridable with `PORTFOLIO_GURU_FILING_LOG_PATH` for tests.
> - Each record carries: user_id, username, form_type, status, derived error
>   category (SAVE_SUCCESS / PARTIAL_SAVE / SAVE_UNVERIFIED / SAVE_FAILURE /
>   LOGIN_FAILED / TIMEOUT / EXCEPTION / FILL_FAILURE / UNKNOWN), filer error
>   string, filled count, skipped field keys, method (deterministic /
>   browser-use), post-save verification flag, and a `synthetic` boolean.
> - `is_synthetic_user` flags test traffic — Telegram user id 99999999 (the
>   pytest fixture) and any extra ids from
>   `PORTFOLIO_GURU_SYNTHETIC_USER_IDS` — and the summary excludes synthetic
>   attempts from headline counts by default while still reporting how many
>   were suppressed.
> - `/filingreport` is admin-only (gated on `ADMIN_USER_ID`); typing
>   `/filingreport all` includes synthetic traffic for debugging. The output
>   is a Telegram-safe text report: attempts / unique users / saved rate /
>   top categories / top forms / recent failures.
> - Existing per-fix `filing_results.ndjson` (consumed by `auto_fix_form_map`)
>   is untouched — this is a separate user-attempt log, not a replacement.
> - No new dependencies, no live testing, no deploy, no restart, no push.
>   Foreground owns activation; the bot picks up the new module on its next
>   restart.
>
> Verification:
>
> - Focused: `cd backend && venv/bin/python -m pytest tests/test_filing_attempt_log.py tests/test_filing_reliability.py tests/test_smoke.py -v` →
>   52 passed.
> - Offline gate: `cd backend && venv/bin/python -m pytest tests/ --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py` →
>   900 passed, 13 deselected.
>
> Files: `backend/filing_attempt_log.py` (new),
> `backend/tests/test_filing_attempt_log.py` (new — 29 tests covering
> success/partial/failure recording, synthetic exclusion, categorisation,
> report rendering, and the admin command itself), `backend/bot.py`
> (`_log_filing_attempt` delegates to the new module, completion-path call
> now passes `filled`, `method`, `verified`; new `filingreport_command` and
> handler registration).
>
> Known follow-ups (not in this slice): timeout / exception sites in bot.py
> don't yet pass `filled`/`method` because those values aren't bound when we
> bail — the category falls back to TIMEOUT / EXCEPTION which is what the
> report needs anyway. If we later want per-method timeout breakdown we can
> thread `method` through `_filing_progress`.

> **2026-06-01 addendum — normal settings hides manual Kaizen sync.**
> Product decision: users should not have to understand or maintain a Kaizen
> evidence sync. `/settings` keeps "📊 Portfolio health" as the visible
> connected-user action, while the manual `ACTION|refresh_portfolio` flow stays
> available as a hidden troubleshooting/support route. Evidence refresh should
> happen automatically after login, inside `/health` when data is missing/stale,
> and after successful filing where needed. Focused coverage now pins that
> normal settings contains no "Refresh portfolio", no "Sync Kaizen evidence",
> and no visible `ACTION|refresh_portfolio` button.

> **2026-06-01 addendum — `/settings` promotes Portfolio health to primary CTA.**
> Product decision: connected users opening `/settings` should see "📊 Portfolio
> health" as the prominent action, not "🔄 Refresh portfolio". The manual
> Kaizen refresh remains available as a secondary utility, relabelled
> "🔄 Sync Kaizen evidence", and still routes to the existing read-only
> confirmation/result flow (`ACTION|refresh_portfolio` →
> `_refresh_portfolio_confirm_*` → `ACTION|confirm_refresh_portfolio`). The
> Portfolio health button reuses the existing `ACTION|health` handler, which
> already gates on Kaizen credentials, prompts the read-only refresh when the
> index is missing/stale, and falls through to `_run_health_analysis`. `/health`
> as a typed command is unchanged and stays fast. New focused coverage in
> `backend/tests/test_health_index_integration.py`
> (`test_settings_makes_portfolio_health_primary_and_sync_secondary`,
> `test_settings_omits_portfolio_health_button_when_not_connected`) pins the
> rule: health sits above sync in the keyboard, the old "Refresh portfolio"
> label is gone, and disconnected users see neither button. Verification:
> `cd backend && venv/bin/python3 -m pytest tests/test_health_index_integration.py tests/test_health_bot.py -v`
> green at 55 passed, 1 warning. No live Kaizen,
> Telegram, restart, deploy, or push from this slice — foreground owns
> activation.

> **2026-06-01 addendum — `/health` is now the primary refresh path.**
> Product decision: users should not need to think "refresh portfolio" before
> asking for health. `/health` and the inline Portfolio Health button now
> check whether the local Kaizen index is usable; when connected users have no
> fresh index yet, Portfolio Guru asks for explicit read-only consent with
> `✅ Refresh and show health`, runs the same guarded Kaizen refresh, and then
> continues straight into Portfolio Health. The `/settings → Sync Kaizen evidence`
> button stays as a secondary utility for retry, reconnect, support,
> and manual data management.

> **2026-06-01 addendum — guarded Refresh portfolio workflow built.**
> `/settings` now exposes a user-facing `🔄 Refresh portfolio` button for
> connected Kaizen users. Tapping it shows a confirmation screen that explains
> the safety boundary in plain language: the refresh reads Kaizen timeline and
> saved-draft activity, but does not save, submit, sign, delete, edit Kaizen,
> create drafts, or send supervisor requests. Only the explicit `✅ Refresh now`
> confirmation runs `sync_kaizen_portfolio_index_for_user`; the result screen
> reports success, partial refresh, reconnect-needed, screen-drift, or generic
> failure without exposing traceback detail. Successful or partial refreshes
> offer `📊 View portfolio health`; auth failures offer `🔗 Reconnect Kaizen`;
> all outcomes offer a return to settings. Tests mock the sync call and prove
> the refresh does not run before confirmation. Next step: run a controlled
> manual Telegram test of `/settings → Refresh portfolio → Refresh now` so
> Moeed can judge the wording, button path, and result screen before we build
> more Portfolio Health behaviour on top.

> **2026-06-01 addendum — live read-only login smoke passed.**
> The new portfolio-index login wrapper has now been exercised against the
> live managed Kaizen browser using Moeed's saved Portfolio Guru credentials,
> but with a temporary local SQLite database only. Outcome: the smoke got past
> the previous sign-in blocker, read one real Kaizen assessment row, wrote one
> `evidence_items` row into the temporary index, and recorded the run as `ok`.
> Sample visible title: `DOPS - (ST3-ST6 - 2025 update)`. The production
> `usage.db` was not populated, no Telegram messages were sent, and there was
> no restart, deploy, push, Kaizen save, submit, sign, delete, or draft action.
> This proves the missing connection was the auth/session bootstrap, not the
> read-only indexer. Next visible build slice: add the guarded user-facing
> "Refresh portfolio" workflow/button so Moeed can test the wording, button
> path, and result screen before further Portfolio Health work builds on it.

> **2026-06-01 addendum — index sync now reuses the trusted login bootstrap.**
> `backend/kaizen_sync.py` gains `sync_kaizen_portfolio_index_for_user`, a
> high-level helper that opens an isolated CDP page via the existing
> `connect_cdp_browser`, attempts `use_cached_session`, and falls back to
> `store.get_credentials` plus the existing `_login` helper from
> `backend/kaizen_form_filer.py` when the cache is stale. On a fresh login it
> persists the new session via `save_session_state` so the next refresh skips
> the password step, then hands the authenticated page to the existing
> read-only `sync_kaizen_portfolio_index`. Bootstrap-stage failures
> (no saved credentials, login refused, CDP unavailable) still write an
> `index_runs` row as `auth_required` / `failed` so `/settings` can surface
> the outcome. The isolated CDP context and Playwright handle are always
> closed in `finally`. The read-only driver itself (`sync_kaizen_portfolio_index`)
> is unchanged — the source guard against write-side browser actions
> (`.click(`, `.fill(`, `.type(`, `file_to_kaizen`, `save_draft`,
> `delete_all_drafts`) still passes because the login work lives in the form
> filer and is only called from the wrapper. Offline coverage in
> `tests/test_kaizen_sync.py` exercises the cached-session path,
> stale-cache + credentials path, missing-credentials path, login-failure
> path, CDP-unavailable path, and that the session is always closed even
> when the inner sync raises. Verification: focused sync/index gate green at
> 24 passed. Next step: a controlled foreground live smoke can now try the
> same login/session bootstrap end-to-end against Moeed's saved credentials,
> inspect indexed row quality, and only then wire a guarded user-facing
> "Refresh portfolio" button. No live Kaizen, credentials, restart, deploy,
> push, or Telegram traffic in this slice.

> **2026-06-01 addendum — first live read-only smoke reached login boundary.**
> The foreground smoke attached to the managed CDP browser on
> `localhost:18800` and attempted the read-only Kaizen sync against a temporary
> local database only. It did not use stored credentials, did not write to
> Kaizen, did not touch Telegram, and did not populate the production
> `usage.db`. Outcome: Kaizen redirected to `auth.kaizenep.com`, so the smoke
> stopped with `auth_required` before reading portfolio rows. Next step:
> Moeed must log in to Kaizen in the managed browser session, then rerun the
> same read-only smoke. If rows index cleanly, the following build slice is the
> guarded user-facing "Refresh portfolio" workflow.

> **2026-06-01 addendum — read-only Kaizen sync driver landed offline.**
> `backend/kaizen_sync.py` now provides the CDP/page-backed read-only sync
> driver for Kaizen Portfolio Index v1. It accepts an already-authenticated
> Playwright-like page, navigates only read surfaces, walks selected timeline
> categories plus `/activities`, opens event/detail pages read-only,
> normalises each item to `EvidenceItemRow`, upserts through
> `backend/kaizen_index.py`, de-duplicates repeated event UUIDs, and records
> `index_runs` as `ok`, `partial`, `drift`, `auth_required`, or `failed`.
> Offline tests cover timeline ingestion, saved drafts, de-duplication,
> detail drift, auth redirect handling, and a source guard against write-side
> browser actions. This slice still does **not** run live Kaizen, read
> credentials, expose a refresh button, touch the filer, restart launchd,
> deploy, push, or send Telegram traffic. Verification: focused sync/index/
> health gate green at 67 passed; full offline backend gate green at
> 848 passed, 13 deselected, 3 snapshots passed, 49 warnings. Next step:
> run a controlled read-only live smoke against Moeed's existing logged-in
> Kaizen/CDP session, inspect indexed row quality, then wire a guarded
> user-facing "Refresh portfolio" trigger only if the data is clean.

> **2026-06-01 addendum — Kaizen Portfolio Index v1 storage substrate landed.**
> The first build slice from `docs/roadmap/kaizen-mapping-sprint-2026-06.md`
> is now wired offline-only. `backend/kaizen_index.py` owns the local SQLite
> tables (`evidence_items`, `index_runs`) inside the existing
> `USAGE_DB_PATH` / `usage.db`, with typed dataclasses
> (`EvidenceItemRow`, `IndexRunRow`, `KaizenSyncStatus`), upsert / list /
> count / start-run / finish-run / latest-run helpers, and a pure
> `evidence_row_to_health_item` conversion onto `health_models.EvidenceItem`.
> `backend/bot.py` `_run_health_analysis` now resolves evidence through
> `_resolve_health_evidence`, which prefers indexed rows when they exist for
> the user and falls back to the existing `get_case_history` path otherwise;
> `case_history_to_evidence_items` is still the fallback path so the AI
> ARCP narrative keeps the same case-history input. `/settings` gains a
> read-only `🔄 Kaizen sync: ...` status row (no refresh button, no live
> action) populated by `_safe_kaizen_sync_status` from the latest
> `index_runs` row plus `evidence_items` count. The actual read-only sync
> driver (CDP adapter that writes rows via these helpers) is still the next
> slice and remains owned by the foreground; nothing in this commit
> navigates Kaizen, touches Playwright/CDP, reads credentials, or changes
> the filer / deploy / launchd path. Verification: focused
> `tests/test_kaizen_index.py`, `tests/test_health_index_integration.py`,
> `tests/test_health_engine.py`, and `tests/test_health_bot.py` green at
> 62 passed; full offline backend gate green at 843 passed, 13 deselected,
> 3 snapshots passed, 49 warnings (pre-existing deprecation warnings).
> No live Kaizen, no Telegram traffic, no restart, no deploy, no push.

> **2026-06-01 addendum — Kaizen Mapping Sprint as public-product foundation.**
> The next sprint is read-only Kaizen mapping, scoped as a reusable platform
> adapter rather than another per-form per-user scrape. Plan and scorecard live
> in `docs/roadmap/kaizen-mapping-sprint-2026-06.md`. The sprint's first build
> slice is Kaizen Portfolio Index v1 — a read-only refresh that produces a
> normalised `evidence_items` table the existing `/health` and the planned
> ARCP/CESR overlays consume. This addendum is docs/planning only: no filer,
> credential, deploy, launchd, Telegram, push, or live Kaizen actions in this
> sprint's docs work. See `## Active Sprint — Kaizen Mapping (2026-06-01)`
> below for the in-line scorecard and proof gate.

> **2026-05-29 addendum — main-bot opt-in gathering mode slice.**
> The vNext conversational collector has now been promoted into the main bot
> as an opt-in, deployment-gated slice. `PG_GATHERING_MODE=1` enables the
> `/gather on|off` user toggle. When enabled for a user, the first case input
> enters `AWAIT_GATHERING`, stores sequential text/voice/photo/document content
> as one case, answers simple side questions without adding them to the case,
> and hands the combined case back to the existing `_process_case_text` flow
> only when the user says "done" / "file this" / "preview". Existing users stay
> on the old single-message flow unless both the env flag and user toggle are
> on. No Kaizen filing, credential, billing, database, or deployment change in
> this slice. Verification: focused gathering/vNext gate green at 73 passed,
> 1 warning; full backend gate green at 825 passed, 24 deselected, 43 warnings,
> 3 snapshots passed.

> **2026-05-29 addendum — vNext conversational collector repair slice.**
> Moeed's first live dogfood exposed the real issue: the private vNext bot
> still behaved like a deterministic parser harness, not a smart case-taking
> assistant. This slice adds `backend/vnext_dialogue_policy.py` and changes
> the private runner so rich case input is acknowledged conversationally first:
> it collects facts across turns, asks one highest-value follow-up, and only
> shows the recommendation/local preview when the user says "done" or asks to
> draft/file/save/preview. "File this" is now treated as a completion request
> for the private bot preview path, not a Kaizen filing action. `/start` copy
> now tells testers they can add details over multiple messages. Raw engine
> state names are no longer exposed in normal private-bot replies. Public
> Portfolio Guru, Kaizen filing, billing, credentials, launchd, and production
> token paths remain untouched. Verification: focused private-vNext gate green
> at 193 passed, 1 warning; full offline backend gate green at 819 passed,
> 13 deselected, 43 warnings, 3 snapshots passed; local smoke confirmed
> first/second case messages collect and "done" shows CBD preview. Private
> bot restarted on the new code. Follow-up polish removed a duplicated
> "say done" instruction and added regression coverage. Second live dogfood
> exposed the next gap: greetings/features/ordinary chat were safe but dumb,
> all receiving the same collector nudge. The reply policy now answers
> greetings, wellbeing checks, and feature/help questions as chat; those turns
> do not create case facts or show the case-collection prompt. Verification:
> focused side-chat/vNext tests green at 76 passed, 1 warning; full offline
> backend gate green at 822 passed, 13 deselected, 43 warnings, 3 snapshots
> passed; local smoke confirmed "hello there", "how are you", and "what are
> your features" produce distinct chat replies before case capture still works.
> Next step: dogfood the same opening chat plus case flow in Telegram.

> **2026-06-01 addendum — retired separate vNext bot path.**
> The separate private vNext bot dogfood path is now historical. Its polling
> loop, private-token scaffold, and runner script have been removed after the
> conversational engine was promoted into the main bot behind
> `PG_GATHERING_MODE`. The live pieces are the in-bot gathering mode,
> conversational collector policy, source-tied extractor, form recommender,
> draft preview helper, Telegram adapter, and engine modules imported by
> `bot.py`.

> **2026-05-28 addendum — QIAT stage/KC beta guardrails.**
> Moeed's image-derived QIAT dogfood showed a generic Higher/HST profile being
> collapsed into exact `ST4` on QIAT's year-specific dropdown, and curriculum
> tags under-shooting the beta requirement for substantive filings. The current
> slice stops generic Higher from filling exact-year schemas, blanks LLM-supplied
> exact training years unless the source names that year, deterministically
> supplements QI/QIAT/run-chart/audit drafts to at least three source-tied KCs
> (`SLO11 KC1`, `SLO11 KC2`, `SLO12 KC2`), derives SLO links from selected KCs,
> and corrects SLO10/SLO11 display labels. Verification: focused form wiring,
> conversation/snapshot checks, and the full offline backend gate are green.

> **2026-05-28 addendum — failed-filing intent gate.**
> Moeed's live LAT filing failure showed the active draft stayed open after an
> unconfirmed Kaizen save, so the next case image/text was treated as more
> detail for the same draft. The current slice adds an explicit checkpoint for
> failed or uncertain filing states: new input now asks whether to retry filing
> the current draft, keep editing it, start a separate case from the new input,
> or cancel the current draft. Verification: focused failed-filing regression
> tests green at 3 passed; full flow-walker gate green at 141 passed.

> **2026-05-27 addendum — LAT click-non-actionable filing robustness.**
> Investigated LAT Kaizen save failure. Fixed Playwright click timeout errors on
> `startDate`, `endDate`, `event-description`, `trainee_post`, `leadership_context`,
> and `clinical_reasoning` fields when elements are obscured/non-actionable. Patched
> the deterministic filing helpers (`_fill_date`, `_fill_text`, and `_fill_field_legacy`)
> in `kaizen_form_filer.py` to catch click exceptions, retry with `force=True`, and
> fall back to direct focus / JS input events if required. Added focused coverage in `test_kaizen_filer.py`
> ensuring non-actionable click exceptions are intercepted and successfully resolved via
> forced click and focus fallback. Full live status depends on launchd restart after verification.

> **2026-05-27 addendum — draft footer rationale polish live.**
> Moeed's management-ticket dogfood showed the draft footer still exposing a
> heavy divider plus verbose LAT model rationale (`EPIC/flow coordinator`,
> `EMLeaders framework assessed by a LAT`). This slice removes the divider from
> draft rationale footers, sanitises form-choice rationale into a short
> user-facing sentence, keeps the concise reply/save/cancel instruction, updates
> render/snapshot tests, and restarts the launchd bot so the new copy is live.
> Verification: focused draft footer tests green at 2 passed; snapshot/render
> gate green at 9 passed, 133 deselected, 3 snapshots passed.

> **2026-05-27 addendum — attachment live proof passed.**
> The narrow beta attachment gate is complete. First controlled DOCX dogfood
> run proved draft save but exposed the real blocker: Kaizen creates its file
> input only after clicking the Upload button, so the old static input lookup
> skipped attachments. The current slice changes the attachment helper to use
> Kaizen's Upload button/file-chooser flow with a legacy input fallback, adds
> focused coverage for the upload-chooser path, restarts the bot, and reruns
> the same controlled proof. Retest result: Telegram document -> CBD
> recommendation -> draft preview -> Kaizen draft save passed; attachment was
> uploaded before save and was not reported as skipped. Verification: focused
> attachment/filer tests green; full offline gate green at 628 passed,
> 13 deselected, 43 warnings; commit pushed. Cleanup note: two synthetic CBD
> test drafts were created during the proof; the attached retest draft shows
> `Replace` / `Remove` controls, but the edit screen did not expose a precise
> draft-delete control, so broad deletion was not attempted.

> **2026-05-27 addendum — beta-ready attachment handoff.**
> Implemented a safe, lightweight attachment handoff for Telegram documents used as
> case source material. The original document is copied to a persistent temp/cache
> directory, and its metadata is stored in `context.user_data`. When the user saves
> the draft to Kaizen, the cached document is passed as `attachment_path` to the
> deterministic filing router. Missing or unsupported attachments at filing time
> are gracefully reported as skipped in the outcome summary without crashing.
> Verification: 6 focused unit tests covering document caching, path handoff,
> non-attachment paths, and graceful skip handling are green. Full offline
> pytest gate passed.

> **2026-05-27 addendum — Kaizen description summary guard.**
> Moeed's beta screenshot showed Kaizen's top `Description (optional)` field
> being filled with a clipped sentence ending mid-word (`recognis...`). The
> current slice routes the shared event-description builder through a one-line
> complete-summary guard: no ellipsis endings, no mid-word truncation, and
> supplied clipped descriptions are sanitised before filing. Verification:
> focused header/form-name tests green at 52 passed, 1 warning; full offline
> pytest gate green at 621 passed, 13 deselected, 43 warnings.

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
on` and `End date` header fields were still blank. The current slice routes
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

## Active Sprint — Kaizen Mapping (2026-06-01)

Read-only Kaizen mapping promoted from per-form skill code into a reusable
**platform adapter**, plus the first build slice (Kaizen Portfolio Index v1).
Full plan: `docs/roadmap/kaizen-mapping-sprint-2026-06.md`. The private-beta
launch cut below remains the operational objective for the live bot;
this sprint runs in parallel as the foundation for Portfolio Health,
ARCP readiness, and the CESR overlay.

### Scorecard (definition of done)

| #   | Deliverable                                                                                                   | Status                                                 |
| --- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| 1   | Sprint plan doc landed and linked from `docs/plan.md` and this file                                           | done (this commit)                                     |
| 2   | Adapter contract reconciles with `domain_skill/README.md` and `portfolio-structure.md` without contradictions | done (audited in sprint doc §"What is already mapped") |
| 3   | Gap list (1–8 in sprint doc §"Gaps to verify") queued for foreground live verification                        | done (recorded; live work is foreground-owned)         |
| 4   | Quality gates checklist exists and is referenced by the Index v1 build slice                                  | done (sprint doc §"Quality gates")                     |
| 5   | Index v1 schema is the contract the next implementing slice will follow                                       | done (storage substrate landed)                        |
| 6   | `docs/PORTFOLIO_HEALTH_SPEC.md` Phase 2 auto-populate clause references the Index                             | done (this commit)                                     |
| 7   | Read-only sync driver populates `evidence_items` from timeline/detail/activity surfaces                       | done offline; live CDP smoke still pending             |
| 8   | No live Kaizen actions in this sprint's docs work                                                             | met                                                    |
| 9   | No write codepath added in this sprint's docs work                                                            | met                                                    |

### Proof gate

- `git diff --check` clean.
- `docs/roadmap/kaizen-mapping-sprint-2026-06.md` exists and is the single
  restartable artefact for this sprint.
- `TASK.md` (this file), `docs/plan.md`, and `docs/PORTFOLIO_HEALTH_SPEC.md`
  all reference the sprint doc; existing history is preserved.
- No edits in this sprint to: bot runtime, filer (`filer.py`,
  `browser_filer.py`, `filer_router.py`, `kaizen_form_filer.py`),
  credential storage, `assessor_writeback.py`, deployment, launchd, GitHub
  Actions runner config, secrets, tests, or live Kaizen.
- No push, PR, deploy, restart, or live Kaizen action from this sprint's docs
  work. Orchestrator owns commit and closure.
- Next step: controlled foreground read-only CDP smoke against the logged-in
  Kaizen session, then wire a guarded refresh trigger only if indexed row
  quality is acceptable.
- Focused sync/index/health gate green at 67 passed. Full offline backend
  gate green at 848 passed, 13 deselected, 3 snapshots passed, 49 warnings.

### Out of scope (carried forward)

- All carried-forward guardrails in `## Guardrails (Carried Forward)` below
  remain unchanged. Filing stays draft-only; assessor save-draft stays
  CBD-only and confirm-gated.
- No new Kaizen surfaces are written to as part of this sprint.

---

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

## 2026-06-01 — Portfolio Health as Action Plan

Product decision: Portfolio Health should not lead with an audit-style dump
of counts, grids, and form types. It should tell the doctor the ARCP risk,
why, and the next three highest-value filing actions.

Implemented:

- ARCP health output now starts with `ARCP risk`, a plain-English `Why`, and
  `Next 3 actions`.
- The old leading `Deterministic health score`, `Domain coverage`, and
  `Form types` dump is removed from the ARCP result surface.
- The result still shows useful context: strong domains, missing domains, and
  total Portfolio Guru filings in the last 6 months.
- Health result buttons now offer `File missing evidence` and `Back to
settings`.
- Health refresh and inline health Back buttons now return to settings, not
  the generic filing welcome/menu.

Verification:

- Focused health tests: `backend/venv/bin/python -m pytest
backend/tests/test_health_bot.py backend/tests/test_health_index_integration.py
-q` → 59 passed.
- Health + callback flow shard: `backend/venv/bin/python -m pytest
backend/tests/test_health_bot.py backend/tests/test_health_index_integration.py
backend/tests/test_flow_walker.py -q` → 201 passed.

Runtime state:

- Code is not live until the Mac Mini bot is restarted/deployed.
