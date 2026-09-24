const sources = require('../parliament_sources.json');
const { randomUUID } = require('node:crypto');

const threadFields = id => Number(id) === 1 ? {} : { message_thread_id: id };

function topicLink(chat, thread) {
  return `https://t.me/c/${String(chat).replace(/^-100/, '')}/${thread}`;
}

// Dependencies are injected so setup can be verified without creating real topics.
async function setupParliament({ message, tg, loadConfig, savePatch, adoptSection }) {
  const chat = message.chat.id;
  let cfg = await loadConfig();
  const reply = (text) => tg('sendMessage', {
    chat_id: chat,
    ...threadFields(message.message_thread_id ||
      (String(cfg?.chat_id) === String(chat) ? cfg.local_thread_id : undefined)),
    text,
  });
  if (!cfg || String(cfg.chat_id) !== String(chat)) {
    await reply('Parliament topics must be set up in the existing Law News group.');
    return { ok: false, error: 'wrong_chat_or_missing_config' };
  }
  if (message.chat.type !== 'supergroup' || message.chat.is_forum === false) {
    await reply('Enable Topics in this group before setting up Parliament.');
    return { ok: false, error: 'not_forum' };
  }
  const anonymousAdmin = message.sender_chat?.id === chat;
  const member = !anonymousAdmin && message.from?.id
    ? await tg('getChatMember', { chat_id: chat, user_id: message.from.id }) : null;
  if (!anonymousAdmin && !(member?.ok && ['creator', 'administrator'].includes(member.result?.status))) {
    await reply('Only a group admin can set up Parliament topics.');
    return { ok: false, error: 'admin_required' };
  }
  if (adoptSection && (!sources[adoptSection] || !message.message_thread_id ||
      (message.message_thread_id === 1 && adoptSection !== 'dewan_rakyat'))) {
    await reply('Inside the existing topic, send /setparliament negara or /setparliament rakyat.');
    return { ok: false, error: 'invalid_destination' };
  }
  const owner = randomUUID();
  let locked = false;
  const patchOwned = patch => savePatch(patch, { parliament_setup_lock: cfg.parliament_setup_lock });
  try {
    if (cfg.parliament_setup_lock?.expires_at > Date.now()) {
      throw new Error('Parliament setup is already running. Please wait for it to finish.');
    }
    // The GitHub SHA compare-and-swap also checks this precondition after a
    // conflict, so two simultaneous commands cannot both acquire the lease.
    cfg = await savePatch({ parliament_setup_lock: { owner, expires_at: Date.now() + 300000 } },
      { parliament_setup_lock: cfg.parliament_setup_lock || null });
    locked = true;
    if (adoptSection) {
      if (Object.keys(sources).some(section => section !== adoptSection &&
          Number(cfg[`${section}_thread_id`]) === Number(message.message_thread_id)) ||
          [cfg.local_thread_id, cfg.international_thread_id].some(id => Number(id) === Number(message.message_thread_id))) {
        throw new Error('Each Parliament chamber needs its own topic.');
      }
      cfg = await patchOwned({
        [`${adoptSection}_thread_id`]: message.message_thread_id,
        [`${adoptSection}_intro_id`]: null,
        parliament_setup_pending: null,
      });
    }
    if (cfg.dewan_rakyat_thread_id && Number(cfg.dewan_rakyat_thread_id) !== 1) {
      throw new Error('Dewan Rakyat already has a different saved topic. Review it before replacing General.');
    }
    if ([cfg.local_thread_id, cfg.international_thread_id, cfg.dewan_negara_thread_id].some(id => Number(id) === 1)) {
      throw new Error('General is already assigned to another news section.');
    }
    // General is Telegram's special built-in topic: rename/reopen it rather
    // than creating a second Rakyat topic or deleting its existing history.
    for (const [method, body] of [
      ['editGeneralForumTopic', { name: '🏛 Dewan Rakyat' }],
      ['unhideGeneralForumTopic', {}], ['reopenGeneralForumTopic', {}],
    ]) {
      const result = await tg(method, { chat_id: chat, ...body });
      if (!result?.ok && !/not[_ ]modified|not[_ ]hidden|not[_ ]closed|already/i.test(result?.description || '')) {
        throw new Error(result?.description || 'Could not rename and reopen General. Check Manage Topics permission.');
      }
    }
    if (!cfg.dewan_rakyat_thread_id) cfg = await patchOwned({ dewan_rakyat_thread_id: 1 });
    for (const [section, spec] of Object.entries(sources)) {
      const key = `${section}_thread_id`;
      const introKey = `${section}_intro_id`;
      if (!cfg[key]) {
        if (cfg.parliament_setup_pending) {
          throw new Error('A previous setup stopped part-way. In the topic already created, send /setparliament negara or /setparliament rakyat to reconnect it without making a duplicate.');
        }
        // Persist intent first. If saving the created ID fails, retries stop
        // here instead of silently creating another topic with the same name.
        cfg = await patchOwned({ parliament_setup_pending: section });
        const created = await tg('createForumTopic', { chat_id: chat, name: `🏛 ${spec.name}` });
        if (!created?.ok || !created.result?.message_thread_id) {
          await patchOwned({ parliament_setup_pending: null });
          throw new Error(created?.description || 'Telegram could not create the topic. Check Manage Topics permission.');
        }
        cfg = await patchOwned({ [key]: created.result.message_thread_id, parliament_setup_pending: null });
      }
      if (!cfg[introKey]) {
        const intro = await tg('sendMessage', {
          chat_id: chat, ...threadFields(cfg[key]),
          text: `🏛 ${spec.name}\n\nOfficial RTM reports on debates, bills, questions and decisions in ${spec.name}.\n\nPosts keep the original source wording and article links, without AI-generated legal claims. For primary records, use Parliament's official agenda and Hansard below. RTMKlik broadcasts are available when scheduled.`,
          reply_markup: { inline_keyboard: [
            [{ text: `📺 Watch ${spec.name} on RTM`, url: spec.live_url }],
            [{ text: '📋 Official agenda', url: spec.agenda_url },
             { text: '📜 Hansard', url: spec.hansard_url }],
          ] },
        });
        if (!intro?.ok || !intro.result?.message_id) throw new Error(intro?.description || 'Could not post the source guide.');
        cfg = await patchOwned({ [introKey]: intro.result.message_id });
      }
      // Pinning is optional: lack of Pin Messages permission does not disable routing.
      try {
        await tg('pinChatMessage', { chat_id: chat, message_id: cfg[introKey], disable_notification: true });
      } catch (_) { /* Source guide remains available even when pinning fails. */ }
    }
    await reply(`✅ Parliament topics are ready.\n\nDewan Negara: ${topicLink(chat, cfg.dewan_negara_thread_id)}\nDewan Rakyat: ${topicLink(chat, cfg.dewan_rakyat_thread_id)}\n\nNew coverage will be routed to its chamber during the regular news checks.`);
    return { ok: true, config: cfg };
  } catch (error) {
    await reply(`⚠️ Parliament setup was not completed: ${error.message}`);
    return { ok: false, error: error.message };
  } finally {
    if (locked) {
      try { await patchOwned({ parliament_setup_lock: null }); } catch (_) { /* Lease expires safely. */ }
    }
  }
}

module.exports = { setupParliament, topicLink };
