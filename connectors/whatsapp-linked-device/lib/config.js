'use strict';

// Configuration for the linked-device sidecar, resolved from environment
// variables *by name only*. No value is ever hardcoded and no secret is ever
// read from disk here. In live beta mode the sidecar can also expose a
// localhost-only outbound endpoint so Portfolio Guru replies travel back through
// the same linked-device session that received the inbound message.

// Env var *names* the sidecar reads (documented in the connector README):
//   PG_WA_AUTH_DIR  — directory the sidecar owns for the linked-device session
//                     (Baileys multi-file auth state). Created/owned at link
//                     time; never committed, never read here in mock mode.
//   PG_WA_FIXTURES  — path to a JSON fixture file used only in --mock mode.
//   PG_WA_QR_DIR    — optional directory for short-lived QR handoff artefacts
//                     (latest.png/latest.txt). Created only in live QR mode.
//   PG_WA_SEND_PORT — optional localhost port for the outbound send endpoint.
const AUTH_DIR_ENV = 'PG_WA_AUTH_DIR';
const FIXTURES_ENV = 'PG_WA_FIXTURES';
const QR_DIR_ENV = 'PG_WA_QR_DIR';
const SEND_PORT_ENV = 'PG_WA_SEND_PORT';

const DEFAULT_AUTH_DIR = '.wa-auth';

function resolveAuthDir(env) {
  const value = (env[AUTH_DIR_ENV] || '').trim();
  return value || DEFAULT_AUTH_DIR;
}

function resolveFixturesPath(env, cliValue) {
  if (cliValue) {
    return cliValue;
  }
  const value = (env[FIXTURES_ENV] || '').trim();
  return value || null;
}

function resolveQrDir(env) {
  const value = (env[QR_DIR_ENV] || '').trim();
  return value || null;
}

function resolveSendPort(env) {
  const value = (env[SEND_PORT_ENV] || '').trim();
  if (!value) {
    return null;
  }
  const port = Number(value);
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    throw new Error(`${SEND_PORT_ENV} must be a valid TCP port`);
  }
  return port;
}

module.exports = {
  AUTH_DIR_ENV,
  FIXTURES_ENV,
  QR_DIR_ENV,
  SEND_PORT_ENV,
  DEFAULT_AUTH_DIR,
  resolveAuthDir,
  resolveFixturesPath,
  resolveQrDir,
  resolveSendPort,
};
