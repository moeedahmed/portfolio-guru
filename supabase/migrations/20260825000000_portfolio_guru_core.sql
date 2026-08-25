-- Portfolio Guru core schema — London (eu-west-2), project wozigfujdifakfqlaurm
--
-- Source of truth for the account layer, per decision 1 of
-- docs/data-architecture-plan-2026-08-24.md. Keyed on telegram_user_id
-- directly: the previous mirror resolved every write through emgurus_user_id,
-- a web-app link exactly one user had completed, so every write silently
-- no-opped for every beta doctor.
--
-- NOT PRESENT, DELIBERATELY: any column holding case narrative, drafted
-- clinical text or extracted patient fields. Decision 2 is that Portfolio Guru
-- holds no clinical content at rest — Kaizen holds the evidence. Adding such a
-- column here reintroduces an Art. 9 store and needs its own DPIA review.
--
-- ACCESS MODEL: RLS is enabled on every table with no policies defined. That
-- denies all anon and authenticated access outright; only the service role
-- (which bypasses RLS) can read or write, and the bot is its only holder. If a
-- web app is ever given direct access, add explicit policies — do not disable
-- RLS.

begin;

-- ── Accounts, tier and billing ──────────────────────────────────────────────
create table if not exists pg_users (
  telegram_user_id        bigint primary key,
  tier                    text not null default 'free'
                            check (tier in ('free', 'pro', 'pro_plus')),
  is_beta                 boolean not null default false,
  stripe_customer_id      text,
  stripe_subscription_id  text,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now()
);
comment on table pg_users is
  'One row per Telegram user. Billing link is retained through a /reset so an active subscription is not orphaned.';

-- ── Consent (append-only; never deleted, not even by erasure) ───────────────
-- The consent history is the evidence of the lawful basis for past processing.
-- A withdrawal appends a row; it never overwrites the grant.
create table if not exists pg_consent_records (
  id                bigserial primary key,
  telegram_user_id  bigint not null,
  consent_version   text not null,
  consent_text_hash text not null,
  action            text not null
                      check (action in ('granted', 're-granted', 'withdrawn')),
  channel           text not null default 'telegram',
  lawful_basis      text not null default 'art9_2a_explicit_consent',
  created_at        timestamptz not null default now()
);
create index if not exists idx_pg_consent_user
  on pg_consent_records (telegram_user_id, consent_version, id desc);
comment on table pg_consent_records is
  'Append-only. No erasure path may delete from this table (UK GDPR accountability).';

-- ── Kaizen credentials (Fernet ciphertext; the key lives in BWS, not here) ──
create table if not exists pg_credentials (
  telegram_user_id     bigint primary key,
  kaizen_username_enc  bytea not null,
  kaizen_password_enc  bytea not null,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);
comment on table pg_credentials is
  'Fernet ciphertext only. FERNET_SECRET_KEY is held in Bitwarden Secrets Manager and never stored alongside the data it protects.';

-- ── Training profile (no clinical content; voice_profile is a style summary) ─
create table if not exists pg_profile (
  telegram_user_id      bigint primary key,
  training_level        text,
  curriculum            text default '2025',
  kaizen_role           text,
  voice_profile         jsonb,
  voice_examples_count  integer not null default 0,
  updated_at            timestamptz not null default now()
);
comment on column pg_profile.voice_profile is
  'Derived writing-style summary. Raw excerpts of the doctor''s prior entries are never persisted.';

-- ── Usage metering ──────────────────────────────────────────────────────────
create table if not exists pg_usage (
  id                bigserial primary key,
  telegram_user_id  bigint not null,
  form_type         text not null,
  status            text not null default 'filed',
  filed_at          timestamptz not null default now(),
  month_key         text generated always as (to_char(filed_at, 'YYYY-MM')) stored
);
create index if not exists idx_pg_usage_user_month
  on pg_usage (telegram_user_id, month_key);

-- ── Curriculum coverage (RCEM taxonomy references, no patient detail) ───────
create table if not exists pg_kc_coverage (
  id                bigserial primary key,
  telegram_user_id  bigint not null,
  form_type         text not null,
  kcs_selected      jsonb not null,
  created_at        timestamptz not null default now()
);
create index if not exists idx_pg_kc_coverage_user
  on pg_kc_coverage (telegram_user_id);

-- ── Filing outcomes — the FACT of a save, never its content ─────────────────
create table if not exists pg_filings (
  id                bigserial primary key,
  telegram_user_id  bigint not null,
  form_type         text not null,
  status            text not null,
  kaizen_event_id   text,
  curriculum_links  jsonb not null default '[]'::jsonb,
  key_capabilities  jsonb not null default '[]'::jsonb,
  source            text not null default 'bot',
  created_at        timestamptz not null default now()
);
create index if not exists idx_pg_filings_user
  on pg_filings (telegram_user_id, created_at desc);
comment on table pg_filings is
  'Replaces portfolio_cases. Holds no case_text and no extracted_fields by design.';

-- ── Assessor chase log ──────────────────────────────────────────────────────
-- Holds a THIRD party's name and email (the supervisor being chased), not just
-- the doctor's. That is personal data about someone who never used the product,
-- so it carries its own ROPA line and its own erasure story.
create table if not exists pg_chase_log (
  id                bigserial primary key,
  telegram_user_id  bigint not null,
  assessor_name     text,
  assessor_email    text,
  chase_date        date,
  method            text not null default 'manual',
  ticket_summary    text,
  chase_number      integer not null default 1,
  created_at        timestamptz not null default now()
);
create index if not exists idx_pg_chase_log_user
  on pg_chase_log (telegram_user_id, created_at desc);

-- ── Beta access requests ────────────────────────────────────────────────────
create table if not exists pg_beta_requests (
  id                bigserial primary key,
  telegram_user_id  bigint not null,
  username          text not null default '',
  tier_requested    text not null default 'beta',
  status            text not null default 'pending'
                      check (status in ('pending', 'approved', 'declined')),
  created_at        timestamptz not null default now(),
  approved_at       timestamptz
);
create index if not exists idx_pg_beta_requests_pending
  on pg_beta_requests (status, username);

-- ── Stripe webhook idempotency ──────────────────────────────────────────────
create table if not exists pg_stripe_webhook_events (
  event_id      text primary key,
  event_type    text not null,
  processed_at  timestamptz not null default now()
);

-- ── Lock everything to the service role ─────────────────────────────────────
alter table pg_users                enable row level security;
alter table pg_consent_records      enable row level security;
alter table pg_credentials          enable row level security;
alter table pg_profile              enable row level security;
alter table pg_usage                enable row level security;
alter table pg_kc_coverage          enable row level security;
alter table pg_filings              enable row level security;
alter table pg_chase_log            enable row level security;
alter table pg_beta_requests        enable row level security;
alter table pg_stripe_webhook_events enable row level security;

commit;
