'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const {
  classifyMessage,
  describeSelfIdentity,
  fingerprint,
  formatUpsertSummary,
  jidScope,
  redactJid,
  summarizeHistorySync,
  summarizeUpsert,
} = require('../lib/diagnostics');
const { extractInbound } = require('../lib/sanitize');

const RAW_UPSERT = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'raw_baileys_upsert.json'), 'utf8')
);

const DIRECT_JID = '447700900000@s.whatsapp.net';
const GROUP_JID = '120363000000000000@g.us';

test('redactJid never leaks the number and carries scope + fingerprint', () => {
  const redacted = redactJid(DIRECT_JID);
  assert.equal(redacted.scope, 'direct');
  assert.equal(redacted.fingerprint, fingerprint(DIRECT_JID));
  // The phone number and raw JID must never survive redaction.
  const serialized = JSON.stringify(redacted);
  assert.ok(!serialized.includes('447700900000'));
  assert.ok(!serialized.includes('@s.whatsapp.net'));
});

test('jidScope classifies direct, group and empty JIDs by suffix only', () => {
  assert.equal(jidScope(DIRECT_JID), 'direct');
  assert.equal(jidScope(GROUP_JID), 'group');
  assert.equal(jidScope('status@broadcast'), 'group');
  assert.equal(jidScope(''), 'none');
});

test('describeSelfIdentity reports platform + redacted self, never the number', () => {
  const identity = describeSelfIdentity({
    platform: 'smba',
    me: { id: DIRECT_JID },
  });
  assert.equal(identity.platform, 'smba');
  assert.equal(identity.self.scope, 'direct');
  assert.ok(!JSON.stringify(identity).includes('447700900000'));
});

test('summarizeUpsert counts emitted/dropped with reasons and no content', () => {
  const summary = summarizeUpsert(RAW_UPSERT);
  // The shared fixture holds two real inbound turns, one fromMe outbound, and
  // one bodiless receipt. "emitted" must mean "actually written to stdout",
  // not merely "routable", so the bodiless receipt is logged as a drop reason.
  assert.equal(summary.total, RAW_UPSERT.messages.length);
  assert.equal(summary.dropReasons.fromMe, 1);
  assert.equal(summary.dropReasons['no-body'], 1);
  // Never leaks content or captions.
  const serialized = JSON.stringify(summary);
  assert.ok(!serialized.includes('synthetic'));
});

test('summarizeUpsert agrees with extractInbound on which frames are emitted', () => {
  // Drift guard: the diagnostic classifier and the real inbound filter must
  // stay in lock-step, so the stderr count can be trusted against what actually
  // reaches stdout.
  const emittedByFilter = extractInbound(RAW_UPSERT).length;
  const summary = summarizeUpsert(RAW_UPSERT);
  assert.equal(summary.emitted, emittedByFilter);
});

test('classifyMessage names the drop reason for each non-routable frame', () => {
  assert.deepEqual(classifyMessage({ key: { fromMe: true, remoteJid: DIRECT_JID } }), {
    action: 'drop',
    reason: 'fromMe',
  });
  assert.deepEqual(classifyMessage({ key: { id: 'PROTO-1' } }), {
    action: 'drop',
    reason: 'no-remoteJid',
  });
  assert.deepEqual(
    classifyMessage({ key: { remoteJid: DIRECT_JID, fromMe: false } }, false),
    { action: 'drop', reason: 'no-body' }
  );
  assert.deepEqual(classifyMessage('not-an-object'), {
    action: 'drop',
    reason: 'not-an-object',
  });
  assert.deepEqual(
    classifyMessage({ key: { remoteJid: GROUP_JID, fromMe: false } }),
    { action: 'emit', reason: null, scope: 'group' }
  );
});

test('formatUpsertSummary renders a compact, reason-tagged line', () => {
  const line = formatUpsertSummary({
    type: 'notify',
    total: 2,
    emitted: 1,
    dropped: 1,
    dropReasons: { fromMe: 1 },
  });
  assert.equal(line, 'messages.upsert type=notify total=2 emitted=1 dropped=1 [fromMe=1]');
});

test('summarizeHistorySync reports batch size for offline delivery visibility', () => {
  const sync = summarizeHistorySync({
    messages: [{}, {}, {}],
    chats: [{}],
    isLatest: true,
  });
  assert.deepEqual(sync, { messages: 3, chats: 1, isLatest: true });
  // Tolerates a partial/empty payload without throwing.
  assert.deepEqual(summarizeHistorySync(undefined), {
    messages: 0,
    chats: 0,
    isLatest: false,
  });
});
