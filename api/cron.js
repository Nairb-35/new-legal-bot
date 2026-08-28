// Secure relay for cron-job.org.
//
// cron-job.org authenticates with CRON_SECRET. The GitHub credential stays in
// Vercel and is never shared with the scheduler.

const { timingSafeEqual } = require('node:crypto');

const DEFAULT_REPO = 'Nairb-35/new-legal-bot';

function requestHeader(req, name) {
  const value = req && req.headers && req.headers[name.toLowerCase()];
  return Array.isArray(value) ? value[0] : value;
}

function secretsMatch(received, expected) {
  const left = Buffer.from(String(received || ''), 'utf8');
  const right = Buffer.from(String(expected || ''), 'utf8');
  return left.length === right.length && left.length > 0 && timingSafeEqual(left, right);
}

function sendJson(res, status, body) {
  res.setHeader('Cache-Control', 'no-store');
  return res.status(status).json(body);
}

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return sendJson(res, 405, { ok: false, error: 'method_not_allowed' });
  }

  const cronSecret = process.env.CRON_SECRET;
  const githubToken = process.env.BOT_GH_TOKEN;
  if (!cronSecret || !githubToken) {
    return sendJson(res, 503, { ok: false, error: 'service_not_configured' });
  }

  const authorization = String(requestHeader(req, 'authorization') || '');
  const suppliedSecret = authorization.replace(/^Bearer\s+/i, '');
  if (!secretsMatch(suppliedSecret, cronSecret)) {
    return sendJson(res, 401, { ok: false, error: 'unauthorized' });
  }

  const repo = process.env.BOT_REPO || DEFAULT_REPO;
  const response = await fetch(
    `https://api.github.com/repos/${repo}/actions/workflows/run_bot.yml/dispatches`,
    {
      method: 'POST',
      headers: {
        authorization: `Bearer ${githubToken}`,
        accept: 'application/vnd.github+json',
        'content-type': 'application/json',
        'user-agent': 'lawbot-cron-relay',
        'x-github-api-version': '2022-11-28',
      },
      body: JSON.stringify({ ref: 'main' }),
    },
  );

  if (!response.ok) {
    console.error('Cron relay: GitHub dispatch failed', response.status);
    return sendJson(res, 502, { ok: false, error: 'github_dispatch_failed' });
  }

  return sendJson(res, 202, { ok: true, dispatched: true });
};
