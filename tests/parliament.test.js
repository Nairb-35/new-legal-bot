const test = require('node:test');
const assert = require('node:assert/strict');
const { setupParliament } = require('../lib/parliament');

function fixture(options = {}) {
  let cfg = { chat_id: -1004348673663, local_thread_id: 2027,
    international_thread_id: 2028, ...options.config };
  let nextTopic = 3000;
  let nextMessage = 4000;
  let saves = 0;
  const calls = [];
  const deps = {
    message: { chat: { id: cfg.chat_id, type: 'supergroup', is_forum: true }, from: { id: 123 }, message_thread_id: 2027 },
    loadConfig: async () => ({ ...cfg }),
    savePatch: async (patch, expected = {}) => {
      if (++saves === options.failSave) throw new Error('save failed');
      for (const [key, value] of Object.entries(expected)) {
        if (JSON.stringify(cfg[key] ?? null) !== JSON.stringify(value ?? null)) throw new Error('concurrent setup');
      }
      cfg = { ...cfg, ...patch };
      return { ...cfg };
    },
    tg: async (method, body) => {
      calls.push({ method, body });
      if (method === 'getChatMember') return { ok: true, result: { status: options.role || 'administrator' } };
      if (method === 'createForumTopic') return options.failCreate
        ? { ok: false, description: 'Missing Manage Topics' }
        : { ok: true, result: { message_thread_id: ++nextTopic } };
      if (method === 'sendMessage') return { ok: true, result: { message_id: ++nextMessage } };
      return { ok: true };
    },
  };
  return { deps, calls, config: () => cfg };
}

test('renames General as Rakyat, creates Negara, and posts separate source cards', async () => {
  const f = fixture();
  const result = await setupParliament(f.deps);
  assert.equal(result.ok, true);
  assert.deepEqual(f.calls.filter(x => x.method === 'createForumTopic').map(x => x.body.name), ['🏛 Dewan Negara']);
  assert.equal(f.calls.find(x => x.method === 'editGeneralForumTopic').body.name, '🏛 Dewan Rakyat');
  assert.ok(f.calls.some(x => x.method === 'unhideGeneralForumTopic'));
  assert.ok(f.calls.some(x => x.method === 'reopenGeneralForumTopic'));
  assert.equal(f.config().dewan_negara_thread_id, 3001);
  assert.equal(f.config().dewan_rakyat_thread_id, 1);
  assert.equal(f.config().local_thread_id, 2027);
  assert.equal(f.config().international_thread_id, 2028);
  const cards = f.calls.filter(x => x.method === 'sendMessage' && x.body.reply_markup);
  assert.equal(cards.length, 2);
  assert.equal(cards[0].body.message_thread_id, 3001);
  assert.equal('message_thread_id' in cards[1].body, false);
  assert.match(cards[0].body.reply_markup.inline_keyboard[0][0].url, /\/khas\/dewannegara$/);
  assert.match(cards[1].body.reply_markup.inline_keyboard[0][0].url, /\/khas\/dewanrakyat$/);
  assert.equal(f.calls.filter(x => x.method === 'pinChatMessage').length, 2);
});

test('repeating setup creates no duplicate topics or source cards', async () => {
  const f = fixture();
  await setupParliament(f.deps);
  await setupParliament(f.deps);
  assert.equal(f.calls.filter(x => x.method === 'createForumTopic').length, 1);
  assert.equal(f.calls.filter(x => x.body.reply_markup).length, 2);
});

test('non-admin and wrong-group requests never create or save destinations', async () => {
  for (const wrongGroup of [false, true]) {
    const f = fixture({ role: wrongGroup ? 'administrator' : 'member' });
    if (wrongGroup) f.deps.message.chat.id = -1009999;
    const result = await setupParliament(f.deps);
    assert.equal(result.ok, false);
    assert.equal(f.calls.filter(x => x.method === 'createForumTopic').length, 0);
    assert.equal(f.config().dewan_negara_thread_id, undefined);
  }
});

test('failed creation leaves no false destination or ready message', async () => {
  const f = fixture({ failCreate: true });
  const result = await setupParliament(f.deps);
  assert.equal(result.ok, false);
  assert.equal(f.config().dewan_negara_thread_id, undefined);
  assert.equal(f.config().parliament_setup_pending, null);
  assert.ok(!f.calls.some(x => x.body.text?.includes('topics are ready')));
});

test('failed save after creation blocks duplicate creation on retry', async () => {
  const f = fixture({ failSave: 4 });
  assert.equal((await setupParliament(f.deps)).ok, false);
  assert.equal(f.config().parliament_setup_pending, 'dewan_negara');
  assert.equal((await setupParliament(f.deps)).ok, false);
  assert.equal(f.calls.filter(x => x.method === 'createForumTopic').length, 1);
});

test('recovery adopts the existing Parliament topic instead of recreating it', async () => {
  const f = fixture({ config: { parliament_setup_pending: 'dewan_negara' } });
  f.deps.message.message_thread_id = 2500;
  f.deps.adoptSection = 'dewan_negara';
  assert.equal((await setupParliament(f.deps)).ok, true);
  assert.equal(f.config().dewan_negara_thread_id, 2500);
  assert.deepEqual(f.calls.filter(x => x.method === 'createForumTopic').map(x => x.body.name), []);
});

test('concurrent setup commands cannot create duplicate topics or guides', async () => {
  const f = fixture();
  const results = await Promise.all([setupParliament(f.deps), setupParliament(f.deps)]);
  assert.equal(results.filter(x => x.ok).length, 1);
  assert.equal(f.calls.filter(x => x.method === 'createForumTopic').length, 1);
  assert.equal(f.calls.filter(x => x.body.reply_markup).length, 2);
  assert.equal(f.config().parliament_setup_lock, null);
});

test('failed General rename does not enable routing or create a different topic', async () => {
  const f = fixture();
  const original = f.deps.tg;
  f.deps.tg = (method, body) => method === 'editGeneralForumTopic'
    ? { ok: false, description: 'Missing Manage Topics' } : original(method, body);
  assert.equal((await setupParliament(f.deps)).ok, false);
  assert.equal(f.config().dewan_rakyat_thread_id, undefined);
  assert.equal(f.calls.filter(x => x.method === 'createForumTopic').length, 0);
});

test('recovery cannot overwrite Local News with a Parliament destination', async () => {
  const f = fixture();
  f.deps.adoptSection = 'dewan_negara';
  assert.equal((await setupParliament(f.deps)).ok, false);
  assert.equal(f.config().dewan_negara_thread_id, undefined);
});
