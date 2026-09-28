# Portfolio Guru — Context Brief

Compiled 2026-09-28 by a Claude thread on the live Mac mini (read-only everywhere).
Companion to `docs/PRODUCT_BRIEF.md` (product why) and `AGENTS.md` (engineering how).
This file covers what those two leave out: where the history lives, the old Hermes
topics and their rules, recurring jobs, records, people, and open items.

Legend: **[checked]** = read directly from the named source on 2026-09-28.
**[inferred]** = reasoned from sources, not directly confirmed.
No personal memory (Hindsight `personal-private`, personal Brain pages) and no
patient-identifiable details are included. Secrets are named, never printed.

## 1. Where the history lives

| Source                                                                           | What it holds                                                                                                    | Where                                                                                                                                                                                                                   |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hermes Founder, topic **Portfolio** (thread 816, Founder forum `-1003789172171`) | Main build lane Aug 7 – Sep 22 2026, 27 sessions                                                                 | `~/.hermes/profiles/founder/state.db`, sessions with `thread_id='816'` [checked]                                                                                                                                        |
| Hermes Founder, topic **Portfolio Testing** (thread 20346, same forum)           | Lane for the separate Hermes test agent, Aug 25 – Sep 16 2026                                                    | same db, `thread_id='20346'` [checked]                                                                                                                                                                                  |
| Hermes Chief forum (`-1003763995029`), topic 3448                                | Home channel of the Hermes test bot; used for its command menu and fixes (Jun 2026)                              | `~/.hermes/state.db`, `thread_id='3448'`; `~/.hermes/profiles/portfolio-guru/config.yaml` `home_channel` [checked]. That this is "Chief's Portfolio Guru topic" is [inferred]: Chief's config names no Portfolio topic. |
| Hermes Chief CLI session "Record Portfolio Guru stabilisation decisions"         | 2026-09-25 feature-freeze decisions                                                                              | `~/.hermes/state.db` session `20260925_103202_99d5be` [checked]                                                                                                                                                         |
| Hermes test profile `portfolio-guru`                                             | Conversational challenger bot, sessions Jun 18 – Sep 24 2026                                                     | `~/.hermes/profiles/portfolio-guru/` (SOUL.md, USER.md, config.yaml, state.db) [checked]                                                                                                                                |
| OpenClaw archive (spare Mac)                                                     | Founder topic 816 and Medic "Portfolio" topic 60 (forum `-1003782500629`), Mar – Aug 2026                        | `old-mac:~/.openclaw/openclaw.json`, `~/.openclaw/agents/{founder,medic}/` [checked]                                                                                                                                    |
| Hindsight                                                                        | Repo memory bank `repository-memory::portfolio-guru` (~1,450 facts)                                              | `http://127.0.0.1:8888` on the live Mac [checked]                                                                                                                                                                       |
| Brain                                                                            | Product entity, readiness, dated decisions                                                                       | `30-entities/product-portfolio-guru`, `20-wiki/portfolio-guru-current-production-readiness`, `20-wiki/portfolio-guru-product-brief`, `50-decisions/portfolio-guru-*` [checked]                                          |
| Notion                                                                           | Business → Products → Portfolio Guru (+ Risk sub-page), content pipeline posts, CEP Cohort 11 application pack   | Notion search "Portfolio Guru" [checked]                                                                                                                                                                                |
| Google Drive                                                                     | "Portfolio Guru" folders (2026-05/06), WhatsApp number options note                                              | Drive search [checked, contents not opened]                                                                                                                                                                             |
| Gmail                                                                            | Stripe receipts, Healthchecks backup alerts, GitHub CI mail, Supabase pause notices, Google Cloud Gemini notices | Gmail search [checked]                                                                                                                                                                                                  |
| Claude Code                                                                      | ~86 past transcripts across portfolio-guru worktrees; no Claude project memory files                             | `~/.claude/projects/*portfolio-guru*` [checked]                                                                                                                                                                         |
| Migration notes                                                                  | 2026-09-27/28 move to the live Mac                                                                               | `~/migration/` [checked]                                                                                                                                                                                                |

## 2. Old Hermes topics and their own rules

**Founder › Portfolio (816)** — near-verbatim [checked, founder `config.yaml`]:
Founder's continuing Portfolio Guru build-and-refinement lane: product validation,
strategy, growth, content, roadmap, implementation briefs, coordinated code work,
verification and launch decisions; not Moeed's own clinical training portfolio.
Canonical repo `~/projects/portfolio-guru`, origin `git@github.com:moeedahmed/portfolio-guru.git`.
Verify product, repo, deployment, user and launch state from owning sources before
readiness claims; OpenClaw archive is secondary history only. Keep Founder and
public-profile separation. (Its "exact approval for deployments" line is superseded
by Moeed's 2026-09-21 / 2026-09-27 standing release approval.)

**Founder › Portfolio Testing (20346)** — near-verbatim [checked]:
Planning, review and approved config/engineering lane for the separate Hermes
Portfolio Guru agent. Not that agent's chat; Founder must not impersonate it,
absorb its runtime state, read its users' data or use its credentials. Canonical
profile workspace `~/.hermes/profiles/portfolio-guru`. Python production repo,
deployment and release decisions stay in the Portfolio topic. Synthetic data only;
exact approval for external messages, restarts, deployments or customer-affecting
changes.

**Chief forum topic 3448 (test bot home)** — no written topic rule in Chief's
config [checked]; used only for test-bot maintenance [inferred].

**OpenClaw era (retired 8 Aug 2026)**: Medic had a separate clinician-facing
"Portfolio" topic; the dogfood repair watcher was detect-and-report only, never
auto-fixing, with a kill switch after 3 failed QA runs [checked,
`old-mac:~/.openclaw/workspace/loops/portfolio-guru-dogfood-repair-loop.md`].
Brain marks OpenClaw-era decisions as historical, not current guidance.

## 3. Decisions not already in PRODUCT_BRIEF.md (dated)

- 2026-03-03 — project started (first commit) [checked, git history].
- 2026-05-23 — shipped for ACCS, Intermediate and Higher; Kaizen mapping checked on four accounts [checked, Brain inbox note].
- 2026-08-20 — compliance treated as ordinary B2C SaaS with anonymised reflections; NHS/medical-device regulation out unless scope grows [checked, Founder 816].
- 2026-09-02/03 — synthetic users find defects but can't answer preference questions; real-doctor testing of the Hermes agent needs separate approval. Python beta stays authoritative; Hermes is a conversational layer only [checked, Founder 20346].
- 2026-09-04 — `/health` frozen for beta; no feature growth until real use shows unmet need [checked, Founder 816].
- 2026-09-05 — build real document-upload evidence ("Option B"); multi-approval release collapsed into single approval [checked, Founder 816].
- 2026-09-06 — one product: natural conversation backed by deterministic case state; beta testers stay on the Python bot until a Hermes candidate matches it; cohort ">30 testers" (user-reported) [checked, Brain readiness page].
- 2026-09-10/13 — six one-word curriculum categories: Clinical, Reflection, Learning, Procedures, Quality, Management; aim for 3 fitting KCs, never invent evidence [checked, Founder 816].
- 2026-09-21 — standing release approval extended to all Founder-owned products; weekly cross-product review moved out of the Portfolio topic [checked, Founder 816].
- 2026-09-25 — feature freeze and stabilisation sprint (per-user concurrency, deploy wiping in-progress case, dead buttons). After it: finish EM profiles (ACCS, Intermediate, SAS), then other colleges on the same platform [checked, Chief session; Brain `50-decisions/portfolio-guru-stabilise-before-expanding`].
- 2026-09-25 — draft first, blanks for missing details [checked, Brain].
- 2026-09-25 — agents may write to Brain autonomously [checked, Chief session; Brain `50-decisions/agents-autonomous-brain-capture`].
- 2026-09-27 — proactive messaging is quiet and event-driven [checked, Brain].
- 2026-09-28 — open beta until public launch, payments off [checked, Brain; AGENTS.md].

## 4. People (roles only)

- Moeed — owner, founder, dogfood user (operator Telegram id is in AGENTS.md telemetry section) [checked].
- Beta testers — ">30" UK EM trainees per Moeed 2026-09-06; no named individuals in the history reviewed [checked].
- Synthetic Kaizen test accounts exist for ACCS, Intermediate, Higher and SAS [checked, Chief session 2026-09-25]; credentials live in encrypted stores / Bitwarden, not here.
- No named advisors, supervisors or partners found. NHS email (Outlook) was not readable.

## 5. Records and where they live

- Repo: `github.com/moeedahmed/portfolio-guru`; live checkout `~/projects/portfolio-guru-live` on the live Mac [checked].
- Bot data: `~/.openclaw/data/portfolio-guru/` on the live Mac (SQLite, NDJSON logs, drafts); loaded from the spare Mac on 2026-09-27 (67 profiles, 15 saved logins, ~4,600 evidence items at migration) [checked, `~/migration`].
- Spare Mac still holds an older data copy and backups to 2026-09-27 — do not start anything there [checked].
- Backups: nightly 03:30 to `gs://portfolio-guru-eu-backups`, 30-day retention, Healthchecks "Mac Mini Portfolio Guru Backup" [checked, plist + Gmail].
- Cloud: GCP project `portfolio-guru-eu` (Vertex AI, London); Supabase project "Portfolio Guru" (org Solvoro Labs) was auto-paused 2026-08-31 for inactivity [checked, Gmail]; Stripe live.
- Secrets (names only): BWS machine token path `PG_VERTEX_BWS_TOKEN_PATH`, `PG_VERTEX_SA_SECRET_ID`, Telegram bot token, Stripe keys, Fernet key — all via Bitwarden Secrets Manager [checked, plist env names].
- Notion: Business › Products › Portfolio Guru, and its Risk page (doctor-authorised assistant inside the user's own account) [checked].
- `~/projects/portfolio-guru-hermes-runtime` no longer exists; only tarballs in `~/migration/dirty/`, though Hermes configs still point a `portfolio-approval-receipts` plugin at it [checked].

## 6. Recurring jobs

| Job                                                                  | Where it runs          | Schedule                         | What it does                                                                                                               |
| -------------------------------------------------------------------- | ---------------------- | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `com.portfolioguru.bot`                                              | launchd, live Mac      | always on                        | The bot [checked]                                                                                                          |
| `com.portfolioguru.backup`                                           | launchd, live Mac      | daily 03:30                      | Encrypted off-site backup [checked]                                                                                        |
| GitHub runner `hub-portfolio-guru`                                   | launchd, live Mac      | on push to main                  | Tests then deploy [checked]                                                                                                |
| `ai.hermes.gateway-portfolio-guru`                                   | launchd, live Mac      | always on                        | Hermes test bot gateway [checked]                                                                                          |
| In-bot `weekly_push`                                                 | bot job queue          | Sun 20:00 UK                     | Weekly portfolio digest to users [checked, `backend/bot.py`]                                                               |
| In-bot `signoff_chase`                                               | bot job queue          | Wed 19:00 UK                     | Off unless `PG_ENABLE_SIGNOFF_CHASE` [checked]                                                                             |
| In-bot `proactive_tick`                                              | bot job queue          | daily 19:00 UK                   | Off unless `PG_ENABLE_PROACTIVE` [checked]                                                                                 |
| In-bot `stripe_reconcile`, `retention`, `heartbeat`, supervisor poll | bot job queue          | daily / 5 min                    | Billing, data retention, ops alerts [checked]                                                                              |
| Hermes cron jobs for Portfolio Guru                                  | —                      | —                                | **None.** No Hermes profile has a Portfolio Guru cron; the test profile has zero jobs [checked]                            |
| Buffer drafts                                                        | Hermes Creator profile | **daily** 20:30, not Sunday-only | Personal-brand LinkedIn draft into Buffer; no Portfolio Guru link in its scripts. Belongs to the Content project [checked] |

## 7. Open items (not already in PRODUCT_BRIEF.md)

- Document-upload evidence (decided 2026-09-05): completion not confirmed in the history read [inferred open].
- Plaintext non-credential fields in local SQLite (flagged 2026-08-20): not confirmed closed [inferred open; check code].
- Hermes test bot parity with the Python bot: not reached as of 2026-09-24 [checked].
- "Improve reflection" button removal / third-KC defect (2026-09-21): no shipped confirmation found [inferred open].
- Platform name: one 2026-09-25 message calls Kaizen "risr/advance"; unconfirmed [inferred].
- Supabase project paused since 2026-08-31 [checked]; ties into the London Supabase question already in PRODUCT_BRIEF.
- Hermes test bot WhatsApp channel disconnected since 2026-09-08 [checked, gateway_state.json].
- Gemini 2.5 retirement notices (migrate before 2026-10-20) [checked, Gmail]; the bot uses `gemini-3.5-flash`, so likely unaffected [inferred].
