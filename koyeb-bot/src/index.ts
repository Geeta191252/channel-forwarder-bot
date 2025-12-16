import express from 'express';
import { createClient } from '@supabase/supabase-js';
import fetch from 'node-fetch';

const app = express();
app.use(express.json());

// Environment variables
const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN!;
const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY!;
const PORT = process.env.PORT || 8000;
const WEBHOOK_URL = process.env.WEBHOOK_URL;

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);
const TELEGRAM_API = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}`;

// Session states
const STATES = {
  IDLE: 'idle',
  WAITING_SOURCE: 'waiting_source',
  WAITING_SKIP: 'waiting_skip',
  WAITING_DEST: 'waiting_dest',
  CONFIRMING: 'confirming',
};

// User session management
async function getUserSession(userId: number) {
  const { data } = await supabase
    .from('user_sessions')
    .select('*')
    .eq('user_id', userId)
    .maybeSingle();
  return data;
}

async function setUserSession(userId: number, updates: any) {
  const existing = await getUserSession(userId);
  if (existing) {
    await supabase
      .from('user_sessions')
      .update({ ...updates, updated_at: new Date().toISOString() })
      .eq('user_id', userId);
  } else {
    await supabase
      .from('user_sessions')
      .insert({ user_id: userId, ...updates });
  }
}

async function clearUserSession(userId: number) {
  await supabase
    .from('user_sessions')
    .update({
      state: STATES.IDLE,
      source_channel: null,
      source_title: null,
      dest_channel: null,
      dest_title: null,
      skip_number: 0,
      updated_at: new Date().toISOString(),
    })
    .eq('user_id', userId);
}

// Database operations
async function loadBotConfig() {
  const { data } = await supabase.from('bot_config').select('*').limit(1).maybeSingle();
  return data;
}

async function saveBotConfig(sourceChannel: string, destChannel: string) {
  const existing = await loadBotConfig();
  if (existing) {
    await supabase.from('bot_config').update({ 
      source_channel: sourceChannel, 
      dest_channel: destChannel,
      updated_at: new Date().toISOString()
    }).eq('id', existing.id);
  } else {
    await supabase.from('bot_config').insert({ 
      source_channel: sourceChannel, 
      dest_channel: destChannel 
    });
  }
}

async function loadProgress() {
  const { data } = await supabase.from('forwarding_progress').select('*').eq('id', 'current').maybeSingle();
  return data;
}

async function saveProgress(progress: any) {
  const { data: existing } = await supabase.from('forwarding_progress').select('id').eq('id', 'current').maybeSingle();
  
  if (existing) {
    await supabase.from('forwarding_progress').update({
      ...progress,
      last_updated_at: new Date().toISOString()
    }).eq('id', 'current');
  } else {
    await supabase.from('forwarding_progress').insert({
      id: 'current',
      ...progress,
      last_updated_at: new Date().toISOString()
    });
  }
}

async function isStopRequested() {
  const progress = await loadProgress();
  return progress?.stop_requested === true;
}

async function requestStop() {
  await supabase.from('forwarding_progress').update({ stop_requested: true }).eq('id', 'current');
}

// Telegram API helpers
async function sendTelegramRequest(method: string, params: any) {
  console.log(`Calling Telegram API: ${method}`, params);
  const response = await fetch(`${TELEGRAM_API}/${method}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  const result = await response.json();
  console.log(`Telegram API response for ${method}:`, result);
  return result as any;
}

async function sendMessage(chatId: string | number, text: string, replyMarkup?: any) {
  const params: any = { chat_id: chatId, text, parse_mode: 'HTML' };
  if (replyMarkup) params.reply_markup = replyMarkup;
  return sendTelegramRequest('sendMessage', params);
}

async function editMessageText(chatId: string | number, messageId: number, text: string, replyMarkup?: any) {
  const params: any = { chat_id: chatId, message_id: messageId, text, parse_mode: 'HTML' };
  if (replyMarkup) params.reply_markup = replyMarkup;
  return sendTelegramRequest('editMessageText', params);
}

function progressButtons(progress: any) {
  return progress?.is_active
    ? [[{ text: '🔄 Refresh', callback_data: 'refresh_progress' }, { text: '⏹️ Stop', callback_data: 'stop_forward' }]]
    : [[{ text: '🔄 Refresh', callback_data: 'refresh_progress' }]];
}

function formatProgressText(progress: any) {
  const percent = progress?.total_count
    ? Math.round(((progress?.success_count || 0) / (progress?.total_count || 1)) * 100)
    : 0;
  const status = progress?.is_active
    ? (progress?.stop_requested ? '⏸️ Stopping' : '🔄 Running')
    : '✅ Complete';

  let elapsedStr = '-';
  if (progress?.started_at) {
    const startedAt = new Date(progress.started_at).getTime();
    const elapsedMs = Date.now() - startedAt;
    const elapsedMins = Math.floor(elapsedMs / 60000);
    const elapsedHrs = Math.floor(elapsedMins / 60);
    const elapsedDays = Math.floor(elapsedHrs / 24);
    
    if (elapsedDays > 0) {
      elapsedStr = `${elapsedDays}d ${elapsedHrs % 24}h ${elapsedMins % 60}m`;
    } else if (elapsedHrs > 0) {
      elapsedStr = `${elapsedHrs}h ${elapsedMins % 60}m`;
    } else {
      elapsedStr = `${elapsedMins}m`;
    }
  }

  let etaStr = '-';
  const speed = progress?.speed || 0;
  const remaining = (progress?.total_count || 0) - (progress?.success_count || 0) - (progress?.skipped_count || 0) - (progress?.failed_count || 0);
  
  if (speed > 0 && remaining > 0 && progress?.is_active) {
    const etaMins = Math.ceil(remaining / speed);
    const etaHrs = Math.floor(etaMins / 60);
    const etaDays = Math.floor(etaHrs / 24);
    
    if (etaDays > 0) {
      etaStr = `${etaDays}d ${etaHrs % 24}h ${etaMins % 60}m`;
    } else if (etaHrs > 0) {
      etaStr = `${etaHrs}h ${etaMins % 60}m`;
    } else {
      etaStr = `${etaMins}m`;
    }
  } else if (!progress?.is_active) {
    etaStr = 'Done';
  }

  return (
    `📊 <b>Progress</b> ${status}\n\n` +
    `✅ Success: ${progress?.success_count || 0} / ${progress?.total_count || 0} (${percent}%)\n` +
    `❌ Failed: ${progress?.failed_count || 0}\n` +
    `⏭️ Skipped: ${progress?.skipped_count || 0}\n` +
    `⚡ Rate limits: ${progress?.rate_limit_hits || 0}\n` +
    `🚀 Speed: ${speed} files/min\n` +
    `📦 Batch: ${progress?.current_batch || 0} / ${progress?.total_batches || 0}\n\n` +
    `⏱️ Elapsed: ${elapsedStr}\n` +
    `⏳ ETA: ${etaStr}`
  );
}

async function updateWatchedProgressMessage(chatId: number, progress: any) {
  try {
    const session = await getUserSession(chatId);
    const messageId = session?.last_message_id;
    if (!messageId) return;

    await editMessageText(chatId, messageId, formatProgressText(progress), {
      inline_keyboard: progressButtons(progress),
    });
  } catch (e) {
    console.log('Progress auto-update skipped:', e);
  }
}

async function copyMessages(fromChatId: string, toChatId: string, messageIds: number[]) {
  return sendTelegramRequest('copyMessages', {
    chat_id: toChatId,
    from_chat_id: fromChatId,
    message_ids: messageIds,
  });
}

async function getForwardedMessageIds(sourceChannel: string, destChannel: string, messageIds: number[]) {
  const { data } = await supabase
    .from('forwarded_messages')
    .select('source_message_id')
    .eq('source_channel', sourceChannel)
    .eq('dest_channel', destChannel)
    .in('source_message_id', messageIds);
  return data?.map(d => d.source_message_id) || [];
}

async function saveForwardedMessageIds(sourceChannel: string, destChannel: string, messageIds: number[]) {
  const records = messageIds.map(id => ({
    source_channel: sourceChannel,
    dest_channel: destChannel,
    source_message_id: id,
  }));
  await supabase.from('forwarded_messages').insert(records);
}

function extractChannelFromMessage(message: any): { chatId: string; title: string; lastMsgId: number } | null {
  if (message.forward_from_chat) {
    const chat = message.forward_from_chat;
    const chatId = chat.id.toString();
    const title = chat.title || chat.username || 'Unknown';
    const lastMsgId = message.forward_from_message_id;
    return { chatId, title, lastMsgId };
  }
  
  if (message.text) {
    const regex = /(?:https?:\/\/)?(?:t\.me|telegram\.me|telegram\.dog)\/(c\/)?(\d+|[a-zA-Z_0-9]+)\/(\d+)/;
    const match = message.text.match(regex);
    if (match) {
      let chatId = match[2];
      if (match[1] === 'c/' && chatId.match(/^\d+$/)) {
        chatId = '-100' + chatId;
      }
      const lastMsgId = parseInt(match[3]);
      return { chatId, title: 'Link', lastMsgId };
    }
  }
  
  return null;
}

async function showMainMenu(chatId: number) {
  const keyboard = {
    inline_keyboard: [
      [
        { text: '🚀 Forward', callback_data: 'forward' },
        { text: '⚙️ Set Config', callback_data: 'config' }
      ],
      [
        { text: '▶️ Resume', callback_data: 'resume' },
        { text: '⏹️ Stop', callback_data: 'stop' }
      ],
      [
        { text: '📊 Progress', callback_data: 'progress' },
        { text: '📡 Status', callback_data: 'status' }
      ],
      [
        { text: '❓ Help', callback_data: 'help' }
      ]
    ]
  };
  await sendMessage(chatId, `🤖 <b>Telegram Forwarder Bot</b>\n\nSelect an option below:`, keyboard);
}

// Bulk forward function
async function bulkForward(
  sourceChannel: string,
  destChannel: string,
  startId: number,
  endId: number,
  isResume: boolean,
  chatId?: number,
) {
  const BATCH_SIZE = 100;

  let currentId = startId;
  const existingProgress = isResume ? await loadProgress() : null;

  let successCount = isResume ? existingProgress?.success_count || 0 : 0;
  let failedCount = isResume ? existingProgress?.failed_count || 0 : 0;
  let skippedCount = isResume ? existingProgress?.skipped_count || 0 : 0;
  let rateLimitHits = isResume ? existingProgress?.rate_limit_hits || 0 : 0;
  let batchNum = isResume ? existingProgress?.current_batch || 0 : 0;

  const originalStartedAt = existingProgress?.started_at 
    ? new Date(existingProgress.started_at).getTime() 
    : Date.now();

  const totalBatches = Math.ceil((endId - startId + 1) / BATCH_SIZE);

  while (currentId <= endId) {
    if (await isStopRequested()) {
      console.log('Stop requested, saving progress...');
      const progressPayload = {
        current_batch: batchNum,
        total_batches: totalBatches,
        success_count: successCount,
        failed_count: failedCount,
        skipped_count: skippedCount,
        total_count: endId - startId + 1,
        rate_limit_hits: rateLimitHits,
        is_active: true,
        stop_requested: true,
        speed: Math.round(successCount / Math.max((Date.now() - originalStartedAt) / 60000, 0.001)),
        started_at: new Date(originalStartedAt).toISOString(),
      };
      await saveProgress(progressPayload);
      if (chatId) await updateWatchedProgressMessage(chatId, progressPayload);
      if (chatId) await sendMessage(chatId, `⏹️ Stopped at batch ${batchNum}. Use /resume to continue.`);
      return { success: successCount, failed: failedCount, needsResume: true };
    }

    const batchEnd = Math.min(currentId + BATCH_SIZE - 1, endId);
    const messageIds = Array.from({ length: batchEnd - currentId + 1 }, (_, i) => currentId + i);

    const alreadyForwarded = await getForwardedMessageIds(sourceChannel, destChannel, messageIds);
    const toForward = messageIds.filter((id) => !alreadyForwarded.includes(id));
    skippedCount += alreadyForwarded.length;

    if (toForward.length > 0) {
      let retries = 0;
      let success = false;

      while (retries < 5 && !success) {
        const result = await copyMessages(sourceChannel, destChannel, toForward);

        if (result.ok) {
          successCount += toForward.length;
          await saveForwardedMessageIds(sourceChannel, destChannel, toForward);
          success = true;
        } else if (result.error_code === 429) {
          rateLimitHits++;
          const waitTime = result.parameters?.retry_after || 60;
          console.log(`Rate limited, waiting ${waitTime}s...`);
          await new Promise((r) => setTimeout(r, waitTime * 1000));
          retries++;
        } else if (result.description?.includes('no messages to forward')) {
          skippedCount += toForward.length;
          success = true;
        } else {
          failedCount += toForward.length;
          console.log('Forward failed:', result);
          success = true;
        }
      }
    }

    batchNum++;
    currentId = batchEnd + 1;

    const elapsed = (Date.now() - originalStartedAt) / 60000;
    const speed = Math.round(successCount / Math.max(elapsed, 0.001));

    const progressPayload = {
      source_channel: sourceChannel,
      dest_channel: destChannel,
      start_id: startId,
      end_id: endId,
      current_batch: batchNum,
      total_batches: totalBatches,
      success_count: successCount,
      failed_count: failedCount,
      skipped_count: skippedCount,
      total_count: endId - startId + 1,
      rate_limit_hits: rateLimitHits,
      is_active: currentId <= endId,
      stop_requested: false,
      speed: speed,
      started_at: new Date(originalStartedAt).toISOString(),
    };

    await saveProgress(progressPayload);
    if (chatId) await updateWatchedProgressMessage(chatId, progressPayload);
  }

  const completeProgressPayload = {
    current_batch: batchNum,
    total_batches: totalBatches,
    success_count: successCount,
    failed_count: failedCount,
    skipped_count: skippedCount,
    total_count: endId - startId + 1,
    rate_limit_hits: rateLimitHits,
    is_active: false,
    stop_requested: false,
    speed: 0,
    started_at: new Date(originalStartedAt).toISOString(),
  };
  await saveProgress(completeProgressPayload);
  if (chatId) await updateWatchedProgressMessage(chatId, completeProgressPayload);

  if (chatId) {
    await sendMessage(chatId, `✅ <b>Forwarding Complete!</b>\n\n✅ Success: ${successCount}\n❌ Failed: ${failedCount}\n⏭️ Skipped: ${skippedCount}`);
  }

  return { success: successCount, failed: failedCount, needsResume: false };
}

// Command handlers
async function handleCommand(chatId: number, text: string, message: any) {
  const parts = text.split(' ');
  const command = parts[0].toLowerCase().replace(/@.*$/, '');

  if (command === '/start') {
    await clearUserSession(chatId);
    await showMainMenu(chatId);
  }
  
  else if (command === '/cancel') {
    await clearUserSession(chatId);
    await sendMessage(chatId, '❌ Process cancelled.', {
      inline_keyboard: [[{ text: '🔙 Main Menu', callback_data: 'menu' }]]
    });
  }
  
  else if (command === '/setconfig') {
    if (parts.length < 3) {
      await sendMessage(chatId, '❌ Usage: /setconfig [source_channel] [dest_channel]\nExample: /setconfig -1001234567890 -1009876543210');
      return;
    }
    const source = parts[1];
    const dest = parts[2];
    await saveBotConfig(source, dest);
    await sendMessage(chatId, `✅ Config saved!\n\n📤 Source: <code>${source}</code>\n📥 Destination: <code>${dest}</code>`, {
      inline_keyboard: [[{ text: '🔙 Main Menu', callback_data: 'menu' }]]
    });
  }
  
  else if (command === '/forward' || command === '/fwd') {
    await setUserSession(chatId, { state: STATES.WAITING_SOURCE });
    await sendMessage(chatId, 
      `<b>( SET SOURCE CHAT )</b>\n\n` +
      `Forward the last message or last message link of source chat.\n` +
      `/cancel - cancel this process`,
      { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'cancel' }]] }
    );
  }
  
  else if (command === '/resume') {
    const progress = await loadProgress();
    if (!progress || !progress.is_active) {
      await sendMessage(chatId, '❌ No active forwarding to resume');
      return;
    }

    const lastBatch = progress.current_batch || 0;
    const startId = (progress.start_id || 0) + (lastBatch * 100);

    await saveProgress({ ...progress, stop_requested: false });
    await sendMessage(chatId, `▶️ Resuming from message ${startId}`);

    // Run in background (non-blocking)
    bulkForward(progress.source_channel, progress.dest_channel, startId, progress.end_id, true, chatId);
  }
  
  else if (command === '/stop') {
    await requestStop();
    await sendMessage(chatId, '⏹️ Stop requested. Will stop after current batch.');
  }
  
  else if (command === '/progress') {
    const progress = await loadProgress();
    if (!progress) {
      await sendMessage(chatId, '📊 No forwarding data');
      return;
    }

    const sent = await sendMessage(chatId, formatProgressText(progress), {
      inline_keyboard: progressButtons(progress),
    });

    if (sent?.ok && sent?.result?.message_id) {
      await setUserSession(chatId, { last_message_id: sent.result.message_id });
    }
  }
  
  else if (command === '/status') {
    const config = await loadBotConfig();
    if (!config) {
      await sendMessage(chatId, '⚙️ Bot not configured. Use /setconfig or the Forward wizard.');
      return;
    }
    await sendMessage(chatId, `✅ <b>Bot Status</b>\n\n📤 Source: <code>${config.source_channel}</code>\n📥 Dest: <code>${config.dest_channel}</code>`);
  }
}

// Handle wizard state messages
async function handleWizardMessage(chatId: number, message: any) {
  const session = await getUserSession(chatId);
  if (!session || session.state === STATES.IDLE) return false;
  
  const text = message.text || '';
  
  if (text.toLowerCase() === '/cancel') {
    await clearUserSession(chatId);
    await sendMessage(chatId, '❌ Process cancelled.', {
      inline_keyboard: [[{ text: '🔙 Main Menu', callback_data: 'menu' }]]
    });
    return true;
  }
  
  if (session.state === STATES.WAITING_SOURCE) {
    const channelInfo = extractChannelFromMessage(message);
    if (!channelInfo) {
      await sendMessage(chatId, '❌ Invalid! Please forward a message from the source channel or paste a message link.');
      return true;
    }
    
    await setUserSession(chatId, {
      state: STATES.WAITING_SKIP,
      source_channel: channelInfo.chatId,
      source_title: channelInfo.title,
      last_message_id: channelInfo.lastMsgId,
    });
    
    await sendMessage(chatId,
      `<b>( SET MESSAGE SKIPPING NUMBER )</b>\n\n` +
      `Skip the message as much as you enter the number and the rest of the message will be forwarded\n` +
      `Default Skip Number = 0\n` +
      `eg: You enter 0 = 0 message skiped\n` +
      ` You enter 5 = 5 message skiped\n` +
      `/cancel - cancel this process`,
      { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'cancel' }]] }
    );
    return true;
  }
  
  if (session.state === STATES.WAITING_SKIP) {
    const skipNum = parseInt(text);
    if (isNaN(skipNum) || skipNum < 0) {
      await sendMessage(chatId, '❌ Please enter a valid number (0 or more).');
      return true;
    }
    
    await setUserSession(chatId, {
      state: STATES.WAITING_DEST,
      skip_number: skipNum,
    });
    
    await sendMessage(chatId,
      `<b>( SET DESTINATION CHAT )</b>\n\n` +
      `Forward any message from the destination channel where you want to forward messages.\n` +
      `/cancel - cancel this process`,
      { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'cancel' }]] }
    );
    return true;
  }
  
  if (session.state === STATES.WAITING_DEST) {
    const channelInfo = extractChannelFromMessage(message);
    if (!channelInfo) {
      await sendMessage(chatId, '❌ Invalid! Please forward a message from the destination channel or paste a message link.');
      return true;
    }
    
    await setUserSession(chatId, {
      state: STATES.CONFIRMING,
      dest_channel: channelInfo.chatId,
      dest_title: channelInfo.title,
    });
    
    const updatedSession = await getUserSession(chatId);
    
    const keyboard = {
      inline_keyboard: [
        [
          { text: '✅ Yes, Start', callback_data: 'confirm_forward' },
          { text: '❌ No, Cancel', callback_data: 'cancel' }
        ]
      ]
    };
    
    await sendMessage(chatId,
      `<b>📋 Forwarding Summary</b>\n\n` +
      `📤 <b>Source:</b> ${updatedSession?.source_title || 'Unknown'}\n` +
      `   ID: <code>${updatedSession?.source_channel}</code>\n\n` +
      `📥 <b>Destination:</b> ${updatedSession?.dest_title || 'Unknown'}\n` +
      `   ID: <code>${updatedSession?.dest_channel}</code>\n\n` +
      `⏭️ <b>Skip:</b> ${updatedSession?.skip_number} messages\n` +
      `📨 <b>Last Msg ID:</b> ${updatedSession?.last_message_id}\n\n` +
      `<b>Start forwarding?</b>`,
      keyboard
    );
    return true;
  }
  
  return false;
}

// Callback query handler
async function handleCallbackQuery(callbackQuery: any) {
  const chatId = callbackQuery.message.chat.id;
  const data = callbackQuery.data;
  const callbackQueryId = callbackQuery.id;

  await sendTelegramRequest('answerCallbackQuery', { callback_query_id: callbackQueryId });

  if (data === 'menu') {
    await clearUserSession(chatId);
    await showMainMenu(chatId);
  }
  else if (data === 'config') {
    await sendMessage(chatId, 
      `⚙️ <b>Set Configuration</b>\n\n` +
      `Use command:\n<code>/setconfig [source_channel] [dest_channel]</code>\n\n` +
      `Example:\n<code>/setconfig -1001234567890 -1009876543210</code>\n\n` +
      `<b>OR</b> use /forward for wizard-style setup!`,
      { inline_keyboard: [[{ text: '🔙 Main Menu', callback_data: 'menu' }]] }
    );
  }
  else if (data === 'forward') {
    await setUserSession(chatId, { state: STATES.WAITING_SOURCE });
    await sendMessage(chatId, 
      `<b>( SET SOURCE CHAT )</b>\n\n` +
      `Forward the last message or last message link of source chat.\n` +
      `/cancel - cancel this process`,
      { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'cancel' }]] }
    );
  }
  else if (data === 'cancel') {
    await clearUserSession(chatId);
    await sendMessage(chatId, '❌ Process cancelled.', {
      inline_keyboard: [[{ text: '🔙 Main Menu', callback_data: 'menu' }]]
    });
  }
  else if (data === 'confirm_forward') {
    const session = await getUserSession(chatId);
    if (!session || session.state !== STATES.CONFIRMING) {
      await sendMessage(chatId, '❌ Session expired. Please start again with /forward');
      return;
    }
    
    await saveBotConfig(session.source_channel, session.dest_channel);
    
    const endId = session.last_message_id;
    const startId = 1 + session.skip_number;
    
    await saveProgress({
      source_channel: session.source_channel,
      dest_channel: session.dest_channel,
      start_id: startId,
      end_id: endId,
      current_batch: 0,
      total_batches: Math.ceil((endId - startId + 1) / 100),
      success_count: 0,
      failed_count: 0,
      skipped_count: 0,
      total_count: endId - startId + 1,
      is_active: true,
      stop_requested: false,
      started_at: new Date().toISOString(),
      rate_limit_hits: 0,
      speed: 0,
    });
    
    await clearUserSession(chatId);
    await sendMessage(chatId, 
      `🚀 <b>Forwarding Started!</b>\n\n` +
      `📤 From: ${session.source_title}\n` +
      `📥 To: ${session.dest_title}\n` +
      `📨 Messages: ${startId} to ${endId}\n` +
      `⏭️ Skipping: ${session.skip_number} messages\n\n` +
      `Use /progress to check status\nUse /stop to stop`,
      { inline_keyboard: [
        [{ text: '📊 Progress', callback_data: 'progress' }, { text: '⏹️ Stop', callback_data: 'stop_forward' }]
      ]}
    );
    
    // Run forwarding in background
    bulkForward(session.source_channel, session.dest_channel, startId, endId, false, chatId);
  }
  else if (data === 'resume') {
    await handleCommand(chatId, '/resume', null);
  }
  else if (data === 'progress') {
    await handleCommand(chatId, '/progress', null);
  }
  else if (data === 'refresh_progress') {
    const messageId = callbackQuery.message.message_id;
    const progress = await loadProgress();
    if (!progress) {
      await editMessageText(chatId, messageId, '📊 No forwarding data');
      return;
    }

    await setUserSession(chatId, { last_message_id: messageId });

    await editMessageText(chatId, messageId, formatProgressText(progress), {
      inline_keyboard: progressButtons(progress),
    });
  }
  else if (data === 'status') {
    await handleCommand(chatId, '/status', null);
  }
  else if (data === 'stop' || data === 'stop_forward') {
    await requestStop();
    await sendMessage(chatId, '⏹️ Stop requested. Will stop after current batch.');
  }
  else if (data === 'help') {
    await sendMessage(chatId, 
      `❓ <b>Help</b>\n\n` +
      `<b>Commands:</b>\n` +
      `/start - Show menu\n` +
      `/forward - Wizard to set source & destination\n` +
      `/setconfig [source] [dest] - Set channels manually\n` +
      `/resume - Resume forwarding\n` +
      `/stop - Stop forwarding\n` +
      `/progress - Check progress\n` +
      `/status - Check bot status\n` +
      `/cancel - Cancel current process\n\n` +
      `<b>How to use:</b>\n` +
      `1. Click 🚀 Forward\n` +
      `2. Forward any message from source channel\n` +
      `3. Enter skip number (0 for all)\n` +
      `4. Forward any message from destination channel\n` +
      `5. Confirm and start!`,
      { inline_keyboard: [[{ text: '🔙 Main Menu', callback_data: 'menu' }]] }
    );
  }
}

// Webhook handler
async function handleWebhook(update: any) {
  console.log('Received Telegram update:', JSON.stringify(update, null, 2));
  
  if (update.callback_query) {
    await handleCallbackQuery(update.callback_query);
    return;
  }

  const message = update.message || update.channel_post;
  if (!message) return;

  const chatId = message.chat.id;
  const text = message.text || '';

  if (text.startsWith('/')) {
    await handleCommand(chatId, text, message);
    return;
  }
  
  const handled = await handleWizardMessage(chatId, message);
  if (handled) return;

  // Auto-forward from source channel
  const config = await loadBotConfig();
  if (config && message.chat.id.toString() === config.source_channel) {
    const hasMedia = message.photo || message.video || message.document || message.audio || message.voice || message.animation;
    if (hasMedia) {
      await copyMessages(config.source_channel, config.dest_channel, [message.message_id]);
      await saveForwardedMessageIds(config.source_channel, config.dest_channel, [message.message_id]);
    }
  }
}

// Express routes
app.get('/', (req, res) => {
  res.json({ status: 'ok', message: 'Telegram Forwarder Bot is running' });
});

app.get('/health', (req, res) => {
  res.json({ status: 'healthy' });
});

app.post('/webhook', async (req, res) => {
  try {
    await handleWebhook(req.body);
    res.json({ ok: true });
  } catch (error) {
    console.error('Webhook error:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Set webhook endpoint
app.get('/set-webhook', async (req, res) => {
  if (!WEBHOOK_URL) {
    return res.status(400).json({ error: 'WEBHOOK_URL not configured' });
  }
  
  const result = await sendTelegramRequest('setWebhook', {
    url: `${WEBHOOK_URL}/webhook`,
    allowed_updates: ['message', 'callback_query', 'channel_post'],
  });
  
  res.json(result);
});

// Delete webhook (for local testing)
app.get('/delete-webhook', async (req, res) => {
  const result = await sendTelegramRequest('deleteWebhook', {});
  res.json(result);
});

// Start server
app.listen(PORT, () => {
  console.log(`🚀 Server running on port ${PORT}`);
  console.log(`📡 Webhook endpoint: POST /webhook`);
  console.log(`🔗 Set webhook: GET /set-webhook`);
});
