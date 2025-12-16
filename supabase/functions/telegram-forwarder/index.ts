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

// Store configuration in memory
let botConfig: { sourceChannel: string; destChannel: string } | null = null;
let stopForwarding = false;

// Live progress tracking
let liveProgress = {
  isRunning: false,
  success: 0,
  failed: 0,
  skipped: 0,
  total: 0,
  rateLimitHits: 0,
  startTime: 0,
  currentBatch: 0,
  totalBatches: 0,
};

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
/setconfig &lt;source&gt; &lt;dest&gt; - Set source and destination channels
/forward &lt;start&gt; &lt;end&gt; - Forward messages from start to end ID
/status - Show current configuration
/stop - Stop current forwarding
/progress - Show live progress

<b>Example:</b>
<code>/setconfig -1001234567890 -1009876543210</code>
<code>/forward 1 10000</code>`;

    case '/setconfig':
      if (args.length < 2) {
        return '❌ Usage: /setconfig &lt;source_channel_id&gt; &lt;dest_channel_id&gt;';
      }
      botConfig = { sourceChannel: args[0], destChannel: args[1] };
      return `✅ Configuration saved!
📤 Source: <code>${args[0]}</code>
📥 Destination: <code>${args[1]}</code>`;

    case '/status':
      if (!botConfig) {
        return '⚠️ Bot not configured. Use /setconfig first.';
      }
      const statusText = liveProgress.isRunning 
        ? `🔄 Forwarding in progress...
✅ Success: ${liveProgress.success}
❌ Failed: ${liveProgress.failed}
⏱️ Speed: ${Math.round(liveProgress.success / ((Date.now() - liveProgress.startTime) / 60000))} files/min`
        : '💤 Idle';
      return `📊 <b>Bot Status</b>

📤 Source: <code>${botConfig.sourceChannel}</code>
📥 Destination: <code>${botConfig.destChannel}</code>

${statusText}`;

    case '/forward':
      if (!botConfig) {
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
      sendMessage(chatId, `🚀 Starting forward: ${startId} to ${endId} (${endId - startId + 1} messages)`);
      
      // Run async without waiting
      bulkForward(botConfig.sourceChannel, botConfig.destChannel, startId, endId)
        .then(result => {
          sendMessage(chatId, `✅ Forwarding complete!
📊 Success: ${result.success}
❌ Failed: ${result.failed}
⚡ Rate limits hit: ${result.rateLimitHits}`);
        })
        .catch(err => {
          sendMessage(chatId, `❌ Error: ${err.message}`);
        });
      
      return ''; // Already sent initial message

    case '/stop':
      stopForwarding = true;
      return '🛑 Stop signal sent. Forwarding will stop after current batch.';

    case '/progress':
      if (!liveProgress.isRunning) {
        return '💤 No forwarding in progress.';
      }
      const elapsedSec = (Date.now() - liveProgress.startTime) / 1000;
      const speed = elapsedSec > 0 ? Math.round(liveProgress.success / elapsedSec * 60) : 0;
      const percent = liveProgress.total > 0 ? Math.round((liveProgress.success / liveProgress.total) * 100) : 0;
      return `📊 <b>Live Progress</b>

✅ Success: ${liveProgress.success} / ${liveProgress.total} (${percent}%)
❌ Failed: ${liveProgress.failed}
⚡ Rate limits: ${liveProgress.rateLimitHits}
🚀 Speed: ${speed} files/min
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
  if (!botConfig) {
    console.log('Bot not configured yet');
    return { ok: true, message: 'Bot not configured' };
  }

  // Check if message is from source channel
  if (chatId !== botConfig.sourceChannel) {
    console.log(`Message from ${chatId} ignored, not source channel ${botConfig.sourceChannel}`);
    return { ok: true };
  }

  // Check if message contains a file
  const hasFile = message.document || message.photo || message.video || 
                  message.audio || message.voice || message.video_note ||
                  message.animation || message.sticker;

  if (hasFile) {
    const alreadyForwarded = await getForwardedMessageIds(
      botConfig.sourceChannel, 
      botConfig.destChannel, 
      [messageId]
    );
    
    if (alreadyForwarded.has(messageId)) {
      console.log(`Message ${messageId} already forwarded, skipping`);
      return { ok: true, skipped: true };
    }
    
    console.log(`Forwarding file from ${botConfig.sourceChannel} to ${botConfig.destChannel}`);
    const result = await forwardMessage(botConfig.sourceChannel, botConfig.destChannel, messageId);
    
    if (result.ok) {
      await saveForwardedMessageIds(botConfig.sourceChannel, botConfig.destChannel, [messageId]);
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
      liveProgress.rateLimitHits++;
      const waitTime = (result.parameters?.retry_after || 5) * 1000;
      console.log(`Rate limited (${liveProgress.rateLimitHits} total), waiting ${waitTime/1000}s...`);
      await new Promise(resolve => setTimeout(resolve, waitTime));
      continue;
    }
    
    // Other error - don't retry
    return { ok: false, count: 0 };
  }
  return { ok: false, count: 0 };
}

// Bulk forward messages - maximum speed
async function bulkForward(
  sourceChannel: string, 
  destChannel: string, 
  startId: number, 
  endId: number
): Promise<{ success: number; failed: number; skipped: number; total: number; stopped: boolean; rateLimitHits: number }> {
  stopForwarding = false;
  
  const total = endId - startId + 1;
  
  // Reset and init live progress
  liveProgress = {
    isRunning: true,
    success: 0,
    failed: 0,
    skipped: 0,
    total,
    rateLimitHits: 0,
    startTime: Date.now(),
    currentBatch: 0,
    totalBatches: 0,
  };
  
  console.log(`Forwarding ${total} messages at max speed`);
  
  // Telegram limit: 100 messages per copyMessages call
  const batchSize = 100;
  const parallelBatches = 10; // Balanced: 10 parallel = 1000 msgs/cycle (avoids rate limits)
  
  // Create all batches
  const batches: number[][] = [];
  for (let i = startId; i <= endId; i += batchSize) {
    const batch: number[] = [];
    for (let j = i; j < Math.min(i + batchSize, endId + 1); j++) {
      batch.push(j);
    }
    batches.push(batch);
  }
  
  liveProgress.totalBatches = batches.length;
  console.log(`Total batches: ${batches.length}, processing ${parallelBatches} in parallel`);
  
  // Process all batches in parallel groups
  for (let i = 0; i < batches.length; i += parallelBatches) {
    if (stopForwarding) {
      console.log('Stopped by user');
      liveProgress.isRunning = false;
      return { 
        success: liveProgress.success, 
        failed: liveProgress.failed, 
        skipped: liveProgress.skipped, 
        total, 
        stopped: true,
        rateLimitHits: liveProgress.rateLimitHits
      };
    }
    
    liveProgress.currentBatch = Math.min(i + parallelBatches, batches.length);
    const group = batches.slice(i, i + parallelBatches);
    console.log(`Group ${Math.floor(i / parallelBatches) + 1}/${Math.ceil(batches.length / parallelBatches)}`);
    
    const results = await Promise.all(
      group.map(async (batch) => {
        const result = await copyMessagesWithRetry(sourceChannel, destChannel, batch);
        if (result.ok) {
          // Save in background - don't wait
          saveForwardedMessageIds(sourceChannel, destChannel, batch);
          return { success: batch.length, failed: 0 };
        }
        return { success: 0, failed: batch.length };
      })
    );
    
    for (const r of results) {
      liveProgress.success += r.success;
      liveProgress.failed += r.failed;
    }
  }
  
  liveProgress.isRunning = false;
  return { 
    success: liveProgress.success, 
    failed: liveProgress.failed, 
    skipped: liveProgress.skipped, 
    total, 
    stopped: false,
    rateLimitHits: liveProgress.rateLimitHits
  };
}

serve(async (req) => {
  // Handle CORS preflight
  if (req.method === 'OPTIONS') {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    const url = new URL(req.url);
    
    // Handle webhook updates from Telegram
    if (req.method === 'POST' && url.pathname.includes('/webhook')) {
      const update = await req.json();
      const result = await handleWebhook(update);
      return new Response(JSON.stringify(result), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    // Handle configuration and other requests
    const body = await req.json();
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
        
        botConfig = { sourceChannel, destChannel };
        console.log('Bot configured:', botConfig);
        
        return new Response(
          JSON.stringify({ 
            success: true, 
            message: 'Bot configured successfully',
            config: botConfig 
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
        return new Response(
          JSON.stringify({ 
            configured: !!botConfig,
            config: botConfig 
          }),
          { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );

      case 'progress':
        const elapsedMs = liveProgress.startTime ? Date.now() - liveProgress.startTime : 0;
        const elapsedSec = elapsedMs / 1000;
        const speed = elapsedSec > 0 ? Math.round(liveProgress.success / elapsedSec * 60) : 0;
        
        return new Response(
          JSON.stringify({
            ...liveProgress,
            elapsedSeconds: Math.round(elapsedSec),
            speedPerMinute: speed,
          }),
          { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
        );

      case 'stop':
        stopForwarding = true;
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