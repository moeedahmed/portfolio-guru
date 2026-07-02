# Portfolio Guru — Launch-Blocker Checklist

> Created 2026-06-25 from the end-to-end production-readiness audit. **Refreshed 2026-07-02** — checkboxes below now reflect what has shipped since (Phase 0 complete, Vertex EU live, live Stripe proven).
> Goal: get from "single-operator private beta" → invite-only **paid** beta → public paid launch, in the safe order.
> Channels in scope: Telegram (live, `@portfolio_guru_bot`), web front (`emgurus.com/portfolio`), WhatsApp (planned), Hermes `emgurus` intelligence layer (in progress).

## Locked decisions (2026-06-25)

| Area       | Decision                                                                                                                |
| ---------- | ----------------------------------------------------------------------------------------------------------------------- |
| AI routing | **UK/EU only → Google Vertex AI (EU, `europe-west2`)** for text/voice/vision; drop DeepSeek/consumer-API default        |
| Infra      | **Hybrid** — Supabase becomes billing/DB source-of-truth (+ Edge Function webhook); filing engine stays on the Mac Mini |
| Launch ICP | **Open to all**, unproven portfolio types (SAS/CESR) clearly labelled beta                                              |
| Deploy     | **Autonomous push**, protected by the gate + smoke + rollback below                                                     |
| Pricing    | **£9.99/mo Unlimited + free 5/mo**, locked                                                                              |
| Legal      | **Drafts** written for solicitor review → `docs/legal/`                                                                 |
| Channels   | WhatsApp + Hermes are **post-launch** (re-open the compliance surface)                                                  |

## Live progress (shipped to `main`, gated deploy verified)

- ✅ **0.1 Deploy gate** — `deploy-mac.yml` gated on Tests passing (proven: blocked a red-CI deploy)
- ✅ **3.1 Smoke + auto-rollback** — `deploy_mac.sh` reverts on runtime failure
- ✅ **0.2 PII fix** — `extracted_fields` Fernet-encrypted before Supabase (was plaintext)
- ✅ **0.3 Erasure** — `delete_user_data()` wired into `/reset` (GDPR Art. 17)
- ✅ **0.4 Backup** — `scripts/backup_db.sh` + daily plist + restore doc (agent install is manual)
- ✅ **0.5 / 3.3 Alerting** — `ops_alert.py`: operator paging on crash + webhook failure + liveness heartbeat
- ✅ **CI fix** — `test.yml` had an invalid Fernet key; suite was red for weeks (masked by the ungated deploy)
- ✅ **Legal drafts** — privacy/terms/DPIA/consent/ROPA in `docs/legal/`

- ✅ **1.1 Vertex EU** — clinical extraction on Vertex AI EU (`europe-west2`, project `portfolio-guru-eu`) since 2026-06-25; DeepSeek dropped in vertex mode
- ✅ **2.x Stripe live** — real £9.99 purchase → upgrade proven; `invoice.paid` reactivation, reconciliation (redirect re-check + daily sweep), and mode guard in `stripe_handler.py`
- ✅ **3.3 Filing admission control** — `PG_MAX_CONCURRENT_FILINGS` semaphore in `filer_router.route_filing` (2026-07-02)
- ✅ **Deps pinned** — `backend/requirements.txt` pinned to the live venv versions (2026-07-02)
- ✅ **1.2 (in-bot half) Consent gate** — versioned Art 9(2)(a) explicit-consent gate before first clinical ingest (`backend/consent.py`, append-only records, `/privacy`, withdrawal on `/reset`) (2026-07-02)
- ✅ **1.5 Retention** — daily purge of encrypted clinical content older than `PG_CLINICAL_RETENTION_DAYS` (default 180) from the Supabase mirror (`backend/retention.py`) (2026-07-02)

**Remaining / blocked:** solicitor review + web publication of `docs/legal/` (1.2 second half — pages on emgurus.com), key-isolation + RCEM ToS risk decision (1.4), webhook-down billing drill (2.1 acceptance), heartbeat monitor URL (3.2 — code live, founder must provision `PG_HEARTBEAT_URL`, e.g. a free healthchecks.io check, into BWS + `run_local.sh`), filing error-budget alert (3.4), SAS/CESR-or-scope decision (4.1), web↔bot loop (4.2), Supabase source-of-truth flip (high-risk — touches live credentials). Note: 4.3 self-serve onboarding is DONE on Telegram — no invite gating exists in code; /start→setup→file→upgrade is fully self-serve.

## How to read this

- **P0** = legal / financial / safety. Cannot charge the public until these are done. Do first.
- **P1** = availability / correctness. Cannot responsibly run paid users without these.
- **P2** = product / scale. Gate _how many_ users and _how public_ you go.
- **P3** = new channels (WhatsApp, Hermes). Explicitly **after** P0–P1 because each one re-opens the compliance + data-flow surface.
- Effort: **S** = <1 day, **M** = 1–3 days, **L** = ~1 week, **XL** = multi-week.
- Each item has an **Acceptance** line — "done" means that is demonstrably true, not "code written."

---

## PHASE 0 — Stop the bleeding (cheap, do this week, no dependencies)

These are fast, reversible, and reduce risk immediately regardless of launch timing.

- [x] **0.1 (S) Gate deploy on tests.** _(✅ shipped 2026-06 — proven: blocked a red-CI deploy)_ `deploy-mac.yml` currently deploys on push to `main` with no dependency on `test.yml`. Add `needs:`/`workflow_run` so a red suite blocks deploy.
  - Files: `.github/workflows/deploy-mac.yml`, `.github/workflows/test.yml`
  - Acceptance: a deliberately-failing test on a branch prevents the deploy job from running.
- [x] **0.2 (S) Fix the plaintext PII leak to cloud.** _(✅ shipped 2026-06 — Fernet-encrypted before mirror)_ `extracted_fields` (structured patient detail) is written unencrypted to Supabase, contradicting the in-code "never leaves the bot" claim.
  - Files: `backend/supabase_sync.py:366`, caller `backend/bot.py:8993`; comment `bot.py:8976-8977`
  - Acceptance: either `extracted_fields` is Fernet-encrypted before mirror, or it is not mirrored at all; the misleading comment is corrected.
- [x] **0.3 (M) Build a real erasure path.** _(✅ shipped 2026-06 — `delete_user_data()` wired to `/reset`)_ `/reset` clears local state but `supabase_sync.py` is write-only — cloud copies of credentials, case text, and fields persist. Add `delete_user_data()` and call it from `/reset`; also sweep the document cache + persistence pickle.
  - Files: `backend/supabase_sync.py` (new fn), `backend/bot.py:5379-5418` (`/reset`), cache cleanup `bot.py:8055-8080`
  - Acceptance: after `/reset`, a Supabase query for that `telegram_user_id` returns zero rows across credentials/cases; temp clinical docs are gone.
- [x] **0.4 (S) Add a daily off-device DB backup.** _(✅ shipped 2026-06 — `scripts/backup_db.sh` + plist; agent install manual)_ No backup today = disk failure is total user-data loss.
  - Files: new `scripts/backup_db.sh` (sqlite `.backup` + pickle copy to off-device/Supabase storage), launchd or cron entry
  - Acceptance: a backup artifact from <24h ago exists off the Mac Mini disk, and a documented restore step works once.
- [x] **0.5 (S) Operator alerting on the two silent-failure paths.** _(✅ shipped 2026-06 — `ops_alert.py`)_ Today nobody is paged when the bot dies/hangs or a paid webhook is dropped.
  - Files: `backend/bot.py:10522` (error handler — add operator notify), `backend/webhook_server.py:65` (alert on `error`/`ignored` outcomes)
  - Acceptance: a forced exception and a forced webhook `error` each produce a message to the operator (Telegram DM / email / uptime service), not just a log line.

---

## PHASE 1 — Compliance minimum (P0 — the legal gate, do before any payment)

This is the hardest blocker and the one with real downside. Most items end with "review with a DPO/solicitor" — the engineering work is to make that review _possible_ and _cheap_.

- [x] **1.1 (M) Decide and enforce the cross-border LLM posture.** _(✅ decided & live 2026-06-25 — Vertex AI EU only, `europe-west2`; DeepSeek dropped in vertex mode; flows documented in `docs/legal/processors-ropa.md`)_ Clinical text defaults to **DeepSeek (China-hosted)**; voice/photo/PDF go to US clouds; there is no de-identification step.
  - Files: `backend/extractor.py:114`, `backend/model_config.py`, `backend/whisper.py`, `backend/vision.py`, `backend/documents.py`
  - Options (pick one, document it): (a) route clinical data only to providers you have a DPA + acceptable transfer basis with (likely drop DeepSeek default); (b) add an automated redaction/de-identification pass before any third-party call; (c) explicit informed consent + Art. 9 condition covering each processor.
  - Acceptance: a written data-flow map naming every third party clinical data reaches, the legal basis for each, and code that matches the map (no silent DeepSeek default if it's not covered).
- [x] **1.2 (M) Publish privacy policy + terms + in-bot consent capture.** _(in-bot half ✅ shipped 2026-07-02: versioned Art 9(2)(a) consent gate before first clinical ingest — `backend/consent.py`, append-only records, /privacy command, withdrawal on /reset; wording archived in `docs/legal/consent-versions/`. Remaining: solicitor review + publish policy/terms pages on emgurus.com)_ None exist in the repo. For special-category data this is mandatory.
  - Files: web (`emgurus.com/portfolio`), new in-bot consent gate before first clinical ingest (`backend/bot.py` start/onboarding flow)
  - Acceptance: a new user cannot send a clinical case until they've accepted a versioned privacy notice + terms; acceptance (version + timestamp) is recorded.
- [ ] **1.3 (L) DPIA + ROPA + processor DPAs.** Special-category processing with cross-border transfers ⇒ a DPIA is effectively required. Get DPAs from each processor (Google, OpenAI, DeepSeek-or-replacement, Telegram, Supabase, Stripe, and later Meta/WhatsApp).
  - Acceptance: a completed DPIA document, a Record of Processing Activities, and signed/accepted DPAs on file for every processor in the 1.1 data-flow map.
- [ ] **1.4 (M) Harden + scope the Kaizen credential store, and get a ToS read.** Encryption is sound but the Fernet key sits in env next to the data; and credential-based automation of `kaizenep.com` may breach RCEM/Kaizen ToS — no doc acknowledges this.
  - Files: `backend/credentials.py`, key handling in `run_local.sh:74`
  - Acceptance: documented key-isolation decision (e.g. key not co-resident with DB backups), and a written risk decision on the RCEM/Kaizen ToS question from the founder/legal.
- [x] **1.5 (S) Retention policy.** _(✅ shipped 2026-07-02: daily job nulls encrypted clinical content on `portfolio_cases` rows older than `PG_CLINICAL_RETENTION_DAYS` (default 180); window documented in privacy-policy §7; temp media already deleted inline after processing)_ No time-based expiry today; clinical data persists forever.
  - Acceptance: a documented retention window and a scheduled job that deletes case data past it (encrypted text, fields, cached docs).

> **Gate:** Do not flip Stripe to live mode for the public until 1.1, 1.2, 1.5 are done and 1.3/1.4 are at least underway with legal sign-off.

---

## PHASE 2 — Billing correctness (P0 — the money gate)

Entitlement currently flips _only_ inside one webhook delivered through a single home tunnel, with no recovery if it's missed.

- [ ] **2.1 (M) Add subscription reconciliation (no more stranded payers).** _(code live 2026-06: redirect re-check + daily sweep in `stripe_handler.py`; remaining: the deliberate webhook-down purchase drill)_ Don't rely on the single webhook.
  - Files: `backend/stripe_handler.py`, `backend/webhook_server.py`, `backend/usage.py`
  - Add: (a) on the checkout success-redirect, re-fetch the subscription from Stripe and set tier; (b) a daily job that reconciles all active Stripe subscriptions → local tiers.
  - Acceptance: with the webhook endpoint deliberately down during a test purchase, the user still ends up on the correct tier within the day (or immediately, via the redirect re-check).
- [ ] **2.2 (M) Handle the missing recurring-billing events.** _(partially shipped: `invoice.paid` re-upgrade + `invoice.payment_failed` handling live; remaining: verify paid-through-period on cancellation + a grace window instead of instant downgrade)_ No `invoice.paid`/`payment_succeeded` handler → recovered payments never re-upgrade; failed-payment revokes instantly with no grace.
  - Files: `backend/stripe_handler.py:108-174`
  - Acceptance: simulated dunning (fail → retry-succeed) re-grants access; cancellation honours paid-through-period instead of revoking immediately.
- [ ] **2.3 (M) Prove live Stripe end-to-end + env-consistency guard.** _(live purchase proven — real £9.99 → upgrade; mode guard logs at startup; remaining: make a mismatched env fail startup, not just warn)_ Live keys/price-IDs/webhook-secret must all be the same Stripe environment; a mismatch silently charges-but-doesn't-upgrade today.
  - Files: `backend/stripe_handler.py:18,126`, `backend/run_local.sh:86-91`, `docs/STRIPE_LOCAL_PROOF.md`
  - Acceptance: one real live-mode purchase upgrades a real account; a deliberately-mismatched price-ID is caught at startup, not silently swallowed.
- [ ] **2.4 (S) Make the webhook ingress production-grade or accept its limits.** The Cloudflare tunnel → `localhost:8099` on the Mac Mini is the billing-critical path.
  - Acceptance: documented uptime expectation for the tunnel + the 2.1 reconciliation makes a tunnel outage non-fatal (this is the real fix — reconciliation, not the tunnel).

---

## PHASE 3 — Ops floor (P1 — the availability gate)

- [x] **3.1 (M) Safe deploy: post-deploy smoke + one-command rollback.** _(✅ shipped 2026-06 — smoke + auto-rollback in `deploy_mac.sh`)_ `deploy_mac.sh` does `py_compile` only (a syntax check), no rollback path.
  - Files: `scripts/deploy_mac.sh`, `scripts/dogfood_smoke.sh` (make it non-interactive/automatable)
  - Acceptance: a green-compile/red-runtime commit is caught by an automated post-deploy smoke and auto-reverts (or a single documented command restores the last known-good commit).
- [ ] **3.2 (M) Liveness heartbeat (not just crash-restart).** launchd restarts a crashed process but not a wedged poller; add a dead-man heartbeat to an external uptime monitor each poll cycle. Add `ThrottleInterval` to avoid boot crash-loops (BWS/Chrome failures).
  - Files: `backend/bot.py` poll loop, `scripts/install_launchd.sh:31-32`, `backend/ensure_chrome.sh`
  - Acceptance: killing the network (wedged-but-alive) triggers an external alert within minutes; a forced BWS-unreachable boot backs off instead of looping every ~10s.
- [x] **3.3 (M) Bound concurrent filings.** _(✅ shipped 2026-07-02 — `PG_MAX_CONCURRENT_FILINGS` semaphore (default 2) in `filer_router.route_filing`; queued filings hold under the existing 'Saving…' progress message; regression test in `tests/test_filing_reliability.py`)_ One shared Chrome, no admission control — N simultaneous approvals spawn N logins. Add an `asyncio.Semaphore`/queue around `route_filing`.
  - Files: `backend/bot.py:8733` (filing dispatch), `backend/filer_router.py`
  - Acceptance: with the limit set to e.g. 2, a third concurrent filing queues rather than spawning a third browser context; users see a "queued" state, not a failure.
- [ ] **3.4 (S) Error-budget visibility on filing.** Filing reliability is pull-only via `/filingreport`. Add a push alert when the partial/failed rate over a window crosses a threshold.
  - Files: `backend/filing_attempt_log.py`, `backend/filing_reliability_matrix.py`
  - Acceptance: a spike in failed filings (e.g. Kaizen UI drift) pages the operator automatically.

---

## PHASE 4 — Prove the USP + minimal self-serve (P1/P2 — the product gate)

- [ ] **4.1 (L) Unblock SAS/CESR live filing — or scope the launch ICP.** Live filing is proven for only 2 of ~4 portfolio types; SAS/CESR is blocked at `auth_required` (your own sprint doc's critical-path blocker).
  - Files: per `docs/roadmap/filing-reliability-readiness-sprint-2026-06.md`
  - Acceptance: either a clean live draft-save for a SAS/CESR account, **or** an explicit launch decision to sell only to the 2 proven portfolio types with the UI/marketing scoped accordingly.
- [ ] **4.2 (L) Close the web↔bot loop on the existing front (`emgurus.com/portfolio`).** The front exists; the Supabase mirror is one-way (no read-back), so the dashboard can't show live state. Make account-linking + dashboard read-back work.
  - Files: `backend/supabase_sync.py` (read path), web app (separate repo `emgurus-hub`)
  - Acceptance: a user links Telegram from the web, pays on the web, and sees their real case/filing state on the dashboard.
- [ ] **4.3 (M) Self-serve onboarding path.** Today onboarding is operator-by-hand. Even a thin link→pay→connect-Kaizen flow beats manual before charging.
  - Acceptance: a brand-new user completes signup → pay → connect Kaizen → first case with zero operator involvement.
- [ ] **4.4 (XL, optional-for-launch) Portfolio/ARCP Health differentiator.** The headline paid value-prop is unbuilt; without it the paid tier is just "unlimited filing." Decide if v1 sells without it.
  - Files: per `docs/PORTFOLIO_HEALTH_SPEC.md`, `docs/plan.md` Phase 2.9
  - Acceptance: KCs persisted per draft + a readiness view — OR a documented decision to launch without it and add post-launch.

---

## PHASE 5 — New channels (P3 — after the gates, because they re-open compliance)

- [ ] **5.1 Channel abstraction first.** Before adding WhatsApp, make sure the consent (1.2), erasure (0.3), and entitlement (Phase 2) logic live behind a channel-agnostic layer, not inside Telegram handlers in `bot.py`. The architecture audit flagged `bot.py` as a 10.6k-line god-file — adding a second channel into it directly will multiply the debt.
  - Acceptance: consent/erasure/billing are callable independent of Telegram before WhatsApp wiring starts.
- [ ] **5.2 WhatsApp = new processor + new compliance row.** WhatsApp Business API (Meta) is another data processor handling the clinical messages — it must be added to the 1.1 data-flow map, 1.3 ROPA/DPA, and the privacy notice **before** it goes live. WhatsApp also has its own messaging-policy/opt-in rules.
  - Acceptance: WhatsApp appears in the DPIA/ROPA with a Meta DPA on file; opt-in consent captured per WhatsApp policy.
- [ ] **5.3 Hermes `emgurus` intelligence layer = more LLM data flow.** An intelligence layer on top means clinical data flows through additional model calls/prompts. Same rule: it must conform to the 1.1 posture (which providers, what redaction) and not reintroduce a creds-in-prompt or PII-in-prompt path (note: the deprecated `filer.py` already has a creds-in-prompt smell — don't let Hermes resurrect that pattern).
  - Acceptance: Hermes data flows are in the 1.1 map; no clinical PII or credentials enter any Hermes prompt without the same legal basis as the core engine.

---

## Tech-debt items (not launch-blocking, but they slow every fix above)

- ~~Pin dependencies~~ ✅ done 2026-07-02 — `backend/requirements.txt` pinned to the live venv versions (no separate lockfile; pins are exact).
- Archive the deprecated `filer.py` (creds-in-prompt smell, only caller is an unstarted route).
- Move `eval_*`/`discover_*`/one-off scripts out of `backend/` into `scripts/`; delete dead code below the `/bulk`,`/unsigned`,`/chase` early returns.
- Begin decomposing `bot.py` (10.6k lines) — at minimum before 5.1 multi-channel work.

---

## Suggested sequencing (critical path)

```
Week 1:      Phase 0 (all) — cheap safety, parallelizable
Weeks 1–3:   Phase 1 compliance (1.1/1.2/1.5 eng) + start legal (1.3/1.4)  ── blocks payment
Weeks 2–3:   Phase 2 billing (parallel with compliance eng)                 ── blocks payment
Weeks 3–4:   Phase 3 ops floor
Weeks 3–6:   Phase 4 prove USP + minimal self-serve
── GATE: invite-only PAID beta (~10 users) once Phase 0–2 done + legal sign-off ──
Post-beta:   Phase 4.4 (Health), then Phase 5 channels
── GATE: public launch once filing proven across ICP + self-serve + demand validated ──
```

**Fastest responsible "ASAP":** invite-only paid beta in ~3–4 weeks (Phase 0–2 + legal sign-off + scoped ICP). Public launch is the back half of Phase 4 plus channel work — realistically 6+ weeks out, longer if Portfolio Health and WhatsApp are in v1.
