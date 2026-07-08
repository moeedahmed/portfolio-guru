'use strict';

// Redacted, content-free diagnostics for the live linked-device path.
//
// The sidecar's stdout is the NDJSON data channel; its stderr is the only place
// a human can watch what the socket is doing. Before this module the stderr log
// went silent between "session open" and process exit, so a live watch could
// not tell whether a `messages.upsert` ever fired, whether a message was dropped
// (and why), or which account was actually linked. That blindness is what made
// every earlier watch inconclusive.
//
// Everything here is pure and returns only routing SHAPE — scope, booleans and
// counts — plus a one-way fingerprint of routing ids. It never returns a phone
// number, a JID in the clear, message text, a caption, or any key material, so
// its output is always safe to write to the stderr log.

const crypto = require('crypto');

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

// A short, non-reversible fingerprint of a routing id. Lets a watcher correlate
// turns from the same conversation across a window without ever exposing the
// number behind the JID.
function fingerprint(value) {
  if (typeof value !== 'string' || !value) {
    return null;
  }
  return crypto.createHash('sha256').update(value).digest('hex').slice(0, 8);
}

// Classify a JID by scope from its suffix only — never revealing the number.
// Mirrors the suffix set the Python normaliser treats as non-DIRECT.
function jidScope(jid) {
  if (typeof jid !== 'string' || !jid) {
    return 'none';
  }
  const lowered = jid.toLowerCase();
  if (
    lowered.endsWith('@g.us') ||
    lowered.endsWith('@broadcast') ||
    lowered.endsWith('@newsletter')
  ) {
    return 'group';
  }
  return 'direct';
}

// Redact a JID to scope + fingerprint. The domain suffix is routing shape, not
// identity, so it is kept; the user part (the phone number) is never returned.
function redactJid(jid) {
  if (typeof jid !== 'string' || !jid) {
    return { scope: 'none', fingerprint: null };
  }
  return { scope: jidScope(jid), fingerprint: fingerprint(jid) };
}

// A one-line, redacted description of which account this session is linked to.
// Reads Baileys `creds.me` (the companion's own identity). Confirming the linked
// account is the dedicated Portfolio Guru account — not, say, a personal one —
// is the first thing a watch needs and was previously unobservable.
function describeSelfIdentity(creds) {
  if (!isObject(creds)) {
    return { platform: null, self: { scope: 'none', fingerprint: null } };
  }
  const me = isObject(creds.me) ? creds.me : {};
  return {
    platform: typeof creds.platform === 'string' ? creds.platform : null,
    self: redactJid(typeof me.id === 'string' ? me.id : null),
  };
}

// Decide what the live inbound path does with one raw Baileys message, and why.
// This is the SAME decision `sanitize.extractInbound` makes; keeping it here as
// an explicit, reason-carrying classifier is what lets a watch see the drop
// reason. A drift test pins it against `extractInbound` on the shared fixture.
function classifyMessage(rawMsg, hasRecognisedBody = null) {
  if (!isObject(rawMsg)) {
    return { action: 'drop', reason: 'not-an-object' };
  }
  const key = isObject(rawMsg.key) ? rawMsg.key : {};
  if (key.fromMe === true) {
    return { action: 'drop', reason: 'fromMe' };
  }
  const remoteJid = typeof key.remoteJid === 'string' ? key.remoteJid : '';
  if (!remoteJid) {
    return { action: 'drop', reason: 'no-remoteJid' };
  }
  if (hasRecognisedBody === false) {
    return { action: 'drop', reason: 'no-body' };
  }
  return { action: 'emit', reason: null, scope: jidScope(remoteJid) };
}

// Summarise one `messages.upsert` payload into counts and drop reasons only.
// `type` is the Baileys upsert kind ('notify' for live messages, 'append' for
// messages delivered on reconnect) — knowing which is what tells a watcher
// whether the "hi" arrived live or was a replay of offline history.
function summarizeUpsert(upsert) {
  const summary = {
    type: isObject(upsert) && typeof upsert.type === 'string' ? upsert.type : null,
    total: 0,
    emitted: 0,
    dropped: 0,
    dropReasons: {},
  };
  if (!isObject(upsert) || !Array.isArray(upsert.messages)) {
    return summary;
  }
  // Import lazily to avoid creating a hard module cycle at load time. This
  // pins the diagnostic "emitted" count to the actual live stdout filter rather
  // than a parallel approximation.
  // eslint-disable-next-line global-require
  const { sanitizeMessageBody } = require('./sanitize');
  for (const rawMsg of upsert.messages) {
    summary.total += 1;
    const hasRecognisedBody =
      isObject(rawMsg) && sanitizeMessageBody(rawMsg.message) !== null;
    const verdict = classifyMessage(rawMsg, hasRecognisedBody);
    if (verdict.action === 'emit') {
      summary.emitted += 1;
    } else {
      summary.dropped += 1;
      summary.dropReasons[verdict.reason] =
        (summary.dropReasons[verdict.reason] || 0) + 1;
    }
  }
  return summary;
}

// Render a summary as one compact stderr line, e.g.
//   messages.upsert type=notify total=2 emitted=1 dropped=1 [fromMe=1]
function formatUpsertSummary(summary) {
  const parts = [
    `messages.upsert type=${summary.type == null ? 'none' : summary.type}`,
    `total=${summary.total}`,
    `emitted=${summary.emitted}`,
    `dropped=${summary.dropped}`,
  ];
  const reasons = Object.keys(summary.dropReasons);
  if (reasons.length > 0) {
    const rendered = reasons
      .sort()
      .map((reason) => `${reason}=${summary.dropReasons[reason]}`)
      .join(' ');
    parts.push(`[${rendered}]`);
  }
  return parts.join(' ');
}

// Summarise a `messaging-history.set` payload (the offline/history sync batch).
// Messages the phone already showed but that arrived before this companion was
// online surface here rather than as a live upsert, so a watch must be able to
// see the batch size to explain a "delivered but not observed" report.
function summarizeHistorySync(historySet) {
  const messages =
    isObject(historySet) && Array.isArray(historySet.messages)
      ? historySet.messages
      : [];
  return {
    messages: messages.length,
    chats:
      isObject(historySet) && Array.isArray(historySet.chats)
        ? historySet.chats.length
        : 0,
    isLatest: isObject(historySet) ? Boolean(historySet.isLatest) : false,
  };
}

function valueLabel(value) {
  if (value == null) {
    return 'none';
  }
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return 'present';
}

function summarizeMessageUpdates(updates) {
  const summary = {
    total: Array.isArray(updates) ? updates.length : 0,
    statuses: {},
    messageIds: [],
    chats: {},
  };
  if (!Array.isArray(updates)) {
    return summary;
  }
  for (const item of updates) {
    if (!isObject(item)) {
      continue;
    }
    const key = isObject(item.key) ? item.key : {};
    const update = isObject(item.update) ? item.update : {};
    const status = valueLabel(update.status);
    summary.statuses[status] = (summary.statuses[status] || 0) + 1;

    const messageId = fingerprint(typeof key.id === 'string' ? key.id : '');
    if (messageId && summary.messageIds.length < 3) {
      summary.messageIds.push(messageId);
    }

    const chat = redactJid(typeof key.remoteJid === 'string' ? key.remoteJid : '');
    if (chat.scope !== 'none') {
      const label = `${chat.scope}/${chat.fingerprint || 'none'}`;
      summary.chats[label] = (summary.chats[label] || 0) + 1;
    }
  }
  return summary;
}

function summarizeReceiptUpdates(receipts) {
  const summary = {
    total: Array.isArray(receipts) ? receipts.length : 0,
    receiptTypes: {},
    messageIds: [],
    chats: {},
  };
  if (!Array.isArray(receipts)) {
    return summary;
  }
  for (const item of receipts) {
    if (!isObject(item)) {
      continue;
    }
    const key = isObject(item.key) ? item.key : {};
    const receipt = isObject(item.receipt) ? item.receipt : {};
    const receiptType = valueLabel(receipt.type);
    summary.receiptTypes[receiptType] = (summary.receiptTypes[receiptType] || 0) + 1;

    const messageId = fingerprint(typeof key.id === 'string' ? key.id : '');
    if (messageId && summary.messageIds.length < 3) {
      summary.messageIds.push(messageId);
    }

    const chat = redactJid(typeof key.remoteJid === 'string' ? key.remoteJid : '');
    if (chat.scope !== 'none') {
      const label = `${chat.scope}/${chat.fingerprint || 'none'}`;
      summary.chats[label] = (summary.chats[label] || 0) + 1;
    }
  }
  return summary;
}

function formatCounts(counts) {
  const keys = Object.keys(counts || {}).sort();
  if (keys.length === 0) {
    return 'none';
  }
  return keys.map((key) => `${key}=${counts[key]}`).join(' ');
}

function formatMessageUpdateSummary(summary) {
  const ids = summary.messageIds.length > 0 ? summary.messageIds.join(',') : 'none';
  return [
    'messages.update',
    `total=${summary.total}`,
    `statuses=[${formatCounts(summary.statuses)}]`,
    `ids=${ids}`,
  ].join(' ');
}

function formatReceiptUpdateSummary(summary) {
  const ids = summary.messageIds.length > 0 ? summary.messageIds.join(',') : 'none';
  return [
    'message-receipt.update',
    `total=${summary.total}`,
    `types=[${formatCounts(summary.receiptTypes)}]`,
    `ids=${ids}`,
  ].join(' ');
}

function summarizeOutboundSend(jid, result) {
  const target = redactJid(jid);
  const key = isObject(result) && isObject(result.key) ? result.key : {};
  return {
    target,
    messageId: fingerprint(typeof key.id === 'string' ? key.id : ''),
    status: isObject(result) && result.status != null ? valueLabel(result.status) : 'none',
  };
}

function formatOutboundSendSummary(summary) {
  return (
    `outbound: send accepted target=${summary.target.scope}/` +
    `${summary.target.fingerprint || 'none'} id=${summary.messageId || 'none'} ` +
    `status=${summary.status || 'none'}`
  );
}

module.exports = {
  classifyMessage,
  describeSelfIdentity,
  fingerprint,
  formatMessageUpdateSummary,
  formatOutboundSendSummary,
  formatReceiptUpdateSummary,
  formatUpsertSummary,
  jidScope,
  redactJid,
  summarizeHistorySync,
  summarizeMessageUpdates,
  summarizeOutboundSend,
  summarizeReceiptUpdates,
  summarizeUpsert,
};
