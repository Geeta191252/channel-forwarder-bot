import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

const TELEGRAM_BOT_TOKEN = Deno.env.get('TELEGRAM_BOT_TOKEN');
const SUPABASE_URL = Deno.env.get('SUPABASE_URL');
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
const RUN_TOKEN = Deno.env.get('TELEGRAM_FORWARDER_RUN_TOKEN');

const supabase = createClient(SUPABASE_URL!, SUPABASE_SERVICE_ROLE_KEY!);
const TELEGRAM_API = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}`;
const FUNCTION_URL = `${SUPABASE_URL}/functions/v1/telegram-forwarder`;

// Self-call to continue forwarding (24/7 auto-run)
async function triggerContinue(sourceChannel: string, destChannel: string, startId: number, endId: number, chatId?: number) {
  console.log('Triggering self-continue...', { sourceChannel, destChannel, startId, endId });
  try {
    const response = await fetch(FUNCTION_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'auto-continue',
        token: RUN_TOKEN,
        sourceChannel,
        destChannel,
        startId,
        endId,
        chatId,
      }),
    });
    console.log('Self-continue triggered, status:', response.status);
    return response.ok;
  } catch (error) {
    console.error('Failed to trigger continue:', error);
    return false;
  }
}

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
      // NOTE: keep last_message_id so we can keep auto-updating the latest /progress message
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
  return result;
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

  // Calculate elapsed time
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

  // Calculate ETA (remaining time)
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

async function copyMessage(fromChatId: string, toChatId: string, messageId: number) {
  return sendTelegramRequest('copyMessage', {
    chat_id: toChatId,
    from_chat_id: fromChatId,
    message_id: messageId,
  });
}

// Track forwarded messages
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

// Extract channel info from message
function extractChannelFromMessage(message: any): { chatId: string; title: string; lastMsgId: number } | null {
  // Check if it's a forwarded message from a channel
  if (message.forward_from_chat) {
    const chat = message.forward_from_chat;
    const chatId = chat.id.toString();
    const title = chat.title || chat.username || 'Unknown';
    const lastMsgId = message.forward_from_message_id;
    return { chatId, title, lastMsgId };
  }
  
  // Check if it's a Telegram link
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

// Show main menu
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

    const task = bulkForward(progress.source_channel, progress.dest_channel, startId, progress.end_id, true, chatId);
    // Keep running after responding to Telegram
    // @ts-ignore - EdgeRuntime is available in Supabase Edge Functions
    if (typeof EdgeRuntime !== 'undefined' && EdgeRuntime?.waitUntil) EdgeRuntime.waitUntil(task);
    else await task;
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

    // Save the progress message id so we can auto-update the SAME message while forwarding runs
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
  
  // Handle cancel command
  if (text.toLowerCase() === '/cancel') {
    await clearUserSession(chatId);
    await sendMessage(chatId, '❌ Process cancelled.', {
      inline_keyboard: [[{ text: '🔙 Main Menu', callback_data: 'menu' }]]
    });
    return true;
  }
  
  // State: Waiting for source channel
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
      `<b>( SET MESSAGE SKIP ID )</b>\n\n` +
      `Aap jis message ID tak skip karna chahte ho, wahi ID enter karein.\n` +
      `Example: 291700 enter => 291701 se forwarding start hogi.\n` +
      `Default = 0 (start from 1)\n` +
      `/cancel - cancel this process`,
      { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'cancel' }]] }
    );
    return true;
  }
  
  // State: Waiting for skip number
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
  
  // State: Waiting for destination channel
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
    
    // Get updated session
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
      `⏭️ <b>Skip until ID:</b> ${updatedSession?.skip_number || 0}\n` +
      `📨 <b>Last Msg ID:</b> ${updatedSession?.last_message_id}\n\n` +
      `<b>Start forwarding?</b>`,
      keyboard
    );
    return true;
  }
  
  return false;
}

// Bulk forward with batching and rate limit handling
async function bulkForward(
  sourceChannel: string,
  destChannel: string,
  startId: number,
  endId: number,
  isResume: boolean,
  chatId?: number,
  runMaxMs: number = 25_000,
) {
  // Very Safe speed: 20 batch + 4s delay (~300 files/min) - for 24/7 continuous use
  const BATCH_SIZE = 20;
  const SAFE_DELAY_MS = 4000; // 4 second delay between batches for maximum safety
  const runStartedAt = Date.now();

  let currentId = startId;
  const existingProgress = isResume ? await loadProgress() : null;

  let successCount = isResume ? existingProgress?.success_count || 0 : 0;
  let failedCount = isResume ? existingProgress?.failed_count || 0 : 0;
  let skippedCount = isResume ? existingProgress?.skipped_count || 0 : 0;
  let rateLimitHits = isResume ? existingProgress?.rate_limit_hits || 0 : 0;
  let batchNum = isResume ? existingProgress?.current_batch || 0 : 0;

  // Use original started_at for consistent speed calculation
  const originalStartedAt = existingProgress?.started_at 
    ? new Date(existingProgress.started_at).getTime() 
    : Date.now();

  const totalBatches = Math.ceil((endId - startId + 1) / BATCH_SIZE);

  while (currentId <= endId) {
    // Check for stop request
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

    // Check for duplicates
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
        } else {
          // Fallback: if batch fails because some message IDs are deleted/missing,
          // try copying one-by-one so remaining messages still forward.
          const desc = (result.description || '').toLowerCase();
          const likelyMissing =
            desc.includes('message to copy not found') ||
            desc.includes('message_id_invalid') ||
            desc.includes('message identifier is not specified') ||
            desc.includes('no messages to forward');

          if (likelyMissing) {
            console.log('Batch copy failed due to missing/deleted msgs, falling back to single copy...', result);

            const forwardedOk: number[] = [];
            for (const id of toForward) {
              const single = await copyMessage(sourceChannel, destChannel, id);
              if (single?.ok) {
                successCount += 1;
                forwardedOk.push(id);
              } else {
                const sdesc = (single?.description || '').toLowerCase();
                const missing =
                  sdesc.includes('message to copy not found') ||
                  sdesc.includes('message_id_invalid') ||
                  sdesc.includes('no message') ||
                  sdesc.includes('no messages to forward');

                if (missing) skippedCount += 1;
                else failedCount += 1;
              }
            }

            if (forwardedOk.length) await saveForwardedMessageIds(sourceChannel, destChannel, forwardedOk);
            success = true;
          } else {
            failedCount += toForward.length;
            console.log('Forward failed:', result);
            success = true;
          }
        }
      }
    }

    batchNum++;
    currentId = batchEnd + 1;

    // Update progress
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

    // Time-slice: if we're nearing runtime limits, trigger self-call to continue
    if (currentId <= endId && Date.now() - runStartedAt > runMaxMs) {
      console.log('Time slice reached, triggering auto-continue...', { currentId, endId, batchNum });
      
      // Save progress before triggering continue
      const continueProgressPayload = {
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
        is_active: true,
        stop_requested: false,
        speed: Math.round(successCount / Math.max((Date.now() - originalStartedAt) / 60000, 0.001)),
        started_at: new Date(originalStartedAt).toISOString(),
      };

      await saveProgress(continueProgressPayload);
      if (chatId) await updateWatchedProgressMessage(chatId, continueProgressPayload);

      // Trigger self-call to continue (fire and forget)
      triggerContinue(sourceChannel, destChannel, currentId, endId, chatId);
      
      return { success: successCount, failed: failedCount, needsResume: false, continued: true };
    }
  }

  // Complete
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

// Callback query handler for buttons
async function handleCallbackQuery(callbackQuery: any) {
  const chatId = callbackQuery.message.chat.id;
  const data = callbackQuery.data;
  const callbackQueryId = callbackQuery.id;

  // Answer callback to remove loading state
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
    
    // Save config
    await saveBotConfig(session.source_channel, session.dest_channel);
    
    // Calculate start message ID
    // User enters the LAST skipped message ID (e.g. 291700 => start forwarding from 291701)
    const endId = session.last_message_id;
    const startId = session.skip_number > 0 ? (session.skip_number + 1) : 1;
    
    // Start forwarding
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
    
    await sendMessage(chatId, 
      `🚀 <b>Forwarding Started!</b>\n\n` +
      `📤 From: ${session.source_title}\n` +
      `📥 To: ${session.dest_title}\n` +
      `📨 Messages: ${startId} to ${endId}\n` +
      `⏭️ Skipped until (ID): ${session.skip_number || 0}\n\n` +
      `Use /progress to check status\nUse /stop to stop`,
      { inline_keyboard: [
        [{ text: '📊 Progress', callback_data: 'progress' }, { text: '⏹️ Stop', callback_data: 'stop_forward' }]
      ]}
    );
    
    // Start forwarding in background (so user doesn't need to press Resume repeatedly)
    const task = bulkForward(session.source_channel, session.dest_channel, startId, endId, false, chatId);
    // @ts-ignore - EdgeRuntime is available in Supabase Edge Functions
    if (typeof EdgeRuntime !== 'undefined' && EdgeRuntime?.waitUntil) EdgeRuntime.waitUntil(task);
    else await task;
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
  
  // Handle callback queries (button clicks)
  if (update.callback_query) {
    await handleCallbackQuery(update.callback_query);
    return;
  }

  const message = update.message || update.channel_post;
  if (!message) return;

  const chatId = message.chat.id;
  const text = message.text || '';

  // Handle commands first
  if (text.startsWith('/')) {
    await handleCommand(chatId, text, message);
    return;
  }
  
  // Handle wizard state messages
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

// Main handler
serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    const body = await req.json().catch(() => ({}));

    // Telegram webhook
    if (body.update_id !== undefined) {
      console.log('Handling Telegram webhook update');
      await handleWebhook(body);
      return new Response(JSON.stringify({ ok: true }), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    // API actions
    const { action, sourceChannel, destChannel, startMessageId, endMessageId, token, startId, endId, chatId: bodyChatId } = body;
    console.log('Received action:', action, { sourceChannel, destChannel, startMessageId, endMessageId });

    if (!action) {
      return new Response(JSON.stringify({ ok: true }), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    // Auto-continue action (secured by token) - for 24/7 operation
    if (action === 'auto-continue') {
      if (token !== RUN_TOKEN) {
        console.log('Invalid token for auto-continue');
        return new Response(JSON.stringify({ error: 'Unauthorized' }), { status: 401, headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
      }
      
      console.log('Auto-continue triggered', { sourceChannel, startId, endId });
      
      // Check if stop was requested
      if (await isStopRequested()) {
        console.log('Stop was requested, not continuing');
        return new Response(JSON.stringify({ stopped: true }), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
      }
      
      // Run in background
      const task = bulkForward(sourceChannel, destChannel, startId, endId, true, bodyChatId);
      // @ts-ignore
      if (typeof EdgeRuntime !== 'undefined' && EdgeRuntime?.waitUntil) EdgeRuntime.waitUntil(task);
      
      return new Response(JSON.stringify({ ok: true, continuing: true }), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    if (action === 'configure') {
      await saveBotConfig(sourceChannel, destChannel);
      return new Response(JSON.stringify({ success: true }), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    if (action === 'bulk-forward') {
      const config = await loadBotConfig();
      if (!config) {
        return new Response(JSON.stringify({ error: 'Not configured' }), { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
      }
      
      await saveProgress({
        source_channel: config.source_channel,
        dest_channel: config.dest_channel,
        start_id: startMessageId,
        end_id: endMessageId,
        current_batch: 0,
        total_batches: Math.ceil((endMessageId - startMessageId + 1) / 100),
        success_count: 0,
        failed_count: 0,
        skipped_count: 0,
        total_count: endMessageId - startMessageId + 1,
        is_active: true,
        stop_requested: false,
        started_at: new Date().toISOString(),
        rate_limit_hits: 0,
        speed: 0,
      });

      const result = await bulkForward(config.source_channel, config.dest_channel, startMessageId, endMessageId, false);
      return new Response(JSON.stringify(result), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    if (action === 'stop') {
      await requestStop();
      return new Response(JSON.stringify({ success: true }), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    if (action === 'progress') {
      const progress = await loadProgress();
      return new Response(JSON.stringify(progress || {}), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    if (action === 'status') {
      const config = await loadBotConfig();
      const progress = await loadProgress();
      return new Response(JSON.stringify({ config, progress }), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    if (action === 'set-webhook') {
      const webhookUrl = body.webhookUrl;
      console.log('Setting Telegram webhook to:', webhookUrl);
      const result = await sendTelegramRequest('setWebhook', {
        url: webhookUrl,
        allowed_updates: ['message', 'channel_post', 'callback_query'],
        drop_pending_updates: true,
      });
      console.log('setWebhook result:', result);
      return new Response(JSON.stringify(result), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    if (action === 'set-webhook-auto') {
      const webhookUrl = `${SUPABASE_URL}/functions/v1/telegram-forwarder`;
      console.log('Setting Telegram webhook (auto) to:', webhookUrl);
      const result = await sendTelegramRequest('setWebhook', {
        url: webhookUrl,
        allowed_updates: ['message', 'channel_post', 'callback_query'],
        drop_pending_updates: true,
      });
      console.log('setWebhook(auto) result:', result);
      return new Response(JSON.stringify({ ...result, webhookUrl }), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    if (action === 'delete-webhook') {
      console.log('Deleting Telegram webhook');
      const result = await sendTelegramRequest('deleteWebhook', { drop_pending_updates: true });
      console.log('deleteWebhook result:', result);
      return new Response(JSON.stringify(result), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    if (action === 'webhook-info') {
      console.log('Fetching Telegram webhook info');
      const result = await sendTelegramRequest('getWebhookInfo', {});
      console.log('getWebhookInfo result:', result);
      return new Response(JSON.stringify(result), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    return new Response(JSON.stringify({ error: 'Unknown action' }), { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } });

  } catch (error: unknown) {
    console.error('Error:', error);
    const message = error instanceof Error ? error.message : 'Unknown error';
    return new Response(JSON.stringify({ error: message }), { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
  }
});
