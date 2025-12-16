import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

const TELEGRAM_BOT_TOKEN = Deno.env.get('TELEGRAM_BOT_TOKEN');
const SUPABASE_URL = Deno.env.get('SUPABASE_URL');
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');

const supabase = createClient(SUPABASE_URL!, SUPABASE_SERVICE_ROLE_KEY!);
const TELEGRAM_API = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}`;

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

async function sendMessage(chatId: string | number, text: string) {
  return sendTelegramRequest('sendMessage', { chat_id: chatId, text, parse_mode: 'HTML' });
}

async function copyMessages(fromChatId: string, toChatId: string, messageIds: number[]) {
  return sendTelegramRequest('copyMessages', {
    chat_id: toChatId,
    from_chat_id: fromChatId,
    message_ids: messageIds,
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

// Command handlers
async function handleCommand(chatId: number, text: string) {
  const parts = text.split(' ');
  const command = parts[0].toLowerCase().replace('@', '').split('@')[0];

  if (command === '/start') {
    await sendMessage(chatId, `🤖 <b>Telegram Forwarder Bot</b>\n\n` +
      `Commands:\n` +
      `/setconfig [source] [dest] - Set channels\n` +
      `/forward [start] [end] - Forward messages\n` +
      `/resume - Resume forwarding\n` +
      `/stop - Stop forwarding\n` +
      `/progress - Check progress\n` +
      `/status - Check bot status`);
  }
  
  else if (command === '/setconfig') {
    if (parts.length < 3) {
      await sendMessage(chatId, '❌ Usage: /setconfig [source_channel] [dest_channel]\nExample: /setconfig -1001234567890 -1009876543210');
      return;
    }
    const source = parts[1];
    const dest = parts[2];
    await saveBotConfig(source, dest);
    await sendMessage(chatId, `✅ Config saved!\n\n📤 Source: ${source}\n📥 Destination: ${dest}`);
  }
  
  else if (command === '/forward') {
    const config = await loadBotConfig();
    if (!config) {
      await sendMessage(chatId, '❌ Please set config first with /setconfig');
      return;
    }
    
    if (parts.length < 3) {
      await sendMessage(chatId, '❌ Usage: /forward [start_id] [end_id]\nExample: /forward 1 1000');
      return;
    }
    
    const startId = parseInt(parts[1]);
    const endId = parseInt(parts[2]);
    
    if (isNaN(startId) || isNaN(endId) || startId > endId) {
      await sendMessage(chatId, '❌ Invalid message IDs');
      return;
    }
    
    await saveProgress({
      source_channel: config.source_channel,
      dest_channel: config.dest_channel,
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
    
    await sendMessage(chatId, `🚀 Starting forward: ${startId} to ${endId}\n⚠️ Use /resume if it stops.`);
    
    // Start forwarding in background
    bulkForward(config.source_channel, config.dest_channel, startId, endId, false, chatId);
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
    
    const percent = progress.total_count ? Math.round((progress.success_count / progress.total_count) * 100) : 0;
    const status = progress.is_active ? (progress.stop_requested ? '⏸️ Stopping' : '🔄 Running') : '✅ Complete';
    
    await sendMessage(chatId, 
      `📊 <b>Progress</b> ${status}\n\n` +
      `✅ Success: ${progress.success_count} / ${progress.total_count} (${percent}%)\n` +
      `❌ Failed: ${progress.failed_count}\n` +
      `⚡ Rate limits: ${progress.rate_limit_hits}\n` +
      `🚀 Speed: ${progress.speed} files/min\n` +
      `📦 Batch: ${progress.current_batch} / ${progress.total_batches}`
    );
  }
  
  else if (command === '/status') {
    const config = await loadBotConfig();
    if (!config) {
      await sendMessage(chatId, '⚙️ Bot not configured. Use /setconfig');
      return;
    }
    await sendMessage(chatId, `✅ <b>Bot Status</b>\n\n📤 Source: ${config.source_channel}\n📥 Dest: ${config.dest_channel}`);
  }
}

// Bulk forward with batching and rate limit handling
async function bulkForward(sourceChannel: string, destChannel: string, startId: number, endId: number, isResume: boolean, chatId?: number) {
  const BATCH_SIZE = 100;
  let currentId = startId;
  let successCount = isResume ? (await loadProgress())?.success_count || 0 : 0;
  let failedCount = isResume ? (await loadProgress())?.failed_count || 0 : 0;
  let skippedCount = isResume ? (await loadProgress())?.skipped_count || 0 : 0;
  let rateLimitHits = isResume ? (await loadProgress())?.rate_limit_hits || 0 : 0;
  let batchNum = isResume ? (await loadProgress())?.current_batch || 0 : 0;
  const totalBatches = Math.ceil((endId - startId + 1) / BATCH_SIZE);
  const startTime = Date.now();

  while (currentId <= endId) {
    // Check for stop request
    if (await isStopRequested()) {
      console.log('Stop requested, saving progress...');
      await saveProgress({
        current_batch: batchNum,
        success_count: successCount,
        failed_count: failedCount,
        skipped_count: skippedCount,
        rate_limit_hits: rateLimitHits,
        is_active: true,
        stop_requested: true,
        speed: Math.round(successCount / ((Date.now() - startTime) / 60000)),
      });
      if (chatId) await sendMessage(chatId, `⏹️ Stopped at batch ${batchNum}. Use /resume to continue.`);
      return { success: successCount, failed: failedCount, needsResume: true };
    }

    const batchEnd = Math.min(currentId + BATCH_SIZE - 1, endId);
    const messageIds = Array.from({ length: batchEnd - currentId + 1 }, (_, i) => currentId + i);
    
    // Check for duplicates
    const alreadyForwarded = await getForwardedMessageIds(sourceChannel, destChannel, messageIds);
    const toForward = messageIds.filter(id => !alreadyForwarded.includes(id));
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
          await new Promise(r => setTimeout(r, waitTime * 1000));
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
    
    // Update progress
    const elapsed = (Date.now() - startTime) / 60000;
    const speed = Math.round(successCount / elapsed);
    
    await saveProgress({
      current_batch: batchNum,
      total_batches: totalBatches,
      success_count: successCount,
      failed_count: failedCount,
      skipped_count: skippedCount,
      total_count: endId - startId + 1,
      rate_limit_hits: rateLimitHits,
      is_active: currentId <= endId,
      speed: speed,
    });

    // No delay - maximum speed
  }

  // Complete
  await saveProgress({
    current_batch: batchNum,
    total_batches: totalBatches,
    success_count: successCount,
    failed_count: failedCount,
    skipped_count: skippedCount,
    is_active: false,
    speed: 0,
  });

  if (chatId) {
    await sendMessage(chatId, `✅ <b>Forwarding Complete!</b>\n\n✅ Success: ${successCount}\n❌ Failed: ${failedCount}\n⏭️ Skipped: ${skippedCount}`);
  }

  return { success: successCount, failed: failedCount, needsResume: false };
}

// Webhook handler
async function handleWebhook(update: any) {
  console.log('Received Telegram update:', JSON.stringify(update, null, 2));
  
  const message = update.message || update.channel_post;
  if (!message) return;

  const chatId = message.chat.id;
  const text = message.text || '';

  // Handle commands
  if (text.startsWith('/')) {
    await handleCommand(chatId, text);
    return;
  }

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
    const url = new URL(req.url);
    const body = await req.json().catch(() => ({}));

    // Telegram webhook
    if (body.update_id !== undefined) {
      console.log('Handling Telegram webhook update');
      await handleWebhook(body);
      return new Response(JSON.stringify({ ok: true }), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    // API actions
    const { action, sourceChannel, destChannel, startMessageId, endMessageId } = body;
    console.log('Received action:', action, { sourceChannel, destChannel, startMessageId, endMessageId });

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

      // Start in background
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
      const result = await sendTelegramRequest('setWebhook', { url: webhookUrl });
      return new Response(JSON.stringify(result), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
    }

    return new Response(JSON.stringify({ error: 'Unknown action' }), { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } });

  } catch (error: unknown) {
    console.error('Error:', error);
    const message = error instanceof Error ? error.message : 'Unknown error';
    return new Response(JSON.stringify({ error: message }), { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
  }
});
