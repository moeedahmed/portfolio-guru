'use strict';

// Unit tests for the live handshake configuration. These construct the socket
// config and exercise the disconnect diagnostics WITHOUT opening a socket,
// emitting a QR, or hitting the network — they only call the pure helpers.

const test = require('node:test');
const assert = require('node:assert/strict');

const { buildLiveSocketConfig, describeDisconnect, BROWSER_NAME } = require('../lib/live');

// A tiny stand-in for Baileys' Browsers map so the test never needs the real
// package (or a network fetch) to validate config shape.
const Browsers = {
  macOS: (name) => ['Mac OS', name, '14.4.1'],
};

test('live socket config pins the fetched WA Web version and a browser identity', () => {
  const state = { creds: {}, keys: {} };
  const version = [2, 3000, 1234567];
  const config = buildLiveSocketConfig({ state, version, Browsers });

  assert.equal(config.auth, state);
  assert.deepEqual(config.version, version);
  assert.deepEqual(config.browser, ['Mac OS', BROWSER_NAME, '14.4.1']);
  // QR is rendered by index.js to stderr, never by Baileys to stdout.
  assert.equal(config.printQRInTerminal, false);
});

test('live socket config omits version when the fetch failed', () => {
  const config = buildLiveSocketConfig({ state: {}, version: undefined, Browsers });
  assert.ok(!('version' in config), 'undefined version must not be forwarded to Baileys');
  assert.deepEqual(config.browser, ['Mac OS', BROWSER_NAME, '14.4.1']);
});

test('describeDisconnect names the observed 405 handshake rejection', () => {
  const label = describeDisconnect(405, {});
  assert.match(label, /handshake/i);
  assert.match(label, /WA Web version/i);
});

test('describeDisconnect prefers the named Baileys DisconnectReason', () => {
  const DisconnectReason = { loggedOut: 401, restartRequired: 515 };
  assert.equal(describeDisconnect(401, DisconnectReason), 'loggedOut');
  assert.equal(describeDisconnect(515, DisconnectReason), 'restartRequired');
});

test('describeDisconnect handles a missing status code', () => {
  assert.match(describeDisconnect(undefined, {}), /no status code/i);
  assert.match(describeDisconnect(null, {}), /no status code/i);
});
