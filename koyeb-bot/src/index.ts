import express from 'express';
import { MongoClient, Db, Collection } from 'mongodb';
import fetch from 'node-fetch';

const app = express();
app.use(express.json());

// Environment variables
const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN!;
const MONGODB_URI = process.env.MONGODB_URI!;
const PORT = process.env.PORT || 8000;
const WEBHOOK_URL = process.env.WEBHOOK_URL;
const ADMIN_USER_ID = process.env.ADMIN_USER_ID ? parseInt(process.env.ADMIN_USER_ID) : null;

// Force Subscribe Channels (comma-separated channel IDs like -1001234567890)
const FORCE_SUB_CHANNELS = process.env.FORCE_SUB_CHANNELS 
  ? process.env.FORCE_SUB_CHANNELS.split(',').map(ch => ch.trim()).filter(ch => ch)
  : [];

// Channel names for display (comma-separated, same order as FORCE_SUB_CHANNELS)
const FORCE_SUB_CHANNEL_NAMES = process.env.FORCE_SUB_CHANNEL_NAMES
  ? process.env.FORCE_SUB_CHANNEL_NAMES.split(',').map(name => name.trim())
  : [];

// Invite links for private channels/groups (comma-separated, same order as FORCE_SUB_CHANNELS)
// Example: https://t.me/+ABC123,https://t.me/+XYZ789
const FORCE_SUB_LINKS = process.env.FORCE_SUB_LINKS
  ? process.env.FORCE_SUB_LINKS.split(',').map(link => link.trim())
  : [];

// Required referrals to unlock bot (default: 10)
const REQUIRED_REFERRALS = process.env.REQUIRED_REFERRALS 
  ? parseInt(process.env.REQUIRED_REFERRALS) 
  : 10;

// Check if user is admin
function isAdmin(userId: number): boolean {
  return ADMIN_USER_ID !== null && userId === ADMIN_USER_ID;
}

// Check if user is member of a channel
async function checkChannelMembership(userId: number, channelId: string): Promise<boolean> {
  try {
    const response = await sendTelegramRequest('getChatMember', {
      chat_id: channelId,
      user_id: userId
    });
    if (response.ok && response.result) {
      const status = response.result.status;
      return ['creator', 'administrator', 'member'].includes(status);
    }
    return false;
  } catch (error) {
    console.error(`Error checking membership for channel ${channelId}:`, error);
    return false;
  }
}

// Check all force subscribe channels
async function checkAllSubscriptions(userId: number): Promise<{ allJoined: boolean; notJoined: string[] }> {
  if (FORCE_SUB_CHANNELS.length === 0) {
    return { allJoined: true, notJoined: [] };
  }
  
  const notJoined: string[] = [];
  for (let i = 0; i < FORCE_SUB_CHANNELS.length; i++) {
    const channelId = FORCE_SUB_CHANNELS[i];
    const isMember = await checkChannelMembership(userId, channelId);
    if (!isMember) {
      notJoined.push(channelId);
    }
  }
  
  return { allJoined: notJoined.length === 0, notJoined };
}

// Show force subscribe message
async function showForceSubscribe(chatId: number, userId: number) {
  const buttons: any[][] = [];
  
  for (let i = 0; i < FORCE_SUB_CHANNELS.length; i++) {
    const channelId = FORCE_SUB_CHANNELS[i];
    const channelName = FORCE_SUB_CHANNEL_NAMES[i] || `Channel/Group ${i + 1}`;
    
    // Use invite link if provided, otherwise generate from channel ID/username
    let channelLink = FORCE_SUB_LINKS[i];
    if (!channelLink) {
      channelLink = channelId.startsWith('@') 
        ? `https://t.me/${channelId.substring(1)}` 
        : `https://t.me/c/${channelId.replace('-100', '')}`;
    }
    
    buttons.push([{ text: `🔗 Join ${channelName} ✅`, url: channelLink }]);
  }
  
  buttons.push([{ text: '✅ Continue ➡️', callback_data: 'check_subscription' }]);
  
  await sendMessage(chatId, 
    `🔐 <b>Access Required</b>\n\n` +
    `To use this bot, you must:\n` +
    `1️⃣ Join our channel(s)/group(s)\n` +
    `2️⃣ Invite ${REQUIRED_REFERRALS} members using your referral link\n\n` +
    `👇 <b>Join below and click Continue:</b>`,
    { inline_keyboard: buttons }
  );
}

// Show referral status message
async function showReferralStatus(chatId: number, userId: number, botUsername: string) {
  const referralCount = await getReferralCount(userId);
  const remaining = Math.max(0, REQUIRED_REFERRALS - referralCount);
  
  const referralLink = `https://t.me/${botUsername}?start=ref_${userId}`;
  
  const buttons: any[][] = [
    [{ text: '🔄 Refresh Status', callback_data: 'check_referrals' }],
    [{ text: '📤 Share Referral Link', url: `https://t.me/share/url?url=${encodeURIComponent(referralLink)}&text=${encodeURIComponent('Join this bot using my link!')}` }]
  ];
  
  if (remaining <= 0) {
    buttons.push([{ text: '✅ Continue to Bot', callback_data: 'referral_complete' }]);
  }
  
  await sendMessage(chatId, 
    `👥 <b>Referral Status</b>\n\n` +
    `✅ You have joined all required channels!\n\n` +
    `Now invite ${REQUIRED_REFERRALS} members to unlock the bot.\n\n` +
    `📊 <b>Progress:</b> ${referralCount}/${REQUIRED_REFERRALS} referrals\n` +
    `📈 <b>Remaining:</b> ${remaining} more needed\n\n` +
    `🔗 <b>Your Referral Link:</b>\n<code>${referralLink}</code>\n\n` +
    `Share this link with friends. When they join using your link, you get credit!`,
    { inline_keyboard: buttons }
  );
}

// Get referral count for a user
async function getReferralCount(userId: number): Promise<number> {
  return await referrals.countDocuments({ referrer_id: userId });
}

// Add referral
async function addReferral(referrerId: number, referredId: number): Promise<boolean> {
  try {
    // Check if this user was already referred
    const existing = await referrals.findOne({ referred_id: referredId });
    if (existing) return false;
    
    // Don't allow self-referral
    if (referrerId === referredId) return false;
    
    await referrals.insertOne({
      referrer_id: referrerId,
      referred_id: referredId,
      created_at: new Date()
    });
    return true;
  } catch (error) {
    console.error('Error adding referral:', error);
    return false;
  }
}

// Check if user has enough referrals
async function hasEnoughReferrals(userId: number): Promise<boolean> {
  if (REQUIRED_REFERRALS <= 0) return true;
  const count = await getReferralCount(userId);
  return count >= REQUIRED_REFERRALS;
}

const TELEGRAM_API = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}`;

// MongoDB setup
let db: Db;

// Documents that use string _id values (we use fixed IDs like 'config' and 'current')
type BotConfigDoc = {
  _id: 'config';
  source_channel: string;
  dest_channel: string;
  updated_at?: Date;
};

type ProgressDoc = {
  _id: 'current';
  source_channel?: string | null;
  dest_channel?: string | null;
  start_id?: number | null;
  end_id?: number | null;
  current_batch?: number | null;
  total_batches?: number | null;
  total_count?: number | null;
  success_count?: number | null;
  failed_count?: number | null;
  skipped_count?: number | null;
  rate_limit_hits?: number | null;
  is_active?: boolean;
  stop_requested?: boolean | null;
  speed?: number | null;
  started_at?: Date | string | null;
  last_updated_at?: Date;
};

let userSessions: Collection;
let botConfig: Collection<BotConfigDoc>;
let forwardingProgress: Collection<ProgressDoc>;
let forwardedMessages: Collection;
let referrals: Collection;
let userChannels: Collection;

async function connectMongoDB() {
  const client = new MongoClient(MONGODB_URI);
  await client.connect();
  db = client.db('telegram_forwarder');

  userSessions = db.collection('user_sessions');
  botConfig = db.collection<BotConfigDoc>('bot_config');
  forwardingProgress = db.collection<ProgressDoc>('forwarding_progress');
  forwardedMessages = db.collection('forwarded_messages');
  referrals = db.collection('referrals');
  userChannels = db.collection('user_channels');

  // Create indexes
  await userSessions.createIndex({ user_id: 1 }, { unique: true });
  await forwardedMessages.createIndex({ source_channel: 1, dest_channel: 1, source_message_id: 1 });
  await referrals.createIndex({ referrer_id: 1 });
  await referrals.createIndex({ referred_id: 1 }, { unique: true });
  await userChannels.createIndex({ user_id: 1, channel_id: 1 }, { unique: true });

  console.log('✅ Connected to MongoDB');
}

// Session states
const STATES = {
  IDLE: 'idle',
  WAITING_SOURCE: 'waiting_source',
  WAITING_SKIP: 'waiting_skip',
  WAITING_DEST: 'waiting_dest',
  CONFIRMING: 'confirming',
  WAITING_TARGET_CHAT: 'waiting_target_chat',
};

// User session management
async function getUserSession(userId: number) {
  return await userSessions.findOne({ user_id: userId });
}

async function setUserSession(userId: number, updates: any) {
  await userSessions.updateOne(
    { user_id: userId },
    { $set: { ...updates, updated_at: new Date() } },
    { upsert: true }
  );
}

async function clearUserSession(userId: number) {
  await userSessions.updateOne(
    { user_id: userId },
    {
      $set: {
        state: STATES.IDLE,
        source_channel: null,
        source_title: null,
        dest_channel: null,
        dest_title: null,
        skip_number: 0,
        updated_at: new Date(),
      }
    },
    { upsert: true }
  );
}

// Database operations
async function loadBotConfig() {
  return await botConfig.findOne({ _id: 'config' });
}

async function saveBotConfig(sourceChannel: string, destChannel: string) {
  await botConfig.updateOne(
    { _id: 'config' },
    {
      $set: {
        source_channel: sourceChannel,
        dest_channel: destChannel,
        updated_at: new Date()
      }
    },
    { upsert: true }
  );
}

async function loadProgress() {
  return await forwardingProgress.findOne({ _id: 'current' });
}

async function saveProgress(progress: any) {
  await forwardingProgress.updateOne(
    { _id: 'current' },
    { $set: { ...progress, last_updated_at: new Date() } },
    { upsert: true }
  );
}

async function isStopRequested() {
  const progress = await loadProgress();
  return progress?.stop_requested === true;
}

async function requestStop() {
  await forwardingProgress.updateOne(
    { _id: 'current' },
    { $set: { stop_requested: true } }
  );
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
  if (progress?.is_active) {
    return [
      [{ text: '●○○○○○○○○○○○○○○○○○○○○', callback_data: 'refresh_progress' }],
      [{ text: '• CANCEL', callback_data: 'stop_forward' }]
    ];
  }
  return [[{ text: '🔙 Main Menu', callback_data: 'menu' }]];
}

function formatProgressText(progress: any) {
  const percent = progress?.total_count
    ? Math.round(((progress?.success_count || 0) / (progress?.total_count || 1)) * 100)
    : 0;
  
  const status = progress?.is_active
    ? (progress?.stop_requested ? 'Stopping...' : 'Forwarding')
    : 'Completed';

  const fetched = progress?.total_count || 0;
  const success = progress?.success_count || 0;
  const duplicate = progress?.skipped_count || 0;
  const deleted = progress?.failed_count || 0;
  // "Skipped" = user-requested skip from start + deleted messages
  const userSkip = Math.max(0, (progress?.start_id || 1) - 1);
  const skipped = userSkip + deleted;
  const filtered = 0;

  // Calculate elapsed time
  let elapsedStr = '0s';
  let etaStr = 'Calculating...';
  
  if (progress?.started_at) {
    const startTime = new Date(progress.started_at).getTime();
    const now = Date.now();
    const elapsedMs = now - startTime;
    elapsedStr = formatDuration(elapsedMs);
    
    // Calculate ETA based on speed
    const processed = success + deleted + duplicate;
    const remaining = fetched - processed;
    
    if (processed > 0 && remaining > 0) {
      const msPerMsg = elapsedMs / processed;
      const etaMs = msPerMsg * remaining;
      etaStr = formatDuration(etaMs);
    } else if (remaining <= 0) {
      etaStr = 'Done';
    }
  }

  return (
    `<pre>` +
    `    ╔ FORWARD STATUS ╦═○؛✿\n` +
    `┌───────────────────────────➣\n` +
    `│-≫ 👷 ғᴇᴄʜᴇᴅ Msɢ : ${fetched}\n` +
    `│\n` +
    `│-≫ ✅ sᴜᴄᴄᴇssғᴜʟʟʏ Fᴡᴅ : ${success}\n` +
    `│\n` +
    `│-≫ 👥 ᴅᴜᴘʟɪᴄᴀᴛᴇ Msɢ : ${duplicate}\n` +
    `│\n` +
    `│-≫ 🪆 Sᴋɪᴘᴘᴇᴅ Msɢ : ${skipped}\n` +
    `│\n` +
    `│-≫ 🔁 Fɪʟᴛᴇʀᴇᴅ Msɢ : ${filtered}\n` +
    `│\n` +
    `│-≫ 📊 Cᴜʀʀᴇɴᴛ Sᴛᴀᴛᴜs: ${status}\n` +
    `│\n` +
    `│-≫ ◇ Pᴇʀᴄᴇɴᴛᴀɢᴇ: ${percent} %\n` +
    `│\n` +
    `│-≫ ⏱️ Eʟᴀᴘsᴇᴅ: ${elapsedStr}\n` +
    `│\n` +
    `│-≫ ⏳ ETA: ${etaStr}\n` +
    `└───────────────────────────➣\n` +
    `    ╚ PROGRESSING ╩═○؛✿\n` +
    `</pre>`
  );
}

function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  
  if (days > 0) {
    const remainingHours = hours % 24;
    const remainingMins = minutes % 60;
    return `${days}d ${remainingHours}h ${remainingMins}m`;
  }
  if (hours > 0) {
    const remainingMins = minutes % 60;
    const remainingSecs = seconds % 60;
    return `${hours}h ${remainingMins}m ${remainingSecs}s`;
  }
  if (minutes > 0) {
    const remainingSecs = seconds % 60;
    return `${minutes}m ${remainingSecs}s`;
  }
  return `${seconds}s`;
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

async function getForwardedMessageIds(sourceChannel: string, destChannel: string, messageIds: number[]) {
  const docs = await forwardedMessages.find({
    source_channel: sourceChannel,
    dest_channel: destChannel,
    source_message_id: { $in: messageIds }
  }).toArray();
  return docs.map(d => d.source_message_id);
}

async function saveForwardedMessageIds(sourceChannel: string, destChannel: string, messageIds: number[]) {
  const records = messageIds.map(id => ({
    source_channel: sourceChannel,
    dest_channel: destChannel,
    source_message_id: id,
    forwarded_at: new Date()
  }));
  await forwardedMessages.insertMany(records);
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

async function showMainMenu(chatId: number, userId?: number) {
  const buttons = [
    [
      { text: '🚀 Forward', callback_data: 'forward' },
      { text: '📢 Channel', callback_data: 'channel' }
    ],
    [
      { text: '⏹️ Stop', callback_data: 'stop' },
      { text: '❓ Help', callback_data: 'help' }
    ]
  ];
  
  // Add Admin Panel button only for admin
  if (userId && isAdmin(userId)) {
    buttons.push([{ text: '👑 Admin Panel', callback_data: 'admin_panel' }]);
  }
  
  const keyboard = { inline_keyboard: buttons };
  await sendMessage(chatId, `🤖 <b>Telegram Forwarder Bot</b>\n\nSelect an option below:`, keyboard);
}

// Get all users from database
async function getAllUsers() {
  return await userSessions.find({}).toArray();
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
  // Very Safe speed: 20 batch + 4s delay (~300 files/min) - for 24/7 continuous use
  const BATCH_SIZE = 20;
  const SAFE_DELAY_MS = 4000; // 4 second delay between batches for maximum safety

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
        started_at: new Date(originalStartedAt),
      };
      await saveProgress(progressPayload);
      if (chatId) await updateWatchedProgressMessage(chatId, progressPayload);
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
      started_at: new Date(originalStartedAt),
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
    started_at: new Date(originalStartedAt),
  };
  await saveProgress(completeProgressPayload);
  if (chatId) await updateWatchedProgressMessage(chatId, completeProgressPayload);

  if (chatId) {
    await sendMessage(chatId, `✅ <b>Forwarding Complete!</b>\n\n✅ Success: ${successCount}\n❌ Failed: ${failedCount}\n⏭️ Skipped: ${skippedCount}`);
  }

  return { success: successCount, failed: failedCount, needsResume: false };
}

// Command handlers
async function handleCommand(chatId: number, text: string, message: any, botUsername: string) {
  const parts = text.split(' ');
  const command = parts[0].toLowerCase().replace(/@.*$/, '');

  if (command === '/start') {
    await clearUserSession(chatId);
    
    // Check for referral parameter
    if (parts.length > 1 && parts[1].startsWith('ref_')) {
      const referrerId = parseInt(parts[1].replace('ref_', ''));
      if (!isNaN(referrerId)) {
        const added = await addReferral(referrerId, chatId);
        if (added) {
          // Notify referrer
          const referrerCount = await getReferralCount(referrerId);
          const remaining = Math.max(0, REQUIRED_REFERRALS - referrerCount);
          await sendMessage(referrerId, 
            `🎉 <b>New Referral!</b>\n\n` +
            `Someone joined using your link!\n` +
            `📊 Progress: ${referrerCount}/${REQUIRED_REFERRALS}\n` +
            `📈 Remaining: ${remaining} more needed${remaining === 0 ? '\n\n✅ Bot unlocked!' : ''}`
          );
        }
      }
    }
    
    // Check force subscribe (skip for admin)
    if (!isAdmin(chatId) && FORCE_SUB_CHANNELS.length > 0) {
      const { allJoined } = await checkAllSubscriptions(chatId);
      if (!allJoined) {
        await showForceSubscribe(chatId, chatId);
        return;
      }
      
      // Check referrals (skip for admin)
      if (REQUIRED_REFERRALS > 0 && !(await hasEnoughReferrals(chatId))) {
        await showReferralStatus(chatId, chatId, botUsername);
        return;
      }
    }
    
    await showMainMenu(chatId, chatId);
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

    if (!progress.source_channel || !progress.dest_channel || !progress.end_id) {
      await sendMessage(chatId, '❌ Cannot resume: missing source/destination or end message id. Please run /forward again.');
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
    await sendMessage(chatId, '❌ Forward Cancelled');
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
    
    // Check if user has saved channels
    const savedChannels = await userChannels.find({ user_id: chatId }).toArray();
    
    // Save skip_number and update state in single call
    await setUserSession(chatId, {
      skip_number: skipNum,
      state: STATES.WAITING_DEST,
    });
    
    if (savedChannels.length > 0) {
      // Show saved channels to select as destination
      const buttons: any[][] = [];
      for (const ch of savedChannels) {
        buttons.push([{ text: `📢 ${ch.channel_title || ch.channel_id}`, callback_data: `select_dest_${ch.channel_id}` }]);
      }
      buttons.push([{ text: '❌ Cancel', callback_data: 'cancel' }]);
      
      await sendMessage(chatId,
        `<b>( SELECT DESTINATION CHAT )</b>\n\n` +
        `Select a channel from your saved channels:`,
        { inline_keyboard: buttons }
      );
    } else {
      // No saved channels, ask to forward message
      
      await sendMessage(chatId,
        `<b>( SET DESTINATION CHAT )</b>\n\n` +
        `Forward any message from the destination channel where you want to forward messages.\n` +
        `/cancel - cancel this process`,
        { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'cancel' }]] }
      );
    }
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
  
  if (session.state === STATES.WAITING_TARGET_CHAT) {
    const channelInfo = extractChannelFromMessage(message);
    if (!channelInfo) {
      await sendMessage(chatId, '❌ Invalid! Please forward a message from your target chat.');
      return true;
    }
    
    // Save channel to database
    try {
      await userChannels.updateOne(
        { user_id: chatId, channel_id: channelInfo.chatId },
        { $set: { 
          user_id: chatId,
          channel_id: channelInfo.chatId, 
          channel_title: channelInfo.title,
          added_at: new Date() 
        }},
        { upsert: true }
      );
    } catch (e) {
      console.log('Channel save error:', e);
    }
    
    await clearUserSession(chatId);
    
    await sendMessage(chatId,
      `✅ <b>Target Chat Added!</b>\n\n` +
      `📢 <b>Channel:</b> ${channelInfo.title}\n` +
      `🆔 <b>ID:</b> <code>${channelInfo.chatId}</code>`,
      { inline_keyboard: [
        [{ text: '➕ Add Another', callback_data: 'add_channel' }],
        [{ text: '🔙 Back to Channels', callback_data: 'channel' }]
      ]}
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
    const userId = callbackQuery.from?.id;
    await clearUserSession(chatId);
    
    // Check force subscribe (skip for admin)
    if (!isAdmin(userId) && FORCE_SUB_CHANNELS.length > 0) {
      const { allJoined } = await checkAllSubscriptions(userId);
      if (!allJoined) {
        await showForceSubscribe(chatId, userId);
        return;
      }
      
      // Check referrals
      if (REQUIRED_REFERRALS > 0 && !(await hasEnoughReferrals(userId))) {
        const botInfo = await sendTelegramRequest('getMe', {});
        const botUsername = botInfo?.result?.username || 'bot';
        await showReferralStatus(chatId, userId, botUsername);
        return;
      }
    }
    
    await showMainMenu(chatId, userId);
  }
  else if (data === 'check_subscription') {
    const userId = callbackQuery.from?.id;
    const { allJoined, notJoined } = await checkAllSubscriptions(userId);
    
    if (allJoined) {
      // Check if referrals are required
      if (REQUIRED_REFERRALS > 0 && !(await hasEnoughReferrals(userId))) {
        const botInfo = await sendTelegramRequest('getMe', {});
        const botUsername = botInfo?.result?.username || 'bot';
        await showReferralStatus(chatId, userId, botUsername);
      } else {
        await sendMessage(chatId, '✅ <b>Verification Successful!</b>\n\nYou have access to the bot.');
        await showMainMenu(chatId, userId);
      }
    } else {
      await sendMessage(chatId, `❌ <b>Not Joined Yet!</b>\n\nPlease join all the channels first, then click Continue again.`);
      await showForceSubscribe(chatId, userId);
    }
  }
  else if (data === 'check_referrals') {
    const userId = callbackQuery.from?.id;
    const botInfo = await sendTelegramRequest('getMe', {});
    const botUsername = botInfo?.result?.username || 'bot';
    await showReferralStatus(chatId, userId, botUsername);
  }
  else if (data === 'referral_complete') {
    const userId = callbackQuery.from?.id;
    
    // Verify referrals before proceeding
    if (await hasEnoughReferrals(userId)) {
      await sendMessage(chatId, '🎉 <b>Congratulations!</b>\n\nYou have completed all requirements. Welcome to the bot!');
      await showMainMenu(chatId, userId);
    } else {
      await sendMessage(chatId, '❌ You still need more referrals. Keep inviting!');
      const botInfo = await sendTelegramRequest('getMe', {});
      const botUsername = botInfo?.result?.username || 'bot';
      await showReferralStatus(chatId, userId, botUsername);
    }
  }
  else if (data === 'admin_panel') {
    const userId = callbackQuery.from?.id;
    if (!userId || !isAdmin(userId)) {
      await sendMessage(chatId, '❌ Access denied. Admin only.');
      return;
    }
    
    const users = await getAllUsers();
    if (users.length === 0) {
      await sendMessage(chatId, '👑 <b>Admin Panel</b>\n\n📭 No users found in database.', {
        inline_keyboard: [[{ text: '🔙 Main Menu', callback_data: 'menu' }]]
      });
      return;
    }
    
    let userList = '👑 <b>Admin Panel - Users Database</b>\n\n';
    userList += `📊 Total Users: ${users.length}\n\n`;
    
    users.forEach((user, index) => {
      const joinDate = user.created_at ? new Date(user.created_at).toLocaleDateString() : 'N/A';
      const lastActive = user.updated_at ? new Date(user.updated_at).toLocaleDateString() : 'N/A';
      userList += `<b>${index + 1}. User ID:</b> <code>${user.user_id}</code>\n`;
      userList += `   📅 Joined: ${joinDate}\n`;
      userList += `   🕐 Last Active: ${lastActive}\n`;
      if (user.state && user.state !== 'idle') {
        userList += `   📍 State: ${user.state}\n`;
      }
      userList += '\n';
    });
    
    await sendMessage(chatId, userList, {
      inline_keyboard: [
        [{ text: '🔄 Refresh', callback_data: 'admin_panel' }],
        [{ text: '🔙 Main Menu', callback_data: 'menu' }]
      ]
    });
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
  else if (data === 'channel') {
    // Fetch user's saved channels
    const channels = await userChannels.find({ user_id: chatId }).toArray();
    
    const buttons: any[][] = [];
    
    // Add channel buttons
    for (const ch of channels) {
      buttons.push([{ text: ch.channel_title || ch.channel_id, callback_data: `ch_${ch.channel_id}` }]);
    }
    
    buttons.push([{ text: '➕ Add Channel ➕', callback_data: 'add_channel' }]);
    buttons.push([{ text: '↩️ Back', callback_data: 'menu' }]);
    
    await sendMessage(chatId, 
      `<u>My Channels</u>\n\n` +
      `you can manage your target chats in here`,
      { inline_keyboard: buttons }
    );
  }
  else if (data === 'add_channel') {
    await setUserSession(chatId, { state: STATES.WAITING_TARGET_CHAT });
    await sendMessage(chatId, 
      `<b>( SET TARGET CHAT )</b>\n\n` +
      `📨 FORWARD A MESSAGE FROM YOUR TARGET CHAT\n\n` +
      `/cancel - CANCEL THIS PROCESS`,
      { inline_keyboard: [[{ text: '❌ Cancel', callback_data: 'cancel_target' }]] }
    );
  }
  else if (data === 'cancel_target') {
    await clearUserSession(chatId);
    await sendMessage(chatId, '❌ Process cancelled.', {
      inline_keyboard: [[{ text: '🔙 Back', callback_data: 'channel' }]]
    });
  }
  else if (data.startsWith('ch_')) {
    const channelId = data.replace('ch_', '');
    const channel = await userChannels.findOne({ user_id: chatId, channel_id: channelId });
    
    if (channel) {
      await sendMessage(chatId,
        `📢 <b>${channel.channel_title || 'Channel'}</b>\n\n` +
        `🆔 ID: <code>${channel.channel_id}</code>`,
        { inline_keyboard: [
          [{ text: '🗑️ Delete', callback_data: `del_${channelId}` }],
          [{ text: '🔙 Back', callback_data: 'channel' }]
        ]}
      );
    } else {
      await sendMessage(chatId, '❌ Channel not found.', {
        inline_keyboard: [[{ text: '🔙 Back', callback_data: 'channel' }]]
      });
    }
  }
  else if (data.startsWith('del_')) {
    const channelId = data.replace('del_', '');
    await userChannels.deleteOne({ user_id: chatId, channel_id: channelId });
    await sendMessage(chatId, '✅ Channel deleted!', {
      inline_keyboard: [[{ text: '🔙 Back', callback_data: 'channel' }]]
    });
  }
  else if (data.startsWith('select_dest_')) {
    const channelId = data.replace('select_dest_', '');
    const channel = await userChannels.findOne({ user_id: chatId, channel_id: channelId });
    
    if (!channel) {
      await sendMessage(chatId, '❌ Channel not found.', {
        inline_keyboard: [[{ text: '🔙 Main Menu', callback_data: 'menu' }]]
      });
      return;
    }
    
    const session = await getUserSession(chatId);
    
    await setUserSession(chatId, {
      state: STATES.CONFIRMING,
      dest_channel: channel.channel_id,
      dest_title: channel.channel_title,
    });
    
    const updatedSession = await getUserSession(chatId);
    const botInfo = await sendTelegramRequest('getMe', {});
    const botUsername = botInfo?.result?.username || 'bot';
    
    await sendMessage(chatId,
      `<u>DOUBLE CHECKING</u> ⚠️\n` +
      `Before forwarding the messages Click the Yes button only after checking the following\n\n` +
      `★ YOUR BOT: <a href="https://t.me/${botUsername}">${botUsername}</a>\n` +
      `★ FROM CHANNEL: ${updatedSession?.source_title || 'Unknown'}\n` +
      `★ TO CHANNEL: ${updatedSession?.dest_title || 'Unknown'}\n` +
      `★ SKIP MESSAGES: ${updatedSession?.skip_number || 0}\n\n` +
      `° <a href="https://t.me/${botUsername}">${botUsername}</a> must be admin in <b>TARGET CHAT</b> (${updatedSession?.dest_title})\n` +
      `° If the <b>SOURCE CHAT</b> is private your userbot must be member or your bot must be admin in there also\n\n` +
      `If the above is checked then the yes button can be clicked`,
      { inline_keyboard: [
        [
          { text: 'Yes', callback_data: 'confirm_forward' },
          { text: 'No', callback_data: 'cancel' }
        ]
      ]}
    );
  }
  else if (data === 'confirm_forward') {
    const messageId = callbackQuery.message?.message_id;
    const session = await getUserSession(chatId);

    if (!session || session.state !== STATES.CONFIRMING) {
      if (typeof messageId === 'number') {
        await editMessageText(
          chatId,
          messageId,
          '❌ Session expired. Please start again with /forward',
          { inline_keyboard: [] }
        );
      }
      return;
    }

    await saveBotConfig(session.source_channel, session.dest_channel);

    const endId = session.last_message_id || 0;
    const skipNumber = session.skip_number || 0;
    // User enters the LAST skipped message ID (e.g. 291700 => start forwarding from 291701)
    const startId = skipNumber > 0 ? (skipNumber + 1) : 1;

    const progressData = {
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
      started_at: new Date(),
      rate_limit_hits: 0,
      speed: 0,
    };

    await saveProgress(progressData);

    await clearUserSession(chatId);

    // Show progress status directly
    if (typeof messageId === 'number') {
      await editMessageText(
        chatId,
        messageId,
        formatProgressText(progressData),
        { inline_keyboard: progressButtons(progressData) }
      );
      // Store messageId for progress updates
      await setUserSession(chatId, { last_message_id: messageId });
    }

    // Run forwarding in background
    bulkForward(session.source_channel, session.dest_channel, startId, endId, false, chatId);
  }
  else if (data === 'resume') {
    const username = await getBotUsername();
    await handleCommand(chatId, '/resume', null, username);
  }
  else if (data === 'progress') {
    const username = await getBotUsername();
    await handleCommand(chatId, '/progress', null, username);
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
    const username = await getBotUsername();
    await handleCommand(chatId, '/status', null, username);
  }
  else if (data === 'stop' || data === 'stop_forward') {
    await requestStop();
    await sendMessage(chatId, '❌ Forward Cancelled');
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

// Bot username cache
let cachedBotUsername: string | null = null;

async function getBotUsername(): Promise<string> {
  if (cachedBotUsername) return cachedBotUsername;
  const botInfo = await sendTelegramRequest('getMe', {});
  const username = botInfo?.result?.username || 'bot';
  cachedBotUsername = username;
  return username;
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
    const username = await getBotUsername();
    await handleCommand(chatId, text, message, username);
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
  res.json({ status: 'ok', message: 'Telegram Forwarder Bot is running (MongoDB)' });
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
async function startServer() {
  await connectMongoDB();
  
  app.listen(PORT, () => {
    console.log(`🚀 Server running on port ${PORT}`);
    console.log(`📡 Webhook endpoint: POST /webhook`);
    console.log(`🔗 Set webhook: GET /set-webhook`);
    console.log(`🗄️ Database: MongoDB`);
  });
}

startServer().catch(console.error);
