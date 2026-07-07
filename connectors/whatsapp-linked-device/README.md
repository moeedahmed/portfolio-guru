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
- The sidecar reads **no repo secret**. The inbound bridge URL and secret belong
  to the Python runner's environment, not this process.

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
npm install            # installs Baileys + qrcode-terminal (isolated to this dir)
node index.js --qr     # emits QR to stderr; streams inbound NDJSON to stdout
```

Pipe live events into the repo-owned Python relay:

```bash
node index.js --qr | (cd ../../backend && venv/bin/python3 whatsapp_connector_runner.py --relay)
```

## Configuration (env var names only — never commit values)

Read by this sidecar:

- `PG_WA_AUTH_DIR` — directory the sidecar owns for the linked-device auth
  session (Baileys multi-file auth state). Created at link time, git-ignored,
  never committed. Defaults to `.wa-auth/` inside this package.
- `PG_WA_FIXTURES` — path to a JSON/NDJSON fixture, used only in `--mock` mode.

Read by the **Python runner** (passed to its environment, not this sidecar):

- `PORTFOLIO_INBOUND_URL` — full URL of the `POST /api/portfolio/inbound` bridge.
- `PORTFOLIO_INBOUND_SECRET` — shared gateway secret sent as `X-Gateway-Secret`.

## Stop conditions / rollback

- Link **only** on the dedicated Portfolio Guru handset — never the EMGurus
  handset, and never attach the linked device to the EMGurus account.
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
