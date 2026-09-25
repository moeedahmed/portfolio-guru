# Data architecture plan — one store, hold less

**Date:** 2026-08-24 · **Status:** Approved · **Decider:** Moeed
**Supersedes:** the Supabase mirror design in `backend/supabase_sync.py` and the
retention model in `backend/retention.py`.

## Decisions

| #   | Question                  | Decision                                                                                                                                                                                      |
| --- | ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Source of truth           | Dedicated **Portfolio Guru** Supabase project `wozigfujdifakfqlaurm`, region **eu-west-2 (London)**, keyed on `telegram_user_id`. SQLite becomes a local write-through buffer, not the owner. |
| 2   | Clinical retention        | **Delete case text on successful Kaizen save.** Portfolio Guru holds zero clinical narrative at rest.                                                                                         |
| 3   | EM Gurus Hub link         | **Dropped.** No `emgurus_user_id`, no `/link` tokens, no backfill.                                                                                                                            |
| 4   | Existing plaintext drafts | The **37 real-user files are deleted**. Synthetic and operator drafts are retained.                                                                                                           |

## Why

The audit on 2026-08-24 (see the published data map) found that the mirror
described in the ROPA has never run: `run_local.sh` exports no `SUPABASE_URL`
or `SUPABASE_SERVICE_ROLE_KEY`, and the BWS secrets are named
`SUPABASE_PORTFOLIO_GURU_*` while `supabase_sync.py` reads `SUPABASE_URL`.

Two half-built paths existed. `supabase_sync.py` targets the **EMGurus**
project (eu-west-1) and resolves every write through `emgurus_user_id`, a link
exactly one user has completed — so even switching it on would silently no-op
for every beta doctor. The dedicated **Portfolio Guru** project (London,
created 2026-08-20) has the keys in BWS and zero tables.

Meanwhile `bot.py:12380` writes plaintext clinical drafts that survive both
`/reset` and `retention.py`, `dogfood-audit.ndjson` has grown to 44 MB of
redacted-but-readable case narrative with no rotation, and FileVault is off.

The lever is decision 2. Almost all legal exposure comes from holding case text
at rest, and nothing in the product needs it after the save — Kaizen holds the
evidence, and `/health`, KC coverage, ARCP projection and the voice profile all
run on form types, dates and KC codes. Removing clinical storage removes the
problem rather than encrypting around it.

## Phase 0 — Today, no code

- Turn FileVault on; escrow the recovery key.
- Delete the 37 real-user files in `~/.openclaw/data/portfolio-guru/drafts/`
  (every user id except `99999999`, `6912896590`, `99999`).

## Phase 1 — Stop the bleeding

- `bot.py:12380` — Fernet-encrypt the pre-save draft backup, delete it on
  successful Kaizen save, and expire orphans (failed filings) after 7 days.
- Add the drafts directory to `_clear_local_portfolio_account_data`
  (`bot.py:6987`) so `/reset` actually erases it.
- `dogfood_audit.py` — restrict logging to operator and synthetic user ids;
  rotate at a size cap; strip existing real-user lines.
- Remove the `DEEPSEEK_API_KEY` export from `run_local.sh` and make
  `extractor._select_providers` fail closed: refuse to start when the resolved
  provider list contains a non-EU endpoint, rather than falling back to one.

## Phase 2 — One store in London

- Create the schema in `wozigfujdifakfqlaurm`, keyed on `telegram_user_id`:
  users/tier, consent records, usage, KC coverage, credentials (Fernet blobs
  passed through unchanged), profile, evidence index. RLS on, no public
  policies — the bot's service role is the only writer.
- **No clinical columns.** `portfolio_cases` is not recreated.
- Rewrite `supabase_sync.py`: delete `_resolve_emgurus_user_id`, `_ensure_user`,
  `consume_link_token`, `_backfill_existing_user`, `mirror_case`.
- `run_local.sh` exports `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` from the
  existing `SUPABASE_PORTFOLIO_GURU_URL` / `..._SERVICE_ROLE_KEY` BWS secrets.
- Migrate `usage.db` and `portfolio_guru.db` with verified row counts.
- Keep SQLite on the hot path — reads stay local so the bot never blocks on the
  network — with a replay queue for failed mirrors and a nightly reconcile that
  proves the two match.

## Phase 3 — Hold less

- On successful Kaizen save: delete the case text, the extracted fields, the
  encrypted draft file, and the in-flight persistence entry.
- `retention.py` shrinks to purging orphaned failed-save drafts.

## Phase 4 — Make the documents true

- Rewrite `docs/legal/processors-ropa.md` to the real data map: six activities
  become four, Supabase moves to a named UK processor with a signed DPA,
  Telegram is documented honestly as an unavoidable transfer.
- Update `docs/legal/dpia.md` and `docs/legal/privacy-policy.md` §7 for
  delete-on-save.
- The consent copy's processing description changes, so bump
  `CONSENT_VERSION` in `backend/consent.py` and archive the new wording under
  `docs/legal/consent-versions/`. Every user re-consents.
- Then solicitor sign-off.

## What this costs and buys

Supabase Pro at ~£25/mo (Pro, not free — daily backups and PITR matter for
clinical-adjacent data). Roughly a week of engineering.

Buys: one store instead of three SQLite files and a directory of loose JSON;
UK region end to end; a real processor with a DPA instead of a home disk; a
ROPA that describes the running system; and an Art 9 storage story that is
"we don't keep it" rather than "we encrypt it".

## What this does not change

The agentic behaviour. `/health`, KC coverage, ARCP projection, form
recommendation, the voice profile and draft refinement across turns all run on
metadata or in-flight state. The only capability lost is amending a draft after
it has been saved to Kaizen.
