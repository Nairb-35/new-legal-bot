const test = require('node:test');
const assert = require('node:assert/strict');

const handler = require('../api/cron');

function responseRecorder() {
  return {
    headers: {},
    statusCode: 200,
    body: null,
    setHeader(name, value) {
      this.headers[name] = value;
    },
    status(code) {
      this.statusCode = code;
      return this;
    },
    json(body) {
      this.body = body;
      return this;
    },
  };
}

function request({ method = 'POST', authorization } = {}) {
  return { method, headers: authorization ? { authorization } : {} };
}

test.beforeEach(() => {
  process.env.CRON_SECRET = 'cron-secret-for-tests';
  process.env.BOT_GH_TOKEN = 'github-token-for-tests';
  process.env.BOT_REPO = 'Nairb-35/new-legal-bot';
});
test.afterEach(() => {
  delete process.env.CRON_SECRET;
  delete process.env.BOT_GH_TOKEN;
  delete process.env.BOT_REPO;
  delete global.fetch;
});

test('only accepts POST', async () => {
  const res = responseRecorder();
  await handler(request({ method: 'GET' }), res);
  assert.equal(res.statusCode, 405);
  assert.equal(res.headers.Allow, 'POST');
  assert.deepEqual(res.body, { ok: false, error: 'method_not_allowed' });
});

test('fails closed when configuration is missing', async () => {
  delete process.env.CRON_SECRET;
  const res = responseRecorder();
  await handler(request({ authorization: 'Bearer anything' }), res);
  assert.equal(res.statusCode, 503);
  assert.deepEqual(res.body, { ok: false, error: 'service_not_configured' });
});

test('rejects an invalid bearer secret without calling GitHub', async () => {
  let called = false;
  global.fetch = async () => {
    called = true;
    return { ok: true, status: 204 };
  };
  const res = responseRecorder();
  await handler(request({ authorization: 'Bearer wrong-secret' }), res);
  assert.equal(res.statusCode, 401);
  assert.equal(called, false);
  assert.deepEqual(res.body, { ok: false, error: 'unauthorized' });
});

test('dispatches the workflow with the Vercel-only GitHub token', async () => {
  let call;
  global.fetch = async (url, options) => {
    call = { url, options };
    return { ok: true, status: 204 };
  };
  const res = responseRecorder();
  await handler(request({ authorization: 'Bearer cron-secret-for-tests' }), res);

  assert.equal(res.statusCode, 202);
  assert.deepEqual(res.body, { ok: true, dispatched: true });
  assert.equal(call.url, 'https://api.github.com/repos/Nairb-35/new-legal-bot/actions/workflows/run_bot.yml/dispatches');
  assert.equal(call.options.method, 'POST');
  assert.equal(call.options.headers.authorization, 'Bearer github-token-for-tests');
  assert.deepEqual(JSON.parse(call.options.body), { ref: 'main' });
});

test('does not expose GitHub failure details', async () => {
  global.fetch = async () => ({ ok: false, status: 401 });
  const originalError = console.error;
  console.error = () => {};
  const res = responseRecorder();
  try {
    await handler(request({ authorization: 'Bearer cron-secret-for-tests' }), res);
  } finally {
    console.error = originalError;
  }
  assert.equal(res.statusCode, 502);
  assert.deepEqual(res.body, { ok: false, error: 'github_dispatch_failed' });
});
