// api/telegram.js — Telegram webhook for the legal-news bot (CommonJS, no deps).
//
// Purpose: give an INSTANT response when a user taps "Make AI Video" (or sends
// /vtest, /news). Telegram pushes each update here the moment it happens; we
// immediately post a "0%" progress message (with a Cancel button), then hand the
// heavy rendering to GitHub Actions by writing job.json to the bot repo and
// triggering a workflow_dispatch. The Actions run reads job.json and renders,
// editing the same progress message as it goes.
//
// Env vars needed on Vercel:
//   TELEGRAMBOTTOKEN  — the bot token (same value as the GitHub secret)
//   BOT_GH_TOKEN      — a GitHub fine-grained PAT for Nairb-35/new-legal-bot with
//                       Contents: read/write AND Actions: read/write
//   BOT_REPO          — optional, defaults to "Nairb-35/new-legal-bot"

const TG = process.env.TELEGRAMBOTTOKEN;
const GH = process.env.BOT_GH_TOKEN;
const REPO = process.env.BOT_REPO || 'Nairb-35/new-legal-bot';

async function tg(method, body) {
  const r = await fetch(`https://api.telegram.org/bot${TG}/${method}`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  });
  return r.json().catch(() => ({}));
}

const ghHeaders = {
  authorization: `Bearer ${GH}`, accept: 'application/vnd.github+json',
  'user-agent': 'lawbot', 'content-type': 'application/json',
};
async function ghPut(path, obj, message) {
  const url = `https://api.github.com/repos/${REPO}/contents/${path}`;
  let sha;
  try { const g = await fetch(url + '?ref=main', { headers: ghHeaders }); if (g.ok) sha = (await g.json()).sha; } catch (e) {}
  const content = Buffer.from(typeof obj === 'string' ? obj : JSON.stringify(obj)).toString('base64');
  const body = { message, content, branch: 'main' };
  if (sha) body.sha = sha;
  return fetch(url, { method: 'PUT', headers: ghHeaders, body: JSON.stringify(body) });
}
async function ghDispatch() {
  return fetch(`https://api.github.com/repos/${REPO}/actions/workflows/run_bot.yml/dispatches`, {
    method: 'POST', headers: ghHeaders, body: JSON.stringify({ ref: 'main' }),
  });
}

const PROG0 = '🎬 <b>Making AI video…</b>\n\n░░░░░░░░░░  0%\n<i>Queued — starting…</i>';

async function startVideo(chat, replyTo, pageId, isVtest) {
  const sent = await tg('sendMessage', {
    chat_id: chat, text: PROG0, parse_mode: 'HTML', reply_to_message_id: replyTo,
  });
  const pmid = sent.result && sent.result.message_id;
  if (pmid) {
    await tg('editMessageReplyMarkup', {
      chat_id: chat, message_id: pmid,
      reply_markup: { inline_keyboard: [[{ text: '✖ Cancel', callback_data: 'x:' + pmid }]] },
    });
  }
  const ts = Math.floor(Date.now() / 1000);
  const job = isVtest
    ? { type: 'vtest', chat_id: chat, progress_msg_id: pmid, reply_to: replyTo, ts }
    : { type: 'render', page_id: pageId, chat_id: chat, progress_msg_id: pmid, reply_to: replyTo, ts };
  await ghPut('job.json', job, 'queue video job');
  await ghDispatch();
}

function readRaw(req) {
  return new Promise((resolve) => { let d = ''; req.on('data', (c) => (d += c)); req.on('end', () => resolve(d)); });
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    // Self-setup: visiting /api/telegram?setup=1 points Telegram's webhook at
    // this URL using the bot token already stored in Vercel env — no need to
    // paste the token into a browser. ?setup=off removes it.
    try {
      const q = req.url || '';
      if (q.includes('setup=1')) {
        const r = await tg('setWebhook', { url: `https://${req.headers.host}/api/telegram` });
        res.status(200).json(r); return;
      }
      if (q.includes('setup=off')) {
        const r = await tg('deleteWebhook', {});
        res.status(200).json(r); return;
      }
    } catch (e) {}
    res.status(200).send('ok'); return;
  }
  let u = req.body;
  if (!u || typeof u !== 'object') { try { u = JSON.parse(await readRaw(req)); } catch (e) { u = {}; } }
  try {
    const cq = u.callback_query;
    if (cq) {
      const data = cq.data || '';
      const chat = cq.message && cq.message.chat && cq.message.chat.id;
      const cmid = cq.message && cq.message.message_id;
      await tg('answerCallbackQuery', { callback_query_id: cq.id, text: data.startsWith('x:') ? 'Cancelling…' : '🎬 Starting…' });
      if (data.startsWith('v:') && chat) {
        await startVideo(chat, cmid, data.slice(2), false);
      } else if (data.startsWith('x:') && chat) {
        const pmid = data.slice(2);
        await ghPut(`cancel_${pmid}.flag`, '1', 'cancel');
        await tg('editMessageText', { chat_id: chat, message_id: Number(pmid), text: '❌ <b>Cancelling…</b>', parse_mode: 'HTML' });
      }
      res.status(200).send('ok'); return;
    }
    const msg = u.message;
    const text = ((msg && msg.text) || '').trim();
    const chat = msg && msg.chat && msg.chat.id;
    if (text && chat) {
      const low = text.toLowerCase();
      if (low.startsWith('/vtest')) {
        await startVideo(chat, msg.message_id, null, true);
      } else if (low.startsWith('/news')) {
        await tg('sendMessage', { chat_id: chat, text: '🔍 Checking for the latest legal news…' });
        await ghPut('job.json', { type: 'news', chat_id: chat, ts: Math.floor(Date.now() / 1000) }, 'news job');
        await ghDispatch();
      } else if (low.startsWith('/help') || low.startsWith('/start')) {
        await tg('sendMessage', { chat_id: chat, text: '⚖️ Legal News Bot\n/news — latest news now\n/vtest — test the AI video maker' });
      } else if (low.startsWith('/id')) {
        await tg('sendMessage', { chat_id: chat, text: '🆔 ' + chat });
      }
    }
    res.status(200).send('ok');
  } catch (e) {
    res.status(200).send('ok');
  }
};
