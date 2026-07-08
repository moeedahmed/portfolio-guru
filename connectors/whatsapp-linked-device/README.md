# Portfolio Guru WhatsApp linked-device sidecar

Thin **transport** sidecar for the direct WhatsApp linked-device path. It uses
[Baileys](https://github.com/WhiskeySockets/Baileys)
(`@whiskeysockets/baileys`, the maintained WhatsApp-Web multi-device library) to
hold a linked-device session for the **dedicated Portfolio Guru WhatsApp Business
account**, emit the QR for a human to scan, and stream each incoming message
envelope as one NDJSON line on stdout.

It carries **no Portfolio Guru product logic**. All extraction, form
recommendation, drafting, and Kaizen access live behind the repo-owned Python
engine. The sidecar's stdout is consumed by
`backend/whatsapp_connector_runner.py --relay`, which normalises each envelope
via `backend/whatsapp_linked_device.py` and forwards handled turns to
`POST /api/portfolio/inbound`.

For the linked-device beta, the same sidecar can expose a localhost-only
WhatsApp send endpoint via `PG_WA_SEND_PORT`. Portfolio Guru still owns the
reply text; the sidecar only sends that text through the already-linked
WhatsApp socket.

```
dedicated PG WhatsApp account
  -> this sidecar (Baileys)  --NDJSON on stdout-->
     whatsapp_connector_runner.py --relay
       -> POST /api/portfolio/inbound
         -> deterministic Portfolio Guru engine
```

## Safety boundary

- Sanitisation is **whitelist-based** (`lib/sanitize.js`): only the routing key
  and the exact fields the Python normaliser reads (`conversation`,
  `extendedTextMessage.text`, and per media container `mimetype` / `caption` /
  `ptt`) are copied through. Every WhatsApp **media key, encrypted URL, direct
  path, thumbnail and hash is dropped by construction** and can never reach
  stdout. Media bytes are resolved out-of-band by the live connector, never
  through this transport frame.
- The QR and all human-facing logs go to **stderr**; **stdout is reserved for
  the NDJSON event stream** so it is never corrupted.
- If `PG_WA_QR_DIR` is set, the sidecar also writes short-lived QR handoff
  artefacts to `latest.png` and `latest.txt` in that directory. Use the PNG for
  Telegram/laptop scanning; treat both files as temporary login artefacts.
- The sidecar reads **no repo secret**. The inbound bridge URL and secret belong
  to the Python runner's environment, not this process. If `PG_WA_SEND_PORT` is
  enabled for outbound beta replies, the sidecar may read
  `PG_WA_OUTBOUND_SECRET` / `PG_WA_OUTBOUND_GATEWAY_TOKEN` from the environment
  to validate localhost send requests; values are never logged or committed.

## Modes

Nothing here links a device until you explicitly run `--qr` on the dedicated
handset. The default invocation prints usage and exits — it never opens a socket.

### Mock (offline, tested seam)

```bash
# Replays recorded envelopes to NDJSON on stdout. No socket, no QR, no Baileys.
node index.js --mock --fixtures ../../backend/tests/fixtures/whatsapp_linked_device_events.json
```

Cross-check the exact frames the Python runner will accept:

```bash
node index.js --mock --fixtures ../../backend/tests/fixtures/whatsapp_linked_device_events.json \
  | (cd ../../backend && venv/bin/python3 whatsapp_connector_runner.py)
```

### Live (QR / link)

Deferred manual step — see the rollout plan. Do **not** run this until the
readiness guard returns `launch-ready`.

```bash
npm install            # installs Baileys + QR rendering dependencies (isolated to this dir)
node index.js --qr     # emits QR to stderr; streams inbound NDJSON to stdout
PG_WA_QR_DIR=/tmp/pg-wa-qr node index.js --qr  # also writes latest.png
```

Pipe live events into the repo-owned Python relay:

```bash
node index.js --qr | (cd ../../backend && venv/bin/python3 whatsapp_connector_runner.py --relay)
```

Live beta with the sidecar owning outbound sends on localhost:

```bash
PG_WA_SEND_PORT=18795 node index.js --qr \
  | (cd ../../backend && \
      PORTFOLIO_INBOUND_URL=http://127.0.0.1:8101/api/portfolio/inbound \
      PORTFOLIO_INBOUND_SECRET=<shared-gateway-secret> \
      venv/bin/python3 whatsapp_connector_runner.py --relay)
```

## Live diagnostics (reading a watch)

stdout stays the clean NDJSON data channel; all diagnostics go to **stderr**, so
a live watch can now tell the states of the inbound path apart instead of going
blind between "session open" and process exit. Every line is redacted — scope,
booleans, counts and a one-way JID fingerprint only, never a number, JID in the
clear, message text, or caption.

- `session open ... (platform=<p>; self=<scope>/<fp>)` — **which account** is
  linked. Confirm `platform` and the `self` fingerprint match the dedicated
  Portfolio Guru account before trusting any inbound result.
- `messages.upsert type=<notify|append> total=N emitted=M dropped=K [reason=n]`
  — a message was **observed** by the sidecar. `type=notify` is a live message,
  `type=append` is one delivered on reconnect. `dropped` reasons are `fromMe`
  (our own echo), `no-remoteJid` (a protocol frame) and `no-body` (a receipt or
  unsupported body). **`total=0` across the whole window means WhatsApp never
  delivered the message to this companion** — the problem is upstream of this
  transport, not a filter here.
- `messaging-history.set messages=N ...` — offline/history batch size. Messages
  the phone already showed but that predate this companion coming online surface
  here; they are logged for visibility but **not** relayed as new inbound.
- `relay: turn N disposition=<...> forwarded=<bool>` (Python runner) — the
  neutral routing verdict per turn.
- `relay: bridge POST ok` / `bridge POST failed (<ErrorType>)` — the forward to
  `POST /api/portfolio/inbound` was attempted and its outcome.
- `outbound: sent reply to <scope>/<fp>` — a Portfolio Guru reply went back out
  through the linked session.

A conclusive watch reads top to bottom: open (right account) → upsert observed →
turn forwarded → bridge POST ok → outbound sent. The first missing line is the
failing hop.

## Configuration (env var names only — never commit values)

Read by this sidecar:

- `PG_WA_AUTH_DIR` — directory the sidecar owns for the linked-device auth
  session (Baileys multi-file auth state). Created at link time, git-ignored,
  never committed. Defaults to `.wa-auth/` inside this package.
- `PG_WA_FIXTURES` — path to a JSON/NDJSON fixture, used only in `--mock` mode.
- `PG_WA_QR_DIR` — optional directory for `latest.png` / `latest.txt` QR handoff
  files. Prefer this for headless Mac Mini workflows so the QR can be sent as a
  scannable Telegram image instead of relying on terminal output.
- `PG_WA_SEND_PORT` — optional localhost port exposing
  `POST /api/channels/whatsapp/:accountId/send` for Portfolio Guru replies.
- `PG_WA_OUTBOUND_SECRET` — optional secret checked against
  `X-Portfolio-Secret` on the localhost send endpoint.
- `PG_WA_OUTBOUND_GATEWAY_TOKEN` — optional bearer token checked on the
  localhost send endpoint.

Read by the **Python runner** (passed to its environment, not this sidecar):

- `PORTFOLIO_INBOUND_URL` — full URL of the `POST /api/portfolio/inbound` bridge.
- `PORTFOLIO_INBOUND_SECRET` — shared gateway secret sent as `X-Gateway-Secret`.

## Stop conditions / rollback

- Link **only** on the dedicated Portfolio Guru handset — never the EMGurus
  handset, and never attach the linked device to the EMGurus account.
- After a first successful QR scan, WhatsApp may close once with
  `restartRequired`; the sidecar reconnects automatically with the saved auth
  state.
- The dedicated account fingerprint must stay **distinct** from the EMGurus
  fingerprint (the readiness guard's `distinct-whatsapp-account` gate).
- Stop if the readiness guard returns `blocked`, or if linking would require
  reading secrets, editing `~/.hermes`, or restarting a shared service.
- **Rollback:** on the dedicated account open WhatsApp → Settings → Linked
  Devices → remove this linked device, then stop the sidecar process. The
  git-ignored auth dir may be deleted to drop the local session. Never fall back
  to routing testers through the EMGurus account.

## Tests

```bash
npm test    # node --test — pure unit + mock-mode tests, no socket, no WhatsApp
```
