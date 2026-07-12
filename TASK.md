# Active Task — 14-Day Telegram Launch-Proof Sprint

> Date: 2026-07-09
> Status: active product focus.
> Prior task archived at `_archived/TASK-hermes-hackathon-production-cut-20260709.md`.

## 2026-07-12 — Beta narrowing / telemetry-provenance hardening

Implemented from an accepted Fable consolidation plan, on
`chore/change-safety-gate`:

- Telemetry provenance repair: `filing_attempt_log.py` / `funnel_metrics.py`
  now classify every event/attempt as real, synthetic test fixture,
  operator/dogfood (Moeed's own `ADMIN_USER_ID`), or legacy/unattributed
  (no `user_id`). Admin reports default to the real cohort only and never
  count unattributed records as completed/repeat real users.
- `/filingreport` and `/funnelreport` now append a `Revision: <branch>@<commit>`
  line (admin-only) sourced from the existing `runtime_identity` mechanism.
- `filer_router.py`: the browser-use fallback is now off by default
  (`PG_ENABLE_BROWSER_USE_FALLBACK`, unset = disabled). An unmapped form or
  platform fails cleanly instead of silently escalating to browser-use.
  Deterministic Playwright remains the only default beta filing route.
- Fixed `/upgrade` advertising "Bulk filing" as a paid perk while `/bulk`
  returns "coming soon" — removed the false promise.

**Open decision for Moeed (not guessed):** the deterministic DOM-mapped form
set is large (~72 forms across 2021/2025 curricula). This hardening pass did
not narrow the advertised/supported form list, because doing so is a
clinical/product judgement call, not something derivable from repo evidence
alone. If a smaller explicit beta form set (e.g. CBD/DOPS/LAT/ACAT only) is
wanted for the 3-5 tester cohort, that selection needs Moeed's call — current
coverage stays as-is (`docs/form-coverage.md`, `filer_router.PLATFORM_REGISTRY`)
until then.

## Decision

Portfolio Guru is now a Telegram-first private beta. The next 7-14 days should
prove that doctors can repeatedly move from rough case note to reviewed Kaizen
draft without the journey feeling fragile.

WhatsApp is paused, not deleted. Keep the code and connector work intact, but
do not invest major build time in WhatsApp, official Cloud API, a web workflow,
or channel parity until Telegram proves real repeat use.

## Commercial Boundary

Commercial activity stays under Solvoro Labs (US). Do not route product revenue,
contracts, or commercial launch work through a UK entity.

Legal and privacy copy are beta-readiness gates, not optional polish. Current
legal files are draft-only and contain review markers; do not treat them as
public in-force terms.

## Goal

By the end of this sprint, Portfolio Guru should be able to answer:

1. How many real users completed case -> preview -> Kaizen draft?
2. How many users did it twice?
3. What is the real live filing failure rate?
4. Which beta users are warm enough to continue testing?
5. What legal/privacy gaps block wider or paid launch?

## Scope This Sprint

1. Telegram funnel tracking:
   - case started
   - recommendation shown
   - form picked
   - draft previewed
   - save attempted
   - draft saved
   - filing failed
2. Filing reliability:
   - keep `/filingreport` as the Kaizen reliability surface
   - page the operator on live save failures or uncertain saves
3. Golden-path Telegram stability:
   - no duplicate/stale buttons
   - setup loops recover cleanly
   - flexible side questions return to the exact current step
   - free text cannot silently amend, save, cancel, reset, or switch cases
   - case capture, recommendation, preview, edit, approve, retry, and reset stay deterministic
4. Private beta readiness:
   - minimal beta consent/terms/privacy boundary
   - re-engagement message for 3-5 warm testers
   - no public launch claim until legal markers are closed

## Parked

Park these unless Moeed explicitly reopens them:

- WhatsApp parity and WhatsApp Cloud API migration
- new web workflow/app expansion
- public launch growth work
- assessor workflow expansion
- ARCP Health expansion
- new form coverage beyond golden-path reliability
- hackathon/demo ledger optimisation

Do not delete parked code. Freeze it and keep it from distracting the launch
proof path.

## Guardrails

- No deploy/restart/live bot refresh without explicit approval.
- No public outreach/send without explicit approval.
- No raw logs, patient data, customer data, credentials, financial detail, or
  visa-sensitive detail in docs or reports.
- No refined draft generator changes unless a specific failing golden-path test
  proves the issue and Moeed approves that scope.
- No WhatsApp rebuilds during this sprint.

## Current Build Actions

Internal actions already approved for execution:

- Add durable PHI-free Telegram funnel metrics.
- Add Kaizen filing failure alerting.
- Add callback/state map to reduce Telegram regressions.
- Document private-beta legal readiness and remaining blockers.
- Draft, but do not send, beta tester re-engagement copy.
- Back up local work to a non-deploy remote branch after tests pass.

Actions needing separate approval before execution:

- Deploy/restart live Telegram bot.
- Push to `main` if it would trigger GitHub Actions/deploy.
- Send beta tester outreach.
- Publish or put legal terms/privacy copy into force.

## Proof Before Re-Engagement

Before asking testers to file real cases again:

- focused Telegram/channel tests pass
- `/funnelreport` exists for admin journey metrics
- `/filingreport` still works for Kaizen reliability
- legal/private beta consent boundary is documented
- live refresh/release is explicitly approved and completed
