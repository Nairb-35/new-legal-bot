const test = require('node:test');
const assert = require('node:assert/strict');
const { TELEGRAM_SECRET_HEADER, deriveWebhookSecret, verifyTelegramWebhook } = require('../lib/telegram-auth');

// Deliberately synthetic credentials; never read real bot configuration in tests.
const TOKEN = '123456:synthetic-test-token';
const OTHER_TOKEN = '654321:another-synthetic-test-token';

test('registration secret is deterministic, Telegram-compatible, and separate from the bot token', () => {
  const secret = deriveWebhookSecret(TOKEN);
  assert.match(secret, /^[0-9a-f]{64}$/);
  assert.equal(secret, deriveWebhookSecret(TOKEN));
  assert.notEqual(secret, TOKEN);
  assert.notEqual(secret, deriveWebhookSecret(OTHER_TOKEN));
});

test('registered secret authenticates Node and Fetch header forms', () => {
  const secret = deriveWebhookSecret(TOKEN);
  assert.equal(verifyTelegramWebhook({ [TELEGRAM_SECRET_HEADER]: secret }, TOKEN), true);
  assert.equal(verifyTelegramWebhook({ 'X-Telegram-Bot-Api-Secret-Token': secret }, TOKEN), true);
  assert.equal(verifyTelegramWebhook(new Headers({ [TELEGRAM_SECRET_HEADER]: secret }), TOKEN), true);
});

test('missing or invalid bot configuration always fails closed', () => {
  const headers = { [TELEGRAM_SECRET_HEADER]: deriveWebhookSecret(TOKEN) };
  for (const token of [undefined, null, '', ' ', '\t', 123, {}, ` ${TOKEN}`, `${TOKEN}\n`]) {
    assert.equal(deriveWebhookSecret(token), null);
    assert.equal(verifyTelegramWebhook(headers, token), false);
  }
});

test('missing, malformed, repeated, or inherited headers cannot authenticate', () => {
  const secret = deriveWebhookSecret(TOKEN);
  for (const headers of [undefined, null, {}, '',
    { [TELEGRAM_SECRET_HEADER]: null },
    { [TELEGRAM_SECRET_HEADER]: [secret] },
    { [TELEGRAM_SECRET_HEADER]: [secret, secret] },
    { [TELEGRAM_SECRET_HEADER]: secret, 'X-Telegram-Bot-Api-Secret-Token': secret },
    Object.create({ [TELEGRAM_SECRET_HEADER]: secret }),
    { [TELEGRAM_SECRET_HEADER]: ` ${secret}` },
    { [TELEGRAM_SECRET_HEADER]: `${secret}\n` },
    { [TELEGRAM_SECRET_HEADER]: `${secret}, ${secret}` },
    { [TELEGRAM_SECRET_HEADER]: TOKEN },
    { [TELEGRAM_SECRET_HEADER]: secret.slice(1) },
    { [TELEGRAM_SECRET_HEADER]: 'a'.repeat(65) },
    { [TELEGRAM_SECRET_HEADER]: '\u00e9'.repeat(64) },
  ]) {
    assert.equal(verifyTelegramWebhook(headers, TOKEN), false);
  }
});

test('a same-length incorrect secret and a different bot token are rejected', () => {
  const secret = deriveWebhookSecret(TOKEN);
  const altered = `${secret[0] === '0' ? '1' : '0'}${secret.slice(1)}`;
  assert.equal(verifyTelegramWebhook({ [TELEGRAM_SECRET_HEADER]: altered }, TOKEN), false);
  assert.equal(verifyTelegramWebhook({ [TELEGRAM_SECRET_HEADER]: secret }, OTHER_TOKEN), false);
});
