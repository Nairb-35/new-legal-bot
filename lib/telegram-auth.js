const { createHmac, timingSafeEqual } = require('node:crypto');

const TELEGRAM_SECRET_HEADER = 'x-telegram-bot-api-secret-token';
const SECRET_DOMAIN = 'lawbot:telegram-webhook:v1';

// Separate the webhook credential from the bot's API credential. The stable
// result survives deployments without requiring another stored environment secret.
function deriveWebhookSecret(botToken) {
  if (typeof botToken !== 'string' || !botToken || botToken.trim() !== botToken) return null;
  return createHmac('sha256', botToken).update(SECRET_DOMAIN, 'utf8').digest('hex');
}

function secretHeader(headers) {
  if (!headers || typeof headers !== 'object') return null;
  if (typeof headers.get === 'function') return headers.get(TELEGRAM_SECRET_HEADER);
  const matches = Object.entries(headers).filter(([name]) => name.toLowerCase() === TELEGRAM_SECRET_HEADER);
  // Reject repeated header fields instead of choosing one of several values.
  if (matches.length !== 1 || typeof matches[0][1] !== 'string') return null;
  return matches[0][1];
}

function verifyTelegramWebhook(headers, botToken) {
  const expected = deriveWebhookSecret(botToken);
  if (!expected) return false;
  const supplied = secretHeader(headers);
  if (typeof supplied !== 'string' || supplied.length !== 64 || !/^[0-9a-f]{64}$/.test(supplied)) return false;
  return timingSafeEqual(Buffer.from(supplied, 'utf8'), Buffer.from(expected, 'utf8'));
}

module.exports = { TELEGRAM_SECRET_HEADER, deriveWebhookSecret, verifyTelegramWebhook };
