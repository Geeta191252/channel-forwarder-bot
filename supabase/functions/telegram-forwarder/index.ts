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
let stopForwarding = false; // Stop flag for bulk forward

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

async function handleWebhook(update: TelegramUpdate) {
  console.log('Received Telegram update:', JSON.stringify(update, null, 2));
  
  if (!botConfig) {
    console.log('Bot not configured yet');
    return { ok: true, message: 'Bot not configured' };
  }

  const message = update.message || update.channel_post;
  if (!message) {
    console.log('No message in update');
    return { ok: true };
  }

  const chatId = String(message.chat?.id);
  const messageId = message.message_id;

  // Check if message is from source channel
  if (chatId !== botConfig.sourceChannel) {
    console.log(`Message from ${chatId} ignored, not source channel ${botConfig.sourceChannel}`);
    return { ok: true };
  }

  // Check if message contains a document/file
  const hasFile = message.document || message.photo || message.video || 
                  message.audio || message.voice || message.video_note ||
                  message.animation || message.sticker;

  if (hasFile) {
    // Check if already forwarded
    const alreadyForwarded = await getForwardedMessageIds(
      botConfig.sourceChannel, 
      botConfig.destChannel, 
      [messageId]
    );
    
    if (alreadyForwarded.has(messageId)) {
      console.log(`Message ${messageId} already forwarded, skipping`);
      return { ok: true, skipped: true, message: 'Already forwarded' };
    }
    
    console.log(`Forwarding file from ${botConfig.sourceChannel} to ${botConfig.destChannel}`);
    const result = await forwardMessage(botConfig.sourceChannel, botConfig.destChannel, messageId);
    
    if (result.ok) {
      await saveForwardedMessageIds(botConfig.sourceChannel, botConfig.destChannel, [messageId]);
    }
    
    return { 
      ok: result.ok, 
      forwarded: true,
      fileName: message.document?.file_name || 'media file'
    };
  }

  return { ok: true, message: 'No file in message' };
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

// Bulk forward messages in batches of 100, with parallel processing
async function bulkForward(
  sourceChannel: string, 
  destChannel: string, 
  startId: number, 
  endId: number
): Promise<{ success: number; failed: number; skipped: number; total: number; stopped: boolean }> {
  stopForwarding = false; // Reset stop flag
  const messageIds: number[] = [];
  
  // Generate message IDs from start to end
  for (let i = startId; i <= endId; i++) {
    messageIds.push(i);
  }
  
  const total = messageIds.length;
  const skipped = 0; // No skip check - forward all
  
  console.log(`Forwarding ${total} messages (no skip check)`);
  
  let success = 0;
  let failed = 0;
  
  // Process in batches of 100
  const batchSize = 100;
  const parallelBatches = 5; // Safe: 5 batches in parallel (500 messages at once)
  
  // Create all batches
  const batches: number[][] = [];
  for (let i = 0; i < messageIds.length; i += batchSize) {
    batches.push(messageIds.slice(i, i + batchSize));
  }
  
  console.log(`Total batches: ${batches.length}, processing ${parallelBatches} in parallel`);
  
  // Process batches in parallel groups
  for (let i = 0; i < batches.length; i += parallelBatches) {
    // Check stop flag
    if (stopForwarding) {
      console.log('Forwarding stopped by user');
      return { success, failed, skipped, total, stopped: true };
    }
    
    const parallelGroup = batches.slice(i, i + parallelBatches);
    console.log(`Processing parallel group ${Math.floor(i / parallelBatches) + 1}: batches ${i + 1} to ${Math.min(i + parallelBatches, batches.length)}`);
    
    const results = await Promise.all(
      parallelGroup.map(async (batch, idx) => {
        try {
          const result = await copyMessages(sourceChannel, destChannel, batch);
          if (result.ok) {
            // Save successfully forwarded messages
            await saveForwardedMessageIds(sourceChannel, destChannel, batch);
            return { success: batch.length, failed: 0 };
          } else {
            console.log(`Batch ${i + idx + 1} failed:`, result.description || result);
            return { success: 0, failed: batch.length };
          }
        } catch (error) {
          console.error(`Batch ${i + idx + 1} error:`, error);
          return { success: 0, failed: batch.length };
        }
      })
    );
    
    // Aggregate results
    for (const r of results) {
      success += r.success;
      failed += r.failed;
    }
    
    // Safe delay to avoid rate limits
    if (i + parallelBatches < batches.length) {
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  }
  
  return { success, failed, skipped, total, stopped: false };
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