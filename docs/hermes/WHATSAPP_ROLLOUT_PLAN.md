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

#### Direct linked-device connector (default path)

The lean direct path is a WhatsApp linked-device (Baileys / WhatsApp-Web
multi-device) session against the dedicated Portfolio Guru account. Set
`PG_WHATSAPP_CONNECTOR` to `linked-device` (or leave it unset — `direct` is the
default and is the same connector family).

The transport boundary is repo-owned and offline-testable today:

- `backend/whatsapp_linked_device.py` normalises a raw linked-device message
  envelope into the channel-neutral `InboundMessage` contract and delegates the
  routing decision to `channel_contract.accept_inbound`. It contains no product
  logic, fetches no bytes, forwards no WhatsApp media key, and is import-clean of
  Telegram and the product engine.
- `whatsapp_linked_device.to_inbound_payload(raw)` produces the exact JSON body
  for the repo-owned `POST /api/portfolio/inbound` bridge, so the live connector
  only relays neutral envelopes — the product boundary is identical to every
  other channel and a future Meta Cloud API webhook can replace the transport
  without touching product logic.
- Dry-run any recorded payload offline (no service, no secret, no device link):

  ```bash
  cd backend && venv/bin/python3 whatsapp_linked_device.py --payload envelope.json
  ```

  It prints routing metadata only (scope, disposition, media kinds) and never
  the clinical text or captions.

##### Runnable connector shell (`backend/whatsapp_connector_runner.py`)

The normaliser is driven by a repo-owned, offline-testable **connector shell**.
It is the transport half of the connector and carries no product logic — it only
normalises raw linked-device events and relays neutral payloads to the inbound
bridge. Two modes:

- **dry-run** (default, always offline): read a recorded batch (a JSON array, a
  single JSON object, or NDJSON) and print the routing verdict per turn. Contacts
  nothing:

  ```bash
  cd backend && venv/bin/python3 whatsapp_connector_runner.py \
    --payload tests/fixtures/whatsapp_linked_device_events.json
  ```

- **relay** (gated): read raw events (NDJSON on stdin, as the live sidecar
  streams them) and forward only DIRECT non-empty turns to the inbound bridge.
  GROUP and empty turns are refused locally and never posted. Relay refuses
  unless `scripts/pg_whatsapp_readiness.py` returns `launch-ready`, so it is
  blocked by default:

  ```bash
  # Blocked by default; runs only when the readiness guard is launch-ready.
  PORTFOLIO_INBOUND_URL=<bridge-url> \
  PORTFOLIO_INBOUND_SECRET=<shared-gateway-secret> \
  <baileys-sidecar> | \
    cd backend && venv/bin/python3 whatsapp_connector_runner.py --relay
  ```

Required env vars for relay (names only; never commit values):

- `PORTFOLIO_INBOUND_URL` — full URL of the `POST /api/portfolio/inbound` bridge.
- `PORTFOLIO_INBOUND_SECRET` — shared gateway secret sent as `X-Gateway-Secret`;
  this is the same value the bridge reads from `PORTFOLIO_INBOUND_SECRET`.

##### Deferred live dependency — the Baileys / WhatsApp-Web sidecar

Emitting the WhatsApp QR and maintaining the linked-device session is **not**
done by the Python shell and is intentionally deferred to a thin Baileys /
WhatsApp-Web multi-device sidecar (a Node dependency not yet added to this repo).
Its only job is transport: authenticate the linked-device session in a persistent
store it owns, then stream each raw incoming message envelope as one JSON object
per line into `whatsapp_connector_runner.py --relay`. It must contain no product
logic, never place a WhatsApp media key or auth token on stdout, and read no repo
secret. The exact next dependency step is to add and wire that sidecar; until
then the shell is the tested seam and the connector is not live-ready.

The readiness guard tiers a direct/linked-device connector by the repo code that
actually exists: `linked-device-adapter-present` (the neutral normaliser) and
`connector-shell-present` (the runnable relay shell). It never asserts a
`live-linked` tier — a real linked-device session is a manual runtime state
proven out-of-band, so the guard cannot and does not claim a device is linked.
Both repo tiers must be present alongside the dedicated account, legal, connector,
and distinct-fingerprint gates for the guard to return `launch-ready`.

#### Next manual live step — link the dedicated account via Linked Devices

This is the first action that touches a live WhatsApp account. Do it only after
the offline readiness guard returns `launch-ready` for the direct connector.

1. On the phone holding the **dedicated Portfolio Guru** WhatsApp Business
   number (never the EMGurus handset), open WhatsApp → Settings → **Linked
   Devices** → **Link a device**.
2. Start the Baileys / WhatsApp-Web sidecar (the deferred Node dependency) in
   QR/link mode and scan the QR with the dedicated handset. The sidecar owns the
   QR and the session; the repo-owned `whatsapp_connector_runner.py --relay`
   consumes the raw events it streams and never emits or scans a QR itself.
   Confirm the newly linked device appears under Linked Devices on the dedicated
   account.
3. Verify the resulting account fingerprint is **distinct** from the EMGurus
   fingerprint before any tester traffic (this is the guard's
   `distinct-whatsapp-account` gate).
4. Exercise shadow mode with synthetic/anonymised payloads first, then a single
   direct-message smoke to the dedicated number only.

Stop conditions for this step:

- The QR would be scanned on the EMGurus handset, or the linked device would
  attach to the EMGurus account.
- The Portfolio Guru and EMGurus account fingerprints match.
- The readiness guard returns `blocked`.
- Linking would require reading secrets, editing `~/.hermes`, or restarting a
  shared service.

Rollback: remove the linked device from **Linked Devices** on the dedicated
account and stop the connector process. Never fall back to routing testers
through the EMGurus account.

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
  tests/test_portfolio_inbound_bridge.py \
  tests/test_whatsapp_linked_device.py \
  tests/test_whatsapp_connector_runner.py -v
```

Do not run live Telegram tests or live WhatsApp tests as part of this repo-only
preparation.
