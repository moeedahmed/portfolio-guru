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

After the 2026-07-08 account restriction/review incident, a recovered WhatsApp
Business account is not automatically safe to reconnect. The rollout now also
requires explicit account-health approval: the account must show no current
restriction, review, lock, or risk flag, and the verification/profile state must
be stable before any connector is started. If the account asks for verification
again, complete that process first and do not generate QR codes or start a
linked-device connector while the account is still being reviewed.

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
- The account has passed any required verification/review and shows no current
  restriction, lock, review, or risk flag.
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

Transport decision gate (no route is the automatic default):

Both transport families are valid. The choice is an explicit, context-driven
operations decision made per rollout, not a ranking. Neither route is
"preferred" or "safe by default"; each is selected by matching this rollout's
volume, legal posture, and lifecycle stage against the matrix below, and each
must clear the *same* account-health, dedicated-account, legal, connector, and
private-canary gates before any traffic flows. The readiness guard validates the
connector you selected; it never selects one for you.

| Transport family | Route values | Fits when | Trade-offs |
| --- | --- | --- | --- |
| Direct linked-device / Baileys | `direct`, `linked-device`, `baileys` | Lean, controlled, low-volume private beta on the dedicated number where a repo-owned transport is wanted end-to-end | Unofficial WhatsApp-Web multi-device session; single phone-bound link; no Meta DPA/SLA; account-ban risk at scale (see production-scale path) |
| Official WhatsApp Business Platform / BSP | `cloud-api`, `meta-cloud-api`, `whatsapp-business-platform`, `kapso`, `2chat-waba` | Durable larger-beta or production behaviour needing webhooks, delivery status, templates, contracted Meta processor/DPA, and throughput tiers | Requires business verification, template approval, and a provider/BSP relationship; more onboarding before first message |
| Hermes thin-transport (optional) | `hermes` | Only when an existing Hermes profile is reused strictly as thin transport | Adds the Hermes-profile shim gates; carries no product logic |

Route-specific rules that apply once a family is selected:

- Direct linked-device/Baileys is selected only after account health is stable
  and only with the one-QR readiness rule. Linked-device pairing must never be
  used as a retry loop or as proof that the Portfolio Guru relay can reply.
- Official/BSP routes carry no linked-device readiness tiers, but they do not
  bypass any gate: the same account-health, dedicated-account, distinct-account,
  legal, connector, and private-canary proofs are mandatory before launch.

Gate (always):

- The connector is thin transport only and carries no product logic.
- Underlying WhatsApp account fingerprint differs from EMGurus.
- Set `PG_WHATSAPP_CONNECTOR` to the chosen connector (`direct`, `hermes`,
  `cloud-api`, `meta-cloud-api`, `whatsapp-business-platform`, `kapso`, or
  `2chat-waba`).

Gate (only when `PG_WHATSAPP_CONNECTOR=hermes`):

- Profile id is `portfolio-guru`.
- The profile command path still resolves to the tracked shim:
  `scripts/hermes-profile/pg` -> `backend/hermes_pg_cli.py`.
- Product logic has not been copied into the Hermes profile.
- The live Hermes WhatsApp identity guard passes:
  `scripts/pg_whatsapp_identity_guard.py` must show the `portfolio-guru` and
  `emgurus` Hermes WhatsApp session fingerprints are distinct before the
  Portfolio Hermes gateway is started. If the fingerprints match, stop the
  Portfolio Hermes gateway and do not ask for more WhatsApp test messages; two
  Baileys bridges are fighting for the same account and WhatsApp will return
  `440 connectionReplaced`.

A direct connector needs no Hermes profile, and the readiness guard does not
require one unless `PG_WHATSAPP_CONNECTOR=hermes`.

#### Direct linked-device connector (lean controlled-beta path)

This is one of the two transport families in the decision gate above, not the
mandatory route. It is the lean, repo-owned path for a controlled beta: a
WhatsApp linked-device (Baileys / WhatsApp-Web multi-device) session against the
dedicated Portfolio Guru account. Select it explicitly by setting
`PG_WHATSAPP_CONNECTOR` to `linked-device` (or `direct` / `baileys` — the same
connector family). If the variable is left unset the guard falls back to the
`direct` family only so the offline report has something to validate; that
fallback is a reporting convenience, not a recommendation to use this route.

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

##### Live dependency — the Baileys / WhatsApp-Web sidecar (present, not yet linked)

Emitting the WhatsApp QR and maintaining the linked-device session is **not**
done by the Python shell — it is the job of a thin Baileys / WhatsApp-Web
multi-device sidecar. That sidecar now exists in the repo as an **isolated Node
package** at `connectors/whatsapp-linked-device/` (its own `package.json` /
`package-lock.json`, dependency `@whiskeysockets/baileys`; the Python backend
requirements are untouched). Its only job is transport: authenticate the
linked-device session in a persistent auth dir it owns, then stream each raw
incoming message envelope as one JSON object per line into
`whatsapp_connector_runner.py --relay`. It contains no product logic, drops every
WhatsApp media key / encrypted URL by whitelist before emitting (see
`connectors/whatsapp-linked-device/lib/sanitize.js`), emits the QR only to
stderr so stdout stays a clean NDJSON channel, and reads no repo secret.

The sidecar is offline-testable today and has **not** linked any live account:

- Unit + mock-mode tests (no socket, no WhatsApp, no QR):

  ```bash
  cd connectors/whatsapp-linked-device && npm test
  ```

- Mock replay whose NDJSON is accepted verbatim by the Python normaliser:

  ```bash
  cd connectors/whatsapp-linked-device && \
    node index.js --mock --fixtures ../../backend/tests/fixtures/whatsapp_linked_device_events.json \
    | (cd ../../backend && venv/bin/python3 whatsapp_connector_runner.py)
  ```

The readiness guard tiers a direct/linked-device connector by the repo code that
actually exists: `linked-device-adapter-present` (the neutral normaliser),
`connector-shell-present` (the runnable relay shell), and
`linked-device-sidecar-present` (the isolated Baileys sidecar transport). None of
these assert a `live-linked` tier — a real linked-device session is a manual
runtime state proven out-of-band, so the guard cannot and does not claim a device
is linked; `linked-device-sidecar-present` attests transport code only. All three
repo tiers must be present alongside the dedicated account, legal, connector, and
distinct-fingerprint gates for the guard to return `launch-ready`.

##### Supervised saved-session beta runner (`scripts/pg_whatsapp_beta_runner.py`)

Once the QR has been scanned exactly once and the saved auth reopens, the beta
operating mode is **not** another QR/link run. Use the supervisor wrapper:

```bash
scripts/pg_whatsapp_beta_runner.py plan
scripts/pg_whatsapp_beta_runner.py start
scripts/pg_whatsapp_beta_runner.py status
scripts/pg_whatsapp_beta_runner.py stop
```

The supervisor starts the linked-device sidecar and Python relay as one local
process group, writes redacted logs to `.artifacts/whatsapp-live/beta-runner.log`,
and refuses to start unless:

- `scripts/pg_whatsapp_readiness.py` returns `launch-ready`.
- saved linked-device auth exists (`creds.json` in the sidecar auth dir).
- `PORTFOLIO_INBOUND_URL`, `PORTFOLIO_INBOUND_SECRET`, and `PG_WA_SEND_PORT`
  are set in the environment.
- no existing beta runner PID is alive.

It always runs the sidecar with `--forbid-qr` / `PG_WA_FORBID_QR=1`. If WhatsApp
asks for a QR, the sidecar exits loudly instead of emitting one. That makes the
normal beta failure mode "runner stopped; relink needs a deliberate manual
approval" rather than "unexpected QR loop".

##### Baileys source-of-truth checklist for the beta runner

Upstream references checked on 2026-07-08: WhiskeySockets/Baileys README,
Baileys wiki Introduction, Socket Configuration, Connecting, and Receiving
Updates pages, plus the Baileys `DisconnectReason` enum in source.

For this repo, those upstream facts translate into these local requirements:

- Baileys is WhatsApp Web over WebSocket via Linked Devices, not WABA/Cloud API.
  Treat it as the lean controlled-beta transport only.
- QR/pairing proves only that a linked-device auth session was created. It does
  not prove the Portfolio Guru bridge, engine, outbound send path, or recipient
  delivery.
- Persist auth and key updates. Baileys emits `creds.update` after auth/key
  changes; losing those updates can stop messages reaching recipients. The demo
  `useMultiFileAuthState` is acceptable for the current local beta runner, but
  the Baileys wiki explicitly warns not to rely on it in production. A durable
  service needs its own auth/key store.
- Add a real message store before calling the linked-device route production
  quality. Baileys expects `getMessage` for resend/retry and other message
  operations; a runner with no store can prove a beta canary but not robust
  delivery semantics.
- Process every item in each `messages.upsert` array. `notify` is usually a new
  live message; `append` is old/already-seen/offline-sync style traffic. Status
  proof must distinguish them so replayed or from-self events are not mistaken
  for fresh tester traffic.
- Local outbound success is not the same as WhatsApp delivery. Beta proof should
  add receipt/update tracking before wider tester use, so "sent" can be separated
  from "delivered/read/failed" rather than inferred from `sendMessage` resolving.
- Reconnect policy must be driven by Baileys disconnect codes:
  `428 connectionClosed`, `408 connectionLost/timedOut`, and
  `515 restartRequired` are reconnectable for a saved session;
  `401 loggedOut`, `500 badSession`, `403 forbidden`, `440 connectionReplaced`,
  and repeated `503 unavailableService` require stop-and-investigate rather than
  blind relink.
- Keep Baileys/protocol logs out of the NDJSON message pipe. If any library line
  leaks into stdout, the Python relay must ignore it as non-message transport
  noise and continue; canary proof still requires a routable inbound envelope,
  bridge `200 OK`, and outbound send/receipt evidence.

#### Next manual live step — link the dedicated account via Linked Devices

This is the first action that touches a live WhatsApp account. Do it only after
the offline readiness guard returns `launch-ready` for the direct connector.

1. On the phone holding the **dedicated Portfolio Guru** WhatsApp Business
   number (never the EMGurus handset), open WhatsApp → Settings → **Linked
   Devices** → **Link a device**.
2. Install the sidecar dependency once and start it in QR/link mode, then scan
   the QR with the dedicated handset. The sidecar owns the QR and the session;
   the repo-owned `whatsapp_connector_runner.py --relay` consumes the raw events
   it streams and never emits or scans a QR itself:

   ```bash
   cd connectors/whatsapp-linked-device
   npm install                 # first time only; isolated to this dir
   PG_WA_AUTH_DIR=.wa-auth node index.js --qr \
     | (cd ../../backend && \
        PORTFOLIO_INBOUND_URL=<bridge-url> \
        PORTFOLIO_INBOUND_SECRET=<shared-gateway-secret> \
        venv/bin/python3 whatsapp_connector_runner.py --relay)
   ```

   The QR prints to stderr; scan it on the dedicated handset. Confirm the newly
   linked device appears under Linked Devices on the dedicated account.
3. Verify the resulting account fingerprint is **distinct** from the EMGurus
   fingerprint before any tester traffic (this is the guard's
   `distinct-whatsapp-account` gate). If the chosen connector is Hermes, also
   run `scripts/pg_whatsapp_identity_guard.py` against the live Hermes profile
   creds and set
   `PG_WHATSAPP_HERMES_IDENTITY_APPROVED=distinct-live-hermes-identity` only
   after that script passes.
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

#### Next beta operating step — receive replies continuously

After a single private canary proves:

1. saved session opens without QR,
2. inbound `messages.upsert` is observed,
3. the relay forwards to `POST /api/portfolio/inbound`,
4. the bridge returns `200 OK`,
5. outbound send succeeds,

start `scripts/pg_whatsapp_beta_runner.py start` for a supervised beta window.
Do not invite testers while the runner is only being launched manually for
bounded watches. Tester traffic needs the supervised runner alive, observable,
and stoppable.

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

## Production-Scale Path for Public WhatsApp (beyond moving the Mac Mini to cloud)

Moving the engine off the Mac Mini onto a cloud host improves availability and
hosting, but it does **not** make WhatsApp production-ready. These are two
independent axes, and public scale needs both handled:

1. **Where the engine runs** — Mac Mini today; a cloud VM/container later for
   uptime, backups, and monitoring. This is a hosting decision only.
2. **How WhatsApp messages arrive** — the transport. This is the axis that
   actually gates public scale, and cloud hosting does nothing for it.

### Why the linked-device (Baileys) path is beta-only, not production

The direct linked-device connector is the right lean choice for a **controlled,
low-volume beta on the dedicated number**, but it is not a public-scale
transport:

- It drives an unofficial WhatsApp-Web multi-device session, which is outside
  Meta's supported/permitted integration surface and carries account-ban risk.
- It is a single phone-bound session: one number, one device link, fragile
  re-pairing, no formal throughput guarantees, no SLA, no support path.
- There is no formal processor/DPA relationship with Meta for that session, so
  it cannot satisfy the public-launch legal posture the Phase 3 review needs.
- Observability, opt-in/opt-out management, and message templating are all
  ad hoc rather than platform-provided.

### A durable production transport: WhatsApp Business Platform (Cloud API)

When a rollout needs public scale, a durable production-readiness route is to
move the **transport** (not the engine) onto the official **WhatsApp Business
Platform / Cloud API**, either directly via Meta or through a Business Solution
Provider (e.g. 360dialog, Twilio, Sinch). This is the route to reach for when the
linked-device limits below bind; it is not automatically correct for a small
controlled beta. It provides:

- A verified WhatsApp Business number as an **identity**, not a device link, with
  documented throughput tiers and a real support/SLA path.
- Official inbound **webhooks** and outbound send APIs, approved message
  templates, and platform-managed opt-in/opt-out.
- Meta as a **contracted processor with a DPA** — the transfer mechanism and
  processor status the `docs/legal/whatsapp-meta-processor-review.md` gate is
  written to cover for a public audience.

### Why this is a transport swap, not a rebuild

The whole point of the channel-neutral boundary is that this migration touches
**transport only**. The product engine, extraction, drafting, approval gates,
first-contact onboarding, and Kaizen save behaviour all sit behind
`POST /api/portfolio/inbound` and the `InboundMessage` contract. Going to the
Cloud API means writing one new transport normaliser — a Cloud API webhook
handler that maps Meta's inbound payload onto the same `InboundMessage` and posts
the same neutral bridge body — in place of the Baileys sidecar + relay. No product
logic, no `channel_contract`, and no first-contact code changes. The dry-run and
readiness scaffolding stay valid; only the `PG_WHATSAPP_CONNECTOR` transport
implementation is replaced.

### Recommended sequence

1. Run the controlled beta now on the linked-device connector against the
   dedicated number (Phases 1–6 above), keeping cohorts small and monitored.
2. In parallel, apply for the WhatsApp Business Platform / a BSP, complete
   business verification, get message templates approved, and sign the Meta DPA
   as part of the Phase 3 legal review — extended to cover public (non-tester)
   scale.
3. Build a Cloud API webhook normaliser feeding the existing inbound bridge;
   exercise it in shadow mode with synthetic payloads exactly like the
   linked-device path.
4. Cut testers over to the Cloud API transport, then decommission the
   linked-device session. Host the engine wherever availability dictates (cloud);
   that hosting move is orthogonal to the transport and can happen before, during,
   or after the transport migration.

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

Sidecar transport contract (Node, offline — no socket, no WhatsApp, no QR):

```bash
cd connectors/whatsapp-linked-device && npm test
```

Do not run live Telegram tests or live WhatsApp tests as part of this repo-only
preparation.
