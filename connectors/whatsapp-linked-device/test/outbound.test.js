'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');

const {
  authorised,
  createOutboundServer,
  isSendPath,
  normaliseRecipient,
} = require('../lib/outbound');
const { resolveSendPort } = require('../lib/config');

function post(port, path, body, headers) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body || {});
    const req = http.request(
      {
        hostname: '127.0.0.1',
        port,
        path,
        method: 'POST',
        headers: Object.assign(
          {
            'content-type': 'application/json',
            'content-length': Buffer.byteLength(payload),
          },
          headers || {}
        ),
      },
      (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          resolve({
            statusCode: res.statusCode,
            body: JSON.parse(Buffer.concat(chunks).toString('utf8')),
          });
        });
      }
    );
    req.on('error', reject);
    req.end(payload);
  });
}

test('normaliseRecipient accepts neutral wa conversation ids and phone numbers', () => {
  assert.equal(
    normaliseRecipient('wa:447700900000@s.whatsapp.net'),
    '447700900000@s.whatsapp.net'
  );
  assert.equal(normaliseRecipient('+44 7700 900000'), '447700900000@s.whatsapp.net');
  assert.equal(normaliseRecipient('120363000000000000@g.us'), '120363000000000000@g.us');
});

test('send path matches the gateway-compatible WhatsApp route shape', () => {
  assert.equal(isSendPath('/api/channels/whatsapp/portfolio-guru/send'), true);
  assert.equal(isSendPath('/api/channels/telegram/portfolio-guru/send'), false);
});

test('authorised checks configured Portfolio secret and optional bearer token', () => {
  assert.equal(
    authorised(
      { headers: { 'x-portfolio-secret': 'ok', authorization: 'Bearer t' } },
      { PG_WA_OUTBOUND_SECRET: 'ok', PG_WA_OUTBOUND_GATEWAY_TOKEN: 't' }
    ),
    true
  );
  assert.equal(
    authorised({ headers: { 'x-portfolio-secret': 'wrong' } }, { PG_WA_OUTBOUND_SECRET: 'ok' }),
    false
  );
});

test('resolveSendPort validates localhost outbound port config', () => {
  assert.equal(resolveSendPort({}), null);
  assert.equal(resolveSendPort({ PG_WA_SEND_PORT: '18795' }), 18795);
  assert.throws(() => resolveSendPort({ PG_WA_SEND_PORT: 'not-a-port' }), /valid TCP port/);
});

test('outbound server sends through the active linked-device socket', async () => {
  const sent = [];
  const server = createOutboundServer({
    getSocket: () => ({
      sendMessage: async (jid, message) => {
        sent.push({ jid, message });
      },
    }),
    env: { PG_WA_OUTBOUND_SECRET: 'secret' },
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  try {
    const response = await post(
      port,
      '/api/channels/whatsapp/portfolio-guru/send',
      { to: 'wa:447700900000@s.whatsapp.net', text: 'Hello from Portfolio Guru' },
      { 'x-portfolio-secret': 'secret' }
    );

    assert.equal(response.statusCode, 200);
    assert.deepEqual(response.body, { ok: true });
    assert.deepEqual(sent, [
      {
        jid: '447700900000@s.whatsapp.net',
        message: { text: 'Hello from Portfolio Guru' },
      },
    ]);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test('outbound server refuses sends before the socket is ready', async () => {
  const server = createOutboundServer({
    getSocket: () => null,
    env: {},
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  try {
    const response = await post(
      port,
      '/api/channels/whatsapp/portfolio-guru/send',
      { to: 'wa:447700900000@s.whatsapp.net', text: 'Hello' }
    );

    assert.equal(response.statusCode, 503);
    assert.equal(response.body.error, 'socket-not-ready');
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
