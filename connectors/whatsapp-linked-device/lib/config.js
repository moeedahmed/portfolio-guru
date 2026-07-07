'use strict';

// Configuration for the linked-device sidecar, resolved from environment
// variables *by name only*. No value is ever hardcoded and no secret is ever
// read from disk here. The two inbound-bridge values
// (PORTFOLIO_INBOUND_URL / PORTFOLIO_INBOUND_SECRET) are deliberately NOT read
// by this sidecar at all — they belong to the Python runner's environment. The
// sidecar is pure transport: it emits NDJSON on stdout and never talks to the
// bridge, so it never needs the bridge secret.

// Env var *names* the sidecar reads (documented in the connector README):
//   PG_WA_AUTH_DIR  — directory the sidecar owns for the linked-device session
//                     (Baileys multi-file auth state). Created/owned at link
//                     time; never committed, never read here in mock mode.
//   PG_WA_FIXTURES  — path to a JSON fixture file used only in --mock mode.
const AUTH_DIR_ENV = 'PG_WA_AUTH_DIR';
const FIXTURES_ENV = 'PG_WA_FIXTURES';

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

module.exports = {
  AUTH_DIR_ENV,
  FIXTURES_ENV,
  DEFAULT_AUTH_DIR,
  resolveAuthDir,
  resolveFixturesPath,
};
