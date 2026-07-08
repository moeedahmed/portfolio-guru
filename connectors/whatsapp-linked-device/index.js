#!/usr/bin/env node
'use strict';

// Portfolio Guru WhatsApp linked-device sidecar (Baileys / WhatsApp-Web
// multi-device transport).
//
// This is the deferred live transport half of the direct linked-device path. Its
// ONLY job is transport: in live mode it authenticates a WhatsApp linked-device
// session (owned by a persistent auth dir it controls), emits the QR for a human
// to scan, and streams each incoming message envelope as one NDJSON line on
// stdout — the exact frame `backend/whatsapp_connector_runner.py --relay`
// consumes. It carries NO Portfolio Guru product logic and never places a
// WhatsApp media key or auth token on stdout (see lib/sanitize.js).
//
// Modes:
//   --mock [--fixtures <path>]  Offline replay. Reads recorded envelopes and
//                               streams their sanitised NDJSON to stdout. Never
//                               requires Baileys, never opens a socket, never
//                               emits a QR. This is the tested seam.
//   --qr                        Live mode. Lazily loads Baileys, opens the
//                               linked-device socket, prints the QR to stderr for
//                               a human to scan, and streams inbound events as
//                               NDJSON to stdout. Not exercised by tests.
//   --forbid-qr                 Saved-session mode. If WhatsApp asks for a QR,
//                               exit loudly instead of emitting one. Used by the
//                               supervised beta runner after the device is linked.
//
// stdout is the data channel (NDJSON events). The QR and all human-facing logs go
// to stderr so they never corrupt the event stream piped into the Python runner.

const fs = require('fs');
const path = require('path');

const { sanitizeEnvelope, extractInbound, serializeEnvelope } = require('./lib/sanitize');
const {
  resolveAuthDir,
  resolveFixturesPath,
  resolveQrDir,
  resolveSendPort,
  AUTH_DIR_ENV,
  FIXTURES_ENV,
  QR_DIR_ENV,
  SEND_PORT_ENV,
} = require('./lib/config');
const {
  buildLiveSocketConfig,
  describeDisconnect,
  shouldReconnectAfterClose,
} = require('./lib/live');
const { startOutboundServer } = require('./lib/outbound');
const {
  describeSelfIdentity,
  formatUpsertSummary,
  summarizeHistorySync,
  summarizeUpsert,
} = require('./lib/diagnostics');

function log(msg) {
  process.stderr.write(`${msg}\n`);
}

function emit(envelope) {
  process.stdout.write(`${serializeEnvelope(envelope)}\n`);
}

function usage() {
  log(
    [
      'Portfolio Guru WhatsApp linked-device sidecar',
      '',
      'Usage:',
      '  node index.js --mock [--fixtures <path>]   Offline replay to NDJSON (no socket, no QR)',
      '  node index.js --qr                         Live: emit QR, stream inbound NDJSON to stdout',
      '  node index.js --qr --forbid-qr             Live saved-session only; exit if QR is needed',
      '',
      'Environment (names only; never commit values):',
      `  ${AUTH_DIR_ENV}   directory the sidecar owns for the linked-device auth session`,
      `  ${FIXTURES_ENV}   path to a JSON fixture used only in --mock mode`,
      `  ${QR_DIR_ENV}     optional directory for latest.png/latest.txt QR handoff artefacts`,
      `  ${SEND_PORT_ENV}  optional localhost port for the outbound send endpoint`,
      '',
      'Pipe the NDJSON stream into the repo-owned Python relay:',
      '  node index.js --qr | (cd ../../backend && venv/bin/python3 whatsapp_connector_runner.py --relay)',
    ].join('\n')
  );
}

function parseArgs(argv) {
  const args = { mock: false, qr: false, forbidQr: false, fixtures: null };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--mock') {
      args.mock = true;
    } else if (arg === '--qr' || arg === '--live') {
      args.qr = true;
    } else if (arg === '--forbid-qr' || arg === '--no-qr') {
      args.forbidQr = true;
    } else if (arg === '--fixtures') {
      i += 1;
      args.fixtures = argv[i];
    } else if (arg === '-h' || arg === '--help') {
      args.help = true;
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return args;
}

function qrForbidden(args, env) {
  return !!(args && args.forbidQr) || env.PG_WA_FORBID_QR === '1';
}

// Parse a fixtures file recorded as a JSON array, a single JSON object, or
// NDJSON (one envelope per line) — the same shapes the Python runner accepts.
function readFixtures(fixturesPath) {
  const text = fs.readFileSync(fixturesPath, 'utf8').trim();
  if (!text) {
    return [];
  }
  try {
    const decoded = JSON.parse(text);
    return Array.isArray(decoded) ? decoded : [decoded];
  } catch (err) {
    return text
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0)
      .map((line) => JSON.parse(line));
  }
}

// Offline replay: sanitise each recorded envelope and stream it as NDJSON.
// Recorded fixtures are already inbound turns, so we emit each one (including a
// contentless turn, so the runner can exercise its refuse-empty path) rather
// than applying the live fromMe/body filtering in extractInbound.
function runMock(fixturesPath) {
  if (!fixturesPath) {
    log(
      `--mock requires a fixtures path via --fixtures <path> or ${FIXTURES_ENV}`
    );
    return 2;
  }
  const raw = readFixtures(fixturesPath);
  let count = 0;
  for (const rawMsg of raw) {
    emit(sanitizeEnvelope(rawMsg));
    count += 1;
  }
  log(`mock: streamed ${count} sanitised envelope(s) from ${fixturesPath}`);
  return 0;
}

async function writeQrArtifacts(qr, qrDir) {
  if (!qrDir) {
    return null;
  }

  fs.mkdirSync(qrDir, { recursive: true });
  const txtPath = path.join(qrDir, 'latest.txt');
  const pngPath = path.join(qrDir, 'latest.png');

  fs.writeFileSync(txtPath, qr, { mode: 0o600 });

  // Lazily loaded so mock mode and pure tests never need QR rendering.
  // eslint-disable-next-line global-require
  const qrcode = require('qrcode');
  await qrcode.toFile(pngPath, qr, {
    errorCorrectionLevel: 'M',
    margin: 2,
    scale: 10,
    type: 'png',
  });
  fs.chmodSync(pngPath, 0o600);

  return { txtPath, pngPath };
}

// Live mode. Baileys is required lazily so mock mode and the unit tests never
// need the dependency installed and never risk opening a socket.
async function runLive(env, args = {}) {
  const authDir = resolveAuthDir(env);
  const qrDir = resolveQrDir(env);
  const sendPort = resolveSendPort(env);
  const forbidQr = qrForbidden(args, env);
  log(`live: using linked-device auth dir ${JSON.stringify(authDir)}`);
  log('live: this will open a WhatsApp linked-device socket and emit a QR.');
  log('live: scan the QR ONLY on the dedicated Portfolio Guru handset.');
  if (forbidQr) {
    log('live: QR emission is forbidden; saved linked-device auth must reopen without pairing.');
  }
  if (qrDir) {
    log(`live: QR image handoff enabled at ${JSON.stringify(qrDir)}.`);
  }
  if (sendPort) {
    log(`live: outbound replies will be accepted on localhost port ${sendPort}.`);
  }

  let baileys;
  try {
    // eslint-disable-next-line global-require
    baileys = require('@whiskeysockets/baileys');
  } catch (err) {
    log(
      'live: @whiskeysockets/baileys is not installed. Run `npm install` in ' +
        'connectors/whatsapp-linked-device before linking a device.'
    );
    return 4;
  }

  const makeWASocket = baileys.default || baileys.makeWASocket;
  const {
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion,
    Browsers,
  } = baileys;

  fs.mkdirSync(authDir, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  let activeSocket = null;
  let outboundServer = null;

  // Pin the current WhatsApp Web build. Connecting with a stale/absent version
  // is what makes WhatsApp close the handshake with a 405 before any QR is
  // offered; a best-effort fetch keeps us on the version the server accepts.
  let version;
  try {
    const fetched = await fetchLatestBaileysVersion();
    version = fetched.version;
    log(
      `live: negotiated WhatsApp Web version ${version.join('.')} (isLatest=${!!fetched.isLatest}).`
    );
  } catch (err) {
    log(
      `live: could not fetch latest WhatsApp Web version (${err && err.message}); ` +
        'falling back to the Baileys default.'
    );
  }

  function startSocket() {
    const socket = makeWASocket(buildLiveSocketConfig({ state, version, Browsers }));
    activeSocket = socket;
    let sawQr = false;

    if (sendPort && !outboundServer) {
      outboundServer = startOutboundServer({
        port: sendPort,
        getSocket: () => activeSocket,
        env,
        log,
      });
    }

    socket.ev.on('creds.update', saveCreds);

    socket.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect, qr } = update;
      if (qr) {
        if (forbidQr) {
          log('live: QR requested but forbidden; exiting instead of starting a new pairing flow.');
          if (outboundServer) {
            outboundServer.close();
          }
          if (activeSocket && typeof activeSocket.end === 'function') {
            activeSocket.end(new Error('qr-forbidden'));
          }
          process.exitCode = 6;
          setTimeout(() => process.exit(6), 50);
          return;
        }
        sawQr = true;
        writeQrArtifacts(qr, qrDir)
          .then((artifact) => {
            if (artifact) {
              log(`live: wrote QR image to ${artifact.pngPath}`);
            }
          })
          .catch((err) => {
            log(`live: failed to write QR image (${err && err.message ? err.message : err})`);
          });
        // QR to stderr — stdout is reserved for the NDJSON event stream.
        let rendered = false;
        try {
          // eslint-disable-next-line global-require
          require('qrcode-terminal').generate(qr, { small: true }, (art) => {
            log('\nScan this QR on the dedicated Portfolio Guru handset:');
            log(art);
            rendered = true;
          });
        } catch (err) {
          rendered = false;
        }
        if (!rendered) {
          log('\nScan this QR string on the dedicated Portfolio Guru handset:');
          log(qr);
        }
      }
      if (connection === 'open') {
        const identity = describeSelfIdentity(state && state.creds);
        log(
          `live: linked-device session open; streaming inbound events ` +
            `(platform=${identity.platform || 'unknown'}; ` +
            `self=${identity.self.scope}/${identity.self.fingerprint || 'none'}).`
        );
      } else if (connection === 'close') {
        const statusCode = lastDisconnect && lastDisconnect.error
          && lastDisconnect.error.output
          && lastDisconnect.error.output.statusCode;
        const loggedOut = DisconnectReason && statusCode === DisconnectReason.loggedOut;
        const reason = describeDisconnect(statusCode, DisconnectReason);
        const phase = sawQr ? 'after QR' : 'before QR';
        log(
          `live: connection closed (status ${statusCode}; reason=${reason}; ` +
            `${phase}; loggedOut=${!!loggedOut}).`
        );
        if (shouldReconnectAfterClose(statusCode, DisconnectReason)) {
          log('live: restart required after pairing; reconnecting with saved auth state.');
          setTimeout(startSocket, 500);
        } else if (loggedOut) {
          if (outboundServer) {
            outboundServer.close();
          }
          process.exitCode = 0;
        } else {
          log('live: non-recoverable close; exiting so supervisors do not treat the relay as healthy.');
          if (outboundServer) {
            outboundServer.close();
          }
          process.exitCode = 5;
          setTimeout(() => process.exit(5), 50);
        }
      }
    });

    socket.ev.on('messages.upsert', (upsert) => {
      // Redacted, content-free summary to stderr so a live watch can see that a
      // message was observed and why any were dropped — stdout stays the clean
      // NDJSON data channel.
      log(`live: ${formatUpsertSummary(summarizeUpsert(upsert))}`);
      for (const envelope of extractInbound(upsert)) {
        emit(envelope);
      }
    });

    // History sync is diagnostic-only: messages the phone already showed but
    // that were delivered before this companion came online surface here rather
    // than as a live upsert. We log the batch size (never content) so a
    // "delivered but not observed" report can be explained, but we do NOT route
    // history messages into the product path — replaying old turns as new
    // inbound would be unsafe.
    socket.ev.on('messaging-history.set', (historySet) => {
      const sync = summarizeHistorySync(historySet);
      log(
        `live: messaging-history.set messages=${sync.messages} ` +
          `chats=${sync.chats} isLatest=${sync.isLatest} (diagnostic only; not relayed).`
      );
    });
  }

  startSocket();

  // Keep the process alive; the socket owns the event loop until logout.
  return new Promise(() => {});
}

async function main(argv, env) {
  let args;
  try {
    args = parseArgs(argv);
  } catch (err) {
    log(err.message);
    usage();
    return 2;
  }

  if (args.help || (!args.mock && !args.qr)) {
    usage();
    return args.help ? 0 : 2;
  }

  if (args.mock) {
    return runMock(resolveFixturesPath(env, args.fixtures));
  }
  return runLive(env, args);
}

if (require.main === module) {
  main(process.argv.slice(2), process.env)
    .then((code) => {
      if (typeof code === 'number') {
        process.exitCode = code;
      }
    })
    .catch((err) => {
      log(`fatal: ${err && err.stack ? err.stack : err}`);
      process.exitCode = 1;
    });
}

module.exports = { main, parseArgs, qrForbidden, readFixtures, runMock, writeQrArtifacts };
