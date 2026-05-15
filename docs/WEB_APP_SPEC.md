# Portfolio Guru — Web App Spec (EM Gurus Hub Module)

**Status:** Draft for review. No code written yet.
**Last updated:** 2026-05-15

This document defines the plan for adding a Portfolio Guru module to the EM Gurus Hub web app, including the migration of the bot's data from on-device SQLite to Supabase, and the buildout of v0 (landing + paywall), v1 (dashboard), and v1.1 (ARCP Health). Everything is phased so each sprint ships something usable.

---

## 1. Architecture overview

```
┌─────────────────────┐         ┌─────────────────────────────┐
│  Telegram Bot       │         │  EM Gurus Hub (Vercel)      │
│  (Mac Mini launchd) │         │  emgurus.com                │
│                     │         │                             │
│  - Chat interface   │         │  /portfolio        landing  │
│  - Voice/photo/text │         │  /portfolio/dashboard       │
│  - Kaizen filing    │         │  /portfolio/health          │
│  - Playwright       │         │  /portfolio/cases           │
└──────────┬──────────┘         │  /portfolio/upgrade         │
           │                    │  /portfolio/settings        │
           │                    └──────────────┬──────────────┘
           │                                   │
           │      ┌─────────────────────┐      │
           └──────┤  Supabase (shared)  ├──────┘
                  │                     │
                  │  Auth (existing)    │
                  │  portfolio_*  (NEW) │
                  │  user_roles (existing) │
                  │  Edge Functions     │
                  │  Stripe webhooks    │
                  └─────────────────────┘
```

**Key principles:**

- Both the bot and the web app share **one Supabase project** (the existing emgurus-hub project), with portfolio tables prefixed `portfolio_*`.
- The bot keeps owning the **filing** action (chat, voice, photo, Playwright). The web app owns **views and bulk actions** (dashboard, ARCP Health, case browser).
- Auth source-of-truth is **emgurus-hub's existing Supabase auth**. Users link their Telegram account to their hub account.
- Stripe webhooks update one `tier` column. Both bot and web read from there.

---

## 2. Data model (Supabase tables)

All tables get Row Level Security so a user can only see their own rows. Schema lives in `supabase/migrations/` (hub repo).

### `portfolio_users`

Links emgurus-hub auth user ↔ Telegram user.

```sql
emgurus_user_id  uuid PRIMARY KEY REFERENCES auth.users(id)
telegram_user_id bigint UNIQUE
linked_at        timestamptz
tier             text DEFAULT 'free'        -- 'free' | 'pro' (legacy) | 'pro_plus'
stripe_customer_id text
stripe_subscription_id text
```

### `portfolio_credentials`

Fernet-encrypted Kaizen credentials. Same encryption key the bot uses today (BWS-loaded).

```sql
emgurus_user_id uuid PRIMARY KEY REFERENCES portfolio_users(emgurus_user_id) ON DELETE CASCADE
encrypted_username bytea
encrypted_password bytea
updated_at timestamptz
```

### `portfolio_profile`

Training stage, curriculum, voice profile JSON.

```sql
emgurus_user_id uuid PRIMARY KEY REFERENCES portfolio_users(emgurus_user_id) ON DELETE CASCADE
training_level text
curriculum text DEFAULT '2025'
voice_profile_json jsonb
voice_examples_count int
updated_at timestamptz
```

### `portfolio_cases`

Every filed case (one row per Kaizen draft attempt).

```sql
id bigserial PRIMARY KEY
emgurus_user_id uuid REFERENCES portfolio_users(emgurus_user_id) ON DELETE CASCADE
form_type text                  -- 'CBD', 'DOPS', etc.
status text                     -- 'success' | 'partial' | 'failed'
filed_at timestamptz DEFAULT now()
kaizen_event_id text            -- for de-dup & audit
case_text text                  -- the raw input (encrypted? TBD)
extracted_fields jsonb          -- the FormDraft fields the bot produced
curriculum_links jsonb          -- SLO codes
key_capabilities jsonb          -- full KC strings
```

### `portfolio_usage`

Lightweight counter for tier enforcement + dashboard. (Bot already has this in SQLite — we migrate.)

```sql
emgurus_user_id uuid REFERENCES portfolio_users(emgurus_user_id) ON DELETE CASCADE
form_type text
filed_at timestamptz
```

### `portfolio_chase_log`

Assessor chase guardrail records (bot already has this as a JSON file).

```sql
emgurus_user_id uuid
assessor_email text
assessor_name text
chase_date date
method text
ticket_summary text
```

### RLS policies

Every table: `(auth.uid() = emgurus_user_id)` or service-role for bot.

---

## 3. Bot ↔ Hub account linking

A user already has a Telegram chat with the bot. They sign up at emgurus.com. We need to link the two.

**Recommended flow — short token:**

1. User logs into emgurus.com → goes to `/portfolio/settings`.
2. Page shows: "Link your Telegram bot. Type `/link 4F2K9P` in Portfolio Guru on Telegram."
3. The bot, on receiving `/link <token>`, looks up the token in `portfolio_link_tokens` (TTL 10 min), gets the emgurus_user_id, and writes `portfolio_users.telegram_user_id = <current chat's user_id>`.
4. Token consumed. User refreshes web — linked.

Edge case: user signs up at emgurus.com AFTER using the bot — same flow, links existing Telegram identity to new hub account.

---

## 4. Web app pages

Lives at `src/modules/portfolio/` in emgurus-hub. Follows the existing module pattern (see `src/modules/exam/` as a template).

### `/portfolio` — landing (public, no auth needed)

- Hero: "Portfolio Guru — file your WPBA entries in seconds, on Telegram."
- How it works (3 steps + screenshots/GIF)
- Pricing card: Free 5/mo vs Unlimited £9.99/mo
- CTAs: "Start free on Telegram" → t.me link, "Upgrade to Unlimited" → /portfolio/upgrade

### `/portfolio/dashboard` — authenticated home

- Plan card: tier + cases this month + reset date
- Filings this month (chart: 7-day rolling)
- Voice profile status + edit link
- Training stage + curriculum (quick edit)
- Recent cases (last 5, with status)
- "Open bot" CTA

### `/portfolio/health` — ARCP Health (Unlimited only)

- Top: readiness gauge (on track / needs attention / at risk)
- Gap analysis chart: form types covered vs required for current training stage
- Suggestions list: "File 2 more DOPS to reach ARCP minimum"
- Time-range filter (last 3 / 6 / 12 months)
- Free users: paywall page with "Upgrade to Unlimited"

### `/portfolio/cases` — case history browser

- Paginated table: date, form, status, summary, action (view in Kaizen)
- Filters: form type, status, date range
- Search by case text
- Export CSV (Unlimited only)

### `/portfolio/upgrade` — Stripe Checkout

- Plan comparison (Free vs Unlimited)
- Stripe-redirect button
- Post-payment: Stripe webhook → updates `portfolio_users.tier = 'pro_plus'`
- Bot sees the new tier on next message

### `/portfolio/settings`

- Linked Telegram account (with re-link option)
- Kaizen login (status only; updating is done in the bot — security best practice not to ask for it again on web)
- Training stage selector
- Curriculum selector
- Voice profile status + "Re-build in bot" link
- Plan + cancel subscription (Stripe portal link)
- Danger zone: delete all data

---

## 5. Stripe integration

Existing bot Stripe setup (`stripe_handler.py`) already handles:

- Checkout session creation
- Webhook events: `checkout.session.completed`, `customer.subscription.deleted`, etc.

For the web app:

- Move the Stripe webhook receiver to a Supabase Edge Function (`supabase/functions/stripe-webhook`).
- Both bot and web `create_checkout_session` calls funnel through it.
- The function updates `portfolio_users.tier`.
- Bot's existing tier-check logic reads from `portfolio_users.tier` (after migration), so it sees web payments instantly.

**One Stripe price ID** (Unlimited £9.99/mo). The Pro tier exists in code for legacy users but is not sold any more.

---

## 6. Migration: SQLite → Supabase

The bot's data lives in SQLite databases on the Mac Mini (`~/.openclaw/data/portfolio-guru/`). To avoid downtime and data loss, the migration is **phased dual-write, then cutover, then SQLite removal.**

### Phase 1 — Schema in Supabase (Sprint 1)

- Create all `portfolio_*` tables in a new migration.
- RLS policies + indexes.
- Backfill: write a one-off script that reads SQLite and bulk-inserts into Supabase for the admin user (you) as the first migration.
- Verify with web app reads.

### Phase 2 — Dual-write (Sprint 2)

- Bot keeps writing to SQLite (no behavior change).
- Bot also writes to Supabase on every credential save, profile update, case filing, usage record, chase entry.
- Reads still go to SQLite (so any inconsistency in Supabase doesn't break the bot).
- Nightly checksum job compares row counts SQLite vs Supabase, logs discrepancies.

### Phase 3 — Migrate all existing users (Sprint 2 tail)

- Backfill script runs for every user.
- Verifies parity per user.
- Manual approval before each subsequent sprint.

### Phase 4 — Web app v0 ships (Sprint 3)

- Landing + paywall live. Stripe webhook writes to Supabase `portfolio_users.tier`.
- Bot's tier check now reads from Supabase (was reading from SQLite usage.py).
- Dual-write continues.

### Phase 5 — Web v1 ships (Sprint 4)

- Dashboard live. Reads from Supabase.
- Bot continues to dual-write so dashboard stays accurate.

### Phase 6 — Web v1.1 ships (Sprint 5)

- ARCP Health page. Pulls case history from Supabase `portfolio_cases`.

### Phase 7 — Cut bot reads to Supabase (Sprint 6)

- Bot reads from Supabase for all queries. SQLite becomes write-only fallback.
- Watch for issues for ~1 week.

### Phase 8 — Drop SQLite (Sprint 7)

- Remove SQLite writes. Bot is pure-Supabase.
- Archive SQLite files for audit.

**Rollback plan:** dual-write is the safety net. At any point in phases 1-6, if Supabase has issues, the bot keeps working on SQLite. Cutover (Phase 7) is the only one-way step.

---

## 7. Encryption

The bot uses Fernet-encrypted credentials with a BWS-loaded key. The web app must use the **same key** when reading credentials from Supabase, so:

- Decryption stays server-side (Supabase Edge Function, not the browser). The web app never decrypts credentials in the user's browser — it never needs to, because credential editing lives in the bot.
- The BWS-loaded key is stored as a Supabase secret, accessed only by Edge Functions.

For `portfolio_cases.case_text`: whether to encrypt is **TBD** — clinical content is sensitive (GDPR), so likely yes. Will spec in Sprint 1.

---

## 8. Risks + mitigations

| Risk                                                           | Mitigation                                                                |
| -------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Existing bot data corruption during migration                  | Dual-write, backup before each phase, admin user goes first               |
| Encryption key mismatch (web can't decrypt what bot encrypted) | Same key, same Fernet version, automated parity test                      |
| Web app down → user thinks bot is broken                       | Bot reads stay on SQLite until Phase 7, web app outage doesn't affect bot |
| Stripe webhook failure → user pays but tier doesn't update     | Stripe retries; manual reconciliation tool for admin                      |
| User links Telegram to wrong hub account                       | Confirmation step in bot ("Linking to user@example.com — confirm?")       |
| Telegram bot account compromise → web account access           | Re-linking requires re-auth on web side too                               |
| Mass scrape via web bypassing tier limits                      | RLS + rate limiting on Edge Functions                                     |

---

## 9. Sprint plan (high level)

| Sprint | What ships                                            | Bot changes                    | Web changes                          |
| ------ | ----------------------------------------------------- | ------------------------------ | ------------------------------------ |
| 1      | Supabase schema + RLS + migration script (admin only) | None                           | None                                 |
| 2      | Dual-write live                                       | Bot writes to both             | None                                 |
| 3      | v0 landing + Stripe checkout                          | Tier check reads from Supabase | Public landing page, /upgrade        |
| 4      | v1 dashboard                                          | None                           | Dashboard, settings, account-link UI |
| 5      | v1.1 ARCP Health                                      | None                           | Health page, charts                  |
| 6      | Cutover — bot reads from Supabase                     | Bot SQLite becomes fallback    | Cases browser                        |
| 7      | SQLite removed                                        | Pure-Supabase bot              | Polish, CSV export                   |

Each sprint is sized to a focused session (or two).

---

## 10. Decisions needed before Sprint 1

1. **Supabase project:** confirm we use the existing emgurus-hub Supabase project (recommended) vs a separate "portfolio-guru" project.
2. **URL structure:** `emgurus.com/portfolio` (recommended — single domain, simpler auth) vs `portfolio.emgurus.com` subdomain.
3. **Encryption key surface:** BWS key is currently loaded into the bot's env. To share with Supabase Edge Functions, we either expose it via Supabase Secrets or re-encrypt with a Supabase-vault-managed key. Pick one.
4. **Case text storage:** encrypt at rest (yes/no). Recommended yes for GDPR but adds complexity.
5. **Marketing copy for the landing page:** I'll draft, but need your sign-off on positioning (target audience, value props, tone).

---

## 11. Out of scope (for now)

- Mobile-native iOS/Android app (web works fine on mobile)
- Multi-user assessor portal
- Integrations beyond Kaizen
- Voice profile sample browser (the bot manages voice profile)
- Real-time draft preview from the web (the bot owns drafting)
