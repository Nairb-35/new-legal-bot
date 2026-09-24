const test = require('node:test');
const assert = require('node:assert/strict');
const { deriveWebhookSecret } = require('../lib/telegram-auth');
const fakeToken = 'test-only-bot-token';
process.env.TELEGRAMBOTTOKEN = fakeToken;
const handler = require('../api/telegram');
const originalFetch = global.fetch;
test.afterEach(() => { global.fetch = originalFetch; });
function response() {
  return { statusCode: 200, status(code) { this.statusCode = code; return this; },
    json(body) { this.body = body; }, send(body) { this.body = body; } };
}
test('forged setup update is rejected before any Telegram or GitHub request', async () => {
  global.fetch = () => { throw new Error('must not fetch'); };
  const res = response();
  await handler({ method: 'POST', headers: {}, body: { message: {
    text: '/setupparliament', sender_chat: { id: -1004348673663 },
    chat: { id: -1004348673663, type: 'supergroup' } } } }, res);
  assert.equal(res.statusCode, 401);
});
test('registration uses fixed production URL and secret, never caller host', async () => {
  let sent;
  global.fetch = async (url, options) => {
    sent = { url, body: JSON.parse(options.body) };
    return { json: async () => ({ ok: true }) };
  };
  const res = response();
  await handler({ method: 'GET', url: '/api/telegram?setup=1', headers: { host: 'evil.example' } }, res);
  assert.equal(sent.body.url, 'https://new-legal-bot.vercel.app/api/telegram');
  assert.equal(sent.body.secret_token, deriveWebhookSecret(fakeToken));
  assert.deepEqual(res.body, { ok: true });
});
test('public setup=off no longer disables the bot', async () => {
  global.fetch = () => { throw new Error('must not fetch'); };
  const res = response();
  await handler({ method: 'GET', url: '/api/telegram?setup=off', headers: {} }, res);
  assert.equal(res.body, 'ok');
});
test('verified Telegram update is accepted', async () => {
  const res = response();
  await handler({ method: 'POST', headers: { 'x-telegram-bot-api-secret-token': deriveWebhookSecret(fakeToken) }, body: {} }, res);
  assert.equal(res.statusCode, 200);
});
test('migration release keeps existing unsigned help commands working', async () => {
  const methods = [];
  global.fetch = async (url, options = {}) => {
    if (url.includes('api.telegram.org')) {
      methods.push(url.split('/').pop());
      return { ok: true, json: async () => ({ ok: true }) };
    }
    return { ok: options.method === 'PUT', status: 200, json: async () => ({}) };
  };
  const res = response();
  await handler({ method: 'POST', headers: {}, body: { message: { text: '/help', chat: { id: 123 } } } }, res);
  assert.equal(res.statusCode, 200);
  assert.deepEqual(methods, ['sendMessage']);
});
