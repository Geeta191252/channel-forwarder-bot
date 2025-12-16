import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

const TELEGRAM_BOT_TOKEN = Deno.env.get('TELEGRAM_BOT_TOKEN');
const TELEGRAM_API = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}`;

const supabaseUrl = Deno.env.get('SUPABASE_URL')!;
const supabaseKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const supabase = createClient(supabaseUrl, supabaseKey);

// Load config from database
async function loadBotConfig(): Promise<{ sourceChannel: string; destChannel: string } | null> {
  const { data, error } = await supabase
    .from('bot_config')
    .select('source_channel, dest_channel')
    .maybeSingle();
  
  if (error || !data) {
    console.log('No bot config found in database');
    return null;
  }
  
  return { sourceChannel: data.source_channel, destChannel: data.dest_channel };
}

// Save config to database
async function saveBotConfig(sourceChannel: string, destChannel: string): Promise<boolean> {
  const { error } = await supabase
    .from('bot_config')
    .upsert({ 
      source_channel: sourceChannel, 
      dest_channel: destChannel,
      updated_at: new Date().toISOString()
    }, { onConflict: 'id' });
  
  if (error) {
    console.error('Error saving bot config:', error);
    return false;
  }
  return true;
}

// Load progress from database
async function loadProgress() {
  const { data, error } = await supabase
    .from('forwarding_progress')
    .select('*')
    .eq('id', 'current')
    .maybeSingle();
  
  if (error || !data) {
    return null;
  }
  return data;
}

// Save progress to database
async function saveProgress(progress: {
  is_active: boolean;
  source_channel?: string;
  dest_channel?: string;
  start_id?: number;
  end_id?: number;
  current_batch?: number;
  total_batches?: number;
  success_count?: number;
  failed_count?: number;
  skipped_count?: number;
  total_count?: number;
  rate_limit_hits?: number;
  speed?: number;
  started_at?: string;
  stop_requested?: boolean;
}) {
  const { error } = await supabase
    .from('forwarding_progress')
    .upsert({ 
      id: 'current',
      ...progress,
      last_updated_at: new Date().toISOString()
    }, { onConflict: 'id' });
  
  if (error) {
    console.error('Error saving progress:', error);
  }
}

// Check if stop was requested
async function isStopRequested(): Promise<boolean> {
  const progress = await loadProgress();
  return progress?.stop_requested === true;
}

// Request stop
async function requestStop() {
  await saveProgress({ is_active: true, stop_requested: true });
}

async function sendTelegramRequest(method: string, params: Record<string, unknown>) {
  console.log(`Calling Telegram API: ${method}`, params);
  
  const response = await fetch(`${TELEGRAM_API}/${method}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  
  const data = await response.json();
  console.log(`Telegram API response for ${method}:`, data);
  
  return data;
}

async function forwardMessage(fromChatId: string, toChatId: string, messageId: number) {
  return sendTelegramRequest('forwardMessage', {
    chat_id: toChatId,
    from_chat_id: fromChatId,
    message_id: messageId,
  });
}

// Batch forward up to 100 messages at once
async function copyMessages(fromChatId: string, toChatId: string, messageIds: number[]) {
  return sendTelegramRequest('copyMessages', {
    chat_id: toChatId,
    from_chat_id: fromChatId,
    message_ids: messageIds,
  });
}

// Get already forwarded message IDs from database
async function getForwardedMessageIds(
  sourceChannel: string, 
  destChannel: string, 
  messageIds: number[]
): Promise<Set<number>> {
  const { data, error } = await supabase
    .from('forwarded_messages')
    .select('source_message_id')
    .eq('source_channel', sourceChannel)
    .eq('dest_channel', destChannel)
    .in('source_message_id', messageIds);
  
  if (error) {
    console.error('Error fetching forwarded messages:', error);
    return new Set();
  }
  
  return new Set(data?.map(row => row.source_message_id) || []);
}

// Save forwarded message IDs to database
async function saveForwardedMessageIds(
  sourceChannel: string, 
  destChannel: string, 
  messageIds: number[]
): Promise<void> {
  const records = messageIds.map(id => ({
    source_channel: sourceChannel,
    dest_channel: destChannel,
    source_message_id: id,
  }));
  
  const { error } = await supabase
    .from('forwarded_messages')
    .upsert(records, { onConflict: 'source_channel,dest_channel,source_message_id' });
  
  if (error) {
    console.error('Error saving forwarded messages:', error);
  }
}

interface TelegramMessage {
  message_id: number;
  chat?: { id: number };
  text?: string;
  document?: { file_name?: string };
  photo?: unknown;
  video?: unknown;
  audio?: unknown;
  voice?: unknown;
  video_note?: unknown;
  animation?: unknown;
  sticker?: unknown;
}

interface TelegramUpdate {
  message?: TelegramMessage;
  channel_post?: TelegramMessage;
}

// Send message to Telegram chat
async function sendMessage(chatId: string, text: string, parseMode = 'HTML') {
  return sendTelegramRequest('sendMessage', {
    chat_id: chatId,
    text,
    parse_mode: parseMode,
  });
}

// Handle bot commands from Telegram
async function handleCommand(chatId: string, text: string): Promise<string> {
  const parts = text.trim().split(/\s+/);
  const command = parts[0].toLowerCase().replace('@', '').split('@')[0];
  const args = parts.slice(1);

  switch (command) {
    case '/start':
    case '/help':
      return `🤖 <b>Telegram File Forwarder Bot</b>

<b>Commands:</b>
/setupwebhook - Auto setup webhook (run once)
/setconfig &lt;source&gt; &lt;dest&gt; - Set source and destination channels
/forward &lt;start&gt; &lt;end&gt; - Forward messages from start to end ID
/resume - Continue from where it stopped
/status - Show current configuration
/stop - Stop current forwarding
/progress - Show live progress

<b>Example:</b>
<code>/setconfig -1001234567890 -1009876543210</code>
<code>/forward 1 10000</code>`;

    case '/setupwebhook':
      const webhookUrl = `https://wqspxhsjujakaldaxhvm.supabase.co/functions/v1/telegram-forwarder`;
      const result = await sendTelegramRequest('setWebhook', {
        url: webhookUrl,
        allowed_updates: ['message', 'channel_post'],
      });
      if (result.ok) {
        return `✅ Webhook set successfully!
🔗 URL: <code>${webhookUrl}</code>

Now use /setconfig to configure channels.`;
      } else {
        return `❌ Failed to set webhook: ${result.description || 'Unknown error'}`;
      }

    case '/setconfig':
      if (args.length < 2) {
        return '❌ Usage: /setconfig &lt;source_channel_id&gt; &lt;dest_channel_id&gt;';
      }
      const configSaved = await saveBotConfig(args[0], args[1]);
      if (!configSaved) {
        return '❌ Failed to save configuration. Please try again.';
      }
      return `✅ Configuration saved!
📤 Source: <code>${args[0]}</code>
📥 Destination: <code>${args[1]}</code>`;

    case '/status':
      const statusConfig = await loadBotConfig();
      if (!statusConfig) {
        return '⚠️ Bot not configured. Use /setconfig first.';
      }
      const statusProgress = await loadProgress();
      let statusText = '💤 Idle';
      if (statusProgress?.is_active) {
        const elapsed = statusProgress.started_at 
          ? (Date.now() - new Date(statusProgress.started_at).getTime()) / 60000 
          : 1;
        const speed = Math.round((statusProgress.success_count || 0) / elapsed);
        statusText = `🔄 Forwarding in progress...
✅ Success: ${statusProgress.success_count || 0}
❌ Failed: ${statusProgress.failed_count || 0}
⏱️ Speed: ${speed} files/min`;
      }
      return `📊 <b>Bot Status</b>

📤 Source: <code>${statusConfig.sourceChannel}</code>
📥 Destination: <code>${statusConfig.destChannel}</code>

${statusText}`;

    case '/forward':
      const forwardConfig = await loadBotConfig();
      if (!forwardConfig) {
        return '⚠️ Bot not configured. Use /setconfig first.';
      }
      if (args.length < 2) {
        return '❌ Usage: /forward &lt;start_id&gt; &lt;end_id&gt;';
      }
      const startId = parseInt(args[0]);
      const endId = parseInt(args[1]);
      if (isNaN(startId) || isNaN(endId)) {
        return '❌ Invalid message IDs';
      }
      
      // Start forwarding in background
      sendMessage(chatId, `🚀 Starting forward: ${startId} to ${endId} (${endId - startId + 1} messages)\n\n⚠️ Use /resume if it stops.`);
      
      // Run async without waiting
      bulkForward(forwardConfig.sourceChannel, forwardConfig.destChannel, startId, endId, false)
        .then(result => {
          if (result.needsResume) {
            sendMessage(chatId, `⏸️ Batch complete! Use /resume to continue.\n📊 Progress: ${result.success}/${result.total}`);
          } else {
            sendMessage(chatId, `✅ Forwarding complete!\n📊 Success: ${result.success}\n❌ Failed: ${result.failed}`);
          }
        })
        .catch(err => {
          sendMessage(chatId, `❌ Error: ${err.message}`);
        });
      
      return ''; // Already sent initial message

    case '/resume':
      const resumeConfig = await loadBotConfig();
      if (!resumeConfig) {
        return '⚠️ Bot not configured. Use /setconfig first.';
      }
      const existingProgress = await loadProgress();
      if (!existingProgress || !existingProgress.start_id || !existingProgress.end_id) {
        return '💤 No forwarding to resume. Use /forward first.';
      }
      if (existingProgress.current_batch >= existingProgress.total_batches) {
        return '✅ Forwarding already complete!';
      }
      
      sendMessage(chatId, `🔄 Resuming from batch ${existingProgress.current_batch || 0}/${existingProgress.total_batches}...`);
      
      bulkForward(resumeConfig.sourceChannel, resumeConfig.destChannel, existingProgress.start_id, existingProgress.end_id, true)
        .then(result => {
          if (result.needsResume) {
            sendMessage(chatId, `⏸️ Batch complete! Use /resume to continue.\n📊 Progress: ${result.success}/${result.total}`);
          } else {
            sendMessage(chatId, `✅ Forwarding complete!\n📊 Success: ${result.success}\n❌ Failed: ${result.failed}`);
          }
        })
        .catch(err => {
          sendMessage(chatId, `❌ Error: ${err.message}`);
        });
      
      return '';

    case '/stop':
      await requestStop();
      return '🛑 Stop signal sent. Forwarding will stop after current batch.';

    case '/progress':
      const progressData = await loadProgress();
      if (!progressData || (!progressData.is_active && progressData.current_batch === 0)) {
        return '💤 No forwarding in progress.';
      }
      const elapsedMs = progressData.started_at 
        ? Date.now() - new Date(progressData.started_at).getTime() 
        : 0;
      const elapsedSec = elapsedMs / 1000;
      const speed = elapsedSec > 0 ? Math.round((progressData.success_count || 0) / elapsedSec * 60) : 0;
      const percent = progressData.total_count > 0 
        ? Math.round(((progressData.success_count || 0) / progressData.total_count) * 100) 
        : 0;
      const progressStatusEmoji = progressData.is_active ? '🔄' : '⏸️';
      const progressStatusText = progressData.is_active ? 'Running' : 'Paused (use /resume)';
      return `📊 <b>Progress</b> ${progressStatusEmoji} ${progressStatusText}

✅ Success: ${progressData.success_count || 0} / ${progressData.total_count || 0} (${percent}%)
❌ Failed: ${progressData.failed_count || 0}
⚡ Rate limits: ${progressData.rate_limit_hits || 0}
🚀 Speed: ${speed} files/min
📦 Batch: ${progressData.current_batch || 0} / ${progressData.total_batches || 0}
⏱️ Elapsed: ${Math.round(elapsedSec)}s`;

    default:
      return '';
  }
}

async function handleWebhook(update: TelegramUpdate) {
  console.log('Received Telegram update:', JSON.stringify(update, null, 2));

  const message = update.message || update.channel_post;
  if (!message) {
    console.log('No message in update');
    return { ok: true };
  }

  const chatId = String(message.chat?.id);
  const messageId = message.message_id;
  const text = message.text || '';

  // Handle bot commands (from any chat)
  if (text.startsWith('/')) {
    const response = await handleCommand(chatId, text);
    if (response) {
      await sendMessage(chatId, response);
    }
    return { ok: true, command: true };
  }

  // Auto-forward files from source channel
  const webhookConfig = await loadBotConfig();
  if (!webhookConfig) {
    console.log('Bot not configured yet');
    return { ok: true, message: 'Bot not configured' };
  }

  // Check if message is from source channel
  if (chatId !== webhookConfig.sourceChannel) {
    console.log(`Message from ${chatId} ignored, not source channel ${webhookConfig.sourceChannel}`);
    return { ok: true };
  }

  // Check if message contains a file
  const hasFile = message.document || message.photo || message.video || 
                  message.audio || message.voice || message.video_note ||
                  message.animation || message.sticker;

  if (hasFile) {
    const alreadyForwarded = await getForwardedMessageIds(
      webhookConfig.sourceChannel, 
      webhookConfig.destChannel, 
      [messageId]
    );
    
    if (alreadyForwarded.has(messageId)) {
      console.log(`Message ${messageId} already forwarded, skipping`);
      return { ok: true, skipped: true };
    }
    
    console.log(`Forwarding file from ${webhookConfig.sourceChannel} to ${webhookConfig.destChannel}`);
    const result = await forwardMessage(webhookConfig.sourceChannel, webhookConfig.destChannel, messageId);
    
    if (result.ok) {
      await saveForwardedMessageIds(webhookConfig.sourceChannel, webhookConfig.destChannel, [messageId]);
    }
    
    return { ok: result.ok, forwarded: true };
  }

  return { ok: true };
}

async function setWebhook(webhookUrl: string) {
  return sendTelegramRequest('setWebhook', {
    url: webhookUrl,
    allowed_updates: ['message', 'channel_post'],
  });
}

async function getWebhookInfo() {
  return sendTelegramRequest('getWebhookInfo', {});
}

// Local progress tracking for current execution
let localProgress = {
  rateLimitHits: 0,
  success: 0,
  failed: 0,
};

// Copy messages with retry on rate limit
async function copyMessagesWithRetry(
  fromChatId: string, 
  toChatId: string, 
  messageIds: number[],
  maxRetries = 3
): Promise<{ ok: boolean; count: number }> {
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    const result = await sendTelegramRequest('copyMessages', {
      chat_id: toChatId,
      from_chat_id: fromChatId,
      message_ids: messageIds,
    });
    
    if (result.ok) {
      return { ok: true, count: result.result?.length || messageIds.length };
    }
    
    // Rate limited - wait and retry
    if (result.error_code === 429) {
      localProgress.rateLimitHits++;
      const waitTime = (result.parameters?.retry_after || 5) * 1000;
      console.log(`Rate limited (${localProgress.rateLimitHits} total), waiting ${waitTime/1000}s...`);
      await new Promise(resolve => setTimeout(resolve, waitTime));
      continue;
    }
    
    // Other error - don't retry
    return { ok: false, count: 0 };
  }
  return { ok: false, count: 0 };
}

// Bulk forward messages - limited batches per call to avoid timeout
const MAX_GROUPS_PER_CALL = 3; // Process 3 groups (30 batches = 3000 messages) per call

async function bulkForward(
  sourceChannel: string, 
  destChannel: string, 
  startId: number, 
  endId: number,
  isResume: boolean = false
): Promise<{ success: number; failed: number; skipped: number; total: number; stopped: boolean; rateLimitHits: number; needsResume: boolean }> {
  const total = endId - startId + 1;
  
  // Load existing progress if resuming
  let existingProgress = isResume ? await loadProgress() : null;
  let startBatchIndex = 0;
  
  // Reset local progress
  localProgress = { rateLimitHits: 0, success: 0, failed: 0 };
  
  if (isResume && existingProgress) {
    startBatchIndex = existingProgress.current_batch || 0;
    localProgress.success = existingProgress.success_count || 0;
    localProgress.failed = existingProgress.failed_count || 0;
    localProgress.rateLimitHits = existingProgress.rate_limit_hits || 0;
    console.log(`Resuming from batch ${startBatchIndex}`);
  } else {
    // Init progress in database
    await saveProgress({
      is_active: true,
      source_channel: sourceChannel,
      dest_channel: destChannel,
      start_id: startId,
      end_id: endId,
      current_batch: 0,
      total_batches: 0,
      success_count: 0,
      failed_count: 0,
      skipped_count: 0,
      total_count: total,
      rate_limit_hits: 0,
      speed: 0,
      started_at: new Date().toISOString(),
      stop_requested: false,
    });
  }
  
  console.log(`Forwarding ${total} messages`);
  
  // Telegram limit: 100 messages per copyMessages call
  const batchSize = 100;
  const parallelBatches = 10;
  
  // Create all batches
  const batches: number[][] = [];
  for (let i = startId; i <= endId; i += batchSize) {
    const batch: number[] = [];
    for (let j = i; j < Math.min(i + batchSize, endId + 1); j++) {
      batch.push(j);
    }
    batches.push(batch);
  }
  
  const totalBatches = batches.length;
  if (!isResume) {
    await saveProgress({ is_active: true, total_batches: totalBatches });
  }
  console.log(`Total batches: ${totalBatches}, starting from ${startBatchIndex}`);
  
  let groupsProcessed = 0;
  
  // Process batches in parallel groups, starting from saved position
  for (let i = startBatchIndex; i < batches.length; i += parallelBatches) {
    // Check stop flag from database
    if (await isStopRequested()) {
      console.log('Stopped by user');
      await saveProgress({ is_active: false, stop_requested: false });
      return { 
        success: localProgress.success, 
        failed: localProgress.failed, 
        skipped: 0, 
        total, 
        stopped: true,
        rateLimitHits: localProgress.rateLimitHits,
        needsResume: false
      };
    }
    
    // Limit groups per call to avoid timeout
    if (groupsProcessed >= MAX_GROUPS_PER_CALL) {
      console.log(`Processed ${groupsProcessed} groups, pausing for resume`);
      await saveProgress({ is_active: false });
      return { 
        success: localProgress.success, 
        failed: localProgress.failed, 
        skipped: 0, 
        total, 
        stopped: false,
        rateLimitHits: localProgress.rateLimitHits,
        needsResume: true
      };
    }
    
    const currentBatch = Math.min(i + parallelBatches, batches.length);
    const group = batches.slice(i, i + parallelBatches);
    console.log(`Group ${Math.floor(i / parallelBatches) + 1}/${Math.ceil(batches.length / parallelBatches)}`);
    
    const results = await Promise.all(
      group.map(async (batch) => {
        const result = await copyMessagesWithRetry(sourceChannel, destChannel, batch);
        if (result.ok) {
          saveForwardedMessageIds(sourceChannel, destChannel, batch);
          return { success: batch.length, failed: 0 };
        }
        return { success: 0, failed: batch.length };
      })
    );
    
    for (const r of results) {
      localProgress.success += r.success;
      localProgress.failed += r.failed;
    }
    
    groupsProcessed++;
    
    // Update progress in database every group
    await saveProgress({
      is_active: true,
      current_batch: currentBatch,
      success_count: localProgress.success,
      failed_count: localProgress.failed,
      rate_limit_hits: localProgress.rateLimitHits,
    });
  }
  
  // Mark as complete
  await saveProgress({ is_active: false, stop_requested: false });
  
  return { 
    success: localProgress.success, 
    failed: localProgress.failed, 
    skipped: 0, 
    total, 
    stopped: false,
    rateLimitHits: localProgress.rateLimitHits,
    needsResume: false
  };
}

serve(async (req) => {
  // Handle CORS preflight
  if (req.method === 'OPTIONS') {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    // Handle configuration and other requests
    const body = await req.json();
    
    // Check if this is a Telegram webhook update (has message or channel_post)
    if (body.message || body.channel_post || body.update_id !== undefined) {
      console.log('Handling Telegram webhook update');
      const result = await handleWebhook(body);
      return new Response(JSON.stringify(result), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }
    
    const { action, sourceChannel, destChannel, webhookUrl, startMessageId, endMessageId } = body;

    console.log('Received action:', action, { sourceChannel, destChannel, startMessageId, endMessageId });

    switch (action) {
      case 'configure':
        if (!sourceChannel || !destChannel) {
          return new Response(
            JSON.stringify({ error: 'Missing channel IDs' }),
            { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
          );
        }
        
        const saved = await saveBotConfig(sourceChannel, destChannel);
        console.log('Bot configured:', { sourceChannel, destChannel });
        
        return new Response(
          JSON.stringify({ 
            success: saved, 
            message: saved ? 'Bot configured successfully' : 'Failed to save config',
            config: { sourceChannel, destChannel } 
          }),
          { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );

      case 'bulk-forward':
        if (!sourceChannel || !destChannel || !startMessageId || !endMessageId) {
          return new Response(
            JSON.stringify({ error: 'Missing parameters for bulk forward' }),
            { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
          );
        }
        
        console.log(`Starting bulk forward: ${startMessageId} to ${endMessageId}`);
        const bulkResult = await bulkForward(
          sourceChannel, 
          destChannel, 
          Number(startMessageId), 
          Number(endMessageId)
        );
        
        return new Response(
          JSON.stringify({ 
            ok: true, 
            ...bulkResult
          }),
          { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );

      case 'set-webhook':
        if (!webhookUrl) {
          return new Response(
            JSON.stringify({ error: 'Missing webhook URL' }),
            { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
          );
        }
        
        const webhookResult = await setWebhook(webhookUrl);
        return new Response(
          JSON.stringify(webhookResult),
          { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );

      case 'webhook-info':
        const info = await getWebhookInfo();
        return new Response(
          JSON.stringify(info),
          { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );

      case 'status':
        const currentConfig = await loadBotConfig();
        return new Response(
          JSON.stringify({ 
            configured: !!currentConfig,
            config: currentConfig 
          }),
          { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );

      case 'progress':
        const dbProgress = await loadProgress();
        if (!dbProgress) {
          return new Response(
            JSON.stringify({ isRunning: false }),
            { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
          );
        }
        const progressElapsedMs = dbProgress.started_at 
          ? Date.now() - new Date(dbProgress.started_at).getTime() 
          : 0;
        const progressElapsedSec = progressElapsedMs / 1000;
        const progressSpeed = progressElapsedSec > 0 
          ? Math.round((dbProgress.success_count || 0) / progressElapsedSec * 60) 
          : 0;
        
        return new Response(
          JSON.stringify({
            isRunning: dbProgress.is_active,
            success: dbProgress.success_count || 0,
            failed: dbProgress.failed_count || 0,
            skipped: dbProgress.skipped_count || 0,
            total: dbProgress.total_count || 0,
            rateLimitHits: dbProgress.rate_limit_hits || 0,
            currentBatch: dbProgress.current_batch || 0,
            totalBatches: dbProgress.total_batches || 0,
            elapsedSeconds: Math.round(progressElapsedSec),
            speedPerMinute: progressSpeed,
          }),
          { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );

      case 'stop':
        await requestStop();
        console.log('Stop signal received');
        return new Response(
          JSON.stringify({ success: true, message: 'Stop signal sent' }),
          { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );

      default:
        return new Response(
          JSON.stringify({ error: 'Unknown action' }),
          { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );
    }
  } catch (error) {
    console.error('Error in telegram-forwarder:', error);
    const errorMessage = error instanceof Error ? error.message : 'Unknown error';
    return new Response(
      JSON.stringify({ error: errorMessage }),
      { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  }
});