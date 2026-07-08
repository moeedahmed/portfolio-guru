'use strict';

const http = require('http');

const {
  formatOutboundSendSummary,
  summarizeOutboundSend,
} = require('./diagnostics');

function normaliseRecipient(to) {
  const value = String(to || '').trim();
  if (!value) {
    throw new Error('recipient is required');
  }
  if (value.startsWith('wa:')) {
    return normaliseRecipient(value.slice(3));
  }
  if (
    value.endsWith('@s.whatsapp.net') ||
    value.endsWith('@lid') ||
    value.endsWith('@g.us')
  ) {
    return value;
  }
  const digits = value.replace(/[^\d]/g, '');
  if (!digits) {
    throw new Error('recipient is not a WhatsApp jid or phone number');
  }
  return `${digits}@s.whatsapp.net`;
}

function isSendPath(pathname) {
  return /^\/api\/channels\/whatsapp\/[^/]+\/send$/.test(pathname);
}

function readJsonBody(req, { maxBytes = 64 * 1024 } = {}) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > maxBytes) {
        reject(new Error('request body too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8').trim();
      if (!raw) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch (err) {
        reject(new Error('request body must be JSON'));
      }
    });
    req.on('error', reject);
  });
}

function writeJson(res, statusCode, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(statusCode, {
    'content-type': 'application/json',
    'content-length': Buffer.byteLength(body),
  });
  res.end(body);
}

function authorised(req, env) {
  const expectedSecret = String(env.PG_WA_OUTBOUND_SECRET || '').trim();
  if (expectedSecret && req.headers['x-portfolio-secret'] !== expectedSecret) {
    return false;
  }

  const expectedToken = String(env.PG_WA_OUTBOUND_GATEWAY_TOKEN || '').trim();
  if (expectedToken) {
    const auth = String(req.headers.authorization || '');
    return auth === `Bearer ${expectedToken}`;
  }
  return true;
}

function createOutboundServer({ getSocket, env, log }) {
  if (typeof getSocket !== 'function') {
    throw new TypeError('getSocket must be a function');
  }

  return http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url || '/', 'http://127.0.0.1');
      if (req.method !== 'POST' || !isSendPath(url.pathname)) {
        writeJson(res, 404, { ok: false, error: 'not-found' });
        return;
      }
      if (!authorised(req, env || {})) {
        writeJson(res, 401, { ok: false, error: 'unauthorised' });
        return;
      }

      const socket = getSocket();
      if (!socket || typeof socket.sendMessage !== 'function') {
        writeJson(res, 503, { ok: false, error: 'socket-not-ready' });
        return;
      }

      const body = await readJsonBody(req);
      const jid = normaliseRecipient(body.to);
      const text = String(body.text || '').trim();
      if (!text) {
        writeJson(res, 422, { ok: false, error: 'text-required' });
        return;
      }

      const sentMessage = await socket.sendMessage(jid, { text });
      if (log) {
        log(formatOutboundSendSummary(summarizeOutboundSend(jid, sentMessage)));
      }
      writeJson(res, 200, { ok: true });
    } catch (err) {
      if (log) {
        log(`outbound: send failed (${err && err.message ? err.message : err})`);
      }
      writeJson(res, 500, { ok: false, error: 'send-failed' });
    }
  });
}

function startOutboundServer({ port, getSocket, env, log }) {
  const server = createOutboundServer({ getSocket, env, log });
  server.listen(port, '127.0.0.1', () => {
    if (log) {
      log(`outbound: localhost send endpoint listening on 127.0.0.1:${port}`);
    }
  });
  return server;
}

module.exports = {
  authorised,
  createOutboundServer,
  isSendPath,
  normaliseRecipient,
  readJsonBody,
  startOutboundServer,
};
