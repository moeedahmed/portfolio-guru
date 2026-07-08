'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const {
  sanitizeEnvelope,
  sanitizeMessageBody,
  extractInbound,
} = require('../lib/sanitize');

const RAW_UPSERT = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'raw_baileys_upsert.json'), 'utf8')
);

// The repo-owned Python normaliser fixture — the sidecar output must be shaped
// so this same set of envelopes flows through backend/whatsapp_linked_device.py.
const PY_FIXTURE = JSON.parse(
  fs.readFileSync(
    path.join(
      __dirname,
      '..',
      '..',
      '..',
      'backend',
      'tests',
      'fixtures',
      'whatsapp_linked_device_events.json'
    ),
    'utf8'
  )
);

// Every secret string planted in the raw image message. None may survive
// sanitisation — a WhatsApp media key or encrypted URL must never reach stdout.
const SECRET_STRINGS = [
  'SECRET-ENCRYPTED-URL',
  'SECRET-DIRECT-PATH',
  'SECRET-MEDIA-KEY-BASE64',
  'SECRET-ENC-SHA',
  'SECRET-SHA',
  'SECRET-THUMBNAIL-BYTES',
];

test('sanitizeEnvelope strips every media key and encrypted URL', () => {
  const imageMsg = RAW_UPSERT.messages.find((m) => m.key.id === 'IN-IMAGE-1');
  const envelope = sanitizeEnvelope(imageMsg);
  const serialized = JSON.stringify(envelope);

  for (const secret of SECRET_STRINGS) {
    assert.ok(!serialized.includes(secret), `leaked secret: ${secret}`);
  }
  // Only the fields the Python normaliser reads survive.
  assert.deepEqual(envelope.message.imageMessage, {
    mimetype: 'image/jpeg',
    caption: 'synthetic caption',
  });
});

test('sanitizeEnvelope keeps text bodies but drops non-whitelisted fields', () => {
  const textMsg = RAW_UPSERT.messages.find((m) => m.key.id === 'IN-TEXT-1');
  const envelope = sanitizeEnvelope(textMsg);

  assert.deepEqual(envelope.message, {
    extendedTextMessage: { text: 'synthetic inbound direct text' },
  });
  // contextInfo/stanzaId and pushName are not part of the neutral contract.
  assert.ok(!JSON.stringify(envelope).includes('stanzaId'));
  assert.ok(!('pushName' in envelope));
});

test('sanitizeEnvelope keeps only routing ids on the key', () => {
  const textMsg = RAW_UPSERT.messages.find((m) => m.key.id === 'IN-TEXT-1');
  const envelope = sanitizeEnvelope(textMsg);
  assert.deepEqual(envelope.key, {
    remoteJid: '447700900000@s.whatsapp.net',
    id: 'IN-TEXT-1',
    fromMe: false,
  });
});

test('sanitizeEnvelope preserves Baileys phone and LID routing ids', () => {
  const envelope = sanitizeEnvelope({
    key: {
      remoteJid: '84125843243120@lid',
      senderPn: '447700900000@s.whatsapp.net',
      senderLid: '84125843243120@lid',
      id: 'IN-LID-1',
      fromMe: false,
    },
    message: { conversation: 'hello' },
    pushName: 'Dr Smith',
  });

  assert.deepEqual(envelope.key, {
    remoteJid: '84125843243120@lid',
    senderPn: '447700900000@s.whatsapp.net',
    senderLid: '84125843243120@lid',
    id: 'IN-LID-1',
    fromMe: false,
  });
});

test('sanitizeEnvelope emits key-only when there is no recognised body', () => {
  const receipt = RAW_UPSERT.messages.find((m) => m.key.id === 'IN-RECEIPT-1');
  const envelope = sanitizeEnvelope(receipt);
  assert.deepEqual(envelope, {
    key: {
      remoteJid: '447700900000@s.whatsapp.net',
      id: 'IN-RECEIPT-1',
      fromMe: false,
    },
  });
  assert.ok(!('message' in envelope));
});

test('sanitizeMessageBody preserves the audio ptt flag for voice-note detection', () => {
  const body = sanitizeMessageBody({
    audioMessage: { mimetype: 'audio/ogg', ptt: true, mediaKey: 'SECRET' },
  });
  assert.deepEqual(body, { audioMessage: { mimetype: 'audio/ogg', ptt: true } });
});

test('extractInbound drops our own outbound and bodiless frames', () => {
  const envelopes = extractInbound(RAW_UPSERT);
  const ids = envelopes.map((e) => e.key.id);
  // OUT-1 (fromMe) and IN-RECEIPT-1 (no body) are dropped; the two real
  // inbound turns survive.
  assert.deepEqual(ids, ['IN-TEXT-1', 'IN-IMAGE-1']);
});

test('extractInbound drops internal frames with no routable remoteJid', () => {
  // A live Baileys session streams protocol/internal frames that carry a body
  // but no key.remoteJid. Emitting one crashed the Python relay before any
  // account link, so the sidecar must not stream it inbound.
  const upsert = {
    type: 'notify',
    messages: [
      { key: { id: 'PROTO-1' }, message: { conversation: 'internal frame' } },
      { message: { protocolMessage: { type: 3 } } },
      {
        key: { remoteJid: '447700900000@s.whatsapp.net', id: 'IN-OK-1', fromMe: false },
        message: { conversation: 'real inbound turn' },
      },
    ],
  };
  const envelopes = extractInbound(upsert);
  assert.deepEqual(envelopes.map((e) => e.key.id), ['IN-OK-1']);
});

test('sidecar output for the Python fixture is stable and content-shaped', () => {
  // Sanitising each recorded envelope reproduces the exact neutral shape the
  // Python normaliser consumes (key.remoteJid + whitelisted body).
  const shaped = PY_FIXTURE.map(sanitizeEnvelope);

  assert.equal(shaped.length, 4);
  assert.equal(shaped[0].message.conversation, 'synthetic direct case text for a dry run');
  assert.deepEqual(shaped[1].message.imageMessage, {
    mimetype: 'image/jpeg',
    caption: 'synthetic caption',
  });
  assert.equal(shaped[2].key.remoteJid, '120363000000000000@g.us');
  assert.equal(shaped[2].key.participant, '447700900001@s.whatsapp.net');
  // The empty turn carries a key but no message body.
  assert.ok(!('message' in shaped[3]));
});
