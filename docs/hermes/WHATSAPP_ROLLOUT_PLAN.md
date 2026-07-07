# Portfolio Guru WhatsApp Rollout Plan

Date: 2026-07-07
Status: repo-owned preparation only; live rollout is blocked until the gates
below are approved.

## Decision

Portfolio Guru is already in active private beta with 20+ tester users. The
product brain remains the deterministic Portfolio Guru engine, exactly like the
Telegram beta bot. WhatsApp is only a thin channel connector for a dedicated
Portfolio Guru WhatsApp number/account. Portfolio Guru is never a Hermes/EMGurus
agent layer, classifier, drafter, or fan-out gateway.

Tester rollout must not use the general EMGurus WhatsApp account. The clean path
is a dedicated Portfolio Guru WhatsApp number and account behind a thin channel
connector before any tester traffic is routed through WhatsApp.

A Hermes profile is **optional**: it may be used only as a thin transport for
that connector, never as product logic. If Hermes is chosen as the transport,
the current `portfolio-guru` Hermes WhatsApp credentials are linked to the same
underlying WhatsApp account as EMGurus, so starting that profile as-is is unsafe;
the dedicated account must come first. A direct channel connector (for example
the `POST /api/portfolio/inbound` bridge in `backend/webhook_server.py`) needs no
Hermes profile at all.

Number choice is an operations decision outside this repo. The current cheap
path is a giffgaff PAYG number if it is actively maintained; SecondSIM is
cleaner but costs more and must be parked or ported before cancellation.

## Product Boundary

The channel connector must not contain Portfolio Guru product logic. If Hermes is
used as the thin transport, its profile must stay a shim: the tracked profile
shim at `scripts/hermes-profile/pg` delegates to `backend/hermes_pg_cli.py`, and
the engine, form recommendation, draft preview, health, save blocking, and
shadow behavior stay repo-owned.

WhatsApp launch work must preserve the same boundary:

- The WhatsApp channel connector (Hermes only if used as thin transport) owns
  channel receipt, identity, rendering, opt-in/out, and delivery.
- Portfolio Guru owns portfolio intake, extraction, recommendation, drafting,
  explicit approval gates, and draft-only Kaizen save behavior.
- Kaizen supervisor submission, signing, approval, rejection, deletion, or
  final submission remains out of scope for this channel.

## Phase Plan

### Phase 0 - Repo-only preparation

Goal: make the decision auditable without touching live accounts.

Required artefacts:

- This rollout plan.
- `docs/legal/whatsapp-meta-processor-review.md`.
- `scripts/pg_whatsapp_readiness.py`.
- Focused offline tests for the readiness guard.

Forbidden in this phase:

- No Hermes runtime edits under `~/.hermes`.
- No BWS/secrets reads.
- No WhatsApp enablement.
- No number purchase, eSIM order, account linking, profile start, service
  restart, push, deploy, or live Telegram/WhatsApp smoke.

### Phase 1 - Dedicated number and account

Goal: create a WhatsApp account that is not the EMGurus account.

Gate:

- A dedicated Portfolio Guru number exists.
- The number is parked/maintained according to the chosen provider's rules.
- The safe account fingerprint for the Portfolio Guru WhatsApp account is
  distinct from the safe account fingerprint for EMGurus.
- The old linked `portfolio-guru` Hermes WhatsApp credentials are not used.

Evidence must be supplied to the readiness guard as non-secret identifiers only.
Do not put raw WhatsApp credentials, QR material, auth tokens, device session
files, or BWS values in the repo or in the guard output.

### Phase 2 - Channel connector (Hermes profile optional)

Goal: wire the dedicated account to a thin channel connector. The connector may
be a direct bridge (`POST /api/portfolio/inbound` in
`backend/webhook_server.py`) or an optional Hermes thin-transport profile.

Gate (always):

- The connector is thin transport only and carries no product logic.
- Underlying WhatsApp account fingerprint differs from EMGurus.
- Set `PG_WHATSAPP_CONNECTOR` to the chosen connector (`direct` or `hermes`).

Gate (only when `PG_WHATSAPP_CONNECTOR=hermes`):

- Profile id is `portfolio-guru`.
- The profile command path still resolves to the tracked shim:
  `scripts/hermes-profile/pg` -> `backend/hermes_pg_cli.py`.
- Product logic has not been copied into the Hermes profile.

A direct connector needs no Hermes profile, and the readiness guard does not
require one unless `PG_WHATSAPP_CONNECTOR=hermes`.

### Phase 3 - Legal and processor review

Goal: make Meta/WhatsApp a reviewed processor before launch.

Gate:

- `docs/legal/whatsapp-meta-processor-review.md` is completed by the founder
  and solicitor/DPO.
- Privacy Policy, Terms, consent copy, DPIA, and ROPA are updated if the review
  changes the WhatsApp/Meta disclosures.
- The review explicitly covers UK GDPR special-category data, WhatsApp terms,
  opt-in/out, human escalation/support, transfer mechanism, retention/deletion,
  and health-information policy fit.

### Phase 4 - Offline readiness preflight

Run the read-only guard from the repo root:

```bash
scripts/pg_whatsapp_readiness.py
```

By default it returns `blocked`. It can only return `launch-ready` when explicit
approval environment variables and non-secret distinct account/profile
fingerprints are supplied:

```bash
# Direct channel connector (no Hermes profile required):
PG_WHATSAPP_ROLLOUT_APPROVED=dedicated-portfolio-guru-whatsapp \
PG_WHATSAPP_LEGAL_APPROVED=meta-whatsapp-processor-reviewed \
PG_WHATSAPP_NUMBER_APPROVED=dedicated-number-ready \
PG_WHATSAPP_CONNECTOR_APPROVED=channel-connector-ready \
PG_WHATSAPP_ACCOUNT_FINGERPRINT=<safe-non-secret-id> \
EMGURUS_WHATSAPP_ACCOUNT_FINGERPRINT=<safe-non-secret-id> \
scripts/pg_whatsapp_readiness.py
```

Only if the chosen connector is Hermes, additionally set
`PG_WHATSAPP_CONNECTOR=hermes` and supply the distinct profile ids
`PG_WHATSAPP_PROFILE_ID=portfolio-guru` and `EMGURUS_WHATSAPP_PROFILE_ID=emgurus`.

The guard is machine-checkable JSON. It does not read BWS, parse credential
files, inspect `~/.hermes`, or start any service.

### Phase 5 - Shadow and live proof

This phase is orchestrator/manual only.

Gate:

- Dedicated account and legal gates are approved.
- Offline readiness guard returns `launch-ready`.
- Shadow mode is exercised with synthetic/anonymised messages.
- Live WhatsApp proof is a small direct-message smoke on the dedicated
  Portfolio Guru account, not the EMGurus account.
- Telegram private beta is left running and is not restarted for this proof.

### Phase 6 - Tester rollout

Gate:

- Invite copy names WhatsApp as beta/limited.
- Testers are routed only to the dedicated Portfolio Guru number.
- Opt-out and support path are visible.
- First cohort is limited and monitored.
- Rollback path is to disable the dedicated WhatsApp profile, not to route
  users through EMGurus.

## Stop Conditions

Stop if any of these are true:

- Portfolio Guru and EMGurus WhatsApp fingerprints match.
- The chosen connector is Hermes and its profile id is missing or not
  `portfolio-guru`.
- The readiness guard returns `blocked`.
- Meta/WhatsApp processor review is incomplete.
- Product logic is found in the channel connector (e.g. a Hermes profile)
  instead of the repo-owned engine / `backend/hermes_pg_cli.py`.
- Testers would be routed through the general EMGurus WhatsApp account or an
  EMGurus fan-out gateway.
- Any runtime action would require reading secrets, editing `~/.hermes`,
  restarting services, or enabling WhatsApp without the explicit rollout gate.

## Verification

Focused offline verification for this preparation slice:

```bash
cd backend && venv/bin/python3 -m pytest \
  tests/test_pg_whatsapp_readiness.py \
  tests/test_hermes_pg_cli.py \
  tests/test_hermes_integration.py \
  tests/test_channel_contract.py \
  tests/test_portfolio_inbound_bridge.py -v
```

Do not run live Telegram tests or live WhatsApp tests as part of this repo-only
preparation.
