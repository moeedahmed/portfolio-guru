'use strict';

// Unit tests for the live handshake configuration. These construct the socket
// config and exercise the disconnect diagnostics WITHOUT opening a socket,
// emitting a QR, or hitting the network — they only call the pure helpers.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const {
  buildLiveSocketConfig,
  createSilentLogger,
  describeDisconnect,
  shouldReconnectAfterClose,
  BROWSER_NAME,
} = require('../lib/live');
const { writeQrArtifacts } = require('../index');

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
  assert.equal(config.logger.level, 'silent');
  assert.equal(config.logger.child(), config.logger);
  assert.doesNotThrow(() => config.logger.info({ event: 'protocol-log' }));
});

test('live socket config omits version when the fetch failed', () => {
  const config = buildLiveSocketConfig({ state: {}, version: undefined, Browsers });
  assert.ok(!('version' in config), 'undefined version must not be forwarded to Baileys');
  assert.deepEqual(config.browser, ['Mac OS', BROWSER_NAME, '14.4.1']);
  assert.equal(config.logger.level, 'silent');
});

test('silent logger has the methods Baileys calls without writing to stdout', () => {
  const logger = createSilentLogger();

  for (const method of ['trace', 'debug', 'info', 'warn', 'error', 'fatal']) {
    assert.equal(typeof logger[method], 'function');
    assert.doesNotThrow(() => logger[method]({ hello: 'world' }, 'ignored'));
  }
  assert.equal(logger.child({ class: 'baileys' }), logger);
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

test('shouldReconnectAfterClose handles the first-pair restart requirement only', () => {
  const DisconnectReason = { loggedOut: 401, restartRequired: 515 };
  assert.equal(shouldReconnectAfterClose(515, DisconnectReason), true);
  assert.equal(shouldReconnectAfterClose(401, DisconnectReason), false);
  assert.equal(shouldReconnectAfterClose(408, DisconnectReason), false);
  assert.equal(shouldReconnectAfterClose(515, null), false);
});

test('writeQrArtifacts creates scannable image handoff files', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pg-wa-qr-'));
  const artifact = await writeQrArtifacts('portfolio-guru-test-qr-payload', dir);

  assert.equal(artifact.txtPath, path.join(dir, 'latest.txt'));
  assert.equal(artifact.pngPath, path.join(dir, 'latest.png'));
  assert.equal(fs.readFileSync(artifact.txtPath, 'utf8'), 'portfolio-guru-test-qr-payload');

  const png = fs.readFileSync(artifact.pngPath);
  assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
});
