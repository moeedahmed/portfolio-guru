# Active Task — Kaizen Mapping Sprint

> **2026-06-15 addendum — EMGurus WhatsApp Gateway bridge: outbound reply path.**
> Scope: wire the first real Portfolio Guru workflow response back to the WhatsApp
> user through the OpenClaw gateway. Offline/local implementation only — no live
> WhatsApp send, no deploy, no restart, no push, no live Kaizen, no Telegram.
>
> Result:
>
> - Added `extensions/whatsapp/src/inbound/portfolio-outbound-route.ts` (OpenClaw
>   worktree) — `registerPortfolioOutboundRoute` registers
>   `POST /api/channels/whatsapp/:accountId/send` via `registerPluginHttpRoute`
>   (`auth: "plugin"`), verifies `X-Portfolio-Secret` against
>   `PORTFOLIO_BRIDGE_SECRET`, calls the existing `createWebSendApi().sendMessage`.
>   Inert unless `PORTFOLIO_BRIDGE_SECRET` is set.
> - Modified `extensions/whatsapp/src/inbound/monitor.ts` (OpenClaw worktree) —
>   registers the outbound route after `createWebSendApi` is created in
>   `attachWebInboxToSocket`, unregisters on `close()`.
> - Modified `backend/webhook_server.py` — `portfolio_inbound` now runs the first
>   workflow step on HANDLE: produces a channel-neutral gathering `ChannelReply`
>   via `_make_initial_gathering_reply`, renders it with `render_numbered`, and
>   POSTs to the gateway via `_send_portfolio_turn_reply` (injectable, testable).
>   GROUP/EMPTY dispositions unchanged. Outbound failures are logged as warnings;
>   the inbound handler always returns successfully. Three new env vars:
>   `PORTFOLIO_OUTBOUND_URL`, `PORTFOLIO_OUTBOUND_ACCOUNT_ID`,
>   `PORTFOLIO_OUTBOUND_SECRET` — all optional; feature inert if absent.
> - Extended `backend/tests/test_portfolio_inbound_bridge.py` — 6 new tests
>   proving outbound is invoked on HANDLE with rendered text; GROUP/EMPTY do not
>   trigger outbound; auth still rejects wrong secret; outbound failure safe; HANDLE
>   returns successfully with no outbound configured.
>
> Verification:
>
> - OpenClaw: `node scripts/run-vitest.mjs
>   extensions/whatsapp/src/inbound/portfolio-outbound-route.test.ts` → 11 passed.
>   `node scripts/run-vitest.mjs
>   extensions/whatsapp/src/auto-reply/monitor/portfolio-bridge.test.ts` → 17 passed (unchanged).
> - Portfolio Guru: `venv/bin/python3 -m pytest tests/test_portfolio_inbound_bridge.py
>   -v` → 15 passed. Full offline gate → **1408 passed, 0 failed, 16 deselected**.
>
> Release classification: **local/build-complete, proof-pending**.
>
> Remaining live/manual gates (orchestrator/foreground only): set
> `PORTFOLIO_OUTBOUND_URL` / `PORTFOLIO_OUTBOUND_ACCOUNT_ID` /
> `PORTFOLIO_OUTBOUND_SECRET` in Mac Mini BWS; set `PORTFOLIO_BRIDGE_SECRET`
> in OpenClaw gateway env; live WhatsApp DM end-to-end smoke; push both repos.

> **2026-06-15 finish-line check — still blocked before live smoke.**
>
> Result:
>
> - Portfolio Guru launcher now exports the outbound aliases expected by
>   `backend/webhook_server.py`: `PORTFOLIO_OUTBOUND_URL` from mapped
>   `PORTFOLIO_BRIDGE_URL`, `PORTFOLIO_OUTBOUND_ACCOUNT_ID=emgurus`, and
>   `PORTFOLIO_OUTBOUND_SECRET` from mapped `PORTFOLIO_BRIDGE_SECRET`.
> - Focused Portfolio bridge test re-run with the real backend venv:
>   `./backend/venv/bin/python -m pytest backend/tests/test_portfolio_inbound_bridge.py -q`
>   → 15 passed.
> - Installed WhatsApp extension test commands are not currently runnable from
>   the installed package: no npm scripts or local Vitest/TypeScript binaries;
>   external Vitest fails with `Tsconfig not found`; `tsc --noEmit -p tsconfig.json`
>   fails because `/Users/moeedahmed/.openclaw/extensions/tsconfig.package-boundary.base.json`
>   is missing and plugin-sdk types cannot resolve.
> - Critical runtime split: OpenClaw loads WhatsApp from
>   `~/.openclaw/extensions/whatsapp/dist/index.js`. The new
>   `src/inbound/portfolio-outbound-route.ts` is not present in loaded `dist`
>   (`rg portfolio-outbound dist src` only finds the new source/test files).
>
> Release classification: **blocked at installed WhatsApp runtime bundle**.
>
> Required Operator action: rebuild/reinstall or safely patch the installed
> WhatsApp extension so the loaded `dist` contains the Portfolio outbound route,
> make the required gateway env available without exposing secrets, run a safe
> gateway restart, then run the private WhatsApp DM smoke.

> **2026-06-15 addendum — Trusted filing sprint (offline dogfood matrix).**
> Scope: make RCEM Kaizen filing feel boringly reliable before widening
> ambition. Offline/release-readiness pass only — no push, deploy, restart,
> live Telegram, live Kaizen, credentials, or draft creation.
>
> Result:
>
> - Added `backend/tests/test_dogfood_matrix.py` — a reusable offline scorecard
>   over the ten high-value forms (CBD, Mini-CEX, DOPS, ACAT, SDL, Reflective
>   Practice Log, Procedure Log, Teaching, QIAT, Educational Activity) across
>   five reliability dimensions: first-message handling, draft/recommendation
>   path, missing-field handling, deterministic Kaizen-save readiness, and
>   incomplete-draft recovery. Dimensions 1–4 are pure data assertions over the
>   live form schema; dimension 5 drives the real conversation handler. 58
>   passed.
> - Confirmed existing generic form-lock + SDL incomplete-draft recovery remain
>   release-ready (`test_sdl_dogfood_fixes.py`). Missing-field recovery is
>   generic over form type (matrix asserts CBD/SDL/REFLECT_LOG/QIAT all
>   re-enter amend mode on a complaint, never reset to idle copy). No raw
>   markdown in any asserted user-visible copy.
> - **Top reliability failure fixed (workflow pattern, test-isolation):**
>   `tests/test_channel_contract.py::test_module_imports_without_telegram`
>   reloaded `channel_contract` in-process, leaving importers
>   (`webhook_server`) bound to stale classes, which made every later
>   `test_portfolio_inbound_bridge` request fail body validation with HTTP 422
>   (a pure test-ordering flake — production unaffected). The check now runs in
>   an isolated subprocess, preserving its intent without poisoning the suite.
> - Carried the `extract_explicit_form_type(require_intent=...)` test-stub
>   fixes in `tests/qa_transcript.py` and `tests/test_e2e_offline.py` (stale
>   `lambda text: None` doubles broke after the committed generic form-lock API
>   change in `b3257cd`).
>
> Known finding (documented, not fixed): explicit keyword form-lock covers 7 of
> the 10 named forms; REFLECT_LOG, PROC_LOG and TEACH do not lock from a bare
> "file a …" phrase and resolve via the recommendation path instead. The matrix
> pins this split (`KEYWORD_LOCK_FORMS` / `RECOMMENDATION_ONLY_FORMS`) so a
> regression either way is caught. Re-adding REFLECT_LOG keywords risks
> colliding with SDL ("reflection") intent, so the keyword gap is left as a
> recorded finding rather than a contested production edit.
>
> Verification: full offline gate via `backend/venv`
> (`venv/bin/python3 -m pytest tests/ --ignore=test_e2e --ignore=test_e2e_live`):
> **1400 passed, 0 failed, 16 deselected.** Focused changed-surface
> (matrix + SDL fixes + channel_contract + inbound_bridge + e2e_offline + qa
> transcript): all green.
>
> Release readiness: `scripts/release_loop.sh --surface telegram --mode prepare`
> → **BLOCKED**, for two reasons, neither a code defect:
>
> 1. _Offline preflight failed (24 collection errors)._ `scripts/preflight.sh`
>    resolves its interpreter to system `python3` (the repo-root `../.venv`
>    symlink is broken and `backend/.venv` does not exist), which lacks project
>    deps (telegram, fastapi, sqlalchemy, sqlmodel, respx) → all 24 errors are
>    `ModuleNotFoundError` at collection, not test failures. The real gate run
>    under `backend/venv` is fully green. This is a pre-existing env/tooling
>    mismatch (preflight does not know about `backend/venv`); left for
>    orchestrator/infra to resolve rather than editing release tooling here.
> 2. _Uncommitted tracked changes_ — resolved by this commit.
>
> Release classification: **release-ready (offline-proven), ship-gated.**
> Remaining live/manual gates (orchestrator/foreground only): fix preflight venv
> resolution (or repo-root `.venv`) so `release_loop prepare` passes; live
> Telegram smoke; manual Haris SDL/dogfood retest; push → `deploy-mac.yml` →
> `dogfood_smoke.sh`. Pre-existing dirty `docs/continuity/RESUME_BRIEF.md` left
> untouched (out of sprint scope).

> **2026-06-14 addendum — EMGurus WhatsApp Gateway boundary (contract only).**
> Scope: prepare Portfolio Guru to sit behind one EMGurus WhatsApp Gateway
> without connecting this repo to WhatsApp or touching live credentials. The
> locked architecture: one WhatsApp business number + one external EMGurus
> gateway/router; Portfolio Guru stays a separate internal service for 1:1
> ARCP/Kaizen workflows. Group/community/exam behaviour belongs to the other
> Gurus behind the same gateway, not this repo.
>
> Result:
>
> - Added `backend/channel_contract.py` — the channel-neutral _inbound_
>   counterpart to `channel_actions.py`. A gateway hands in an `InboundMessage`
>   (`SessionRef` channel/conversation/user, `ConversationScope` DIRECT|GROUP,
>   `text`, `MediaRef` tuple, `private=True` default). `accept_inbound()` is the
>   single entrypoint: `HANDLE` for DIRECT-with-content, `REFUSE_GROUP` (with a
>   channel-neutral `ChannelReply` refusal that never echoes content) for group
>   scope, `REFUSE_EMPTY` otherwise. DM-vs-group routing is explicitly the
>   gateway's job; Portfolio Guru refuses and does not own group mode.
> - Portfolio evidence is private by default and never shared into group context
>   (privacy contract enforced by `private=True` default + group refusal).
> - No live handler imports the contract yet; the Telegram path is unchanged.
>   No Meta/WhatsApp connection, no credentials, no Kaizen save path touched.
> - Architecture, responsibility split (gateway-owned vs PG-owned), and the next
>   build slice are recorded in `docs/plan.md` (2026-06-14 section).
>
> Verification: `tests/test_channel_contract.py` — 10 passed. Full offline gate
> (`pytest tests/ --ignore=test_e2e --ignore=test_e2e_live`): 1307 passed, 1
> pre-existing unrelated failure
> (`test_flow_walker.py::...routes_to_settings_for_stats` asserts `plan: free`
> but the account now shows `plan: beta (unlimited)` — billing copy drift, a
> locked no-touch area, not caused by this slice). Not run: live Telegram smoke,
> push, deploy, restart.
>
> Launch surface classification: **private-by-design.** This is an internal
> integration contract; nothing user-facing changed and there is no public
> surface to prove. No Notion sync needed (no human-facing state changed).

> **2026-06-08 addendum — Portfolio defaults back-button routing fix.**
> Scope: follow-up to the settings grouping below, after a Telegram screenshot
> showed the section-level Back buttons skipping the new submenu.
>
> Result:
>
> - The Portfolio defaults sections (Portfolio type, Pathway, Curriculum) now
>   have a `🔙 Back to portfolio defaults` button routing to
>   `ACTION|portfolio_defaults`, instead of jumping straight to main `/settings`.
> - The Portfolio defaults submenu's own Back button is relabelled
>   `🔙 Back to settings` (was a bare `🔙 Back`) and still routes to
>   `ACTION|settings`, matching the wording used elsewhere.
> - Save/select flows (`handle_set_level`, `handle_set_curriculum`,
>   `handle_pathway_choice`) keep their current return-to-settings behaviour.
> - Added regression tests pinning all four back-button routes in
>   `tests/test_health_bot.py`.
>
> Verification: focused settings tests passed (health_bot, health_index_integration,
> setup_manual_profile_fallback, flow_walker — 260 passed). Not run: live Telegram
> smoke, push, deploy, restart.

> **2026-06-08 addendum — settings menu grouping.**
> Scope: clean up the Telegram `/settings` surface after dogfood screenshots
> showed the main settings screen behaving like a debug/control panel.
>
> Result:
>
> - Top-level `/settings` now shows configuration groups only: Kaizen
>   connection, Writing style, Portfolio defaults, and Reset data.
> - Portfolio type, Portfolio Health pathway, and curriculum selection now sit
>   behind a focused `Portfolio defaults` submenu with a Back button.
> - Portfolio Health remains available through `/health`, not as a settings
>   control.
> - Settings, setup redirect, weird-prompt QA, and health/settings regression
>   tests were updated to pin the grouped layout.
>
> Verification: focused settings tests passed; offline Telegram QA passed; E2E
> offline tests passed; full offline gate passed with 1291 tests and snapshots.
> Not run: live Telegram smoke, push, deploy, restart.

> **2026-06-06 addendum — same-Telegram Kaizen account-switch isolation repair.**
> Scope: critical offline follow-up after live evidence showed `/delete` then
> reconnecting Sana's Kaizen credentials could still show Moeed's old Portfolio
> Health data for the same Telegram account.
>
> Result:
>
> - `/delete` now clears local filing history, KC coverage, Kaizen index rows,
>   index run audits, health profile data, and Kaizen session cache, not just
>   credentials/profile state. The delete confirmation text now says local
>   filing history and Portfolio Health evidence are cleared.
> - `/setup` detects a changed Kaizen username for the same Telegram user and
>   clears local account-scoped health/evidence/cache before accepting the new
>   account metadata.
> - Setup credential verification now uses an isolated Playwright login context
>   and detects portfolio type from that logged-in page, avoiding reuse of a
>   managed CDP tab/profile that may already be logged in as another account.
> - Read-only Kaizen sync now restores/saves session cache with the Kaizen
>   username as part of the cache key, matching deterministic filing.
> - Added offline regression pins for stale `/health` source clearing after an
>   account switch, setup-triggered purge on username change, isolated login
>   role detection, and username-scoped sync cache restore/save.
>
> Verification: focused health/setup/sync/login/profile tests passed; broader
> related files passed. No live Telegram, live Kaizen, BWS output, CDP browser,
> Kaizen writes, deploy, push, restart, or real data deletion used. Remaining
> gate: one approved manual/live reconnect smoke should confirm the live managed
> CDP browser no longer reports the previous Kaizen account after reconnect.

> **2026-06-06 addendum — Sana Non-Trainee Higher/CESR detection repair.**
> Scope: offline-only fix for a live-reported first-link regression where a
> Non-Trainee Higher / CESR-Portfolio Pathway account could be treated as HST.
>
> Result:
>
> - Kaizen portfolio detection now normalises Unicode dash / non-breaking-space
>   variants before matching, so `Non‑Trainee Higher` markers remain visible.
> - Portfolio Health autoset now maps `non_training_higher` and
>   `non_training_unknown` to `Pathway.cesr_portfolio`; real HST/ACCS/
>   Intermediate still map to `training_arcp`.
> - Health result buttons keep `File missing evidence` and replace weak
>   `Back to settings` with `Change pathway`.
> - Added deterministic offline pins for the Sana-like fixture, non-training
>   health autoset/setup flow, and health result keyboard.
>
> Verification: focused regression tests and the main offline pytest gate
> passed; no live Telegram, Kaizen writes, BWS output, deploy, push, restart,
> or reset used.

> **2026-06-06 addendum — weekly digest lean nudge (slice 2).**
> Scope: turn the weekly Portfolio Health push from a dense 4-panel dashboard
> chart into a compact behaviour nudge. The dense dashboard stays behind /health;
> the weekly reminder is now a simple card + short caption.
>
> Result:
>
> - New `generate_weekly_nudge_chart_async` in `backend/portfolio_chart.py` —
>   renders a compact "Portfolio Check-In" card with at most 3 data points
>   (cases this week, form types this month, gap) and one highlighted next
>   action. No dense bar charts, no SLO grid, no usage headline. Kept under
>   600x320 — a nudge, not a report.
> - `_build_weekly_digest_text` in `backend/bot.py` rewritten to produce a
>   short (<200 char), human, action-led caption that pairs with the card.
>   Format: one win, one signal, one deficiency, one action. Stateless empty
>   and no-gap variants handled.
> - `weekly_push` job now calls `generate_weekly_nudge_chart_async` instead of
>   `generate_health_chart_async`. The `/health` command path is untouched and
>   still uses the dense dashboard chart.
> - All weekly digest copy is pathway-neutral — no ARCP, CESR, or CCT framing
>   in the weekly nudge surface.
> - New focused tests in `backend/tests/test_health_bot.py` (14 tests):
>   caption composition (empty/gap/no-gap/pathway-neutral/length limits),
>   chart helper functions (win/signal/deficiency/action line formatters),
>   and chart rendering (PNG output for normal/empty/no-gap states).
>
> Verification: offline gate `1250 passed, 16 deselected`; zero regressions.
> No live Telegram, Kaizen, BWS, CDP, deploy, restart, or push used.
> Activation: next bot restart will pick up the new chart + caption in
> `weekly_push` jobs. No manual deploy gate needed beyond normal restart.

> **2026-06-06 addendum — deterministic release-closure loop (slice 1).**
> Scope: stop leaving deploy/restart as a remembered second step after a local
> fix. Wrap the repeatable closure behind one gated entrypoint while AI keeps
> doing diagnosis + code + commit.
>
> Result:
>
> - New `scripts/release_loop.sh --surface telegram --mode prepare|ship` — a thin
>   orchestrator that reuses existing pieces, never reimplements deploy logic.
> - `prepare` is safe/non-live: prints git state, runs `scripts/preflight.sh` +
>   `scripts/telegram_qa_offline.sh`, checks branch/clean/fast-forward gates, and
>   reports READY (exit 0) or BLOCKED (exit 1) with reasons. Never pushes/deploys.
> - `ship` is gated and conservative. Checks approval FIRST (before any git fetch
>   or mutation): `RELEASE_APPROVED=telegram-YYYYMMDD` (date+surface scoped) or
>   `--approved`. Refuses on main/detached, dirty tracked tree, non-fast-forward,
>   or nothing-ahead. Then re-runs the offline gates, reconciles branch→main and
>   pushes (fast-forward only) so `.github/workflows/deploy-mac.yml` →
>   `scripts/deploy_mac.sh` deploys + restarts on the Mac Mini, prints
>   deploy/restart proof commands (optional `gh run watch` via
>   `RELEASE_LOOP_WATCH_DEPLOY=1`), and runs the `scripts/dogfood_smoke.sh`
>   checkpoint. Deploy/restart is delegated to CI, not reimplemented.
> - New `backend/tests/test_release_loop.py` (8 tests): syntax, --help, every
>   usage/refusal gate. Fast + offline; never ships.
> - Docs updated: `docs/dev-workflow.md` (Release closure section) and `AGENTS.md`
>   point future agents at this entrypoint.
>
> Verification: `bash -n` + `shellcheck` clean; `--help` and ship-refusal paths
> exit as designed (2 = no/stale approval, 3 = tree/branch gate, 64 = usage);
> `prepare` reported READY with 1227 offline tests + telegram offline QA passing.
> Not run: `ship` with approval, push, deploy, restart, live Telegram.
>
> **2026-06-05 addendum — autonomous QA-to-fix loop wired.**
> Scope: extend the offline weird-prompt QA harness so failures produce a
> machine-readable fix queue a coding agent can act on without touching live
> Telegram.
>
> Result:
>
> - `WeirdPromptCase` gains a `category` field (product-help / safety /
>   form-choice / command / capability / random / style); all 13 existing
>   cases are tagged.
> - `WeirdPromptObservation` gains `category` and `fix_hint` fields; fix hints
>   are derived from failure reasons and category at observation time.
> - New `_generate_fix_queue(observations)` helper produces a structured dict
>   with `failure_count`, `total_cases`, and a `fixes` list. Each fix entry
>   records: prompt id, category, reply preview (≤150 chars), button labels +
>   action IDs, state flags (`entered_case_processing`, `has_gathering_case`),
>   user data keys, failure reasons, and a concrete fix hint.
> - `_write_reports` now returns a third path and writes
>   `.artifacts/weird-prompt-qa/latest/fix-queue.json` only when
>   `failure_count > 0`.
> - `_render_markdown` shows category, button action IDs, and a bold Fix hint
>   line for failed cases.
> - `scripts/weird_prompt_qa.sh` captures pytest's exit code, prints the fix
>   queue path + failure count when the file is present, and prints a
>   next-action line. Exits non-zero when pytest fails.
> - New unit tests: `test_fix_queue_empty_when_all_pass`,
>   `test_fix_queue_contains_failed_case_with_full_evidence`,
>   `test_fix_queue_reply_preview_truncated_at_150`,
>   `test_derive_fix_hint_routing_failure`,
>   `test_derive_fix_hint_forbidden_text`,
>   `test_derive_fix_hint_missing_expected_text`.
> - `TESTING.md` Layer 5B and `TASK.md` updated.
>
> Usage: `bash scripts/weird_prompt_qa.sh` → if failures exist, read
> `.artifacts/weird-prompt-qa/latest/fix-queue.json`, fix the routing/reply
> gaps, re-run to verify.
>
> **2026-06-05 addendum — weird-prompt QA harness added.**
> Scope: replace screenshot-driven random prompt testing with an offline
> deterministic runner that exercises the Telegram handler without contacting
> live Telegram.
>
> Result:
>
> - New `backend/tests/test_weird_prompt_qa_offline.py` feeds product-help,
>   safety, prompt-injection, pricing, settings/stats, random, style, and
>   form-choice prompts through `handle_case_input` with `BotSimulator`.
> - The harness writes Markdown + JSON reports under
>   `.artifacts/weird-prompt-qa/` and fails if a non-case prompt creates
>   gathering state, enters `_process_case_text`, or shows `Draft now`.
> - New `scripts/weird_prompt_qa.sh` runs the report lane directly.
> - Pricing and style questions are now deterministic: no "completely free"
>   pricing hallucination and no marketing-style "lock it in" copy.
>
> Verification: `bash scripts/weird_prompt_qa.sh` passed and focused
> answer/gathering tests passed (`43 passed`).
>
> **2026-06-05 addendum — intelligence-layer question routing repair landed offline on
> branch `feature/conversation-supervisor-20260605`.**
> Scope: dogfood fix after random/product questions were being treated as case
> material, creating stale `Draft now` paths and generic/long catalogue answers.
>
> Result:
>
> - Standalone product/help/form-choice/safety/random prompts now route before
>   extraction or drafting, but after the existing settings/stats menu router.
> - Stale `Draft now` callbacks now refuse to draft when no gathered case is
>   present and clear stale gathering state.
> - Obvious form-choice prompts now return brief recommendations instead of the
>   supported-forms catalogue: procedural sedation -> DOPS / Procedural Log,
>   septic shock -> CBD / Mini-CEX / ACAT, child wheeze -> CBD / Mini-CEX,
>   teaching -> Teaching Session / STAT.
> - Supported-forms copy stays concise: 45-form truth, five examples, no
>   duplicated names, and no stale "...and 9 more" copy.
>
> Verification: focused routing/gathering/menu gate passed (`96 passed`), failed
> full-suite menu regressions were fixed, and the full offline gate passed:
> `1174 passed, 27 deselected`.
>
> Runtime state: ready for local bot restart/health check.
>
> **2026-06-05 addendum — conversation-supervisor slice landed offline on
> branch `feature/conversation-supervisor-20260605`.**
> Scope: consolidate fragmented gathering-mode decisioning into one
> channel-agnostic control loop and make the Telegram flow portable to
> WhatsApp. Filing reliability was explicitly out of scope.
>
> What changed:
>
> - New `backend/channel_actions.py`: a reply is defined once (`ChannelReply`)
>   and renders losslessly as Telegram buttons (`callback_data == action_id`)
>   and as a WhatsApp numbered block; `resolve_numbered_choice` maps a reply
>   back to the action id.
> - New `backend/conversation_supervisor.py`: `classify_gathering_turn` +
>   `decide_gathering_turn` separate canonical intent, turn kind, and the
>   channel-agnostic reply. Side questions go through an injected grounded
>   `answer_question` and always return a continuation line back to the case.
> - `message_policy.py` now owns capability/greeting/gathering copy.
> - The live "vNext private test bot / dogfood" side-chat copy is removed from
>   `vnext_dialogue_policy.py` and `bot.handle_gathering_input`, which now
>   delegates to the supervisor.
>
> Verification: `backend/venv/bin/python3 -m pytest tests/test_channel_actions.py
tests/test_conversation_supervisor.py tests/test_gathering_mode.py
tests/test_vnext_dialogue_policy.py -v` → 48 passed; full offline gate
> (`tests/ --ignore test_e2e --ignore test_e2e_live`) → 1140 passed.
>
> Runtime state: not live until the Mac Mini bot is restarted/deployed.

> **2026-06-02 addendum — local Kaizen form catalogue audit/fix landed offline.**
> Scope: follow-up after the Intermediate QIAT regression. The issue was local
> catalogue drift, not Kaizen: ACCS and Intermediate were distinct in role
> detection but collapsed back to the same `ST3` form-list object at the picker
> surface.
>
> Result:
>
> - `TRAINING_LEVEL_FORMS["ACCS"]` and `TRAINING_LEVEL_FORMS["INTERMEDIATE"]`
>   are now distinct list objects, so future portfolio-specific drift cannot
>   silently alias `ST3`.
> - Intermediate still exposes `QIAT`, and QI/audit projects continue to show
>   `Use best fit: QIAT`; genuine teaching remains `Teaching`.
> - Kaizen-visible but not fully wired ACCS/Intermediate forms are now recorded
>   in `KAIZEN_CATALOGUE_STATUS` instead of becoming broken buttons:
>   `ASAT`, `EPA1`, `EPA2`, `DOPS_ACCS`, `PROCEDURAL_LOG_ACCS`,
>   `ACCS_PROGRESS`, `INTERMEDIATE_PROGRESS`, `MCR_MTR_ACCS`, `HALO_ICM`,
>   `HALO_PROCEDURAL_SEDATION`, `IAC`, and `EDUCATIONAL_AGREEMENT`.
> - Hidden utility/admin surfaces remain non-clickable:
>   `ADD_POST`, `ADD_SUPERVISOR`, `FILE_UPLOAD`, `OOP`, `HIGHER_PROG`,
>   `ABSENCE`, and `CCT`.
>
> Verification:
>
> - Focused catalogue/recommender/wiring tests: `60 passed`.
> - QIAT / teaching conversation-path regressions: `5 passed`.
> - Full preflight on branch `fix/catalogue-audit-mappings`:
>   `1018 passed, 9 deselected, 88 warnings`; 3 snapshots passed.
>
> Boundary: no live Kaizen, Telegram, BWS, CDP, deploy, restart, push, or
> external action in this offline slice.
>
> **2026-06-02 addendum — P3 Ahmed/consultant assessor-boundary read-only smoke passed; supervisor write path parked.**
> Scope: final fixture in the filing-phase matrix after CESR 2021. Per
> Moeed's direction, this did not enter the full assessor/supervisor pathway:
> no ticket was opened, no feedback was drafted, no save/sign/submit/approve
> path was exercised, and no Telegram supervisor workflow was driven.
>
> Result:
>
> - Used the private BWS assessor credential aliases for Ahmed/consultant
>   access. No credential values were printed or stored in repo docs.
> - Kaizen login succeeded and the provider classified the account as
>   `assessor`.
> - Read-only navigation confirmed the account has a Clinical Supervisor
>   surface and no trainee `Create event` affordance on the checked pages.
> - Live drift found: the historical MyTimeline assessor barrier text
>   (`You cannot create any events!`) was not present on the current surface,
>   so a strict barrier-only role detector would return `unknown`.
> - `backend/role_detector.py` now keeps the historical barrier marker and
>   also recognises the current `Clinical Supervisor` marker, with a read-only
>   dashboard fallback when MyTimeline is inconclusive.
> - Regression pins added in `backend/tests/test_role_detector.py`.
>
> Verification:
>
> - Focused supervisor/role gate:
>   `54 passed, 23 warnings` in `test_role_detector.py`,
>   `test_supervisor_workflow.py`, and `test_supervisor_scheduler.py`.
>
> Boundary: no live Telegram impersonation, no trainee filing attempt, no
> supervisor ticket open, no Fill in, no Kaizen write, no save, no submit, no
> sign, no send, no approve, no reject, no delete, no deploy, no push, and no
> production rollout. The assessor/supervisor product pathway remains a next
> phase after filing coverage.
>
> **2026-06-02 addendum — P3 saved CESR 2021 bot-handler draft smoke passed and cleaned up; Sana identity not pinned.**
> Scope: next controlled SAS/CESR fixture after Harris/Intermediate. The first
> step pinned the two locally saved SAS/CESR candidates before counting either
> as Sana. Live dashboard checks showed neither saved candidate is Sana:
> candidate `8520547917` is `Non-Trainee Higher`; candidate `613452099` is a
> `CESR (2021 Curriculum)` portfolio. The smoke therefore counts as saved
> CESR-shape coverage, not as Sana proof.
>
> Result:
>
> - Candidate `613452099` was used for the controlled CESR 2021 smoke because
>   it is the true `CESR (2021 Curriculum)` account.
> - The natural bot path (`Use best fit`) resolved the case to `CBD_2021`.
> - A forced `FORM|CBD` callback was deliberately not counted: Kaizen
>   redirected the 2025 CBD form URL to `/events/list`, no fields were filled,
>   and the activities check found no synthetic test draft or markers.
> - Bot-handler path then saved one synthetic `CBD - Case Based Discussion
(2021)` draft using the real save-as-draft approval handler.
> - Filing QA was GREEN for the 2021 CBD surface: 6 fields filled, stage
>   intentionally skipped / acceptable-empty for the CESR account.
> - Opened draft `8bfa7f9a-019c-4b38-aa3a-ebfd90710a10` was `DRAFT PRIVATE`
>   and contained the synthetic perforated-viscus / urgent-CT / CESR portfolio
>   evidence case.
> - Cleanup deleted only that document id after private-draft and case-marker
>   checks passed. Post-cleanup Saved drafts no longer contained that document
>   or the synthetic case markers.
> - Candidate profiles restored to their original local rows:
>   `training_level=SAS`, `curriculum=2021`, `kaizen_role=None`.
>
> Boundary: this did **not** prove Sana's personal account, and did not
> impersonate any user on live Telegram. No submit, sign, send, approve,
> reject, deploy, push, production rollout, or live Telegram message was sent.
>
> Next executable gate: either obtain/confirm the actual Sana identity if she
> must be counted specifically, or proceed to Ahmed/consultant supervisor
> boundary using the existing controlled-smoke approach.
>
> **2026-06-02 addendum — P3 Harris/Intermediate bot-handler draft smoke passed and cleaned up.**
> Scope: third controlled fixture after Moeed/HST and Harris/ACCS. The smoke
> used Harris's stored Portfolio Guru user id and credentials, temporarily
> scoped the local profile to `INTERMEDIATE` with the 2025 trainee curriculum,
> drove the same bot draft/approval handlers locally, saved one synthetic CBD
> draft in Kaizen, verified it, deleted it, then restored the original Harris
> profile row.
>
> Result:
>
> - Bot-handler path produced a CBD draft and called the real save-as-draft
>   approval flow for Harris's stored fixture user.
> - Kaizen deterministic filer set the stage to `Intermediate`.
> - Saved draft `6742b8c4-537a-49fb-8a29-b73cb0bc1f90` opened as
>   `DRAFT PRIVATE` and showed `Stage of training Intermediate / ST3`.
> - The opened draft contained the synthetic severe asthma case, including
>   magnesium, NIV/intubation planning, and clearer role allocation.
> - Cleanup deleted only that document id after private-draft and case-marker
>   checks passed. Post-cleanup Saved drafts no longer contained that document
>   or the severe-asthma markers.
> - Filing log row: `CBD`, deterministic, `success`, 7 fields, no error.
> - Harris profile restored to its original local row:
>   `training_level=INTERMEDIATE`, `curriculum=None`, `kaizen_role=unknown`.
>
> Follow-up fixed in the same slice:
>
> - Live verification proved the stage persisted, but the non-blocking QA pass
>   initially reported a false `stage_of_training(value_not_persisted)` gap
>   because Kaizen's saved summary view can replace the select element with
>   read-only text.
> - `backend/kaizen_form_filer.py` now counts saved summary text such as
>   `Stage of training Intermediate / ST3` as persisted stage evidence.
> - Regression pin added in `backend/tests/test_kaizen_filer.py`.
> - Focused QA check: 5 passed, 33 deselected.
>
> Boundary: this did **not** impersonate Harris on live Telegram. No submit,
> sign, send, approve, reject, deploy, push, production rollout, or live
> Telegram message was sent.
>
> Next executable gate: Sana/SAS-CESR controlled draft-only smoke, with the
> saved SAS profile identity confirmed before counting it as Sana.
>
> **2026-06-02 addendum — P3 Harris/ACCS bot-handler draft smoke passed and cleaned up.**
> Scope: second controlled fixture after Moeed/HST. The smoke used Harris's
> stored Portfolio Guru user id and credentials, temporarily scoped the local
> profile to `ACCS`, drove the same bot draft/approval handlers locally, saved
> one synthetic CBD draft in Kaizen, verified it, deleted it, then restored the
> original Harris profile row.
>
> Result:
>
> - Bot-handler path produced a CBD draft and called the real save-as-draft
>   approval flow for Harris's stored fixture user.
> - Kaizen deterministic filer set the stage to `ACCS - ST1 - ST2/ CT1 -CT2`.
> - Filing QA was GREEN: 7 fields filled, 0 expected-empty gaps.
> - Filing log row: `CBD`, deterministic, `success`, 7 fields, no error.
> - Opened draft `34e6cdaa-e14a-4bc6-ade7-d94baff393dd` was `DRAFT PRIVATE`
>   and contained the synthetic septic shock / sepsis six case.
> - Cleanup deleted only that document id after private-draft and case-marker
>   checks passed. Post-cleanup Saved drafts no longer contained that document
>   or a CBD saved draft.
> - Harris profile restored to its original local row:
>   `training_level=INTERMEDIATE`, `curriculum=None`, `kaizen_role=unknown`.
>
> Boundary: this did **not** impersonate Harris on live Telegram. It proved
> Harris/ACCS credentials, profile scoping, bot approval handler, deterministic
> Kaizen CBD save, verification, and cleanup. No submit, sign, send, approve,
> reject, deploy, push, production rollout, or live Telegram message was sent.
>
> Next executable gate: Harris/Intermediate controlled draft-only smoke.
>
> **2026-06-02 addendum — P3 Moeed/HST Telegram bot-path smoke passed and cleaned up.**
> Scope: real user-flow smoke after the stale-session fix: Moeed manually
> resent the synthetic HST CBD case to Portfolio Guru, reviewed the CBD draft
> preview, and tapped Save as draft. No Telegram automation was used.
>
> Result:
>
> - Bot-path save succeeded: Telegram showed the Kaizen draft saved state with
>   7 completed fields.
> - Kaizen verification found exactly one saved CBD draft dated 2 Jun 2026.
> - Opened event `8f801d70-e409-47fb-95f1-e171b35179fc` was `DRAFT PRIVATE`
>   and contained the synthetic STEMI/PPCI/closed-loop communication case.
> - Cleanup deleted only that event ID after the private-draft and case-marker
>   checks passed.
> - Post-cleanup Kaizen activities check showed Saved drafts empty: "There are
>   no items available."
>
> Boundary: no submit, sign, send, approve, reject, deploy, push, production
> DB write, or automated Telegram drive-by. Local bot restart was performed
> only to load the already-tested stale-session fix.
>
> Production-readiness interpretation: Moeed/HST now has both required proofs:
> direct deterministic Kaizen CBD filing and actual Telegram bot-path draft
> save, each verified in Kaizen and cleaned up.
>
> **2026-06-02 addendum — P3 Moeed/HST Telegram bot-path save failure fixed locally.**
> Scope: first real user-flow smoke after the direct Kaizen draft smoke:
> Moeed manually sent the synthetic HST CBD case to Portfolio Guru, selected
> CBD, previewed the draft, and tapped save. No Telegram automation was used.
>
> Result:
>
> - Draft generation worked: the bot produced the expected CBD preview for the
>   synthetic STEMI case.
> - Save failed before any field fill: filing log shows the Kaizen form load
>   redirected to `auth.kaizenep.com`, with `filled_count=0`.
> - Root cause: cached Kaizen session validation accepted the auth subdomain as
>   a valid Kaizen app page because the host still contained `kaizenep.com`.
> - Safety check: read-only Kaizen activities inspection found Saved drafts
>   empty and zero hits for the synthetic test terms; no cleanup draft was left
>   behind.
>
> Fix:
>
> - `backend/kaizen_form_filer.py` now treats only the exact `kaizenep.com`
>   app host as a valid cached session, rejects `auth.kaizenep.com` /
>   interaction/login redirects, and re-authenticates once if a previously
>   accepted cache expires during form navigation.
> - `backend/bot.py` now classifies auth redirects/session expiry as a
>   login/session failure rather than a vague filling failure.
> - `backend/tests/test_kaizen_filer.py` adds regression pins for auth-subdomain
>   rejection and re-authentication before filling.
>
> Verification:
>
> - Focused stale-session pins: `3 passed`.
> - Filing-focused suites: `73 passed, 1 warning` and `22 passed, 1 warning`.
> - Full offline backend gate: `998 passed, 13 deselected, 3 snapshots passed`.
>
> Boundary at the time of the fix: local source fix only. No submit, sign,
> send, approve, reject, deploy, restart, push, production DB write, Telegram
> automation, or live retry had happened yet. Superseded by the later passed
> bot-path smoke above.

> **2026-06-02 addendum — P3 Moeed/HST direct Kaizen draft smoke passed and cleaned up.**
> Scope: first controlled live smoke for the approved Moeed/HST fixture, CBD
> form only, direct deterministic Kaizen filing path only. This was not the
> Telegram bot-path smoke.
>
> Result:
>
> - Local smoke branch: `smoke/moeed-hst-draft-only-20260602`.
> - Preflight gate: `995 passed, 9 deselected, 3 snapshots passed`.
> - Live direct filing: one synthetic CBD draft saved to Kaizen with a unique
>   `INTEGRATION TEST — DO NOT USE` run marker.
> - Cleanup: the default marker cleanup helper initially refused because the
>   persistent CDP profile was at the Kaizen auth screen and could not see the
>   draft marker. Cleanup then re-authenticated with the same Moeed fixture,
>   verified the exact run marker, event id, private-draft state, and visible
>   delete control, then deleted that single draft.
> - No submit, sign, send, approve, reject, deploy, restart, push, production
>   DB write, or Telegram automation.
>
> Production-readiness interpretation: this proves the deterministic Kaizen
> CBD filer can create and clean up a real HST draft, but it does **not** yet
> prove the end-user Telegram path. Next gate is Moeed/HST bot-path smoke via
> manual Telegram taps against the dev bot: synthetic text case → CBD preview
> → Save as draft → verify in Kaizen → delete by hand.
>
> **2026-06-02 addendum — consolidated Kaizen filing E2E test plan landed (docs + one offline pin).**
> Orchestrator-commissioned: a single restartable end-to-end testing plan for
> Kaizen filing across the approved four-account fixture matrix (Moeed/HST,
> Haris-Harris/ACCS+Intermediate dual access, Sana/SAS-CESR, Ahmed/consultant).
> The plan composes the existing Filing Reliability Readiness Sprint and the
> three-account validation doc into ordered phases (offline matrix → read-only
> mapping → draft-preview flow → controlled draft-only live smoke → Moeed
> manual checklist → fix-loop and promotion criteria) and pins the exact
> per-fixture stop-go gates plus the Moeed manual checklist that fires only
> after the next live gate.
>
> Files changed in this slice:
>
> - `docs/roadmap/kaizen-filing-e2e-test-plan-2026-06.md` (new) — the
>   consolidated plan + per-fixture P3/P4/P5 procedure + promotion criteria
>   roll-forward.
> - `backend/tests/test_detected_role_training_level_mapping.py` — two new
>   offline pins for Ahmed's consultant fixture: `assessor` detected role
>   maps to the `HIGHER` `training_level` bucket (UX continuity fallback),
>   and the raw `assessor` role / `HIGHER` bucket stay decoupled so the
>   supervisor workflow keys off the raw role, not the bucket. Closes the
>   only offline gap noticed during reconciliation; the other Ahmed-shape
>   contracts (`_pathway_for_detected_role("assessor") -> None`, role
>   detector body-text classification) were already pinned in
>   `test_health_bot.py` and `test_role_detector.py`.
>
> Boundary:
>
> - Offline-only. No live Kaizen, no Telegram, no BWS read, no CDP,
>   no deploy, no restart, no push. No filer / credential / supervisor
>   source files touched.
>
> Verification (no live action):
>
> - Focused new pins:
>   `cd backend && venv/bin/python -m pytest tests/test_detected_role_training_level_mapping.py -v`
>   → 21 passed, 30 warnings (pre-existing deprecation warnings only). Up
>   from 19 pre-this-slice; the delta is the two new Ahmed/consultant pins,
>   no regressions.
> - `git diff --check` clean.
>
> Next executable gate: P3 controlled draft-only live Kaizen smoke, one
> fixture at a time (Moeed/HST → Harris/ACCS → Harris/Intermediate → Sana
> → Ahmed supervisor confirmation-boundary). Foreground/operator-owned and
> approval-gated; worker does not run it. Plan: `docs/roadmap/kaizen-filing-e2e-test-plan-2026-06.md` §8.

> **2026-06-02 addendum — browser-agent architecture decision.**
> Browser-agent tooling should shape Portfolio Guru as a guarded fallback and
> mapping aid, not as the product's primary filing architecture. The product
> promise remains: API/document-first where available, deterministic
> portfolio adapters for supported forms, transparent browser automation only
> where Kaizen or another portfolio portal forces it, and draft-only saves
> behind explicit doctor approval.
>
> Decision:
>
> - Browser-agent-first is rejected for the core Kaizen filing path because it
>   would make a reliability product depend on opaque, non-deterministic UI
>   judgement at the exact moment users are paying for dependable filing.
> - Deterministic adapter/browser automation behind guardrails remains valid
>   for Kaizen today: DOM-mapped Playwright/CDP, form coverage tests, explicit
>   skipped-field reporting, attempt logging, retry semantics, and no
>   credentials or patient details in LLM prompts.
> - API/document-first with browser fallback is the strategic direction for
>   Portfolio Guru. Browser Use / Browser Harness / Browser Use Terminal can
>   support discovery, read-only mapping, emergency unsupported-form bridges,
>   and future non-Kaizen forced-portal adapters, but they must not replace
>   source-backed drafts, user preview, approval gates, or deterministic
>   filing tests.
>
> Sprint implication: the controlled P5 Kaizen smoke stays draft-only and
> deterministic. It should prove the current guarded adapter path across HST,
> ACCS/Intermediate, SAS/CESR, and consultant/supervisor-shaped surfaces. It
> should not introduce browser-agent-first filing, cloud browser credentials,
> or autonomous live portal exploration. Any Browser Harness domain-skill
> evidence can inform selectors and platform maps, but promotion still depends
> on offline gates plus the controlled draft-only smoke evidence.

> **2026-06-02 addendum — approved Kaizen fixture credentials recorded privately.**
> Moeed clarified the intended testing context for the shared Kaizen details:
> they are representative Portfolio Guru user fixtures for mapping the product
> across real portfolio surfaces and for extending bot features through deeper
> analysis of those portfolio types. The exact BWS credential IDs are now
> recorded only in the private OpenClaw secret registry as Portfolio Guru
> aliases; do not duplicate them in repo docs, prompts, tickets, or chat.
>
> Approved fixture coverage:
>
> 1. **Moeed** — HST portfolio.
> 2. **Haris / Harris** — ACCS and Intermediate portfolio access.
> 3. **Sana** — SAS / non-trainee / CESR portfolio.
> 4. **Ahmed** — consultant / supervisor portfolio access.
>
> Default use remains read-only mapping and analysis. Draft creation, Kaizen
> save/submit/delete, deploy, restart, push, spending, or any privacy-sensitive
> expansion still needs the specific gate approved before execution.

> **2026-06-02 addendum — P2/P3 SAS-CESR read-only gate cleared.**
> Filing Reliability Readiness Sprint §6: the foreground credential/live
> recovery gate for the local SAS/CESR saved-profile candidates has now passed
> read-only smoke. The smoke used the existing
> `sync_kaizen_portfolio_index_for_user(...)` helper, existing saved encrypted
> Portfolio Guru credentials, managed CDP Chrome on `localhost:18800`, and a
> temporary `/tmp` SQLite evidence DB. No draft was created, saved, submitted,
> signed, approved, sent, rejected, or deleted; no Telegram automation,
> production `usage.db` write, deploy, restart, or push occurred. The temporary
> evidence DB was unlinked after the run.
>
> Results:
>
> - SAS saved-profile candidate `8520547917`: `ok`; 15 rows seen, 13 indexed,
>   0 drifted.
> - SAS saved-profile candidate `613452099`: `ok`; 29 rows seen, 29 indexed,
>   0 drifted.
>
> Interpretation:
>
> - The SAS/CESR live read-only branch is no longer blocked by the previous
>   `auth_required` result for the saved local SAS-profile candidates. If
>   "Sana" refers to a different Telegram identity outside these saved SAS
>   profiles, that identity still needs a separate mapping check before being
>   counted as green.
> - P3 read-only smoke is now green for Moeed/HST, Harris dual-access, and the
>   locally saved SAS-profile candidates. Next executable gate: P5 controlled
>   draft-only live smoke. P5 remains approval-gated and must save one
>   synthetic draft per account, verify visibility in Kaizen, then delete the
>   draft by hand. No submission.

> **2026-06-02 addendum — P4 concurrency/idempotency slice landed (offline).**
> Filing Reliability Readiness Sprint §7: offline multi-user reliability
> proof is now pinned for draft-state isolation, filing-log isolation,
> profile/credential row isolation, and retry-after-DOM-drift behaviour.
>
> Files changed:
>
> - `backend/tests/test_concurrent_user_isolation.py` (new) — proves active
>   draft and retryable last-filed-case state stay isolated between two
>   simulated Telegram user contexts.
> - `backend/tests/test_filing_attempt_log.py` — proves two user attempts
>   append distinct filing-log rows, real users are not treated as synthetic,
>   and synthetic fixture traffic is excluded from real-user shape outcomes.
> - `backend/tests/test_profile_store_kaizen_role.py` — proves interleaved
>   `kaizen_role` / `training_level` writes do not collide and credential rows
>   remain user-scoped.
> - `backend/tests/test_filing_reliability.py` — proves an explicit retry
>   after simulated DOM drift reuses the original saved-draft URL and surfaces
>   the drifted field as skipped instead of creating a new draft path.
>
> Boundary:
>
> - Offline-only. No BWS, live Kaizen, CDP/browser session, Telegram
>   automation, production DB write, deploy, restart, push, or real
>   submission.
>
> Next executable gate: foreground live/credential path for P2/P3
> Sana/SAS-CESR recovery + read-only smoke, then P5 draft-only smoke only
> after P3 is green.

> **2026-06-02 addendum — P1.d slice landed (offline).**
> Filing Reliability Readiness Sprint §4 P1.d: filing-attempt outcomes are
> now grouped by internal portfolio shape so the admin report can expose
> shape-specific partial saves without putting Moeed / Harris / Sana into
> the product surface. Tester accounts remain fixtures only.
>
> Files changed:
>
> - `backend/filing_attempt_log.py` — records a PHI-free
>   `portfolio_shape`, normalised to lowercase, and adds `by_shape` summary
>   buckets for success, partial, failure, category, and skipped-field
>   counts. The admin report now includes a `Shape outcomes` section.
> - `backend/bot.py` — `_log_filing_attempt(...)` resolves the raw Kaizen
>   role first and falls back to local `training_level`, so Harris's
>   dual-access fixture can be distinguished from an Intermediate-only user
>   in internal reliability reporting.
> - `backend/tests/test_filing_attempt_log.py` — pins SAS stage partial-save
>   reporting, ACCS / Intermediate / Harris dual-access / HST success
>   outcomes, portfolio-shape normalisation, and bot wrapper shape capture.
>
> Boundary:
>
> - Offline-only. No BWS, live Kaizen, CDP/browser session, Telegram
>   automation, production DB write, deploy, restart, push, or real
>   submission.
>
> Next executable slice: P4.a/P4.b concurrency and idempotency offline
> proof. P2/P3/P5 remain live/credential-gated and should only run from the
> foreground operator path.

> **2026-06-02 addendum — P1.c slice landed (offline).**
> Filing Reliability Readiness Sprint §4 P1.c: recommended-form fallback
> is now pinned per portfolio shape and no longer falls through to the
> legacy `ST5` / HST superset for SAS, CESR, unknown, or empty profile
> buckets. Moeed / Harris / Sana remain trusted test fixtures only; these
> names and special account shapes are not product-facing app concepts.
>
> Files changed:
>
> - `backend/bot.py` — added `_allowed_forms_for_training_level(...)` as the
>   single helper for saved-profile → allowed-form catalogue. The three
>   recommender call sites plus the "See all forms" allowed-list path now
>   use this helper instead of inline fallbacks.
> - `backend/tests/test_form_recommender_per_shape.py` (new) — offline pins
>   for HST, ACCS-only, Intermediate-only, Harris dual-access storage alias,
>   SAS / CESR, and unknown/empty training levels. ACCS and Intermediate
>   stay distinct test ids even though they currently share the ST3 catalogue.
>
> Boundary:
>
> - Offline-only. No BWS, live Kaizen, CDP/browser session, Telegram
>   automation, production DB write, deploy, restart, push, or real
>   submission.
>
> Next executable slice: P1.d (partial-save / outcome categorisation by
> shape) — still offline-only unless the sprint gate explicitly reaches
> a live-smoke phase.

> **2026-06-02 addendum — P1.b slice landed (offline).**
> Filing Reliability Readiness Sprint §4 P1.b: per-shape detected-role →
> `training_level` mapping. Offline-only, no live Kaizen, no CDP, no BWS,
> no Telegram, no deploy/restart/push.
>
> Files changed:
>
> - `backend/tests/test_detected_role_training_level_mapping.py` (new) — 19
>   tests covering the five P1 shapes (`hst` / `accs` / `intermediate` /
>   `accs_intermediate_dual_access` / `sas_cesr`):
>   - Five parametrised pins on the setup/login bucket map: `hst -> HIGHER`,
>     `accs -> ACCS`, `intermediate -> INTERMEDIATE`, `sas -> SAS`, and
>     Harris's dual-access `accs_intermediate -> INTERMEDIATE` storage
>     alias.
>   - Explicit ACCS-vs-Intermediate distinctness guard so a silent collapse
>     is loud, not silent.
>   - Explicit pin that `accs_intermediate` is the dual-access storage
>     alias, not a standalone Kaizen portfolio type.
>   - Unknown / empty / `None` roles return `None` so setup falls through
>     to the manual portfolio-profile picker.
>   - Per-shape pins that `profile_store.store_kaizen_role(...)` preserves
>     the raw role verbatim and does **not** mutate `training_level`.
>   - Multi-user round-trip across all five shapes in a single in-memory
>     profile-store engine — raw role and `training_level` stay isolated;
>     no last-write-wins, no shared-row collisions; explicit ACCS vs
>     Intermediate row distinctness check on the persisted state.
> - `backend/bot.py` — exposes a tiny pure helper:
>   `detected_role_to_training_level(detected_role)`, backed by
>   `_DETECTED_ROLE_TO_TRAINING_LEVEL`. The setup-flow call site at
>   `setup_password` now uses the helper instead of an inline dict, and
>   the `label_map` gains an `intermediate` entry so an Intermediate-only
>   detected role gets the right label. No other bot behaviour changed;
>   `profile_store.store_kaizen_role` is untouched.
>
> Verification (no live action):
>
> - `cd backend && venv/bin/python -m pytest tests/test_detected_role_training_level_mapping.py tests/test_profile_store_kaizen_role.py tests/test_login_classification_per_shape.py tests/test_three_account_filing_matrix.py -q`
>   → 69 passed, 42 warnings (pre-existing deprecation warnings only).
> - Full offline gate: `cd backend && venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py`
>   → 972 passed, 13 deselected, 74 warnings. Up from 953 pre-P1.b; the
>   delta is the 19 new P1.b tests, no regressions.
> - `git diff --check` clean.
>
> No commit made — orchestrator reviews and runs the full offline gate
> before committing. Next executable slice: P1.c (recommended-form
> fallback does not leak HST-only forms to SAS) — see Filing Reliability
> Readiness Sprint plan §4. P1.c stays offline-only and follows the same
> boundary as P1.a/P1.b.

> **2026-06-02 addendum — portfolio-type correction + P1.a slice landed (offline).**
> Moeed corrected the portfolio-type model used by the Filing Reliability
> Readiness Sprint: ACCS and Intermediate are **separate portfolio types** on
> Kaizen, not a single collapsed shape. Harris is the dual-access edge case —
> one trainee with access to both ACCS and the Intermediate Portfolio. The
> bot's current storage collapses dual access into a single
> `accs_intermediate` Kaizen role / `INTERMEDIATE` `training_level` bucket,
> which is an implementation/storage behaviour worth testing, not a product
> truth. HST (Moeed), SAS / CESR Portfolio Pathway (Sana), and the trainee
> portfolio types are also distinct; several Kaizen differences are still
> unconfirmed, so the plan and its tests should not pretend to know more than
> the evidence proves.
>
> Doc corrections (no runtime behaviour changed):
>
> - `docs/roadmap/filing-reliability-readiness-sprint-2026-06.md` — executive
>   summary lists HST / ACCS / Intermediate / SAS / CESR as separate types
>   with Harris as the dual-access edge case; adds a portfolio-type
>   terminology callout; P1.a acceptance criteria now enumerate five
>   shapes (`hst`, `accs`, `intermediate`, `accs_intermediate_dual_access`,
>   `sas_cesr`) and four outcomes (`credential_failure`, `infra_failure`,
>   `auth_required`, `success`); P1.c / P1.d wording reframes
>   `accs_intermediate` as Harris's storage bucket rather than a portfolio
>   type; §6 Sana recovery and §9 nice-to-have admin columns updated to
>   match.
> - `docs/roadmap/three-account-filing-validation-2026-06.md` — matrix
>   intro adds the same "do not collapse" callout, explicit that Harris
>   exercises both ACCS and Intermediate, and that the SAS / CESR vs HST
>   differences are a working hypothesis until evidence lands.
>
> P1.a slice landed (offline only, no live Kaizen, no CDP, no BWS, no
> Telegram, no deploy/restart/push):
>
> - New file `backend/tests/test_login_classification_per_shape.py` — 20
>   parametrised tests = 5 shapes × 4 outcomes, ids visible as
>   `hst` / `accs` / `intermediate` / `accs_intermediate_dual_access` /
>   `sas_cesr` so a future regression surfaces the exact shape, not just a
>   generic failure. Reuses the offline stub style from
>   `test_kaizen_login_reliability.py` (`_test_kaizen_login` wrapper) and
>   `test_kaizen_sync.py` (`_open_kaizen_session_page`,
>   `_restore_cached_session`, `_load_user_credentials`,
>   `_login_kaizen_page`). The `accs_intermediate_dual_access` test is the
>   only one whose expected provider role string differs from its shape id
>   (`accs_intermediate`) — that delta documents the storage collapse,
>   not a product claim.
>
> Verification (no live action):
>
> - `cd backend && venv/bin/python -m pytest tests/test_login_classification_per_shape.py tests/test_kaizen_login_reliability.py tests/test_three_account_filing_matrix.py tests/test_profile_store_kaizen_role.py -q`
>   → 65 passed, 17 warnings
>   (pre-existing deprecation warnings only).
> - `cd backend && venv/bin/python -m pytest tests/test_login_classification_per_shape.py tests/test_kaizen_sync.py tests/test_kaizen_index.py -q`
>   → 45 passed (sibling sync/index paths
>   not regressed by the new file's `USAGE_DB_PATH` reload fixture).
> - `git diff --check` clean.
>
> Files changed in this slice: `docs/roadmap/filing-reliability-readiness-sprint-2026-06.md`,
> `docs/roadmap/three-account-filing-validation-2026-06.md`,
> `backend/tests/test_login_classification_per_shape.py` (new), `TASK.md`
> (this addendum). No source files outside docs/tests touched. No commit
> made — orchestrator reviews and runs the full offline gate before
> committing.
>
> Next executable slice: P1.b (detected-role → `training_level` mapping per
> shape) — see Filing Reliability Readiness Sprint plan §4. P1.b is still
> offline-only and follows the same boundary as this slice.

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
> matrix codified; P3 read-only smoke ok for Moeed/HST and Harris's
> ACCS + Intermediate dual-access account; Sana/SAS-CESR blocked at `auth_required` and is
> the critical-path blocker for promotion. The plan is doc-only; no live
> Kaizen, no Telegram, no BWS read, no push, no deploy, no restart.
>
> **Next executable slice:** Plan §4 P1.a —
> `backend/tests/test_login_classification_per_shape.py` covering
> credential-failure / infra-failure / auth-required classification for
> `hst`, `accs`, `intermediate`, Harris's `accs_intermediate` dual-access
> alias, and `sas_cesr`. Followed by P1.b, P1.c, P1.d
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
pathway · ARCP readiness check` (was `Training (CCT) ARCP readiness`).
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
> - **Harris / ACCS + Intermediate dual access:** `ok`; 21 rows seen, 21 indexed in the
>   temporary DB.
> - **Sana / SAS-CESR candidate:** `auth_required`; RCEM/Kaizen login did not
>   land on a portfolio page within the read-only smoke window. No rows indexed.
>
> Interpretation: the read-only indexer and CDP session bootstrap work for the
> senior-HST account and Harris's dual-access junior account. Sana's account still
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
>    access. ACCS and Intermediate are separate portfolio types; Harris's
>    `accs_intermediate` Kaizen role is the dual-access storage alias.
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
>   catalogue (HST superset, current ACCS/INTERMEDIATE shared ST3 catalogue, SAS fall-through to
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
>   - `accs_intermediate` Kaizen role maps to the current `INTERMEDIATE`
>     bucket as Harris's dual-access alias. Pinned (`ACCS` and
>     `INTERMEDIATE` currently share `ST3`'s form catalogue) so a refactor
>     that breaks either separate type or the alias is loud.
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
