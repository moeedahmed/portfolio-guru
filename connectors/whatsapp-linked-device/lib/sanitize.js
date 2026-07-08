'use strict';

// Pure transport sanitisation for the Portfolio Guru WhatsApp linked-device
// sidecar. This module owns WhatsApp plumbing only: it takes a raw Baileys
// message object and reduces it to the exact channel-neutral envelope shape the
// repo-owned Python normaliser (backend/whatsapp_linked_device.py) reads. It
// contains NO Portfolio Guru product logic — no extraction, no form
// recommendation, no drafting, no Kaizen access.
//
// Safety boundary: sanitisation is whitelist-based, never blacklist-based. Only
// the routing key and the small set of fields the Python normaliser actually
// reads are copied through. Every WhatsApp media key, encrypted URL, direct
// path, thumbnail and hash is therefore dropped by construction and can never
// reach stdout — the live connector resolves media bytes out-of-band, never
// through this transport frame.

// Media containers the neutral contract understands, mapped to the fields the
// Python normaliser reads. Anything not listed here (mediaKey, url, directPath,
// fileEncSha256, fileSha256, jpegThumbnail, mediaKeyTimestamp, ...) is dropped.
const MEDIA_CONTAINERS = {
  imageMessage: ['mimetype', 'caption'],
  documentMessage: ['mimetype', 'caption'],
  videoMessage: ['mimetype', 'caption'],
  stickerMessage: ['mimetype', 'caption'],
  // audioMessage additionally carries `ptt` so the normaliser can tell a
  // push-to-talk voice note from an audio file.
  audioMessage: ['mimetype', 'caption', 'ptt'],
};

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

// Copy only the whitelisted keys that are actually present on `source`.
function pick(source, keys) {
  const out = {};
  for (const key of keys) {
    if (source[key] !== undefined && source[key] !== null) {
      out[key] = source[key];
    }
  }
  return out;
}

// Reduce a raw Baileys message key to the routing identity the neutral contract
// needs. No display name, no clinical content — only routing ids.
function sanitizeKey(rawKey) {
  if (!isObject(rawKey)) {
    return {};
  }
  const key = {};
  if (typeof rawKey.remoteJid === 'string') {
    key.remoteJid = rawKey.remoteJid;
  }
  if (typeof rawKey.participant === 'string') {
    key.participant = rawKey.participant;
  }
  if (rawKey.id !== undefined && rawKey.id !== null) {
    key.id = String(rawKey.id);
  }
  if (typeof rawKey.fromMe === 'boolean') {
    key.fromMe = rawKey.fromMe;
  }
  // Baileys can receive user chats on a LID JID while also providing the
  // companion phone-number JID on the message key. Preserve those routing-only
  // fields so the product bridge can keep the LID as the stable conversation id
  // but reply via the phone-number JID when WhatsApp exposes it.
  for (const jidKey of ['senderPn', 'participantPn', 'senderLid', 'participantLid']) {
    if (typeof rawKey[jidKey] === 'string') {
      key[jidKey] = rawKey[jidKey];
    }
  }
  return key;
}

// Reduce a raw Baileys `message` container to the whitelisted text/media fields
// the neutral contract reads. Returns null when nothing recognised is present,
// so a receipt or protocol frame yields no message body at all.
function sanitizeMessageBody(rawMessage) {
  if (!isObject(rawMessage)) {
    return null;
  }
  const body = {};

  if (typeof rawMessage.conversation === 'string' && rawMessage.conversation) {
    body.conversation = rawMessage.conversation;
  }

  const extended = rawMessage.extendedTextMessage;
  if (isObject(extended) && typeof extended.text === 'string' && extended.text) {
    body.extendedTextMessage = { text: extended.text };
  }

  for (const [container, fields] of Object.entries(MEDIA_CONTAINERS)) {
    const payload = rawMessage[container];
    if (isObject(payload)) {
      body[container] = pick(payload, fields);
    }
  }

  return Object.keys(body).length > 0 ? body : null;
}

// Map one raw Baileys message object to the exact envelope the Python
// normaliser consumes: { key: {...}, message?: {...} }. The `message` field is
// omitted entirely when no recognised body is present, mirroring how the fixture
// records a contentless turn (the normaliser then refuses it as empty).
function sanitizeEnvelope(rawMsg) {
  if (!isObject(rawMsg)) {
    throw new TypeError('linked-device message must be an object');
  }
  const envelope = { key: sanitizeKey(rawMsg.key) };
  const body = sanitizeMessageBody(rawMsg.message);
  if (body) {
    envelope.message = body;
  }
  return envelope;
}

// From a Baileys `messages.upsert` payload ({ messages, type }), yield the
// sanitised envelopes worth streaming inbound. Messages we sent ourselves
// (`key.fromMe`) are dropped — echoing our own outbound is a pure transport
// de-duplication concern, not product logic. Frames with no routable
// `key.remoteJid` (internal/protocol frames) and frames with no recognised body
// are also dropped in the live path (receipts, protocol frames), so the runner
// never receives a frame it cannot route.
function extractInbound(upsert) {
  if (!isObject(upsert) || !Array.isArray(upsert.messages)) {
    return [];
  }
  const out = [];
  for (const rawMsg of upsert.messages) {
    if (!isObject(rawMsg)) {
      continue;
    }
    if (isObject(rawMsg.key) && rawMsg.key.fromMe === true) {
      continue;
    }
    const envelope = sanitizeEnvelope(rawMsg);
    if (envelope.key.remoteJid && envelope.message) {
      out.push(envelope);
    }
  }
  return out;
}

// Serialise one sanitised envelope as a single NDJSON line (no embedded
// newlines), the exact frame `whatsapp_connector_runner.py --relay` reads.
function serializeEnvelope(envelope) {
  return JSON.stringify(envelope);
}

module.exports = {
  MEDIA_CONTAINERS,
  sanitizeKey,
  sanitizeMessageBody,
  sanitizeEnvelope,
  extractInbound,
  serializeEnvelope,
};
