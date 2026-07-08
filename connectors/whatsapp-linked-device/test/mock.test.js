'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('child_process');
const path = require('path');
const { parseArgs, qrForbidden } = require('../index');

const INDEX = path.join(__dirname, '..', 'index.js');
const PY_FIXTURE = path.join(
  __dirname,
  '..',
  '..',
  '..',
  'backend',
  'tests',
  'fixtures',
  'whatsapp_linked_device_events.json'
);
const SECRET_FIXTURE = path.join(__dirname, 'fixtures', 'raw_baileys_upsert.json');

function runMock(fixture, extraEnv) {
  return spawnSync(process.execPath, [INDEX, '--mock', '--fixtures', fixture], {
    encoding: 'utf8',
    // A clean env with no Baileys module path — proves mock never needs it.
    env: Object.assign({}, process.env, extraEnv || {}),
  });
}

test('mock mode streams one NDJSON envelope per recorded event on stdout', () => {
  const result = runMock(PY_FIXTURE);
  assert.equal(result.status, 0, result.stderr);

  const lines = result.stdout.trim().split('\n').filter(Boolean);
  assert.equal(lines.length, 4);

  const envelopes = lines.map((line) => JSON.parse(line));
  assert.deepEqual(
    envelopes.map((e) => e.key.remoteJid),
    [
      '447700900000@s.whatsapp.net',
      '447700900000@s.whatsapp.net',
      '120363000000000000@g.us',
      '447700900000@s.whatsapp.net',
    ]
  );
  // The last (empty) turn carries a key but no body — the runner refuses it.
  assert.ok(!('message' in envelopes[3]));
});

test('mock mode never opens a socket or emits a QR', () => {
  const result = runMock(PY_FIXTURE);
  assert.equal(result.status, 0, result.stderr);
  const combined = result.stdout + result.stderr;
  assert.ok(!/QR/i.test(combined), 'mock mode must not emit a QR');
  assert.ok(!/socket/i.test(result.stdout), 'mock stdout must be pure NDJSON');
});

test('mock mode strips media keys even from a raw Baileys-shaped fixture', () => {
  // The raw upsert fixture is a { messages, type } object; mock treats it as a
  // single record and sanitises it, so no planted secret survives to stdout.
  const result = runMock(SECRET_FIXTURE);
  assert.equal(result.status, 0, result.stderr);
  for (const secret of [
    'SECRET-ENCRYPTED-URL',
    'SECRET-MEDIA-KEY-BASE64',
    'SECRET-THUMBNAIL-BYTES',
    'SECRET-DIRECT-PATH',
  ]) {
    assert.ok(!result.stdout.includes(secret), `leaked secret to stdout: ${secret}`);
  }
});

test('mock mode requires a fixtures path', () => {
  const result = spawnSync(process.execPath, [INDEX, '--mock'], {
    encoding: 'utf8',
    env: Object.assign({}, process.env, { PG_WA_FIXTURES: '' }),
  });
  assert.equal(result.status, 2);
});

test('no arguments prints usage and exits non-zero (no accidental live socket)', () => {
  const result = spawnSync(process.execPath, [INDEX], { encoding: 'utf8' });
  assert.equal(result.status, 2);
  assert.ok(/Usage:/.test(result.stderr));
  // Default invocation must not enter live mode.
  assert.ok(!/linked-device socket/.test(result.stderr));
});

test('saved-session live mode can forbid QR emission', () => {
  const args = parseArgs(['--qr', '--forbid-qr']);

  assert.equal(args.qr, true);
  assert.equal(args.forbidQr, true);
  assert.equal(qrForbidden(args, {}), true);
  assert.equal(qrForbidden({ forbidQr: false }, { PG_WA_FORBID_QR: '1' }), true);
  assert.equal(qrForbidden({ forbidQr: false }, {}), false);
});

test('usage documents the no-QR saved-session mode', () => {
  const result = spawnSync(process.execPath, [INDEX, '--help'], { encoding: 'utf8' });

  assert.equal(result.status, 0);
  assert.ok(/--forbid-qr/.test(result.stderr));
  assert.ok(/saved-session/i.test(result.stderr));
});
