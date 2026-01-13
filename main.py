# FORCE_REBUILD_CHECK_2024_12_24_22_30
import os
import re
import asyncio
import time
import io
import signal
import sys
from datetime import datetime
from flask import Flask, request, jsonify
from pyrogram import Client, filters, idle

# Ensure logs are not buffered (so Koyeb shows ENV CHECK / bot start logs)
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# Backward/forward compatibility: some builds may reference filters.supergroup
if not hasattr(filters, "supergroup"):
    filters.supergroup = filters.group

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.errors import (
    FloodWait,
    SlowmodeWait,
    ChatAdminRequired,
    ChannelPrivate,
    MessageNotModified,
    ChatWriteForbidden,
    Forbidden,
)

# Chat type helper: match both GROUP and SUPERGROUP reliably
GROUP_CHAT = filters.group | filters.supergroup
from pymongo import MongoClient
from dotenv import load_dotenv
import threading
from PIL import Image, ImageDraw, ImageFont

# Build marker (changes on each code update) to verify Koyeb is running the latest image
BUILD_MARKER = "2026-01-13T20:30:00Z"
print(f"✅ BOT BUILD_MARKER: {BUILD_MARKER}", flush=True)

load_dotenv()
flask_app = Flask(__name__)
# Alias for WSGI servers like gunicorn (some platforms expect `app`)
app = flask_app

# MongoDB setup
MONGO_URI = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or ""
mongo_client = MongoClient(MONGO_URI) if MONGO_URI else None
db = mongo_client["telegram_forwarder"] if mongo_client is not None else None

# Collections
sessions_col = db["user_sessions"] if db is not None else None
progress_col = db["forwarding_progress"] if db is not None else None
forwarded_col = db["forwarded_messages"] if db is not None else None
config_col = db["bot_config"] if db is not None else None
autoapprove_col = db["auto_approve"] if db is not None else None
pending_join_requests_col = db["pending_join_requests"] if db is not None else None
logo_col = db["logo_config"] if db is not None else None
moderation_col = db["group_moderation"] if db is not None else None
warnings_col = db["user_warnings"] if db is not None else None
user_channels_col = db["user_channels"] if db is not None else None
force_sub_col = db["force_subscribe"] if db is not None else None
referrals_col = db["referrals"] if db is not None else None
bot_settings_col = db["bot_settings"] if db is not None else None
group_forcejoin_col = db["group_forcejoin"] if db is not None else None
new_member_wait_col = db["new_member_wait"] if db is not None else None
joinwait_invites_col = db["joinwait_invites"] if db is not None else None
broadcast_users_col = db["broadcast_users"] if db is not None else None
broadcast_groups_col = db["broadcast_groups"] if db is not None else None
admin_groups_col = db["admin_groups"] if db is not None else None

# Force join config per group
group_forcejoin_config = {}

# Join-wait config
new_member_wait_config = {}

# Cached group admins
GROUP_ADMIN_CACHE = {}
GROUP_ADMIN_CACHE_TTL = int(os.getenv("GROUP_ADMIN_CACHE_TTL", "45"))

# Public access control
public_access_enabled = False

# User state for channel input
user_channel_state = {}

# Forward wizard state
forward_wizard_state = {}

# Active forwarding progress per user
user_forward_progress = {}

# Force subscribe channels list
force_subscribe_channels = []

# Admin IDs
ADMIN_IDS = set()
admin_ids_env = os.getenv("ADMIN_IDS", "") or os.getenv("ADMIN_USER_ID", "")
if admin_ids_env:
    ADMIN_IDS = set(int(x.strip()) for x in admin_ids_env.split(",") if x.strip().isdigit())

BOT_ADMINS = ADMIN_IDS

# Referral requirement
REQUIRED_REFERRALS = int(os.getenv("REQUIRED_REFERRALS", "10"))

# User account credentials (MTProto)
API_ID = os.getenv("API_ID", "")
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()

# Startup env sanity checks
print(
    "🔎 ENV CHECK | "
    f"API_ID={'✅' if bool(API_ID) else '❌'} "
    f"API_HASH={'✅' if bool(API_HASH) else '❌'} "
    f"BOT_TOKEN={'✅' if bool(BOT_TOKEN) else '❌'} "
    f"SESSION_STRING={'✅' if bool(os.getenv('SESSION_STRING','')) else '❌'}"
)
if not BOT_TOKEN:
    print("⚠️ BOT_TOKEN missing: bot commands like /start will NOT work.")
if not API_ID or not API_HASH:
    print("⚠️ API_ID/API_HASH missing: Pyrogram bot client cannot start.")


def get_all_session_strings():
    """Get all SESSION_STRING environment variables dynamically"""
    sessions = []
    first_session = os.getenv("SESSION_STRING", "")
    if first_session:
        sessions.append(("SESSION_STRING", first_session))
    for i in range(2, 101):
        key = f"SESSION_STRING_{i}"
        value = os.getenv(key, "")
        if value:
            sessions.append((key, value))
    return sessions


# Speed settings
BATCH_SIZE = 10
DELAY_BETWEEN_BATCHES = 1
DELAY_BETWEEN_MESSAGES = 0.1

# Global state
is_forwarding = False
stop_requested = False
current_progress = {
    "success_count": 0,
    "failed_count": 0,
    "skipped_count": 0,
    "total_count": 0,
    "current_id": 0,
    "start_id": 0,
    "end_id": 0,
    "is_active": False,
    "speed": 0,
    "rate_limit_hits": 0,
    "active_accounts": 0
}

# Auto-approve state
auto_approve_channels = set()
auto_approve_stats = {"approved": 0, "failed": 0}

# Logo/Watermark state
logo_config = {
    "enabled": False,
    "logo_file_id": None,
    "text": None,
    "position": "bottom-right",
    "opacity": 128,
    "size": 20
}
logo_stats = {"watermarked": 0, "failed": 0}

# Content Moderation state
moderation_config = {}
moderation_stats = {"deleted_forward": 0, "deleted_links": 0, "deleted_badwords": 0, "deleted_mentions": 0, "warnings": 0, "bans": 0, "auto_deleted": 0}
user_warnings = {}

# Auto-delete message queue
auto_delete_queue = {}
MAX_WARNINGS = 3

# Bad words list
BAD_WORDS = [
    "sex", "xxx", "porn", "nude", "naked", "fuck", "bitch", "ass", "dick", "pussy",
    "boobs", "tits", "cock", "cum", "horny", "slut", "whore", "sexy", "adult",
    "vagina", "penis", "orgasm", "masturbat", "blowjob", "handjob", "dildo",
    "nipple", "erotic", "seduce", "onlyfans", "xvideos", "pornhub", "xnxx",
    "milf", "threesome", "gangbang", "creampie", "anal", "69",
    "chut", "lund", "gaand", "bhosdike", "madarchod", "behenchod", "chutiya",
    "randi", "harami", "kamina", "gandu", "lawde", "sala", "kutta", "kutti",
    "chod", "muth", "jhant", "boor", "bund", "chuchi", "boobs", "raand",
    "chakka", "hijra", "dalla", "dalal", "pataka", "maal", "item",
    "chodne", "chudai", "chudwana", "land", "lauda", "loda", "choot",
    "bhadwa", "bhadwe", "bsdk", "mc", "bc", "mkc", "bkc"
]

# Pyrogram clients
user_clients = []
bot_client = None
bot_watchdog_task = None
current_client_index = 0


def load_logo_config():
    global logo_config
    if logo_col is not None:
        saved = logo_col.find_one({})
        if saved:
            logo_config.update({
                "enabled": saved.get("enabled", False),
                "logo_file_id": saved.get("logo_file_id"),
                "text": saved.get("text"),
                "position": saved.get("position", "bottom-right"),
                "opacity": saved.get("opacity", 128),
                "size": saved.get("size", 20)
            })


def save_logo_config():
    if logo_col is not None:
        logo_col.update_one(
            {},
            {"$set": {**logo_config, "updated_at": datetime.utcnow()}},
            upsert=True
        )


def load_public_access():
    global public_access_enabled
    if bot_settings_col is not None:
        saved = bot_settings_col.find_one({"setting": "public_access"})
        if saved:
            public_access_enabled = saved.get("enabled", False)


def save_public_access(enabled):
    global public_access_enabled
    public_access_enabled = enabled
    if bot_settings_col is not None:
        bot_settings_col.update_one(
            {"setting": "public_access"},
            {"$set": {"enabled": enabled, "updated_at": datetime.utcnow()}},
            upsert=True
        )


def load_moderation_config(chat_id):
    global moderation_config
    if moderation_col is not None:
        saved = moderation_col.find_one({"chat_id": chat_id})
        if saved:
            moderation_config[chat_id] = {
                "enabled": saved.get("enabled", False),
                "block_forward": saved.get("block_forward", False),
                "block_links": saved.get("block_links", False),
                "block_badwords": saved.get("block_badwords", False),
                "block_mentions": saved.get("block_mentions", False),
                "auto_delete_2min": saved.get("auto_delete_2min", False)
            }
            return moderation_config[chat_id]
    return {"enabled": False, "block_forward": False, "block_links": False, "block_badwords": False, "block_mentions": False, "auto_delete_2min": False}


def save_moderation_config(chat_id):
    if moderation_col is not None and chat_id in moderation_config:
        moderation_col.update_one(
            {"chat_id": chat_id},
            {"$set": {
                **moderation_config[chat_id],
                "chat_id": chat_id,
                "updated_at": datetime.utcnow()
            }},
            upsert=True
        )


def contains_link(text):
    url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    tg_pattern = r'(?:t\.me|telegram\.me)/[a-zA-Z0-9_]+'
    if re.search(url_pattern, text, re.IGNORECASE):
        return True
    if re.search(tg_pattern, text, re.IGNORECASE):
        return True
    return False


def contains_mention(text):
    mention_pattern = r'@[a-zA-Z0-9_]{3,}'
    return bool(re.search(mention_pattern, text))


def contains_bad_words(text):
    text_lower = text.lower()
    for word in BAD_WORDS:
        if word in text_lower:
            return True
    return False


def get_config():
    if config_col is not None:
        return config_col.find_one({}) or {}
    return {}


def save_config(source_channel, dest_channel):
    if config_col is not None:
        config_col.update_one(
            {},
            {"$set": {
                "source_channel": source_channel,
                "dest_channel": dest_channel,
                "updated_at": datetime.utcnow()
            }},
            upsert=True
        )


def load_force_subscribe():
    global force_subscribe_channels
    force_subscribe_channels = []
    
    if force_sub_col is not None:
        channels = force_sub_col.find({})
        for ch in channels:
            force_subscribe_channels.append({
                "channel_id": ch.get("channel_id"),
                "channel_name": ch.get("channel_name", "Channel"),
                "invite_link": ch.get("invite_link", "")
            })
    
    channels_env = os.getenv("FORCE_SUB_CHANNELS", "")
    names_env = os.getenv("FORCE_SUB_CHANNEL_NAMES", "")
    links_env = os.getenv("FORCE_SUB_LINKS", "")
    
    if channels_env:
        channel_ids = [c.strip() for c in channels_env.split(",") if c.strip()]
        channel_names = [n.strip() for n in names_env.split(",") if n.strip()] if names_env else []
        channel_links = [l.strip() for l in links_env.split(",") if l.strip()] if links_env else []
        
        for i, channel_id in enumerate(channel_ids):
            channel_name = channel_names[i] if i < len(channel_names) else f"Channel {i+1}"
            invite_link = channel_links[i] if i < len(channel_links) else ""
            existing = [ch for ch in force_subscribe_channels if ch["channel_id"] == channel_id]
            if not existing:
                force_subscribe_channels.append({
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "invite_link": invite_link
                })
                print(f"📢 Force sub from env: {channel_name} ({channel_id})")
    
    for i in range(1, 51):
        env_var = os.getenv(f"FORCE_SUB_{i}", "")
        if env_var:
            parts = env_var.split("|")
            channel_id = parts[0].strip()
            channel_name = parts[1].strip() if len(parts) > 1 else channel_id
            invite_link = parts[2].strip() if len(parts) > 2 else ""
            existing = [ch for ch in force_subscribe_channels if ch["channel_id"] == channel_id]
            if not existing:
                force_subscribe_channels.append({
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "invite_link": invite_link
                })
                print(f"📢 Force sub from env: {channel_name} ({channel_id})")
    
    print(f"📢 Total force subscribe channels: {len(force_subscribe_channels)}")
    return force_subscribe_channels


def add_force_subscribe(channel_id, channel_name, invite_link):
    global force_subscribe_channels
    if force_sub_col is not None:
        existing = force_sub_col.find_one({"channel_id": str(channel_id)})
        if existing:
            return False
        force_sub_col.insert_one({
            "channel_id": str(channel_id),
            "channel_name": channel_name,
            "invite_link": invite_link,
            "added_at": datetime.utcnow()
        })
        force_subscribe_channels.append({
            "channel_id": str(channel_id),
            "channel_name": channel_name,
            "invite_link": invite_link
        })
        return True
    force_subscribe_channels.append({
        "channel_id": str(channel_id),
        "channel_name": channel_name,
        "invite_link": invite_link
    })
    return True


def remove_force_subscribe(channel_id):
    global force_subscribe_channels
    if force_sub_col is not None:
        force_sub_col.delete_one({"channel_id": str(channel_id)})
    force_subscribe_channels = [ch for ch in force_subscribe_channels if ch["channel_id"] != str(channel_id)]
    return True


# Admin Groups Functions
async def save_admin_group(chat_id, chat_title, chat_type, member_count, permissions, username=None, invite_link=None):
    """Save a group where bot is admin to MongoDB"""
    if admin_groups_col is not None:
        admin_groups_col.update_one(
            {"chat_id": str(chat_id)},
            {"$set": {
                "chat_id": str(chat_id),
                "chat_title": chat_title,
                "chat_type": chat_type,
                "member_count": member_count,
                "permissions": permissions,
                "username": username,
                "invite_link": invite_link,
                "updated_at": datetime.utcnow()
            }},
            upsert=True
        )
        print(f"✅ Saved admin group: {chat_title} ({chat_id})")


async def remove_admin_group(chat_id):
    """Remove a group from admin groups"""
    if admin_groups_col is not None:
        admin_groups_col.delete_one({"chat_id": str(chat_id)})


def get_all_admin_groups():
    """Get all groups where bot is admin from MongoDB"""
    if admin_groups_col is not None:
        groups = list(admin_groups_col.find({}))
        return groups
    return []


async def refresh_admin_groups(client):
    """Refresh admin groups by verifying existing saved groups.
    
    Bot accounts cannot enumerate dialogs, so we only verify
    groups already saved in the database (from on_chat_member_updated events).
    """
    print("🔄 Refreshing admin groups/channels...", flush=True)
    
    # Get all saved groups and verify each one
    saved_groups = get_all_admin_groups()
    admin_count = 0
    removed_count = 0
    checked_count = 0
    
    print(f"🔍 Found {len(saved_groups)} saved groups to verify", flush=True)
    
    for group in saved_groups:
        chat_id = group.get("chat_id")
        if not chat_id:
            continue
        
        checked_count += 1
        print(f"🔄 Checking chat: {chat_id} - {group.get('chat_title', 'Unknown')}", flush=True)
        
        try:
            chat_id_int = int(chat_id)
            
            # Get chat info
            try:
                chat = await client.get_chat(chat_id_int)
                print(f"✅ Got chat info: {chat.title}", flush=True)
            except Exception as e:
                print(f"❌ Failed to get chat {chat_id}: {type(e).__name__}: {e}", flush=True)
                await remove_admin_group(chat_id_int)
                removed_count += 1
                continue
            
            # Get bot member status
            try:
                me = await client.get_chat_member(chat_id_int, "me")
                print(f"📍 Bot status in {chat.title}: {me.status}", flush=True)
            except Exception as e:
                print(f"❌ Failed to get member status for {chat_id}: {type(e).__name__}: {e}", flush=True)
                await remove_admin_group(chat_id_int)
                removed_count += 1
                continue
            
            admin_statuses = [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
            if me.status in admin_statuses:
                # Update info
                permissions = {}
                if hasattr(me, "privileges") and me.privileges:
                    permissions = {
                        "can_delete_messages": getattr(me.privileges, "can_delete_messages", False),
                        "can_restrict_members": getattr(me.privileges, "can_restrict_members", False),
                        "can_promote_members": getattr(me.privileges, "can_promote_members", False),
                        "can_change_info": getattr(me.privileges, "can_change_info", False),
                        "can_invite_users": getattr(me.privileges, "can_invite_users", False),
                        "can_pin_messages": getattr(me.privileges, "can_pin_messages", False),
                        "can_manage_chat": getattr(me.privileges, "can_manage_chat", False),
                    }
                
                member_count = getattr(chat, "members_count", None) or 0
                username = getattr(chat, "username", None)
                
                # Get invite link for private chats
                invite_link = None
                if not username:
                    # First try to get existing invite link from chat info
                    invite_link = getattr(chat, "invite_link", None)
                    print(f"📎 Existing invite_link from chat: {invite_link}", flush=True)
                    
                    # If no existing link, try to create one
                    if not invite_link:
                        try:
                            invite_link = await client.export_chat_invite_link(chat_id_int)
                            print(f"📎 Generated invite_link: {invite_link}", flush=True)
                        except Exception as e:
                            print(f"⚠️ Could not generate invite link for {chat.title}: {type(e).__name__}: {e}", flush=True)
                            # Fallback to internal link format
                            chat_id_str = str(abs(chat_id_int))
                            if chat_id_str.startswith("100"):
                                chat_id_str = chat_id_str[3:]
                            invite_link = f"https://t.me/c/{chat_id_str}"
                            print(f"📎 Using fallback link: {invite_link}", flush=True)
                else:
                    print(f"📎 Public username: @{username}", flush=True)
                
                await save_admin_group(
                    chat_id_int,
                    chat.title,
                    str(chat.type),
                    member_count,
                    permissions,
                    username,
                    invite_link,
                )
                admin_count += 1
                print(f"✅ Verified: {chat.title}", flush=True)
            else:
                # No longer admin - remove
                await remove_admin_group(chat_id_int)
                removed_count += 1
                print(f"➖ Removed {chat.title} - no longer admin (status: {me.status})", flush=True)
                
        except (ChannelPrivate, Forbidden) as e:
            # Bot kicked or channel made private - remove
            await remove_admin_group(int(chat_id))
            removed_count += 1
            print(f"➖ Removed chat {chat_id} - access denied: {e}", flush=True)
        except Exception as e:
            print(f"⚠️ Error checking chat {chat_id}: {type(e).__name__}: {e}", flush=True)
    
    print(f"✅ Done! Verified {admin_count}, removed {removed_count}, checked {checked_count}", flush=True)
    return {"verified": admin_count, "removed": removed_count, "checked": checked_count}


async def auto_track_admin_group(client, chat):
    """Auto-track a group/channel when bot is added as admin."""
    try:
        me = await client.get_chat_member(chat.id, "me")
        admin_statuses = [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
        if me.status in admin_statuses:
            permissions = {}
            if hasattr(me, "privileges") and me.privileges:
                permissions = {
                    "can_delete_messages": getattr(me.privileges, "can_delete_messages", False),
                    "can_restrict_members": getattr(me.privileges, "can_restrict_members", False),
                    "can_promote_members": getattr(me.privileges, "can_promote_members", False),
                    "can_change_info": getattr(me.privileges, "can_change_info", False),
                    "can_invite_users": getattr(me.privileges, "can_invite_users", False),
                    "can_pin_messages": getattr(me.privileges, "can_pin_messages", False),
                    "can_manage_chat": getattr(me.privileges, "can_manage_chat", False),
                }
            
            member_count = getattr(chat, "members_count", None) or 0
            username = getattr(chat, "username", None)
            
            # Get invite link for private chats
            invite_link = None
            if not username:
                # First try to get existing invite link from chat info
                invite_link = getattr(chat, "invite_link", None)
                
                # If no existing link, try to create one
                if not invite_link:
                    try:
                        invite_link = await client.export_chat_invite_link(chat.id)
                    except Exception as e:
                        print(f"⚠️ Could not generate invite link for {chat.title}: {e}", flush=True)
                        # Fallback to internal link format
                        chat_id_str = str(abs(chat.id))
                        if chat_id_str.startswith("100"):
                            chat_id_str = chat_id_str[3:]
                        invite_link = f"https://t.me/c/{chat_id_str}"
            
            await save_admin_group(
                chat.id,
                chat.title,
                str(chat.type),
                member_count,
                permissions,
                username,
                invite_link,
            )
            print(f"✅ Auto-tracked admin group: {chat.title} (link: {username or invite_link})", flush=True)
            return True
    except Exception as e:
        print(f"⚠️ Error auto-tracking {getattr(chat, 'title', chat.id)}: {e}", flush=True)
    return False


# Flask routes
@flask_app.route("/")
def home():
    return jsonify({
        "status": "running",
        "build_marker": BUILD_MARKER,
        "bot_connected": bot_client is not None and bot_client.is_connected if bot_client else False
    })


@flask_app.route("/health")
def health():
    return jsonify({"status": "ok", "build": BUILD_MARKER})


@flask_app.route("/admin-groups", methods=["GET"])
def api_admin_groups():
    """Get all groups where bot is admin"""
    groups = get_all_admin_groups()
    result = []
    for g in groups:
        result.append({
            "chat_id": g.get("chat_id"),
            "chat_title": g.get("chat_title"),
            "chat_type": g.get("chat_type"),
            "member_count": g.get("member_count", 0),
            "permissions": g.get("permissions", {}),
            "updated_at": str(g.get("updated_at", ""))
        })
    return jsonify({"groups": result, "count": len(result)})


@flask_app.route("/refresh-admin-groups", methods=["POST"])
def api_refresh_admin_groups():
    """Trigger refresh of admin groups"""
    if bot_client and bot_client.is_connected:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            count = loop.run_until_complete(refresh_admin_groups(bot_client))
            return jsonify({"success": True, "count": count, "message": f"Found {count} admin groups"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            loop.close()
    return jsonify({"success": False, "error": "Bot not connected"}), 503


def run_flask():
    """Run Flask in a separate thread"""
    flask_app.run(host="0.0.0.0", port=8000, debug=False, use_reloader=False)


# Main entry point
if __name__ == "__main__":
    print("🚀 Starting bot...")
    
    # Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("✅ Flask server started on port 8000")
    
    # Initialize Pyrogram bot client
    if API_ID and API_HASH and BOT_TOKEN:
        bot_client = Client(
            "bot_client",
            api_id=int(API_ID),
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            in_memory=True
        )
        
        @bot_client.on_message(filters.command("start") & filters.private)
        async def start_command(client, message):
            await message.reply_text(
                "👋 **Welcome to Channel Forwarder Bot!**\n\n"
                "I can help you forward messages between channels.\n\n"
                "Use /help to see available commands.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Help", callback_data="help")]
                ])
            )
        
        @bot_client.on_message(filters.command("help") & filters.private)
        async def help_command(client, message):
            await message.reply_text(
                "📚 **Available Commands:**\n\n"
                "/start - Start the bot\n"
                "/help - Show this help message\n"
                "/addgroup - Add/scan one group/channel (no message in group)\n"
                "/admingroups - List groups/channels where bot is admin\n"
                "/refreshgroups - Refresh saved admin groups list"
            )

        @bot_client.on_message(filters.command("addgroup") & filters.private)
        async def addgroup_command(client, message):
            if not message.from_user or message.from_user.id not in ADMIN_IDS:
                await message.reply_text("❌ You are not authorized.")
                return

            # Usage:
            # 1) /addgroup @username
            # 2) /addgroup -1001234567890
            # 3) Reply to a forwarded message from the group/channel with /addgroup
            chat_ref = None

            # If admin replied to a forwarded message, extract chat from forward
            if message.reply_to_message and getattr(message.reply_to_message, "forward_from_chat", None):
                chat_ref = message.reply_to_message.forward_from_chat.id
            else:
                parts = message.text.split(maxsplit=1)
                if len(parts) == 2:
                    chat_ref = parts[1].strip()

            if not chat_ref:
                await message.reply_text(
                    "➕ **Add group/channel for scanning**\n\n"
                    "Send one of these:\n"
                    "• `/addgroup @channelusername`\n"
                    "• `/addgroup -1001234567890`\n\n"
                    "Or: **forward any message from that group/channel** to me, then reply to it with `/addgroup`.\n\n"
                    "(This does **not** send any message in the group.)",
                    disable_web_page_preview=True,
                )
                return

            try:
                # Normalize numeric IDs
                if isinstance(chat_ref, str) and (chat_ref.lstrip("-").isdigit()):
                    chat_ref = int(chat_ref)

                chat = await client.get_chat(chat_ref)
            except Exception as e:
                await message.reply_text(f"❌ Chat not found / access denied. ({type(e).__name__}: {e})")
                return

            tracked = await auto_track_admin_group(client, chat)
            if not tracked:
                await message.reply_text(
                    "⚠️ I can see the chat, but I'm **not admin** there.\n\n"
                    "Make me **Admin** in that group/channel, then run `/addgroup` again.",
                    disable_web_page_preview=True,
                )
                return

            await message.reply_text(
                f"✅ Added/updated: **{chat.title}**\n\nNow use /admingroups to see the full list.",
                disable_web_page_preview=True,
            )

        @bot_client.on_message(filters.command("admingroups") & filters.private)
        async def admingroups_command(client, message):
            if message.from_user.id not in ADMIN_IDS:
                await message.reply_text("❌ You are not authorized.")
                return
            
            groups = get_all_admin_groups()
            if not groups:
                await message.reply_text("📭 No admin groups/channels found. Use /refreshgroups to scan.")
                return
            
            text = "📋 **Groups/Channels where bot is admin:**\n\n"
            for g in groups[:20]:
                # Determine link
                username = g.get('username')
                invite_link = g.get('invite_link')
                if username:
                    link = f"https://t.me/{username}"
                elif invite_link:
                    link = invite_link
                else:
                    link = None
                
                text += f"• **{g.get('chat_title', 'Unknown')}**\n"
                text += f"  ID: `{g.get('chat_id')}`\n"
                text += f"  Type: {g.get('chat_type', '')}\n"
                text += f"  Members: {g.get('member_count', 0)}\n"
                if link:
                    text += f"  🔗 {link}\n"
                text += "\n"
            
            if len(groups) > 20:
                text += f"\n_...and {len(groups) - 20} more chats_"
            
            await message.reply_text(text, disable_web_page_preview=True)
        
        @bot_client.on_message(filters.command("refreshgroups") & filters.private)
        async def refreshgroups_command(client, message):
            if message.from_user.id not in ADMIN_IDS:
                await message.reply_text("❌ You are not authorized.")
                return
            
            msg = await message.reply_text("🔄 Refreshing admin groups/channels...")
            result = await refresh_admin_groups(client)
            
            verified = result.get("verified", 0)
            removed = result.get("removed", 0)
            checked = result.get("checked", 0)
            
            await msg.edit_text(
                f"✅ **Refresh Complete!**\n\n"
                f"📊 **Results:**\n"
                f"• Verified: **{verified}** groups/channels\n"
                f"• Removed: **{removed}** (no longer admin)\n"
                f"• Total checked: **{checked}**\n\n"
                f"💡 _Note: Telegram bots cannot auto-scan all chats. If Total checked is 0, add chats using /addgroup (via @username, ID, or forwarding a message) then run /refreshgroups._"
            )
        
        # Auto-track when bot is added to a group or made admin
        @bot_client.on_chat_member_updated()
        async def on_chat_member_update(client, chat_member_updated):
            """Auto-track when bot becomes admin in a group/channel."""
            try:
                # Check if this update is about the bot itself
                new_member = chat_member_updated.new_chat_member
                if not new_member:
                    return
                
                bot_id = (await client.get_me()).id
                if new_member.user.id != bot_id:
                    return
                
                chat = chat_member_updated.chat
                if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL]:
                    return
                
                # Check if bot became admin
                if new_member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                    await auto_track_admin_group(client, chat)
                elif new_member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
                    # Bot demoted or left - remove from tracked
                    await remove_admin_group(chat.id)
                    print(f"➖ Removed {chat.title} - bot demoted/left", flush=True)
            except Exception as e:
                print(f"⚠️ Error in chat_member_update: {e}", flush=True)
        
        # Also track when bot receives any message in a group (fallback for missed events)
        @bot_client.on_message(filters.group | filters.channel, group=99)
        async def auto_track_on_message(client, message):
            """Auto-track group silently when bot sees activity (NO replies in group)."""
            chat = message.chat
            if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL]:
                return

            # Best-effort update; do not reply in the group
            await auto_track_admin_group(client, chat)
            return
        # Run bot
        print("🤖 Starting Pyrogram bot client...")
        bot_client.run()
    else:
        print("❌ Missing API_ID, API_HASH or BOT_TOKEN. Bot cannot start.")
        # Keep Flask running
        while True:
            time.sleep(60)
