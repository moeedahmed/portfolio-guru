'use strict';

// Pure construction + diagnostics helpers for the live linked-device socket.
// Nothing here opens a socket, emits a QR, reads secrets, or touches the
// network, so the live handshake configuration stays unit-testable without
// linking a device. index.js does the actual (untested) socket work.

const BROWSER_NAME = 'Portfolio Guru';

function noop() {}

// Baileys' default logger can emit JSON diagnostics to stdout. stdout is this
// sidecar's data channel into the Python relay, so protocol diagnostics must be
// silenced at the socket boundary rather than filtered downstream.
function createSilentLogger() {
  return {
    level: 'silent',
    child() {
      return this;
    },
    trace: noop,
    debug: noop,
    info: noop,
    warn: noop,
    error: noop,
    fatal: noop,
  };
}

// Build the Baileys socket config for a live linked-device session. Takes the
// resolved multi-file auth state, the negotiated WhatsApp Web `version` tuple
// (from fetchLatestBaileysVersion; may be undefined to fall back to the Baileys
// default), and Baileys' `Browsers` map. Returns a plain config object.
//
// The explicit `version` + `browser` identity are the fix for the observed
// "connection closed (status 405)" before any QR: WhatsApp rejects the web
// handshake when the client advertises a stale/absent WA Web build, so pinning
// the latest version and presenting a real browser identity is what lets the
// server hand back a QR instead of a 405.
function buildLiveSocketConfig({ state, version, Browsers }) {
  const config = {
    auth: state,
    browser: Browsers.macOS(BROWSER_NAME),
    logger: createSilentLogger(),
    printQRInTerminal: false,
  };
  if (version) {
    config.version = version;
  }
  return config;
}

// Short, non-secret human label for a connection-close status code. Uses the
// named Baileys DisconnectReason where one exists, plus a couple of raw HTTP-ish
// codes WhatsApp returns on the web handshake that Baileys does not name (405
// being the outdated-client rejection we hit before the QR). Returns a plain
// string; never includes auth, QR, or key material.
function describeDisconnect(statusCode, DisconnectReason) {
  if (statusCode == null) {
    return 'unknown (no status code)';
  }
  const extras = {
    428: 'connection closed — WhatsApp closed the web socket; reconnect the saved session',
    405: 'connection failure — WhatsApp rejected the web handshake, usually a stale/absent WA Web version',
    409: 'conflict — another session may already hold this linked device',
  };
  const named = DisconnectReason
    ? Object.keys(DisconnectReason).find(
        (key) => Number.isNaN(Number(key)) && DisconnectReason[key] === statusCode
      )
    : null;
  if (named) {
    return named;
  }
  if (extras[statusCode]) {
    return extras[statusCode];
  }
  return 'unrecognised status';
}

function shouldReconnectAfterClose(statusCode, DisconnectReason) {
  return Boolean(
    statusCode === 428 ||
    DisconnectReason
      && statusCode === DisconnectReason.restartRequired
  );
}

module.exports = {
  buildLiveSocketConfig,
  createSilentLogger,
  describeDisconnect,
  shouldReconnectAfterClose,
  BROWSER_NAME,
};
