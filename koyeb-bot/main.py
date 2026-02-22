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
from pyrogram import Client, filters, idle, StopPropagation

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# Backward/forward compatibility: some builds may reference filters.supergroup
if not hasattr(filters, "supergroup"):
    filters.supergroup = filters.group

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
from pyrogram.enums import ChatType, ChatMemberStatus, ParseMode
from pyrogram.errors import (
    FloodWait,
    SlowmodeWait,
    ChatAdminRequired,
    ChannelPrivate,
    MessageNotModified,
    ChatWriteForbidden,
    Forbidden,
    PeerIdInvalid,
    UserNotParticipant,
)

# Chat type helper: match both GROUP and SUPERGROUP reliably
GROUP_CHAT = filters.group | filters.supergroup
from pymongo import MongoClient
from dotenv import load_dotenv
import threading
from PIL import Image, ImageDraw, ImageFont
from translations import TRANSLATIONS, t, get_user_lang

# Build marker (changes on each code update) to verify Koyeb is running the latest image
BUILD_MARKER = "2025-12-24T22:20:00Z"
print(f"✅ BOT BUILD_MARKER: {BUILD_MARKER}", flush=True)

load_dotenv()
flask_app = Flask(__name__)
# Alias for WSGI servers like gunicorn (some platforms expect `app`)
app = flask_app

# CORS: allow the web dashboard to call this API from a different domain
@flask_app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


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
group_forcejoin_col = db["group_forcejoin"] if db is not None else None  # Force join config per group
new_member_wait_col = db["new_member_wait"] if db is not None else None  # Join-wait config per group
joinwait_invites_col = db["joinwait_invites"] if db is not None else None  # Per-user invite/add counters
broadcast_users_col = db["broadcast_users"] if db is not None else None  # Users who interacted with bot
broadcast_groups_col = db["broadcast_groups"] if db is not None else None  # Groups where bot is admin
admin_groups_col = db["admin_groups"] if db is not None else None  # Groups where bot is admin with permissions

# Force join config per group: {chat_id: {"channel_id": "", "channel_name": "", "invite_link": ""}}
group_forcejoin_config = {}

# Join-wait config: {chat_id: {"enabled": True, "required_adds": 3}}
new_member_wait_config = {}

# Cached group admins to avoid per-message API calls (speeds up instant delete)
GROUP_ADMIN_CACHE = {}  # {chat_id: {"ts": float, "ids": set[int]}}
GROUP_ADMIN_CACHE_TTL = int(os.getenv("GROUP_ADMIN_CACHE_TTL", "45"))  # seconds

# Public access control
public_access_enabled = False  # Default: only admins can use bot

# User state for channel input
user_channel_state = {}  # {user_id: "waiting_add_channel"}

# Forward wizard state
forward_wizard_state = {}  # {user_id: {"state": "...", "source_channel": "", "source_title": "", "skip_number": 0, "last_message_id": 0}}

# Active forwarding progress per user
user_forward_progress = {}  # {user_id: {progress data...}}

# Force subscribe channels list (loaded from DB)
force_subscribe_channels = []  # [{"channel_id": "", "channel_name": "", "invite_link": ""}]

# Admin IDs (loaded from env) - supports both ADMIN_IDS and ADMIN_USER_ID
ADMIN_IDS = set()
admin_ids_env = os.getenv("ADMIN_IDS", "") or os.getenv("ADMIN_USER_ID", "")
if admin_ids_env:
    ADMIN_IDS = set(int(x.strip()) for x in admin_ids_env.split(",") if x.strip().isdigit())

# Bot admin ids (used by /approveall etc.)
BOT_ADMINS = ADMIN_IDS

# Referral requirement
REQUIRED_REFERRALS = int(os.getenv("REQUIRED_REFERRALS", "10"))

# User account credentials (MTProto)
API_ID = os.getenv("API_ID", "")
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()

# Startup env sanity checks (helps debug "bot not responding" issues on hosts)
print(
    "🔎 ENV CHECK | "
    f"API_ID={'✅' if bool(API_ID) else '❌'} "
    f"API_HASH={'✅' if bool(API_HASH) else '❌'} "
    f"BOT_TOKEN={'✅' if bool(BOT_TOKEN) else '❌'} "
    f"SESSION_STRING={'✅' if bool(os.getenv('SESSION_STRING','')) else '❌'}"
)
if not BOT_TOKEN:
    print("⚠️ BOT_TOKEN missing: bot commands like /start will NOT work. Add BOT_TOKEN in your host environment variables.")
if not API_ID or not API_HASH:
    print("⚠️ API_ID/API_HASH missing: Pyrogram bot client cannot start. Add API_ID and API_HASH in your host environment variables.")

def get_all_session_strings():
    """Get all SESSION_STRING environment variables dynamically"""
    sessions = []
    
    # Check for SESSION_STRING (first one)
    first_session = os.getenv("SESSION_STRING", "")
    if first_session:
        sessions.append(("SESSION_STRING", first_session))
    
    # Check for SESSION_STRING_2, SESSION_STRING_3, ... up to 100
    for i in range(2, 101):
        key = f"SESSION_STRING_{i}"
        value = os.getenv(key, "")
        if value:
            sessions.append((key, value))
    
    return sessions


# Speed settings - More accounts = higher speed
BATCH_SIZE = 10  # Messages per batch per account
DELAY_BETWEEN_BATCHES = 1  # Reduced delay with multiple accounts
DELAY_BETWEEN_MESSAGES = 0.1  # 100ms between individual messages

# Global state
is_forwarding = False
stop_requested = False

# User language preferences {user_id: "lang_code"} - imported from translations.py
from translations import user_languages

# Language collection in MongoDB
lang_col = db["user_languages"] if db is not None else None

def load_user_language(user_id):
    """Load user language from DB"""
    if lang_col is not None:
        doc = lang_col.find_one({"user_id": user_id})
        if doc:
            user_languages[user_id] = doc.get("lang", "en")
            return doc.get("lang", "en")
    return "en"

def save_user_language(user_id, lang_code):
    """Save user language to DB"""
    user_languages[user_id] = lang_code
    if lang_col is not None:
        lang_col.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "lang": lang_code, "updated_at": datetime.utcnow()}},
            upsert=True,
        )

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
auto_approve_channels = set()  # Set of channel IDs with auto-approve enabled
auto_approve_stats = {"approved": 0, "failed": 0}

# Logo/Watermark state
logo_config = {
    "enabled": False,
    "logo_file_id": None,  # Telegram file_id of logo image
    "text": None,  # Text watermark
    "position": "bottom-right",  # Position: top-left, top-right, bottom-left, bottom-right, center
    "opacity": 128,  # 0-255
    "size": 20  # Percentage of image size
}
logo_stats = {"watermarked": 0, "failed": 0}

# Content Moderation state
moderation_config = {}  # {chat_id: {block_forward, block_links, block_badwords, block_mentions, auto_delete_2min, enabled}}
moderation_stats = {"deleted_forward": 0, "deleted_links": 0, "deleted_badwords": 0, "deleted_mentions": 0, "warnings": 0, "bans": 0, "auto_deleted": 0}
user_warnings = {}  # {(chat_id, user_id): warning_count}

# Auto-delete message queue: {chat_id: [(message_id, timestamp), ...]}
auto_delete_queue = {}
MAX_WARNINGS = 3  # Default, overridden per-group via warning_config

# Warning config per group: {chat_id: {"punishment": "mute", "max_warns": 3, "mute_duration": 3}}
# punishment: "off", "kick", "mute", "ban"
warning_config = {}  # loaded from DB per group

warning_config_col = db["warning_config"] if db is not None else None


def load_warning_config(chat_id):
    """Load warning config for a group from DB"""
    global warning_config
    if warning_config_col is not None:
        saved = warning_config_col.find_one({"chat_id": chat_id})
        if saved:
            warning_config[chat_id] = {
                "punishment": saved.get("punishment", "mute"),
                "max_warns": saved.get("max_warns", 3),
                "mute_duration": saved.get("mute_duration", 3),
            }
            return warning_config[chat_id]
    return {"punishment": "mute", "max_warns": 3, "mute_duration": 3}


def save_warning_config(chat_id):
    """Save warning config for a group to DB"""
    if warning_config_col is not None and chat_id in warning_config:
        warning_config_col.update_one(
            {"chat_id": chat_id},
            {"$set": {**warning_config[chat_id], "chat_id": chat_id, "updated_at": datetime.utcnow()}},
            upsert=True,
        )


def get_warning_config(chat_id):
    """Get warning config for a group, loading from DB if needed"""
    if chat_id not in warning_config:
        load_warning_config(chat_id)
    return warning_config.get(chat_id, {"punishment": "mute", "max_warns": 3, "mute_duration": 3})


def build_warnings_menu(chat_id):
    """Build warnings submenu text and keyboard"""
    wc = get_warning_config(chat_id)
    punishment = wc.get("punishment", "mute")
    max_w = wc.get("max_warns", 3)
    mute_dur = wc.get("mute_duration", 3)
    punishment_labels = {"off": "Off", "kick": "Kick", "mute": "Mute", "ban": "Ban"}
    current_p = punishment_labels.get(punishment, "Mute")

    text = (
        "❗ <b>User warnings</b>\n"
        "The warning system allows you to give <u>warnings to users</u> for incorrect behavior in the group, before actually punishing them.\n\n"
        "From this menu you can set:\n"
        " • the <u>punishment</u> for users who exceed the maximum of warnings allowed\n"
        " • the <u>maximum number</u> of warns allowed\n"
        " • the <u>mute duration</u> when punishment is Mute\n\n"
        f"<b>Punishment:</b> {current_p}\n"
        f"<b>Max Warns allowed:</b> {max_w}\n"
        f"<b>Mute Duration:</b> {mute_dur}h"
    )

    def p_label(key, emoji, lbl):
        return f"{emoji} {lbl} ✅" if punishment == key else f"{emoji} {lbl}"

    max_warn_buttons = []
    for n in range(2, 7):
        label = f"{n} ✅" if n == max_w else str(n)
        max_warn_buttons.append(InlineKeyboardButton(label, callback_data=f"warn_maxw_{n}"))

    keyboard = [
        [InlineKeyboardButton("📋 Warned List", callback_data="warn_list")],
        [
            InlineKeyboardButton(p_label("off", "✖", "Off"), callback_data="warn_p_off"),
            InlineKeyboardButton(p_label("kick", "❗", "Kick"), callback_data="warn_p_kick"),
        ],
        [
            InlineKeyboardButton(p_label("mute", "🔇", "Mute"), callback_data="warn_p_mute"),
            InlineKeyboardButton(p_label("ban", "🚫", "Ban"), callback_data="warn_p_ban"),
        ],
        [InlineKeyboardButton(f"🔇⏱ Set mute duration ({mute_dur}h)", callback_data="warn_mute_dur_menu")],
        max_warn_buttons,
        [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
    ]
    return text, InlineKeyboardMarkup(keyboard)

# Bad words list for content filtering (Hindi + English inappropriate/sexual words)
BAD_WORDS = [
    # English sexual words
    "sex", "xxx", "porn", "nude", "naked", "fuck", "bitch", "ass", "dick", "pussy",
    "boobs", "tits", "cock", "cum", "horny", "slut", "whore", "sexy", "adult",
    "vagina", "penis", "orgasm", "masturbat", "blowjob", "handjob", "dildo",
    "nipple", "erotic", "seduce", "onlyfans", "xvideos", "pornhub", "xnxx",
    "milf", "threesome", "gangbang", "creampie", "anal", "69",
    # Hindi/Urdu sexual/abusive words  
    "chut", "lund", "gaand", "bhosdike", "madarchod", "behenchod", "chutiya",
    "randi", "harami", "kamina", "gandu", "lawde", "sala", "kutta", "kutti",
    "chod", "muth", "jhant", "boor", "bund", "chuchi", "boobs", "raand",
    "chakka", "hijra", "dalla", "dalal", "pataka", "maal", "item",
    "chodne", "chudai", "chudwana", "land", "lauda", "loda", "choot",
    "bhadwa", "bhadwe", "bsdk", "mc", "bc", "mkc", "bkc"
]

# Pyrogram clients - Multiple user accounts for speed
user_clients = []  # List of (name, client) tuples
bot_client = None   # Bot for commands/UI
bot_watchdog_task = None  # Background task to auto-recover bot polling
current_client_index = 0  # For round-robin rotation


def load_logo_config():
    """Load logo config from database"""
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
    """Save logo config to database"""
    if logo_col is not None:
        logo_col.update_one(
            {},
            {"$set": {**logo_config, "updated_at": datetime.utcnow()}},
            upsert=True
        )


def load_public_access():
    """Load public access setting from database"""
    global public_access_enabled
    if bot_settings_col is not None:
        saved = bot_settings_col.find_one({"setting": "public_access"})
        if saved:
            public_access_enabled = saved.get("enabled", False)


def save_public_access(enabled):
    """Save public access setting to database"""
    global public_access_enabled
    public_access_enabled = enabled
    if bot_settings_col is not None:
        bot_settings_col.update_one(
            {"setting": "public_access"},
            {"$set": {"enabled": enabled, "updated_at": datetime.utcnow()}},
            upsert=True
        )


def load_moderation_config(chat_id):
    """Load moderation config for a chat from database"""
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
                "auto_delete_2min": saved.get("auto_delete_2min", False),
                "bf_punishment": saved.get("bf_punishment", "mute"),
                "bl_punishment": saved.get("bl_punishment", "mute"),
                "bbw_punishment": saved.get("bbw_punishment", "mute"),
            }
            return moderation_config[chat_id]
    return {"enabled": False, "block_forward": False, "block_links": False, "block_badwords": False, "block_mentions": False, "auto_delete_2min": False, "bf_punishment": "mute", "bl_punishment": "mute", "bbw_punishment": "mute"}


def save_moderation_config(chat_id):
    """Save moderation config for a chat to database"""
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
    """Check if text contains any URL/link (not @mentions)"""
    import re
    url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    tg_pattern = r'(?:t\.me|telegram\.me)/[a-zA-Z0-9_]+'
    
    if re.search(url_pattern, text, re.IGNORECASE):
        return True
    if re.search(tg_pattern, text, re.IGNORECASE):
        return True
    return False


def contains_mention(text):
    """Check if text contains @username mentions"""
    import re
    # Match @username pattern (at least 3 characters after @)
    mention_pattern = r'@[a-zA-Z0-9_]{3,}'
    return bool(re.search(mention_pattern, text))


def contains_bad_words(text):
    """Check if text contains inappropriate words"""
    text_lower = text.lower()
    for word in BAD_WORDS:
        if word in text_lower:
            return True
    return False


def get_config():
    """Get bot configuration from database"""
    if config_col is not None:
        return config_col.find_one({}) or {}
    return {}


def save_config(source_channel, dest_channel):
    """Save bot configuration to database"""
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
    """Load force subscribe channels from database AND environment variables"""
    global force_subscribe_channels
    force_subscribe_channels = []
    
    # Load from database first
    if force_sub_col is not None:
        channels = force_sub_col.find({})
        for ch in channels:
            force_subscribe_channels.append({
                "channel_id": ch.get("channel_id"),
                "channel_name": ch.get("channel_name", "Channel"),
                "invite_link": ch.get("invite_link", "")
            })
    
    # Load from environment variables - New format like screenshot
    # FORCE_SUB_CHANNELS = -1002200226545,-1001234567890 (comma-separated IDs)
    # FORCE_SUB_CHANNEL_NAMES = Update Channel,My Group (comma-separated names)
    # FORCE_SUB_LINKS = https://t.me/+abc,https://t.me/+xyz (comma-separated links)
    channels_env = os.getenv("FORCE_SUB_CHANNELS", "")
    names_env = os.getenv("FORCE_SUB_CHANNEL_NAMES", "")
    links_env = os.getenv("FORCE_SUB_LINKS", "")
    
    if channels_env:
        channel_ids = [c.strip() for c in channels_env.split(",") if c.strip()]
        channel_names = [n.strip() for n in names_env.split(",") if n.strip()] if names_env else []
        channel_links = [l.strip() for l in links_env.split(",") if l.strip()] if links_env else []
        
        for i, channel_id in enumerate(channel_ids):
            # Get name (use channel_id if not provided)
            channel_name = channel_names[i] if i < len(channel_names) else f"Channel {i+1}"
            # Get link (empty if not provided)
            invite_link = channel_links[i] if i < len(channel_links) else ""
            
            # Check if already in list
            existing = [ch for ch in force_subscribe_channels if ch["channel_id"] == channel_id]
            if not existing:
                force_subscribe_channels.append({
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "invite_link": invite_link
                })
                print(f"📢 Force sub from env: {channel_name} ({channel_id})")
    
    # Also support old format (FORCE_SUB_1, FORCE_SUB_2, ... up to 50)
    # Format: FORCE_SUB_1=@channel|Channel Name|https://t.me/channel
    for i in range(1, 51):
        env_var = os.getenv(f"FORCE_SUB_{i}", "")
        if env_var:
            parts = env_var.split("|")
            channel_id = parts[0].strip()
            channel_name = parts[1].strip() if len(parts) > 1 else channel_id
            invite_link = parts[2].strip() if len(parts) > 2 else ""
            
            # Check if already in list
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
    """Add a force subscribe channel"""
    global force_subscribe_channels
    if force_sub_col is not None:
        # Check if already exists
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
    # If no DB, still add to memory
    force_subscribe_channels.append({
        "channel_id": str(channel_id),
        "channel_name": channel_name,
        "invite_link": invite_link
    })
    return True


def remove_force_subscribe(channel_id):
    """Remove a force subscribe channel"""
    global force_subscribe_channels
    if force_sub_col is not None:
        force_sub_col.delete_one({"channel_id": str(channel_id)})
    force_subscribe_channels = [ch for ch in force_subscribe_channels if ch["channel_id"] != str(channel_id)]
    return True


async def check_user_joined(client, user_id):
    """Check if user has joined all force subscribe channels"""
    if not force_subscribe_channels:
        return True, []
    
    not_joined = []
    for channel in force_subscribe_channels:
        try:
            channel_id = channel["channel_id"]
            # Try to get chat member status
            if channel_id.startswith("-"):
                chat_id = int(channel_id)
            elif channel_id.startswith("@"):
                chat_id = channel_id
            else:
                chat_id = channel_id
            
            member = await client.get_chat_member(chat_id, user_id)
            if member.status in ["left", "kicked", "banned"]:
                not_joined.append(channel)
        except Exception as e:
            # If we can't check, assume not joined
            not_joined.append(channel)
    
    return len(not_joined) == 0, not_joined


def is_admin(user_id):
    """Check if user is admin"""
    return user_id in ADMIN_IDS


async def safe_edit_message(message, text, reply_markup=None, parse_mode=None):
    """Safely edit message, ignoring MESSAGE_NOT_MODIFIED errors"""
    try:
        kwargs = {"reply_markup": reply_markup}
        if parse_mode == "html":
            kwargs["parse_mode"] = ParseMode.HTML
        elif parse_mode == "markdown":
            kwargs["parse_mode"] = ParseMode.MARKDOWN
        elif parse_mode is not None:
            kwargs["parse_mode"] = parse_mode
        await message.edit_text(text, **kwargs)
    except MessageNotModified:
        pass  # Ignore if message content is the same
    except Exception as e:
        print(f"Error editing message: {e}")


def get_referral_count(user_id):
    """Get number of users referred by this user"""
    if referrals_col is not None:
        return referrals_col.count_documents({"referrer_id": user_id})
    return 0


def get_user_referrer(user_id):
    """Get who referred this user"""
    if referrals_col is not None:
        doc = referrals_col.find_one({"user_id": user_id})
        if doc:
            return doc.get("referrer_id")
    return None


def add_referral(user_id, referrer_id):
    """Add a referral record"""
    if referrals_col is not None:
        # Check if user already has a referrer
        existing = referrals_col.find_one({"user_id": user_id})
        if existing:
            return False
        
        # Can't refer yourself
        if user_id == referrer_id:
            return False
        
        referrals_col.insert_one({
            "user_id": user_id,
            "referrer_id": referrer_id,
            "referred_at": datetime.utcnow()
        })
        return True
    return False


def get_referral_link(bot_username, user_id):
    """Generate referral link for user"""
    return f"https://t.me/{bot_username}?start=ref_{user_id}"


def save_progress():
    """Save current progress to database"""
    if progress_col is not None:
        progress_col.update_one(
            {},
            {"$set": {
                **current_progress,
                "last_updated_at": datetime.utcnow()
            }},
            upsert=True
        )


def load_progress():
    """Load progress from database"""
    global current_progress
    if progress_col is not None:
        saved = progress_col.find_one({})
        if saved:
            current_progress.update({
                "success_count": saved.get("success_count", 0),
                "failed_count": saved.get("failed_count", 0),
                "skipped_count": saved.get("skipped_count", 0),
                "total_count": saved.get("total_count", 0),
                "current_id": saved.get("current_id", 0),
                "start_id": saved.get("start_id", 0),
                "end_id": saved.get("end_id", 0),
                "is_active": saved.get("is_active", False),
                "speed": saved.get("speed", 0),
                "rate_limit_hits": saved.get("rate_limit_hits", 0),
                "active_accounts": saved.get("active_accounts", 0)
            })


def is_message_forwarded(source_channel, message_id):
    """Check if message was already forwarded"""
    if forwarded_col is not None:
        return forwarded_col.find_one({
            "source_channel": source_channel,
            "source_message_id": message_id
        }) is not None
    return False


def mark_message_forwarded(source_channel, dest_channel, message_id):
    """Mark message as forwarded"""
    if forwarded_col is not None:
        forwarded_col.insert_one({
            "source_channel": source_channel,
            "dest_channel": dest_channel,
            "source_message_id": message_id,
            "forwarded_at": datetime.utcnow()
        })


def format_forward_status(user_id):
    """Format the forward status message"""
    if user_id not in user_forward_progress:
        return "No active forwarding"
    
    p = user_forward_progress[user_id]
    elapsed_time = int(time.time() - p.get("started_at", time.time()))
    
    # Format elapsed time
    hours = elapsed_time // 3600
    minutes = (elapsed_time % 3600) // 60
    seconds = elapsed_time % 60
    elapsed_str = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"
    
    return (
        f"╔ FORWARD STATUS ╤═○:⊱\n"
        f"┃\n"
        f"┃-» 👷 ғᴇᴄʜᴇᴅ Msɢ : {p.get('fetched_msg', 0)}\n"
        f"┃\n"
        f"┃-» ✅ sᴜᴄᴄᴇssғᴜʟʟʏ Fᴡᴅ : {p.get('success_fwd', 0)}\n"
        f"┃\n"
        f"┃-» 👥 ᴅᴜᴘʟɪᴄᴀᴛᴇ Msɢ : {p.get('duplicate_msg', 0)}\n"
        f"┃\n"
        f"┃-» 🙅 Sᴋɪᴘᴘᴇᴅ Msɢ : {p.get('skipped_msg', 0)}\n"
        f"┃\n"
        f"┃-» 🔄 Fɪʟᴛᴇʀᴇᴅ Msɢ : {p.get('filtered_msg', 0)}\n"
        f"┃\n"
        f"┃-» 📊 Cᴜʀʀᴇɴᴛ Sᴛᴀᴛᴜs: {p.get('status', 'Starting')}\n"
        f"┃\n"
        f"┃-» ◇ Pᴇʀᴄᴇɴᴛᴀɢᴇ: {p.get('percentage', 0)} %\n"
        f"┃\n"
        f"┃-» 🕐 Eʟᴀᴘsᴇᴅ: {elapsed_str}\n"
        f"┃\n"
        f"┃-» ⏳ ETA: {p.get('eta', 'Calculating...')}\n"
        f"╚═ ᴘʀᴏɢʀᴇssɪɴɢ ╧═○:⊱"
    )


def format_eta(seconds):
    """Format ETA from seconds"""
    if seconds <= 0:
        return "Almost done..."
    
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


def get_next_client():
    """Get next client using round-robin rotation"""
    global current_client_index
    
    if not user_clients:
        return None
    
    client = user_clients[current_client_index][1]
    current_client_index = (current_client_index + 1) % len(user_clients)
    return client


def get_watermark_position(base_size, watermark_size, position):
    """Calculate watermark position based on position setting"""
    base_w, base_h = base_size
    wm_w, wm_h = watermark_size
    padding = 10
    
    positions = {
        "top-left": (padding, padding),
        "top-right": (base_w - wm_w - padding, padding),
        "bottom-left": (padding, base_h - wm_h - padding),
        "bottom-right": (base_w - wm_w - padding, base_h - wm_h - padding),
        "center": ((base_w - wm_w) // 2, (base_h - wm_h) // 2)
    }
    return positions.get(position, positions["bottom-right"])


def add_image_watermark(image_bytes, logo_bytes, position="bottom-right", opacity=128, size_percent=20):
    """Add image logo watermark to an image"""
    try:
        # Open base image
        base_image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
        
        # Calculate logo size (percentage of base image)
        base_w, base_h = base_image.size
        logo_w = int(base_w * size_percent / 100)
        logo_h = int(logo.size[1] * (logo_w / logo.size[0]))
        logo = logo.resize((logo_w, logo_h), Image.Resampling.LANCZOS)
        
        # Adjust opacity
        if opacity < 255:
            alpha = logo.split()[3]
            alpha = alpha.point(lambda p: int(p * opacity / 255))
            logo.putalpha(alpha)
        
        # Get position
        pos = get_watermark_position(base_image.size, logo.size, position)
        
        # Paste logo
        base_image.paste(logo, pos, logo)
        
        # Convert back to RGB for JPEG
        output = io.BytesIO()
        if base_image.mode == 'RGBA':
            rgb_image = Image.new('RGB', base_image.size, (255, 255, 255))
            rgb_image.paste(base_image, mask=base_image.split()[3])
            rgb_image.save(output, format='JPEG', quality=95)
        else:
            base_image.save(output, format='JPEG', quality=95)
        
        output.seek(0)
        return output.getvalue()
    except Exception as e:
        print(f"Error adding image watermark: {e}")
        return None


def add_text_watermark(image_bytes, text, position="bottom-right", opacity=128):
    """Add text watermark to an image"""
    try:
        base_image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        
        # Create text layer
        txt_layer = Image.new('RGBA', base_image.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(txt_layer)
        
        # Try to use a font, fallback to default
        try:
            font_size = max(20, base_image.size[0] // 20)
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except:
            font = ImageFont.load_default()
        
        # Get text size
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        
        # Get position
        pos = get_watermark_position(base_image.size, (text_w, text_h), position)
        
        # Draw text with shadow
        shadow_offset = 2
        draw.text((pos[0] + shadow_offset, pos[1] + shadow_offset), text, font=font, fill=(0, 0, 0, opacity))
        draw.text(pos, text, font=font, fill=(255, 255, 255, opacity))
        
        # Composite
        result = Image.alpha_composite(base_image, txt_layer)
        
        # Convert to RGB for JPEG
        output = io.BytesIO()
        rgb_image = Image.new('RGB', result.size, (255, 255, 255))
        rgb_image.paste(result, mask=result.split()[3])
        rgb_image.save(output, format='JPEG', quality=95)
        output.seek(0)
        return output.getvalue()
    except Exception as e:
        print(f"Error adding text watermark: {e}")
        return None


async def forward_single_message(dest_channel, source_channel, msg_id):
    """Forward a single message using rotating clients with optional watermark"""
    global logo_stats
    
    client = get_next_client()
    if not client:
        return False, "No client available"
    
    try:
        # Check if watermarking is enabled
        if logo_config.get("enabled") and (logo_config.get("logo_file_id") or logo_config.get("text")):
            # Get the message to check if it's a photo
            try:
                message = await client.get_messages(source_channel, msg_id)
                
                if message and message.photo:
                    # Download the photo
                    photo_bytes = await client.download_media(message, in_memory=True)
                    
                    if photo_bytes:
                        watermarked = None
                        
                        # Apply image logo watermark
                        if logo_config.get("logo_file_id"):
                            try:
                                logo_bytes = await client.download_media(logo_config["logo_file_id"], in_memory=True)
                                if logo_bytes:
                                    watermarked = add_image_watermark(
                                        photo_bytes.getvalue(),
                                        logo_bytes.getvalue(),
                                        logo_config.get("position", "bottom-right"),
                                        logo_config.get("opacity", 128),
                                        logo_config.get("size", 20)
                                    )
                            except Exception as e:
                                print(f"Error downloading logo: {e}")
                        
                        # Apply text watermark if no image logo or as additional
                        if logo_config.get("text"):
                            source_bytes = watermarked if watermarked else photo_bytes.getvalue()
                            watermarked = add_text_watermark(
                                source_bytes,
                                logo_config["text"],
                                logo_config.get("position", "bottom-right"),
                                logo_config.get("opacity", 128)
                            )
                        
                        if watermarked:
                            # Send watermarked photo
                            await client.send_photo(
                                chat_id=dest_channel,
                                photo=io.BytesIO(watermarked),
                                caption=message.caption or ""
                            )
                            logo_stats["watermarked"] += 1
                            return True, None
                        else:
                            logo_stats["failed"] += 1
            except Exception as e:
                print(f"Watermark error: {e}")
                # Fall back to normal copy
        
        # Normal copy without watermark
        await client.copy_message(
            chat_id=dest_channel,
            from_chat_id=source_channel,
            message_id=msg_id
        )
        return True, None
    except FloodWait as e:
        return False, f"flood:{e.value}"
    except Exception as e:
        return False, str(e)


async def forward_messages(source_channel, dest_channel, start_id, end_id, is_resume=False):
    """Forward messages using multiple MTProto accounts - ULTRA FAST!"""
    global is_forwarding, stop_requested, current_progress
    
    if not user_clients:
        print("No user clients initialized!")
        return
    
    is_forwarding = True
    stop_requested = False
    
    num_accounts = len(user_clients)
    
    # Initialize progress
    if not is_resume:
        current_progress = {
            "success_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "total_count": end_id - start_id + 1,
            "current_id": start_id,
            "start_id": start_id,
            "end_id": end_id,
            "is_active": True,
            "speed": 0,
            "rate_limit_hits": 0,
            "active_accounts": num_accounts
        }
    else:
        current_progress["is_active"] = True
        current_progress["active_accounts"] = num_accounts
    
    save_progress()
    
    current_id = current_progress["current_id"] if is_resume else start_id
    batch_start_time = time.time()
    batch_count = 0
    
    # Larger batch size with multiple accounts
    effective_batch_size = BATCH_SIZE * num_accounts
    
    print(f"🚀 Starting forward with {num_accounts} accounts!")
    print(f"📊 {source_channel} -> {dest_channel}, IDs: {current_id} to {end_id}")
    print(f"⚡ Expected speed: ~{num_accounts * 30}/min")
    
    try:
        while current_id <= end_id and not stop_requested:
            # Process larger batch with multiple accounts
            batch_ids = list(range(current_id, min(current_id + effective_batch_size, end_id + 1)))
            
            for msg_id in batch_ids:
                if stop_requested:
                    break
                
                # Check if already forwarded
                if is_message_forwarded(source_channel, msg_id):
                    current_progress["skipped_count"] += 1
                    current_progress["current_id"] = msg_id
                    continue
                
                # Try to forward using rotating clients
                success, error = await forward_single_message(dest_channel, source_channel, msg_id)
                
                if success:
                    current_progress["success_count"] += 1
                    mark_message_forwarded(source_channel, dest_channel, msg_id)
                    batch_count += 1
                elif error and error.startswith("flood:"):
                    # Handle rate limit
                    wait_time = int(error.split(":")[1])
                    print(f"⚠️ FloodWait: sleeping {wait_time}s")
                    current_progress["rate_limit_hits"] += 1
                    save_progress()
                    await asyncio.sleep(wait_time)
                    
                    # Retry with next client
                    retry_success, _ = await forward_single_message(dest_channel, source_channel, msg_id)
                    if retry_success:
                        current_progress["success_count"] += 1
                        mark_message_forwarded(source_channel, dest_channel, msg_id)
                        batch_count += 1
                    else:
                        current_progress["failed_count"] += 1
                else:
                    error_lower = error.lower() if error else ""
                    if "not found" in error_lower or "empty" in error_lower or "deleted" in error_lower:
                        current_progress["skipped_count"] += 1
                    else:
                        print(f"❌ Error {msg_id}: {error}")
                        current_progress["failed_count"] += 1
                
                current_progress["current_id"] = msg_id
                
                # Very small delay between messages (multiple accounts handle load)
                await asyncio.sleep(DELAY_BETWEEN_MESSAGES)
            
            # Calculate speed
            elapsed = time.time() - batch_start_time
            if elapsed > 0:
                current_progress["speed"] = round((batch_count / elapsed) * 60, 1)  # msgs/min
            
            # Save progress after each batch
            save_progress()
            
            # Move to next batch
            current_id += effective_batch_size
            
            # Shorter delay between batches with multiple accounts
            await asyncio.sleep(DELAY_BETWEEN_BATCHES)
            
            print(f"📈 Progress: {current_progress['success_count']}/{current_progress['total_count']} @ {current_progress['speed']}/min ({num_accounts} accounts)")
    
    except Exception as e:
        print(f"❌ Forward error: {e}")
    
    finally:
        is_forwarding = False
        current_progress["is_active"] = False
        save_progress()
        print("✅ Forwarding completed!")


async def wizard_forward_messages(user_id, source_channel, dest_channel, skip_number, last_message_id, filters, bot_client):
    """Forward messages using wizard flow with live status updates and filters"""
    global user_forward_progress
    
    if user_id not in user_forward_progress:
        return
    
    progress = user_forward_progress[user_id]
    progress["status"] = "Forwarding"
    
    client = get_next_client()
    if not client:
        progress["status"] = "Error: No accounts"
        progress["is_active"] = False
        return
    
    try:
        # Calculate start message ID (skip specified number)
        start_id = 1 + skip_number
        end_id = last_message_id
        total_to_forward = end_id - start_id + 1
        
        if total_to_forward <= 0:
            progress["status"] = "Completed"
            progress["percentage"] = 100
            progress["is_active"] = False
            return
        
        current_id = start_id
        update_counter = 0
        batch_start_time = time.time()
        forwarded_count = 0
        
        while current_id <= end_id and progress.get("is_active", False):
            # Forward single message
            try:
                # Check if already forwarded
                if is_message_forwarded(source_channel, current_id):
                    progress["duplicate_msg"] = progress.get("duplicate_msg", 0) + 1
                    current_id += 1
                    continue
                
                # Get message to check type for filtering
                try:
                    msg = await client.get_messages(source_channel, current_id)
                except:
                    msg = None
                
                # Apply filters if message exists
                if msg and filters:
                    should_skip = False
                    
                    # Check video filter
                    if filters.get("skip_videos") and (msg.video or msg.video_note or msg.animation):
                        should_skip = True
                    # Check photo filter
                    elif filters.get("skip_photos") and msg.photo:
                        should_skip = True
                    # Check file/document filter
                    elif filters.get("skip_files") and msg.document:
                        should_skip = True
                    # Check audio filter
                    elif filters.get("skip_audio") and (msg.audio or msg.voice):
                        should_skip = True
                    # Check sticker filter
                    elif filters.get("skip_stickers") and msg.sticker:
                        should_skip = True
                    # Check text-only filter
                    elif filters.get("skip_text") and msg.text and not any([
                        msg.photo, msg.video, msg.document, msg.audio, 
                        msg.voice, msg.sticker, msg.animation, msg.video_note
                    ]):
                        should_skip = True
                    
                    if should_skip:
                        progress["filtered_msg"] = progress.get("filtered_msg", 0) + 1
                        current_id += 1
                        continue
                
                # Try to copy message
                await client.copy_message(
                    chat_id=dest_channel,
                    from_chat_id=source_channel,
                    message_id=current_id
                )
                
                progress["success_fwd"] = progress.get("success_fwd", 0) + 1
                mark_message_forwarded(source_channel, dest_channel, current_id)
                forwarded_count += 1
                
            except FloodWait as e:
                progress["status"] = f"Waiting {e.value}s"
                await asyncio.sleep(e.value)
                continue
            except Exception as e:
                error_str = str(e).lower()
                if "message" in error_str and "not found" in error_str:
                    progress["filtered_msg"] = progress.get("filtered_msg", 0) + 1
                else:
                    progress["duplicate_msg"] = progress.get("duplicate_msg", 0) + 1
            
            current_id += 1
            
            # Calculate progress
            done = current_id - start_id
            progress["percentage"] = round((done / total_to_forward) * 100, 1)
            
            # Calculate ETA
            elapsed = time.time() - batch_start_time
            if forwarded_count > 0:
                rate = forwarded_count / elapsed  # messages per second
                remaining = end_id - current_id
                if rate > 0:
                    eta_seconds = remaining / rate
                    progress["eta"] = format_eta(int(eta_seconds))
            
            progress["status"] = "Forwarding"
            
            # Update status message every 5 forwards
            update_counter += 1
            if update_counter >= 5:
                update_counter = 0
                try:
                    cancel_keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("• CANCEL", callback_data="cancel_fwd_active")]
                    ])
                    await bot_client.edit_message_text(
                        chat_id=progress.get("chat_id"),
                        message_id=progress.get("status_message_id"),
                        text=format_forward_status(user_id),
                        reply_markup=cancel_keyboard
                    )
                except:
                    pass
            
            # Small delay between messages
            await asyncio.sleep(0.3)
        
        # Final update
        progress["status"] = "Completed" if progress.get("is_active") else "Cancelled"
        progress["percentage"] = 100 if progress.get("is_active") else progress.get("percentage", 0)
        progress["is_active"] = False
        progress["eta"] = "Done!"
        
        try:
            await bot_client.edit_message_text(
                chat_id=progress.get("chat_id"),
                message_id=progress.get("status_message_id"),
                text=format_forward_status(user_id)
            )
        except:
            pass
        
    except Exception as e:
        print(f"Wizard forward error: {e}")
        progress["status"] = f"Error: {str(e)[:20]}"
        progress["is_active"] = False
    
    finally:
        # Clean up wizard state
        forward_wizard_state.pop(user_id, None)


async def start_bot_client():
    """Start the bot client (commands like /start)."""
    global bot_client

    if not (BOT_TOKEN and API_ID and API_HASH):
        print("⚠️ Bot client not starting: missing BOT_TOKEN/API_ID/API_HASH")
        return

    bot_client = Client(
        "bot_session",
        api_id=int(API_ID),
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
    )

    # Register handlers BEFORE starting (required for Pyrogram polling)
    register_bot_handlers()
    print("📝 Bot handlers registered")

    # Start with FloodWait handling (Telegram can rate-limit frequent restarts)
    for attempt in range(1, 6):
        try:
            await bot_client.start()

            # Ensure Bot API webhook is disabled so polling works
            try:
                await bot_client.delete_webhook(drop_pending_updates=True)
                print("🔄 Bot webhook cleared (polling mode active)")
            except Exception as wh_err:
                print(f"⚠️ Could not clear webhook via bot client: {wh_err}")

            try:
                me = await bot_client.get_me()
                uname = getattr(me, "username", "") or ""
                uid = getattr(me, "id", "")
                print(f"🤖 Bot client started as @{uname} (id={uid}) - now listening for messages!")

                # Optional startup self-test message
                admin_chat_id = (os.getenv("ADMIN_CHAT_ID") or "").strip()
                if admin_chat_id:
                    try:
                        await bot_client.send_message(int(admin_chat_id), f"✅ Bot started: @{uname} (id={uid})")
                        print(f"📨 Startup self-test sent to ADMIN_CHAT_ID={admin_chat_id}")
                    except Exception as send_err:
                        print(f"⚠️ Startup self-test failed: {send_err}")
            except Exception as info_err:
                print(f"⚠️ Bot started but get_me/self-test failed: {info_err}")

            return
        except FloodWait as e:
            wait_s = int(getattr(e, "value", 0) or 0)
            wait_s = max(wait_s, 10)
            print(f"⏳ FloodWait while starting bot: waiting {wait_s}s (attempt {attempt}/5)")
            await asyncio.sleep(wait_s)
        except Exception as e:
            print(f"❌ Failed to start bot client: {e}")
            raise


async def init_clients():
    """Initialize Pyrogram clients - supports unlimited accounts!"""
    global user_clients, bot_client, auto_approve_channels

    def is_auth_key_duplicated(err: Exception) -> bool:
        s = str(err)
        return ("AUTH_KEY_DUPLICATED" in s) or ("406" in s and "DUPLIC" in s.upper())

    # Load auto-approve channels from database
    if autoapprove_col is not None:
        enabled_channels = autoapprove_col.find({"enabled": True})
        for doc in enabled_channels:
            auto_approve_channels.add(doc["channel"])
        print(f"📥 Loaded {len(auto_approve_channels)} auto-approve channels")

    # Load logo config from database
    load_logo_config()
    if logo_config.get("enabled"):
        print("🖼️ Logo watermark enabled")

    # Load public access setting from database
    load_public_access()
    print(f"🌐 Public access: {'✅ ENABLED' if public_access_enabled else '❌ DISABLED (Only admins)'}")

    # IMPORTANT: Start bot client ASAP so /start works even if MTProto sessions take time
    await start_bot_client()

    # Get all session strings from environment
    session_strings = get_all_session_strings()
    print(f"🔍 Found {len(session_strings)} session string(s)")

    # Initialize user clients for fast forwarding (MTProto)
    if session_strings and API_ID and API_HASH:
        for idx, (name, session_string) in enumerate(session_strings):
            client = Client(
                f"user_session_{idx}",
                api_id=int(API_ID),
                api_hash=API_HASH,
                session_string=session_string,
            )

            # Start with retry handling (AUTH_KEY_DUPLICATED can happen on redeploy when old instance hasn't disconnected yet)
            for attempt in range(1, 7):
                try:
                    await client.start()
                    user_clients.append((name, client))
                    print(f"✅ {name} connected!")
                    break
                except FloodWait as e:
                    wait_s = int(getattr(e, "value", 0) or 0)
                    wait_s = max(wait_s, 5)
                    print(f"⏳ FloodWait while starting {name}: waiting {wait_s}s (attempt {attempt}/6)")
                    await asyncio.sleep(wait_s)
                except Exception as e:
                    if is_auth_key_duplicated(e) and attempt < 6:
                        # Give Telegram time to drop the old connection
                        wait_s = min(20 * attempt, 120)
                        print(
                            f"♻️ {name} AUTH_KEY_DUPLICATED — waiting {wait_s}s then retry (attempt {attempt}/6). "
                            "This usually means an old instance is still connected."
                        )
                        await asyncio.sleep(wait_s)
                        continue

                    print(f"❌ Failed to start {name}: {e}")
                    break

    print(f"🚀 Total active accounts: {len(user_clients)}")

    # Calculate expected speed
    if user_clients:
        expected_speed = len(user_clients) * 30  # ~30 msgs/min per account
        print(f"⚡ Expected forwarding speed: ~{expected_speed}/min")



def register_bot_handlers():
    """Register bot command handlers"""
    
    # Load force subscribe channels on startup
    load_force_subscribe()

    # Debug: log every private message (helps confirm polling is working)
    # Use group=-1 so this runs AFTER command handlers (group=0) and doesn't block them
    @bot_client.on_message(filters.private, group=-1)
    async def _debug_private_message(client, message):
        try:
            chat_id = getattr(message.chat, "id", None)
            from_id = getattr(getattr(message, "from_user", None), "id", None)
            text = getattr(message, "text", "")
            print(f"🛰️ private msg | chat_id={chat_id} from_id={from_id} text={text!r}")
        except Exception:
            pass
        # Don't reply here - let command handlers do their job

    def _is_cmd(text: str, cmd: str) -> bool:
        """Match /cmd or /cmd@BotUsername in any chat."""
        if not text:
            return False
        return re.match(rf"^/{cmd}(?:@[A-Za-z0-9_]+)?(?:\s|$)", text.strip(), re.IGNORECASE) is not None

    # ============ GROUP COMMAND HELPER FUNCTIONS ============
    
    async def check_group_admin(client, message):
        """Check if user is bot admin or group admin. Returns (is_admin, user_id)"""
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None
        
        is_bot_admin = bool(user_id and user_id in ADMIN_IDS)
        is_group_admin = False
        
        if message.sender_chat and message.sender_chat.id == chat_id:
            is_group_admin = True
        elif user_id:
            try:
                member = await client.get_chat_member(chat_id, user_id)
                # Handle both string and enum status
                status_str = str(member.status).lower()
                if "admin" in status_str or "creator" in status_str or "owner" in status_str:
                    is_group_admin = True
            except Exception as e:
                print(f"⚠️ get_chat_member failed: {e}")
        
        return (is_bot_admin or is_group_admin, user_id)

    # ============ ADMIN GROUPS HELPER FUNCTIONS ============
    
    async def save_admin_group(chat_id: int, chat_title: str, chat_type: str, member_count: int, permissions: dict, username: str = None, invite_link: str = None):
        """Save group where bot is admin with full permissions info + invite link"""
        if admin_groups_col is None:
            print(f"❌ save_admin_group: admin_groups_col is None")
            return False
        try:
            doc = {
                "chat_id": chat_id,
                "chat_title": chat_title,
                "chat_type": chat_type,
                "member_count": member_count,
                "permissions": permissions,
                "updated_at": datetime.utcnow()
            }
            if username:
                doc["username"] = username
            if invite_link:
                doc["invite_link"] = invite_link
            
            admin_groups_col.update_one(
                {"chat_id": chat_id},
                {"$set": doc},
                upsert=True
            )
            print(f"✅ Saved admin group: {chat_title} (link: {invite_link or username or 'none'})")
            return True
        except Exception as e:
            print(f"Error saving admin group: {e}")
            return False

    async def get_all_admin_groups():
        """Get all groups where bot is admin"""
        if admin_groups_col is None:
            return []
        try:
            groups = list(admin_groups_col.find({}).sort("updated_at", -1))
            return groups
        except Exception as e:
            print(f"Error getting admin groups: {e}")
            return []

    @bot_client.on_chat_member_updated()
    async def _auto_track_admin_groups(client, update):
        """Auto-save group when the bot becomes admin (works without user sessions)."""
        try:
            if admin_groups_col is None or bot_client is None:
                return

            chat = getattr(update, "chat", None)
            if chat is None:
                return

            if getattr(chat, "type", None) not in [ChatType.GROUP, ChatType.SUPERGROUP]:
                return

            new_member = getattr(update, "new_chat_member", None)
            if new_member is None:
                return

            me = await bot_client.get_me()
            if getattr(getattr(new_member, "user", None), "id", None) != me.id:
                return

            if new_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                return

            chat_id = chat.id
            chat_title = getattr(chat, "title", None) or str(chat_id)
            chat_type = "supergroup" if getattr(chat, "type", None) == ChatType.SUPERGROUP else "group"
            member_count = getattr(chat, "members_count", 0) or 0

            permissions = {
                "is_owner": new_member.status == ChatMemberStatus.OWNER,
                "can_delete_messages": getattr(new_member.privileges, "can_delete_messages", False) if hasattr(new_member, "privileges") else False,
                "can_restrict_members": getattr(new_member.privileges, "can_restrict_members", False) if hasattr(new_member, "privileges") else False,
                "can_promote_members": getattr(new_member.privileges, "can_promote_members", False) if hasattr(new_member, "privileges") else False,
                "can_change_info": getattr(new_member.privileges, "can_change_info", False) if hasattr(new_member, "privileges") else False,
                "can_invite_users": getattr(new_member.privileges, "can_invite_users", False) if hasattr(new_member, "privileges") else False,
                "can_pin_messages": getattr(new_member.privileges, "can_pin_messages", False) if hasattr(new_member, "privileges") else False,
                "can_manage_chat": getattr(new_member.privileges, "can_manage_chat", False) if hasattr(new_member, "privileges") else False,
            }

            username, invite_link = await _get_best_join_link(chat_id, chat_obj=chat)
            print(f"🔗 _auto_track_admin_groups: {chat_title} | username={username} | link={invite_link}")
            
            await save_admin_group(chat_id, chat_title, chat_type, member_count, permissions, username=username, invite_link=invite_link)

            if broadcast_groups_col is not None:
                broadcast_groups_col.update_one(
                    {"chat_id": chat_id},
                    {"$set": {"chat_id": chat_id, "chat_title": chat_title, "updated_at": datetime.utcnow()}},
                    upsert=True,
                )
        except Exception:
            pass

    @bot_client.on_message(filters.all, group=-10)
    async def universal_command_router(client, message):
        """
        Universal command router that handles ALL commands reliably, even with @BotUsername suffix.
        Uses group=-10 and filters.all to catch everything including commands.
        """
        text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        if not text:
            return  # No text/caption, let other handlers process
        
        chat_id = message.chat.id
        is_group = message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]
        
        # Debug command - works everywhere
        if _is_cmd(text, "debug"):
            debug_info = (
                f"🔍 **Debug Info**\n\n"
                f"Chat ID: `{chat_id}`\n"
                f"Chat Type: `{message.chat.type}`\n"
                f"Is Group: `{is_group}`\n"
                f"Text: `{text[:50]}...`\n"
                f"From User: `{message.from_user.id if message.from_user else 'None'}`\n"
                f"Message ID: `{message.id}`"
            )
            await message.reply(debug_info)
            message.stop_propagation()
            return

        # ===== PRIVATE + GROUP COMMANDS =====
        
        if _is_cmd(text, "start"):
            await handle_start(client, message)
            message.stop_propagation()
            return

        if _is_cmd(text, "help"):
            user_id = message.from_user.id if message.from_user else None
            
            # Show different help based on admin status
            if user_id and user_id in ADMIN_IDS:
                help_text = (
                    "📚 **Available Commands:**\n\n"
                    "**📤 Forwarding:**\n"
                    "/start - Start the bot\n"
                    "/help - Show this help message\n"
                    "/forward - Start forwarding wizard\n"
                    "/setconfig - Set source/dest channels\n"
                    "/resume - Resume forwarding\n"
                    "/stop - Stop forwarding\n"
                    "/progress - Show progress\n"
                    "/status - Show status\n"
                    "/accounts - Show connected accounts\n\n"
                    "**👥 Admin Groups:**\n"
                    "/addgroup - Add/scan one group/channel\n"
                    "/admingroups - List groups where bot is admin\n"
                    "/refreshgroups - Refresh admin groups list\n\n"
                    "**📢 Broadcast:**\n"
                    "/broadcast - Send to all users (reply)\n"
                    "/gbroadcast - Send to all groups (reply)\n"
                    "/broadcaststats - View broadcast stats\n\n"
                    "**🛡️ Moderation (in groups):**\n"
                    "/enablemod - Enable moderation\n"
                    "/blockforward - Block forwards\n"
                    "/blocklinks - Block links\n"
                    "/blockbadwords - Block bad content\n"
                    "/blockmention - Block @mentions\n"
                    "/autodelete2min - Auto-delete after 2min\n"
                    "/modstatus - View settings\n\n"
                    "**🔐 Force Join:**\n"
                    "/setforcejoin - Set force join channel\n"
                    "/removeforcejoin - Remove force join\n"
                    "/forcejoininfo - View force join status\n\n"
                    "**📥 Join Request:**\n"
                    "/autoapprove - Enable auto-approve\n"
                    "/stopapprove - Disable auto-approve\n"
                    "/approveall - Approve all pending\n"
                    "/approvelist - Show enabled channels\n\n"
                    "**🖼️ Logo/Watermark:**\n"
                    "/setlogo - Set logo (reply to image)\n"
                    "/enablelogo - Enable watermark\n"
                    "/disablelogo - Disable watermark\n"
                    "/logoinfo - View logo settings\n\n"
                    "**🔧 Other:**\n"
                    "/myid - Show your Telegram ID\n"
                    "/enablepublic - Enable public access\n"
                    "/disablepublic - Disable public access"
                )
            else:
                help_text = (
                    "📚 **Available Commands:**\n\n"
                    "**📤 Forwarding:**\n"
                    "/start - Start the bot\n"
                    "/help - Show this help message\n"
                    "/forward - Start forwarding wizard\n\n"
                    "**🛡️ Moderation (in groups):**\n"
                    "/enablemod - Enable moderation\n"
                    "/blockforward - Block forwards\n"
                    "/blocklinks - Block links\n"
                    "/blockbadwords - Block bad content\n"
                    "/modstatus - View settings\n\n"
                    "**🔧 Other:**\n"
                    "/myid - Show your Telegram ID"
                )
            
            await message.reply(
                help_text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📁 Help", callback_data="help")]
                ])
            )
            message.stop_propagation()
            return

        if _is_cmd(text, "admingroups"):
            if message.from_user is None or message.from_user.id not in ADMIN_IDS:
                await message.reply("❌ Only admins can view admin groups.")
                message.stop_propagation()
                return
            
            groups = await get_all_admin_groups()
            if not groups:
                await message.reply(
                    "📂 **No Admin Groups Found**\n\n"
                    "Bot is not admin in any group/channel yet.\n\n"
                    "**How to add:**\n"
                    "1. Add bot as admin in group/channel\n"
                    "2. Use /addgroup @username or /addgroup -100xxxxx\n"
                    "3. Or use /refreshgroups to scan all"
                )
                message.stop_propagation()
                return
            
            def _escape_html(s: str) -> str:
                import html
                return html.escape(s or "", quote=True)

            msg_lines = ["👥 <b>Groups Where Bot is Admin:</b>\n"]
            for i, g in enumerate(groups[:20], 1):  # Limit to 20
                title_raw = g.get("chat_title", "Unknown")
                title = _escape_html(str(title_raw))
                chat_id_raw = g.get("chat_id", "?")

                # Try to produce a REAL joinable link.
                # Notes:
                # - https://t.me/c/<id> is NOT a reliable "open group" link (usually needs message id).
                # - For private groups without username, the only clickable join link is an invite link.
                # - Bots can create/export invite links ONLY if they have the proper admin right.
                invite_link = (g.get("invite_link") or "").strip()
                username = (g.get("username") or "").strip().lstrip("@")

                def _looks_like_internal_link(link: str) -> bool:
                    l = (link or "").strip().lower()
                    # t.me/c/<id> is NOT a join link for private groups; it usually needs a message-id.
                    return "t.me/c/" in l or "telegram.me/c/" in l

                # If DB has an internal/non-joinable link, treat it as missing so we regenerate a real invite.
                if invite_link and _looks_like_internal_link(invite_link):
                    invite_link = ""

                chat_id: int | None = None
                try:
                    chat_id = int(chat_id_raw)
                except Exception:
                    chat_id = None

                # If we don't have a REAL link saved, try to fetch/generate it on demand using the shared helper.
                if (not invite_link and not username) and chat_id is not None:
                    try:
                        username, invite_link = await _get_best_join_link(chat_id)
                        if admin_groups_col is not None:
                            admin_groups_col.update_one(
                                {"chat_id": chat_id},
                                {"$set": {"username": username or None, "invite_link": invite_link or None, "updated_at": datetime.utcnow()}},
                                upsert=True,
                            )
                    except Exception:
                        pass

                # Normalize link so Telegram surely treats it as clickable
                link_url = invite_link or (f"https://t.me/{username}" if username else "")
                if link_url and not link_url.startswith(("http://", "https://")):
                    if link_url.startswith("t.me/") or link_url.startswith("telegram.me/"):
                        link_url = f"https://{link_url}"
                    elif link_url.startswith("+"):
                        link_url = f"https://t.me/{link_url}"

                link_url_esc = _escape_html(link_url)

                if link_url:
                    # Title + explicit URL on next line (most reliable click behavior in Telegram)
                    msg_lines.append(
                        f"{i}. <b>{title}</b>\n"
                        f"   🔗 <a href=\"{link_url_esc}\">{link_url_esc}</a>\n"
                        f"   ID: <code>{_escape_html(str(chat_id_raw))}</code>"
                    )
                else:
                    msg_lines.append(
                        f"{i}. <b>{title}</b>\n"
                        f"   ID: <code>{_escape_html(str(chat_id_raw))}</code>\n"
                        "   ⚠️ No link (private). Bot needs 'Invite Users' permission."
                    )

            if len(groups) > 20:
                msg_lines.append(f"\n... and {len(groups) - 20} more")

            msg_lines.append(f"\n📊 <b>Total:</b> {len(groups)} groups")

            try:
                await message.reply(
                    "\n".join(msg_lines),
                    disable_web_page_preview=True,
                    parse_mode="html",
                )
            except Exception as e:
                # Fallback: plain text (in case HTML parsing fails)
                plain = "\n".join([line.replace("<b>", "").replace("</b>", "").replace("<code>", "`").replace("</code>", "`") for line in msg_lines])
                await message.reply(f"{plain}\n\n(⚠️ render fallback: {type(e).__name__})")

            message.stop_propagation()
            return

        if _is_cmd(text, "addgroup"):
            if message.from_user is None or message.from_user.id not in ADMIN_IDS:
                await message.reply("❌ Only admins can add groups.")
                message.stop_propagation()
                return
            
            # Parse group from command or reply
            parts = text.split(maxsplit=1)
            target = None
            
            if len(parts) > 1:
                target = parts[1].strip()
            elif message.reply_to_message and message.reply_to_message.forward_from_chat:
                target = message.reply_to_message.forward_from_chat.id
            
            if not target:
                await message.reply(
                    "📂 **Add Group/Channel**\n\n"
                    "**Usage:**\n"
                    "`/addgroup @username` - Add by username\n"
                    "`/addgroup -100xxxxxxxxxx` - Add by ID\n"
                    "Or reply `/addgroup` to a forwarded message\n\n"
                    "Bot must be admin in the group/channel!"
                )
                message.stop_propagation()
                return
            
            status_msg = await message.reply("🔍 Scanning group/channel...")
            
            try:
                # Get chat info
                chat = await client.get_chat(target)
                chat_id = chat.id
                chat_title = chat.title or str(chat_id)
                chat_type = str(chat.type)
                member_count = getattr(chat, "members_count", 0) or 0
                
                # Check if bot is admin
                try:
                    me = await client.get_me()
                    member = await client.get_chat_member(chat_id, me.id)
                    status_str = str(member.status).lower()
                    is_admin = "admin" in status_str or "creator" in status_str or "owner" in status_str
                except Exception:
                    is_admin = False
                
                if not is_admin:
                    await status_msg.edit(
                        f"❌ Bot is not admin in **{chat_title}**\n\n"
                        f"Please make bot admin first, then try again."
                    )
                    message.stop_propagation()
                    return
                
                # Get invite link
                invite_link = ""
                username = getattr(chat, "username", "") or ""
                
                if username:
                    invite_link = f"https://t.me/{username}"
                elif hasattr(chat, "invite_link") and chat.invite_link:
                    invite_link = chat.invite_link
                else:
                    try:
                        invite_link = await client.export_chat_invite_link(chat_id)
                    except Exception:
                        # Use internal link format
                        chat_id_str = str(chat_id).replace("-100", "")
                        invite_link = f"https://t.me/c/{chat_id_str}"
                
                # Get permissions
                perms = {}
                try:
                    me = await client.get_me()
                    member = await client.get_chat_member(chat_id, me.id)
                    if hasattr(member, "privileges") and member.privileges:
                        p = member.privileges
                        perms = {
                            "can_post_messages": getattr(p, "can_post_messages", False),
                            "can_edit_messages": getattr(p, "can_edit_messages", False),
                            "can_delete_messages": getattr(p, "can_delete_messages", False),
                            "can_invite_users": getattr(p, "can_invite_users", False),
                            "can_restrict_members": getattr(p, "can_restrict_members", False),
                            "can_promote_members": getattr(p, "can_promote_members", False),
                        }
                except Exception:
                    pass
                
                # Save to database with link included
                print(f"🔗 /addgroup: {chat_title} | username={username} | invite_link={invite_link}")
                await save_admin_group(chat_id, chat_title, chat_type, member_count, perms, username=username, invite_link=invite_link)
                
                link_display = f"🔗 {invite_link}" if invite_link else "🔗 No link available"
                
                await status_msg.edit(
                    f"✅ **Group Added!**\n\n"
                    f"📂 **{chat_title}**\n"
                    f"🆔 ID: `{chat_id}`\n"
                    f"📊 Type: {chat_type}\n"
                    f"👥 Members: {member_count}\n"
                    f"{link_display}\n\n"
                    f"Use /admingroups to see all groups."
                )
                
            except Exception as e:
                await status_msg.edit(
                    f"❌ Failed to add group: {e}\n\n"
                    f"Make sure:\n"
                    f"• Bot is added to the group/channel\n"
                    f"• Bot has admin permissions\n"
                    f"• Username/ID is correct"
                )
            
            message.stop_propagation()
            return

        if _is_cmd(text, "ping"):
            await message.reply("✅ Pong")
            message.stop_propagation()
            return

        if _is_cmd(text, "whoami"):
            try:
                me = await client.get_me()
                uname = getattr(me, "username", "") or ""
                uid = getattr(me, "id", "")
                await message.reply(f"🤖 Running as: @{uname}\n🆔 Bot ID: `{uid}`")
            except Exception as e:
                await message.reply(f"❌ whoami failed: {e}")
            message.stop_propagation()
            return

        # ===== PRIVATE-ONLY BROADCAST COMMANDS =====

        if not is_group:
            # Track user for broadcasts (best-effort)
            try:
                if broadcast_users_col is not None and message.from_user is not None:
                    broadcast_users_col.update_one(
                        {"user_id": message.from_user.id},
                        {"$set": {
                            "user_id": message.from_user.id,
                            "username": getattr(message.from_user, "username", None),
                            "first_name": getattr(message.from_user, "first_name", None),
                            "updated_at": datetime.utcnow(),
                        }},
                        upsert=True,
                    )
            except Exception:
                pass

            # /broadcaststats
            if _is_cmd(text, "broadcaststats"):
                if message.from_user is None or message.from_user.id not in ADMIN_IDS:
                    await message.reply("❌ Only admins can view broadcast stats.")
                    message.stop_propagation()
                    return

                user_count = broadcast_users_col.count_documents({}) if broadcast_users_col is not None else 0
                group_count = broadcast_groups_col.count_documents({}) if broadcast_groups_col is not None else 0

                await message.reply(
                    f"📊 **Broadcast Statistics**\n\n"
                    f"👤 Total Users: {user_count}\n"
                    f"👥 Total Groups: {group_count}\n\n"
                    f"**Commands:**\n"
                    f"Reply any message + `/broadcast` (users)\n"
                    f"Reply any message + `/gbroadcast` (groups)\n"
                    f"`/refreshgroups` (re-scan groups)\n"
                    f"`/broadcaststats`"
                )
                message.stop_propagation()
                return

            # /refreshgroups (scan dialogs and rebuild groups list)
            if _is_cmd(text, "refreshgroups"):
                if message.from_user is None or message.from_user.id not in ADMIN_IDS:
                    await message.reply("❌ Only admins can refresh groups.")
                    message.stop_propagation()
                    return

                status_msg = await message.reply("🔄 Scanning groups... (this can take a bit)")

                try:
                    result = await refresh_broadcast_groups()
                    broadcast_count = broadcast_groups_col.count_documents({}) if broadcast_groups_col is not None else 0
                    admin_count = admin_groups_col.count_documents({}) if admin_groups_col is not None else 0
                    err_line = ""
                    if result.get("last_error"):
                        err_line = f"\n❗ Last error: `{result.get('last_error')}`"

                    await status_msg.edit(
                        "✅ **Groups Refreshed**\n\n"
                        f"👀 Seen in dialogs: {result.get('total_seen', 0)}\n"
                        f"✅ Saved (admin): {result.get('saved', 0)}\n"
                        f"🗑️ Removed (not admin): {result.get('removed', 0)}\n"
                        f"⚠️ Errors: {result.get('errors', 0)}\n\n"
                        f"👥 Broadcast DB: {broadcast_count}\n"
                        f"🛡️ Admin DB: {admin_count}"
                        f"{err_line}"
                    )
                except Exception as e:
                    await status_msg.edit(f"❌ refreshgroups failed: {e}")

                message.stop_propagation()
                return

            # /broadcast (users)
            if _is_cmd(text, "broadcast"):
                if message.from_user is None or message.from_user.id not in ADMIN_IDS:
                    await message.reply("❌ Only admins can use broadcast.")
                    message.stop_propagation()
                    return

                if not message.reply_to_message:
                    await message.reply(
                        "📢 **Broadcast to Users**\n\n"
                        "Reply to any message (text/photo/video/document) with:\n"
                        "`/broadcast` - Send to all users"
                    )
                    message.stop_propagation()
                    return

                if broadcast_users_col is None:
                    await message.reply("❌ Database not connected.")
                    message.stop_propagation()
                    return

                status_msg = await message.reply("📢 Starting broadcast to users...")
                users = list(broadcast_users_col.find({}))
                total = len(users)
                success = failed = blocked = 0

                for u in users:
                    user_id = u.get("user_id")
                    if not user_id:
                        continue
                    try:
                        await message.reply_to_message.copy(user_id)
                        success += 1
                        if success % 50 == 0:
                            await status_msg.edit(f"📢 Users: ✅ {success}/{total} | ❌ {failed} | 🚫 {blocked}")
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        err = str(e).lower()
                        if "blocked" in err or "deactivated" in err:
                            blocked += 1
                            try:
                                broadcast_users_col.delete_one({"user_id": user_id})
                            except Exception:
                                pass
                        else:
                            failed += 1

                await status_msg.edit(
                    f"✅ **Broadcast Complete!**\n\n"
                    f"📊 Total users: {total}\n"
                    f"✅ Sent: {success}\n"
                    f"❌ Failed: {failed}\n"
                    f"🚫 Blocked (removed): {blocked}"
                )
                message.stop_propagation()
                return

            # /gbroadcast (groups)
            if _is_cmd(text, "gbroadcast"):
                if message.from_user is None or message.from_user.id not in ADMIN_IDS:
                    await message.reply("❌ Only admins can use group broadcast.")
                    message.stop_propagation()
                    return

                if not message.reply_to_message:
                    await message.reply(
                        "📢 **Broadcast to Groups**\n\n"
                        "Reply to any message (text/photo/video/document) with:\n"
                        "`/gbroadcast` - Send to all groups where bot is admin"
                    )
                    message.stop_propagation()
                    return

                if broadcast_groups_col is None:
                    await message.reply("❌ Database not connected.")
                    message.stop_propagation()
                    return

                status_msg = await message.reply("📢 Starting broadcast to groups...")
                groups = list(broadcast_groups_col.find({}))
                total = len(groups)
                success = failed = removed = 0

                for g in groups:
                    chat_id2 = g.get("chat_id")
                    if not chat_id2:
                        continue
                    try:
                        await message.reply_to_message.copy(chat_id2)
                        success += 1
                        if success % 20 == 0:
                            await status_msg.edit(f"📢 Groups: ✅ {success}/{total} | ❌ {failed} | 🗑️ {removed}")
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        err = str(e).lower()
                        if "forbidden" in err or "not a member" in err or "chat not found" in err or "kicked" in err:
                            removed += 1
                            try:
                                broadcast_groups_col.delete_one({"chat_id": chat_id2})
                            except Exception:
                                pass
                        else:
                            failed += 1

                await status_msg.edit(
                    f"✅ **Group Broadcast Complete!**\n\n"
                    f"📊 Total groups: {total}\n"
                    f"✅ Sent: {success}\n"
                    f"❌ Failed: {failed}\n"
                    f"🗑️ Removed (left/kicked): {removed}"
                )
                message.stop_propagation()
                return

            # Not a command we handle in private here
            return

        # ===== GROUP-ONLY MODERATION COMMANDS =====
        
        # /enablemod
        if _is_cmd(text, "enablemod"):
            is_admin, user_id = await check_group_admin(client, message)
            if not is_admin:
                await message.reply(f"❌ Only admins can enable moderation!\nDebug: user_id={user_id}")
                message.stop_propagation()
                return
            
            if chat_id not in moderation_config:
                moderation_config[chat_id] = load_moderation_config(chat_id)
            moderation_config[chat_id]["enabled"] = True
            save_moderation_config(chat_id)
            
            await message.reply(
                "✅ **Content Moderation Enabled!**\n\n"
                "Commands:\n"
                "• /blockforward - Block forwarded messages\n"
                "• /blocklinks - Block links/URLs\n"
                "• /blockbadwords - Block inappropriate content\n"
                "• /modstatus - View moderation settings\n"
                "• /disablemod - Disable moderation"
            )
            message.stop_propagation()
            return
        
        # /disablemod
        if _is_cmd(text, "disablemod"):
            is_admin, user_id = await check_group_admin(client, message)
            if not is_admin:
                await message.reply("❌ Only admins can disable moderation!")
                message.stop_propagation()
                return
            
            if chat_id not in moderation_config:
                moderation_config[chat_id] = load_moderation_config(chat_id)
            moderation_config[chat_id]["enabled"] = False
            save_moderation_config(chat_id)
            
            await message.reply("🔴 **Content Moderation Disabled!**")
            message.stop_propagation()
            return
        
        # /blockforward
        if _is_cmd(text, "blockforward"):
            is_admin, user_id = await check_group_admin(client, message)
            if not is_admin:
                await message.reply("❌ Only admins can change this!")
                message.stop_propagation()
                return
            
            if chat_id not in moderation_config:
                moderation_config[chat_id] = load_moderation_config(chat_id)
            current = moderation_config[chat_id].get("block_forward", False)
            moderation_config[chat_id]["block_forward"] = not current
            moderation_config[chat_id]["enabled"] = True
            save_moderation_config(chat_id)
            
            status = "🟢 ON" if not current else "🔴 OFF"
            await message.reply(f"📨 **Block Forwarded Messages:** {status}")
            message.stop_propagation()
            return
        
        # /blocklinks
        if _is_cmd(text, "blocklinks"):
            is_admin, user_id = await check_group_admin(client, message)
            if not is_admin:
                await message.reply("❌ Only admins can change this!")
                message.stop_propagation()
                return
            
            if chat_id not in moderation_config:
                moderation_config[chat_id] = load_moderation_config(chat_id)
            current = moderation_config[chat_id].get("block_links", False)
            moderation_config[chat_id]["block_links"] = not current
            moderation_config[chat_id]["enabled"] = True
            save_moderation_config(chat_id)
            
            status = "🟢 ON" if not current else "🔴 OFF"
            await message.reply(f"🔗 **Block Links/URLs:** {status}")
            message.stop_propagation()
            return
        
        # /blockbadwords
        if _is_cmd(text, "blockbadwords"):
            is_admin, user_id = await check_group_admin(client, message)
            if not is_admin:
                await message.reply("❌ Only admins can change this!")
                message.stop_propagation()
                return
            
            if chat_id not in moderation_config:
                moderation_config[chat_id] = load_moderation_config(chat_id)
            current = moderation_config[chat_id].get("block_badwords", False)
            moderation_config[chat_id]["block_badwords"] = not current
            moderation_config[chat_id]["enabled"] = True
            save_moderation_config(chat_id)
            
            status = "🟢 ON" if not current else "🔴 OFF"
            await message.reply(f"🤬 **Block Bad Words:** {status}")
            message.stop_propagation()
            return
        
        # /blockmention
        if _is_cmd(text, "blockmention"):
            is_admin, user_id = await check_group_admin(client, message)
            if not is_admin:
                await message.reply("❌ Only admins can change this!")
                message.stop_propagation()
                return
            
            if chat_id not in moderation_config:
                moderation_config[chat_id] = load_moderation_config(chat_id)
            current = moderation_config[chat_id].get("block_mention", False)
            moderation_config[chat_id]["block_mention"] = not current
            moderation_config[chat_id]["enabled"] = True
            save_moderation_config(chat_id)
            
            status = "🟢 ON" if not current else "🔴 OFF"
            await message.reply(f"📢 **Block @Mentions:** {status}")
            message.stop_propagation()
            return
        
        # /autodelete2min
        if _is_cmd(text, "autodelete2min"):
            is_admin, user_id = await check_group_admin(client, message)
            if not is_admin:
                await message.reply("❌ Only admins can change this!")
                message.stop_propagation()
                return
            
            if chat_id not in moderation_config:
                moderation_config[chat_id] = load_moderation_config(chat_id)
            current = moderation_config[chat_id].get("auto_delete_2min", False)
            moderation_config[chat_id]["auto_delete_2min"] = not current
            moderation_config[chat_id]["enabled"] = True
            save_moderation_config(chat_id)
            
            status = "🟢 ON" if not current else "🔴 OFF"
            await message.reply(f"⏱️ **Auto-Delete After 2 Minutes:** {status}")
            message.stop_propagation()
            return
        
        # /modstatus
        if _is_cmd(text, "modstatus"):
            if chat_id not in moderation_config:
                moderation_config[chat_id] = load_moderation_config(chat_id)
            cfg = moderation_config[chat_id]
            
            await message.reply(
                "🛡️ **Moderation Status**\n\n"
                f"**Enabled:** {'🟢 YES' if cfg.get('enabled') else '🔴 NO'}\n"
                f"**Block Forwards:** {'🟢 ON' if cfg.get('block_forward') else '🔴 OFF'}\n"
                f"**Block Links:** {'🟢 ON' if cfg.get('block_links') else '🔴 OFF'}\n"
                f"**Block Bad Words:** {'🟢 ON' if cfg.get('block_badwords') else '🔴 OFF'}\n"
                f"**Block Mentions:** {'🟢 ON' if cfg.get('block_mention') else '🔴 OFF'}\n"
                f"**Auto-Delete 2min:** {'🟢 ON' if cfg.get('auto_delete_2min') else '🔴 OFF'}"
            )
            message.stop_propagation()
            return
        
        # /setforcejoin
        if _is_cmd(text, "setforcejoin"):
            is_admin, user_id = await check_group_admin(client, message)
            if not is_admin:
                await message.reply("❌ Only group admins can set force join!")
                message.stop_propagation()
                return
            
            # Parse channel from command
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await message.reply(
                    "❌ Usage: `/setforcejoin @channel_username`\n\n"
                    "Example: `/setforcejoin @MyChannel`"
                )
                message.stop_propagation()
                return
            
            channel_input = parts[1].strip()
            if not channel_input.startswith("@"):
                channel_input = "@" + channel_input
            
            try:
                channel_info = await client.get_chat(channel_input)
                channel_id = str(channel_info.id)
                channel_name = channel_info.title or channel_input
                invite_link = channel_info.invite_link or f"https://t.me/{channel_input.replace('@', '')}"
                
                config = {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "invite_link": invite_link
                }
                group_forcejoin_config[chat_id] = config
                
                if group_forcejoin_col:
                    group_forcejoin_col.update_one(
                        {"chat_id": chat_id},
                        {"$set": config},
                        upsert=True
                    )
                
                await message.reply(
                    f"✅ **Force Join Set!**\n\n"
                    f"Channel: {channel_name}\n"
                    f"Link: {invite_link}\n\n"
                    "Users who haven't joined will have messages deleted."
                )
            except Exception as e:
                await message.reply(f"❌ Failed to set force join: {e}")
            
            message.stop_propagation()
            return
        
        # /removeforcejoin
        if _is_cmd(text, "removeforcejoin"):
            is_admin, user_id = await check_group_admin(client, message)
            if not is_admin:
                await message.reply("❌ Only group admins can remove force join!")
                message.stop_propagation()
                return
            
            if chat_id in group_forcejoin_config:
                del group_forcejoin_config[chat_id]
            
            if group_forcejoin_col:
                group_forcejoin_col.delete_one({"chat_id": chat_id})
            
            await message.reply("✅ **Force Join Removed!**\n\nUsers can now send messages without joining.")
            message.stop_propagation()
            return
        
        # /forcejoininfo
        if _is_cmd(text, "forcejoininfo"):
            config = group_forcejoin_config.get(chat_id)
            if config:
                await message.reply(
                    f"🔐 **Force Join Info**\n\n"
                    f"Channel: {config.get('channel_name', 'Unknown')}\n"
                    f"Link: {config.get('invite_link', 'N/A')}"
                )
            else:
                await message.reply("ℹ️ No force join configured for this group.\n\nUse `/setforcejoin @channel` to set one.")
            message.stop_propagation()
            return

        # /setjoinwait
        if _is_cmd(text, "setjoinwait"):
            await setjoinwait_handler(client, message)
            message.stop_propagation()
            return

        # /removejoinwait
        if _is_cmd(text, "removejoinwait"):
            await removejoinwait_handler(client, message)
            message.stop_propagation()
            return

        # /joinwaitstatus
        if _is_cmd(text, "joinwaitstatus"):
            await joinwaitstatus_handler(client, message)
            message.stop_propagation()
            return

        # /mentionall - tag all group members (ADMIN ONLY)
        if _is_cmd(text, "mentionall"):
            is_admin_user, user_id = await check_group_admin(client, message)
            if not is_admin_user:
                await message.reply(
                    "❌ **Sirf Admin hi yeh command use kar sakta hai!**\n\n"
                    "Aapko group ka admin hona zaroori hai `/mentionall` command use karne ke liye.",
                    parse_mode=ParseMode.MARKDOWN
                )
                message.stop_propagation()
                return
            try:
                members = []
                async for member in client.get_chat_members(chat_id):
                    user = member.user
                    if user and not user.is_bot and not user.is_deleted:
                        members.append(user)
                
                if not members:
                    await message.reply("❌ No members found in this group.")
                    return
                
                # Send mentions in batches of 50
                batch_size = 50
                total = len(members)
                sent = 0
                for i in range(0, total, batch_size):
                    batch = members[i:i+batch_size]
                    mention_text = ""
                    for user in batch:
                        name = user.first_name or "User"
                        mention_text += f"[{name}](tg://user?id={user.id}) "
                    
                    await client.send_message(chat_id, mention_text, parse_mode=ParseMode.MARKDOWN)
                    sent += len(batch)
                    
                    if sent < total:
                        await asyncio.sleep(1)  # Avoid flood
                
                await message.reply(f"✅ Mentioned {total} members!")
            except Exception as e:
                print(f"[MENTION_ALL] Error: {e}", flush=True)
                await message.reply(f"❌ Error: {e}")
            message.stop_propagation()
            return

        # For all other messages/commands, do NOT stop propagation so other handlers can process them

    @bot_client.on_message(filters.command(["myid", "checkadmin"]))
    async def myid_handler(client, message):
        """Show your Telegram ID and whether the bot sees you as an admin"""
        user_id = message.from_user.id if message.from_user else None
        if not user_id:
            return await message.reply("❌ Couldn't read your user id. If you're using anonymous admin mode in a group, turn it off and try again.")

        await message.reply(
            "🆔 **Your Telegram ID**\n"
            f"`{user_id}`\n\n"
            f"🛡️ **Bot admin:** {'✅ YES' if user_id in ADMIN_IDS else '❌ NO'}\n"
            f"👥 **Bot admin IDs loaded:** {len(ADMIN_IDS)}"
        )
    
    @bot_client.on_message(filters.command(["enablepublic", "publicon"]) & filters.private)
    async def enable_public_handler(client, message):
        user_id = message.from_user.id if message.from_user else None
        if not user_id or user_id not in ADMIN_IDS:
            return await message.reply("❌ Only bot admins can use this command.")
        
        save_public_access(True)
        await message.reply(
            "✅ **Public Access Enabled!**\n\n"
            "Now all users can start and use this bot.\n"
            "Use /disablepublic to disable public access."
        )
    
    @bot_client.on_message(filters.command(["disablepublic", "publicoff"]) & filters.private)
    async def disable_public_handler(client, message):
        user_id = message.from_user.id if message.from_user else None
        if not user_id or user_id not in ADMIN_IDS:
            return await message.reply("❌ Only bot admins can use this command.")
        
        save_public_access(False)
        await message.reply(
            "🔒 **Public Access Disabled!**\n\n"
            "Now only bot admins can use this bot.\n"
            "Other users will see 'Private Mode' message.\n\n"
            "Use /enablepublic to enable public access."
        )
    
    async def handle_start(client, message):
        """Handle /start consistently (works even if filters.command isn't firing)."""
        # Debug: confirm bot is receiving updates
        try:
            chat_id = getattr(message.chat, "id", None)
            from_id = getattr(getattr(message, "from_user", None), "id", None)
            text = getattr(message, "text", "")
            print(f"📩 /start received | chat_id={chat_id} from_id={from_id} text={text!r}")
        except Exception:
            pass

        user_id = message.from_user.id if message.from_user else None
        if not user_id:
            return await message.reply("❌ Couldn't read your user id. Please try again.")

        # Load user language from DB if not in memory
        if user_id not in user_languages:
            load_user_language(user_id)

        # Bot username (used for referral links) — keep it resilient
        bot_username = ""
        try:
            bot_info = await client.get_me()
            bot_username = bot_info.username or ""
        except Exception as e:
            print(f"⚠️ get_me() failed in /start: {e}")

        # Parse referral code from /start ref_USERID
        if hasattr(message, "command") and message.command and len(message.command) > 1:
            param = message.command[1]
            if param.startswith("ref_"):
                try:
                    referrer_id = int(param[4:])
                    # Add referral if valid
                    if referrer_id != user_id:
                        add_referral(user_id, referrer_id)
                except Exception:
                    pass

        # Check if user is admin (skip all requirements)
        if is_admin(user_id):
            return await show_main_menu(client, message)

        # Check public access - if disabled, only admins can use bot
        if not public_access_enabled:
            try:
                await message.reply(
                    "🔒 **Bot is Private Mode**\n\n"
                    "This bot is currently in private mode.\n"
                    "Only admins can use it.\n\n"
                    "Admin ko bolo /enablepublic run kare (private chat me)."
                )
            except Exception as e:
                print(f"❌ Failed to reply private-mode message: {e}")
            return

        # Check force subscribe first
        if force_subscribe_channels:
            is_joined, not_joined = await check_user_joined(client, user_id)

            if not is_joined:
                # Show force subscribe message
                buttons = []
                for idx, channel in enumerate(not_joined):
                    link = channel.get("invite_link") or f"https://t.me/{channel['channel_id'].replace('@', '')}"
                    buttons.append([InlineKeyboardButton(f"📢 Join {channel['channel_name']}", url=link)])

                buttons.append([InlineKeyboardButton("✅ Joined All - Verify", callback_data="check_joined")])

                await message.reply(
                    "🔐 **Join Required!**\n\n"
                    "To use this bot, you must join the following channels/groups:\n\n"
                    f"📢 **{len(not_joined)} channel(s) remaining**\n\n"
                    "👇 Click below to join, then click **Verify**:",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                return

        # Check referral requirement
        ref_count = get_referral_count(user_id)
        if ref_count < REQUIRED_REFERRALS:
            ref_link = get_referral_link(bot_username, user_id)
            remaining = REQUIRED_REFERRALS - ref_count

            await message.reply(
                f"👥 **Referral Required!**\n\n"
                f"You need to invite **{REQUIRED_REFERRALS} users** to use this bot.\n\n"
                f"✅ Your referrals: **{ref_count}/{REQUIRED_REFERRALS}**\n"
                f"❌ Remaining: **{remaining}**\n\n"
                f"📤 **Your Referral Link:**\n`{ref_link}`\n\n"
                "Share this link with friends. When they start the bot using your link, you get +1 referral!",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔄 Check Again", callback_data="check_referrals")]]
                ),
            )
            return

        # User passed all checks - show main menu
        await show_main_menu(client, message)

    def get_settings_keyboard(user_id=None):
        """Get the full settings keyboard"""
        num_accounts = len(user_clients)
        expected_speed = num_accounts * 30 if num_accounts else 0
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📤 Forward", callback_data="forward"),
                InlineKeyboardButton("📢 Channel", callback_data="channel")
            ],
            [
                InlineKeyboardButton("🔍 Filters", callback_data="filters_menu"),
                InlineKeyboardButton("🆘 @Admin", callback_data="admin")
            ],
            [
                InlineKeyboardButton("🚫 Block Forward", callback_data="mod_cmd_blockforward"),
                InlineKeyboardButton("🔗 Block Links", callback_data="mod_cmd_blocklinks")
            ],
            [
                InlineKeyboardButton("🔞 Block Bad Words", callback_data="mod_cmd_blockbadwords"),
                InlineKeyboardButton("👁 Mod Status", callback_data="mod_cmd_modstatus")
            ],
            [
                InlineKeyboardButton("⚠️ Warnings", callback_data="mod_cmd_warnings"),
                InlineKeyboardButton("🔄 Reset Warnings", callback_data="mod_cmd_resetwarnings")
            ],
            [
                InlineKeyboardButton("📣 Mention All", callback_data="mention_all")
            ],
            [
                InlineKeyboardButton("📥 Join Request", callback_data="join_request"),
                InlineKeyboardButton("📁 File Logo", callback_data="file_logo")
            ],
            [
                InlineKeyboardButton("👥 Referral", callback_data="my_referral"),
                InlineKeyboardButton("🇬🇧 Languages", callback_data="languages")
            ],
            [
                InlineKeyboardButton("🔙 Back", callback_data="manage_settings")
            ]
        ])

    async def show_main_menu(client, message, user_id=None):
        """Show welcome message with Add to Group + Manage Settings buttons"""
        if user_id is None:
            user_id = message.from_user.id if message.from_user else 0
        
        # Load user language if not cached
        if user_id and user_id not in user_languages:
            load_user_language(user_id)

        try:
            bot_info = await client.get_me()
            bot_username = bot_info.username or ""
        except Exception:
            bot_username = ""

        add_group_url = f"https://t.me/{bot_username}?startgroup=true" if bot_username else ""
        
        keyboard_buttons = []
        if add_group_url:
            keyboard_buttons.append([InlineKeyboardButton(t(user_id, "btn_add_group"), url=add_group_url)])
        keyboard_buttons.append([InlineKeyboardButton(t(user_id, "btn_settings"), callback_data="manage_settings")])
        keyboard_buttons.append([
            InlineKeyboardButton(t(user_id, "btn_group"), url=f"https://t.me/{bot_username}" if bot_username else "https://t.me"),
            InlineKeyboardButton(t(user_id, "btn_channel"), url=f"https://t.me/{bot_username}" if bot_username else "https://t.me")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(t(user_id, "btn_support"), callback_data="support_info"),
            InlineKeyboardButton(t(user_id, "btn_info"), callback_data="bot_info")
        ])
        keyboard_buttons.append([InlineKeyboardButton(t(user_id, "btn_languages"), callback_data="languages")])
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        msg_text = t(user_id, "welcome")
        
        await message.reply(msg_text, reply_markup=keyboard)
    
    # ============ FORCE SUBSCRIBE MANAGEMENT COMMANDS ============
    
    @bot_client.on_message(filters.command("addforcesub"))
    async def add_forcesub_handler(client, message):
        """Add a force subscribe channel/group"""
        try:
            parts = message.text.split(maxsplit=2)
            if len(parts) < 2:
                await message.reply(
                    "**Usage:** /addforcesub <channel_id/username> [invite_link]\n\n"
                    "**Examples:**\n"
                    "• /addforcesub @mychannel https://t.me/mychannel\n"
                    "• /addforcesub -1001234567890 https://t.me/+abcdef\n"
                    "• /addforcesub @mygroup"
                )
                return
            
            channel_id = parts[1]
            invite_link = parts[2] if len(parts) > 2 else ""
            
            # Try to get channel info
            try:
                chat = await client.get_chat(channel_id)
                channel_name = chat.title or channel_id
                actual_id = str(chat.id)
            except:
                channel_name = channel_id
                actual_id = channel_id
            
            if add_force_subscribe(actual_id, channel_name, invite_link):
                await message.reply(
                    f"✅ **Force Subscribe Added!**\n\n"
                    f"📢 Channel: {channel_name}\n"
                    f"🆔 ID: `{actual_id}`\n"
                    f"🔗 Link: {invite_link or 'Auto-generated'}\n\n"
                    f"Total force subs: {len(force_subscribe_channels)}"
                )
            else:
                await message.reply("⚠️ This channel is already in force subscribe list!")
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
    
    @bot_client.on_message(filters.command("removeforcesub"))
    async def remove_forcesub_handler(client, message):
        """Remove a force subscribe channel/group"""
        try:
            parts = message.text.split()
            if len(parts) < 2:
                await message.reply(
                    "**Usage:** /removeforcesub <channel_id/username>\n\n"
                    "Use /forcelist to see all force subscribe channels"
                )
                return
            
            channel_id = parts[1]
            
            # Try to find by ID or username
            found = False
            for ch in force_subscribe_channels:
                if ch["channel_id"] == channel_id or ch["channel_id"] == channel_id.replace("@", ""):
                    remove_force_subscribe(ch["channel_id"])
                    found = True
                    await message.reply(f"✅ Removed `{ch['channel_name']}` from force subscribe!")
                    break
            
            if not found:
                await message.reply("❌ Channel not found in force subscribe list!")
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
    
    @bot_client.on_message(filters.command("forcelist"))
    async def forcelist_handler(client, message):
        """List all force subscribe channels"""
        if not force_subscribe_channels:
            await message.reply(
                "📢 **No Force Subscribe Channels**\n\n"
                "Use /addforcesub to add channels/groups"
            )
            return
        
        channels_text = ""
        for idx, ch in enumerate(force_subscribe_channels, 1):
            channels_text += f"{idx}. **{ch['channel_name']}**\n"
            channels_text += f"   🆔 `{ch['channel_id']}`\n"
            channels_text += f"   🔗 {ch['invite_link'] or 'No link'}\n\n"
        
        await message.reply(
            f"📢 **Force Subscribe Channels ({len(force_subscribe_channels)})**\n\n"
            f"{channels_text}"
            f"**Commands:**\n"
            f"/addforcesub - Add channel\n"
            f"/removeforcesub - Remove channel"
        )
    
    @bot_client.on_callback_query()
    async def callback_handler(client, callback_query):
        data = callback_query.data
        
        # Handle "Manage Group Settings" button - show group list only
        if data == "manage_settings":
            user_id = callback_query.from_user.id
            
            # Fetch all admin groups from database
            admin_groups = []
            if admin_groups_col is not None:
                try:
                    admin_groups = list(admin_groups_col.find({}).sort("updated_at", -1))
                except Exception as e:
                    print(f"❌ Error fetching admin groups: {e}", flush=True)
            
            # Build group buttons - each group is a clickable button
            keyboard_buttons = []
            if admin_groups:
                for grp in admin_groups:
                    title = grp.get("title") or grp.get("chat_title") or "Unknown"
                    chat_id = grp.get("chat_id", "")
                    member_count = grp.get("member_count", 0)
                    # Check for link arrow
                    username = grp.get("username")
                    invite = grp.get("invite_link")
                    has_link = bool(username or invite)
                    
                    btn_text = f"{'🔗 ' if has_link else ''}{title}"
                    if member_count:
                        btn_text += f" — {member_count}%"
                    
                    keyboard_buttons.append([InlineKeyboardButton(btn_text, callback_data=f"select_group_{chat_id}")])
            
            keyboard_buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_start")])
            
            msg_text = (
                f"**Manage group Settings**\n"
                f"👉 Select the group whose settings you want to change.\n\n"
                f"If a group in which **you are an administrator** doesn't appear here:\n"
                f"  • Send /reload in the group and try again\n"
                f"  • Send /settings in the group and then press \"Open in pvt\""
            )
            
            await safe_edit_message(
                callback_query.message,
                msg_text,
                reply_markup=InlineKeyboardMarkup(keyboard_buttons)
            )
            await callback_query.answer()
            return
        
        # Handle group selection - show settings for that group
        if data.startswith("select_group_"):
            selected_group_id = data.replace("select_group_", "")
            user_id = callback_query.from_user.id
            
            # Get group info
            group_title = "Group"
            if admin_groups_col is not None:
                try:
                    grp_data = admin_groups_col.find_one({"chat_id": int(selected_group_id) if selected_group_id.lstrip('-').isdigit() else selected_group_id})
                    if grp_data:
                        group_title = grp_data.get("title") or grp_data.get("chat_title") or "Group"
                except Exception:
                    pass
            
            num_accounts = len(user_clients)
            expected_speed = num_accounts * 30 if num_accounts else 0
            
            if user_id in ADMIN_IDS:
                msg_text = (
                    f"⚙️ **{group_title} — Settings**\n\n"
                    f"👥 Active accounts: {num_accounts}\n"
                    f"⚡ Expected speed: ~{expected_speed}/min\n\n"
                    f"Select an option below:"
                )
            else:
                msg_text = (
                    f"⚙️ **{group_title} — Settings**\n\n"
                    f"Select an option below:"
                )
            
            await safe_edit_message(
                callback_query.message,
                msg_text,
                reply_markup=get_settings_keyboard(user_id)
            )
            await callback_query.answer()
            return
        
        # Handle "Back to Start" button
        if data == "back_to_start":
            user_id = callback_query.from_user.id
            if user_id not in user_languages:
                load_user_language(user_id)

            try:
                bot_info = await client.get_me()
                bot_username = bot_info.username or ""
            except Exception:
                bot_username = ""

            add_group_url = f"https://t.me/{bot_username}?startgroup=true" if bot_username else ""
            
            keyboard_buttons = []
            if add_group_url:
                keyboard_buttons.append([InlineKeyboardButton(t(user_id, "btn_add_group"), url=add_group_url)])
            keyboard_buttons.append([InlineKeyboardButton(t(user_id, "btn_settings"), callback_data="manage_settings")])
            keyboard_buttons.append([
                InlineKeyboardButton(t(user_id, "btn_group"), url=f"https://t.me/{bot_username}" if bot_username else "https://t.me"),
                InlineKeyboardButton(t(user_id, "btn_channel"), url=f"https://t.me/{bot_username}" if bot_username else "https://t.me")
            ])
            keyboard_buttons.append([
                InlineKeyboardButton(t(user_id, "btn_support"), callback_data="support_info"),
                InlineKeyboardButton(t(user_id, "btn_info"), callback_data="bot_info")
            ])
            keyboard_buttons.append([InlineKeyboardButton(t(user_id, "btn_languages"), callback_data="languages")])
            
            msg_text = t(user_id, "welcome")
            
            await safe_edit_message(
                callback_query.message,
                msg_text,
                reply_markup=InlineKeyboardMarkup(keyboard_buttons)
            )
            await callback_query.answer()
            return
        
        # Handle support info
        if data == "support_info":
            await callback_query.answer("🆘 Support: Contact @YourSupportUsername for help!", show_alert=True)
            return
        
        # Handle bot info
        if data == "bot_info":
            await callback_query.answer("💬 This bot helps you manage Telegram groups with moderation, forwarding, and more!", show_alert=True)
            return
        
        # Handle force subscribe verification
        if data == "check_joined":
            user_id = callback_query.from_user.id
            is_joined, not_joined = await check_user_joined(client, user_id)
            
            if is_joined:
                # Check if admin (bypass referral)
                if is_admin(user_id):
                    keyboard = get_settings_keyboard(user_id)
                    
                    msg_text = (
                        f"✅ **Verification Successful!**\n\n"
                        f"⚙️ **Group Settings**\n\n"
                        f"Select an option below:"
                    )
                    
                    await safe_edit_message(
                        callback_query.message,
                        msg_text,
                        reply_markup=keyboard
                    )
                    await callback_query.answer()
                    return
                
                # Check referral requirement
                ref_count = get_referral_count(user_id)
                if ref_count < REQUIRED_REFERRALS:
                    bot_info = await client.get_me()
                    ref_link = get_referral_link(bot_info.username, user_id)
                    remaining = REQUIRED_REFERRALS - ref_count
                    
                    await safe_edit_message(
                        callback_query.message,
                        f"✅ **Channels Joined!**\n\n"
                        f"👥 **Referral Required!**\n\n"
                        f"You need to invite **{REQUIRED_REFERRALS} users** to use this bot.\n\n"
                        f"✅ Your referrals: **{ref_count}/{REQUIRED_REFERRALS}**\n"
                        f"❌ Remaining: **{remaining}**\n\n"
                        f"📤 **Your Referral Link:**\n`{ref_link}`\n\n"
                        f"Share this link with friends!",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔄 Check Again", callback_data="check_referrals")]
                        ])
                    )
                    await callback_query.answer()
                    return
                
                # User passed all checks - show settings menu
                keyboard = get_settings_keyboard(user_id)
                msg_text = (
                    f"✅ **Verification Successful!**\n\n"
                    f"⚙️ **Group Settings**\n\n"
                    f"Select an option below:"
                )
                
                await safe_edit_message(
                    callback_query.message,
                    msg_text,
                    reply_markup=keyboard
                )
            else:
                # Still not joined
                buttons = []
                for channel in not_joined:
                    link = channel.get("invite_link") or f"https://t.me/{channel['channel_id'].replace('@', '').replace('-', '')}"
                    buttons.append([InlineKeyboardButton(f"📢 Join {channel['channel_name']}", url=link)])
                
                buttons.append([InlineKeyboardButton("✅ Joined All - Verify", callback_data="check_joined")])
                
                await safe_edit_message(
                    callback_query.message,
                    "❌ **Not Joined Yet!**\n\n"
                    f"You still need to join **{len(not_joined)}** channel(s):\n\n"
                    "👇 Click below to join, then click **Verify** again:",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
            
            await callback_query.answer()
            return
        
        if data == "check_referrals":
            user_id = callback_query.from_user.id
            
            # Check if admin
            if is_admin(user_id):
                keyboard = get_settings_keyboard(user_id)
                await safe_edit_message(
                    callback_query.message,
                    f"✅ **Admin Access!**\n\n"
                    f"⚙️ **Group Settings**\n\n"
                    f"Select an option below:",
                    reply_markup=keyboard
                )
                await callback_query.answer()
                return
            
            ref_count = get_referral_count(user_id)
            
            if ref_count >= REQUIRED_REFERRALS:
                keyboard = get_settings_keyboard(user_id)
                await safe_edit_message(
                    callback_query.message,
                    f"✅ **Referral Complete!**\n\n"
                    f"⚙️ **Group Settings**\n\n"
                    f"Select an option below:",
                    reply_markup=keyboard
                )
            else:
                bot_info = await client.get_me()
                ref_link = get_referral_link(bot_info.username, user_id)
                remaining = REQUIRED_REFERRALS - ref_count
                
                await safe_edit_message(
                    callback_query.message,
                    f"👥 **Referral Required!**\n\n"
                    f"You need to invite **{REQUIRED_REFERRALS} users** to use this bot.\n\n"
                    f"✅ Your referrals: **{ref_count}/{REQUIRED_REFERRALS}**\n"
                    f"❌ Remaining: **{remaining}**\n\n"
                    f"📤 **Your Referral Link:**\n`{ref_link}`\n\n"
                    f"Share this link with friends!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Check Again", callback_data="check_referrals")]
                    ])
                )
            
            await callback_query.answer()
            return
        
        if data == "my_referral":
            user_id = callback_query.from_user.id
            bot_info = await client.get_me()
            ref_link = get_referral_link(bot_info.username, user_id)
            ref_count = get_referral_count(user_id)
            
            await safe_edit_message(
                callback_query.message,
                f"👥 **Your Referral Stats**\n\n"
                f"✅ Total referrals: **{ref_count}**\n"
                f"🎯 Required: **{REQUIRED_REFERRALS}**\n\n"
                f"📤 **Your Referral Link:**\n`{ref_link}`\n\n"
                f"Share this link to invite friends!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                ])
            )
            await callback_query.answer()
            return
        
        # Helper function to verify user requirements
        async def verify_user_access(callback_query, client):
            """Check if user has completed force subscribe and referral requirements"""
            user_id = callback_query.from_user.id
            
            # Admins bypass all checks
            if is_admin(user_id):
                return True
            
            # Check force subscribe
            if force_subscribe_channels:
                is_joined, not_joined = await check_user_joined(client, user_id)
                if not is_joined:
                    buttons = []
                    for channel in not_joined:
                        link = channel.get("invite_link") or f"https://t.me/{channel['channel_id'].replace('@', '').replace('-', '')}"
                        buttons.append([InlineKeyboardButton(f"📢 Join {channel['channel_name']}", url=link)])
                    buttons.append([InlineKeyboardButton("✅ Joined All - Verify", callback_data="check_joined")])
                    
                    await safe_edit_message(
                        callback_query.message,
                        "🔐 **Join Required!**\n\n"
                        f"You still need to join **{len(not_joined)}** channel(s):\n\n"
                        "👇 Click below to join, then click **Verify**:",
                        reply_markup=InlineKeyboardMarkup(buttons)
                    )
                    await callback_query.answer("❌ Please join all channels first!", show_alert=True)
                    return False
            
            # Check referral requirement
            ref_count = get_referral_count(user_id)
            if ref_count < REQUIRED_REFERRALS:
                bot_info = await client.get_me()
                ref_link = get_referral_link(bot_info.username, user_id)
                remaining = REQUIRED_REFERRALS - ref_count
                
                await safe_edit_message(
                    callback_query.message,
                    f"👥 **Referral Required!**\n\n"
                    f"You need to invite **{REQUIRED_REFERRALS} users** to use this bot.\n\n"
                    f"✅ Your referrals: **{ref_count}/{REQUIRED_REFERRALS}**\n"
                    f"❌ Remaining: **{remaining}**\n\n"
                    f"📤 **Your Referral Link:**\n`{ref_link}`\n\n"
                    f"Share this link with friends!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Check Again", callback_data="check_referrals")]
                    ])
                )
                await callback_query.answer("❌ Complete referrals first!", show_alert=True)
                return False
            
            return True
        
        if data == "forward":
            user_id = callback_query.from_user.id
            
            # Verify user access
            if not await verify_user_access(callback_query, client):
                return
            
            # Check if user has accounts connected
            if not user_clients:
                await safe_edit_message(callback_query.message, "❌ No user accounts connected!")
                await callback_query.answer()
                return
            
            # Start forward wizard - Set source chat
            forward_wizard_state[user_id] = {
                "state": "waiting_source",
                "source_channel": "",
                "source_title": "",
                "skip_number": 0,
                "last_message_id": 0,
                "dest_channel": "",
                "dest_title": "",
                "filters": {
                    "skip_videos": False,
                    "skip_photos": False,
                    "skip_files": False,
                    "skip_audio": False,
                    "skip_stickers": False,
                    "skip_text": False
                }
            }
            
            cancel_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_forward")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
            ])
            
            await safe_edit_message(
                callback_query.message,
                "**( SET SOURCE CHAT )**\n\n"
                "Forward the last message or last message link of source chat.\n"
                "/cancel - cancel this process",
                reply_markup=cancel_keyboard
            )
            await callback_query.answer()
        elif data == "channel":
            # Verify user access
            if not await verify_user_access(callback_query, client):
                return
            
            # Get user's saved channels
            user_id = callback_query.from_user.id
            user_channels = []
            if user_channels_col is not None:
                saved = user_channels_col.find({"user_id": user_id})
                user_channels = [c.get("channel") for c in saved if c.get("channel")]
            
            channels_text = "\n".join([f"• `{ch}`" for ch in user_channels]) if user_channels else "No channels added yet"
            
            channel_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Channel ➕", callback_data="add_channel")],
                [InlineKeyboardButton("🗑️ Remove Channel", callback_data="remove_channel")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
            ])
            
            await safe_edit_message(
                callback_query.message,
                f"📢 **My Channels**\n\n"
                f"you can manage your target chats in here\n\n"
                f"**Your Channels ({len(user_channels)}):**\n{channels_text}",
                reply_markup=channel_keyboard
            )
            await callback_query.answer()
        elif data == "add_channel":
            # Verify user access
            if not await verify_user_access(callback_query, client):
                return
            
            user_id = callback_query.from_user.id
            user_channel_state[user_id] = "waiting_add_channel"
            
            await safe_edit_message(
                callback_query.message,
                "📢 **Add Channel**\n\n"
                "Send me the channel/chat username or link:\n\n"
                "Examples:\n"
                "• @channelname\n"
                "• https://t.me/channelname\n"
                "• -1001234567890\n\n"
                "Just send the message below 👇",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="channel")]
                ])
            )
            await callback_query.answer()
        elif data == "remove_channel":
            # Verify user access
            if not await verify_user_access(callback_query, client):
                return
            
            user_id = callback_query.from_user.id
            user_channels = []
            if user_channels_col is not None:
                saved = user_channels_col.find({"user_id": user_id})
                user_channels = [c.get("channel") for c in saved if c.get("channel")]
            
            if not user_channels:
                await safe_edit_message(
                    callback_query.message,
                    "❌ No channels to remove!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="channel")]
                    ])
                )
                await callback_query.answer()
                return
            
            # Create buttons for each channel to remove
            buttons = [[InlineKeyboardButton(f"🗑️ {ch}", callback_data=f"del_ch_{ch}")] for ch in user_channels[:10]]
            buttons.append([InlineKeyboardButton("🔙 Back", callback_data="channel")])
            
            await safe_edit_message(
                callback_query.message,
                "🗑️ **Remove Channel**\n\n"
                "Select a channel to remove:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            await callback_query.answer()
        elif data.startswith("del_ch_"):
            channel_to_delete = data.replace("del_ch_", "")
            user_id = callback_query.from_user.id
            
            if user_channels_col is not None:
                user_channels_col.delete_one({"user_id": user_id, "channel": channel_to_delete})
            
            await safe_edit_message(
                callback_query.message,
                f"✅ Channel `{channel_to_delete}` removed!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="channel")]
                ])
            )
            await callback_query.answer()
        elif data == "back_main":
            # Go back to main menu
            num_accounts = len(user_clients)
            expected_speed = num_accounts * 30 if num_accounts else 0
            
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📤 Forward", callback_data="forward"),
                    InlineKeyboardButton("📢 Channel", callback_data="channel")
                ],
                [
                    InlineKeyboardButton("🔍 Filters", callback_data="filters_menu"),
                    InlineKeyboardButton("🆘 @Admin", callback_data="admin")
                ],
                [
                    InlineKeyboardButton("🚫 Block Forward", callback_data="mod_cmd_blockforward"),
                    InlineKeyboardButton("🔗 Block Links", callback_data="mod_cmd_blocklinks")
                ],
                [
                    InlineKeyboardButton("🔞 Block Bad Words", callback_data="mod_cmd_blockbadwords"),
                    InlineKeyboardButton("👁 Mod Status", callback_data="mod_cmd_modstatus")
                ],
                [
                    InlineKeyboardButton("⚠️ Warnings", callback_data="mod_cmd_warnings"),
                    InlineKeyboardButton("🔄 Reset Warnings", callback_data="mod_cmd_resetwarnings")
                ],
                [
                    InlineKeyboardButton("📣 Mention All", callback_data="mention_all")
                ],
                [
                    InlineKeyboardButton("📥 Join Request", callback_data="join_request"),
                    InlineKeyboardButton("📁 File Logo", callback_data="file_logo")
                ],
                [
                    InlineKeyboardButton("👥 Referral", callback_data="my_referral"),
                    InlineKeyboardButton("🇬🇧 Languages", callback_data="languages")
                ]
            ])
            
            # Only show account info to admins
            user_id = callback_query.from_user.id
            if user_id in ADMIN_IDS:
                msg_text = (
                    f"🚀 **Telegram Forwarder Bot**\n\n"
                    f"👥 Connected accounts: {num_accounts}\n"
                    f"⚡ Expected speed: ~{expected_speed} msg/min\n\n"
                    "Select an option below:"
                )
            else:
                msg_text = (
                    f"🚀 **Telegram Forwarder Bot**\n\n"
                    "Select an option below:"
                )
            
            await safe_edit_message(
                callback_query.message,
                msg_text,
                reply_markup=keyboard
            )
            await callback_query.answer()
        elif data == "mention_all":
            await callback_query.answer(
                "📣 Mention All\n\n"
                "Command: /mentionall\n\n"
                "👉 Yeh command apne group mein send karo.\n"
                "Bot group ke sab members ko blue tick mention ke sath tag karega.",
                show_alert=True
            )
        elif data == "moderation":
            # Verify user access
            if not await verify_user_access(callback_query, client):
                return
            
            chat_id = callback_query.message.chat.id
            mc = moderation_config.get(chat_id) or load_moderation_config(chat_id)
            mod_on = mc.get("enabled", False)
            status_text = "✅ **Moderation is ENABLED**" if mod_on else "❌ **Moderation is DISABLED**"
            enable_label = "✅ Enable Mod ✅" if mod_on else "✅ Enable Mod"
            disable_label = "❌ Disable Mod ✅" if not mod_on else "❌ Disable Mod"
            
            await safe_edit_message(
                callback_query.message,
                f"🛡️ **Content Moderation**\n\n"
                f"{status_text}\n\n"
                "👇 Buttons se sab set karo, koi command type nahi karna\n\n"
                "⚠️ **Warning System:**\n"
                "• Set punishment: Off/Kick/Mute/Ban\n"
                "• Admins are exempt from all filters",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(enable_label, callback_data="mod_cmd_enablemod"), InlineKeyboardButton(disable_label, callback_data="mod_cmd_disablemod")],
                    [InlineKeyboardButton("🚫 Block Forward", callback_data="mod_cmd_blockforward"), InlineKeyboardButton("🔗 Block Links", callback_data="mod_cmd_blocklinks")],
                    [InlineKeyboardButton("🔞 Block Bad Words", callback_data="mod_cmd_blockbadwords"), InlineKeyboardButton("👁 Mod Status", callback_data="mod_cmd_modstatus")],
                    [InlineKeyboardButton("⚠️ Warnings", callback_data="mod_cmd_warnings"), InlineKeyboardButton("🔄 Reset Warnings", callback_data="mod_cmd_resetwarnings")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                ])
            )
            await callback_query.answer()
        elif data.startswith("mod_cmd_"):
            # Moderation command buttons
            print(f"[MOD_CMD] Handling callback: {data}", flush=True)
            if data == "mod_cmd_warnings":
                chat_id = callback_query.message.chat.id
                text, markup = build_warnings_menu(chat_id)
                await safe_edit_message(callback_query.message, text, reply_markup=markup, parse_mode="html")
                await callback_query.answer()
            elif data == "mod_cmd_blockforward":
                chat_id = callback_query.message.chat.id
                mc = moderation_config.get(chat_id) or load_moderation_config(chat_id)
                status = mc.get("block_forward", False)
                status_text = "✅ Enabled" if status else "❌ Disabled"
                bf_p = mc.get("bf_punishment", "mute")
                def bf_label(key, emoji, lbl):
                    return f"{emoji} {lbl} ✅" if bf_p == key else f"{emoji} {lbl}"
                text = (
                    "🚫 <b>Block Forward</b>\n\n"
                    "When enabled, all forwarded messages from other channels/groups will be <b>automatically deleted</b>.\n\n"
                    f"<b>Current Status:</b> {status_text}\n"
                    f"<b>Punishment:</b> {bf_p.title()}"
                )
                keyboard = [
                    [
                        InlineKeyboardButton(f"✅ Enable{' ✅' if status else ''}", callback_data="mod_toggle_blockforward_on"),
                        InlineKeyboardButton(f"❌ Disable{' ✅' if not status else ''}", callback_data="mod_toggle_blockforward_off"),
                    ],
                    [
                        InlineKeyboardButton(bf_label("off", "✖", "Off"), callback_data="bf_p_off"),
                        InlineKeyboardButton(bf_label("kick", "❗", "Kick"), callback_data="bf_p_kick"),
                    ],
                    [
                        InlineKeyboardButton(bf_label("mute", "🔇", "Mute"), callback_data="bf_p_mute"),
                        InlineKeyboardButton(bf_label("ban", "🚫", "Ban"), callback_data="bf_p_ban"),
                    ],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
                ]
                await safe_edit_message(callback_query.message, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="html")
                await callback_query.answer()

            elif data == "mod_cmd_blocklinks":
                chat_id = callback_query.message.chat.id
                mc = moderation_config.get(chat_id) or load_moderation_config(chat_id)
                status = mc.get("block_links", False)
                status_text = "✅ Enabled" if status else "❌ Disabled"
                bl_p = mc.get("bl_punishment", "mute")
                def bl_label(key, emoji, lbl):
                    return f"{emoji} {lbl} ✅" if bl_p == key else f"{emoji} {lbl}"
                text = (
                    "🔗 <b>Block Links</b>\n\n"
                    "When enabled, messages containing <b>URLs, Telegram links</b> (t.me) will be <b>automatically deleted</b>.\n\n"
                    f"<b>Current Status:</b> {status_text}\n"
                    f"<b>Punishment:</b> {bl_p.title()}"
                )
                keyboard = [
                    [
                        InlineKeyboardButton(f"✅ Enable{' ✅' if status else ''}", callback_data="mod_toggle_blocklinks_on"),
                        InlineKeyboardButton(f"❌ Disable{' ✅' if not status else ''}", callback_data="mod_toggle_blocklinks_off"),
                    ],
                    [
                        InlineKeyboardButton(bl_label("off", "✖", "Off"), callback_data="bl_p_off"),
                        InlineKeyboardButton(bl_label("kick", "❗", "Kick"), callback_data="bl_p_kick"),
                    ],
                    [
                        InlineKeyboardButton(bl_label("mute", "🔇", "Mute"), callback_data="bl_p_mute"),
                        InlineKeyboardButton(bl_label("ban", "🚫", "Ban"), callback_data="bl_p_ban"),
                    ],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
                ]
                await safe_edit_message(callback_query.message, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="html")
                await callback_query.answer()

            elif data == "mod_cmd_blockbadwords":
                chat_id = callback_query.message.chat.id
                mc = moderation_config.get(chat_id) or load_moderation_config(chat_id)
                status = mc.get("block_badwords", False)
                status_text = "✅ Enabled" if status else "❌ Disabled"
                bbw_p = mc.get("bbw_punishment", "mute")
                def bbw_label(key, emoji, lbl):
                    return f"{emoji} {lbl} ✅" if bbw_p == key else f"{emoji} {lbl}"
                text = (
                    "🔞 <b>Block Bad Words</b>\n\n"
                    "When enabled, messages containing <b>inappropriate/abusive words</b> (Hindi + English) will be <b>automatically deleted</b>.\n\n"
                    f"<b>Current Status:</b> {status_text}\n"
                    f"<b>Punishment:</b> {bbw_p.title()}"
                )
                keyboard = [
                    [
                        InlineKeyboardButton(f"✅ Enable{' ✅' if status else ''}", callback_data="mod_toggle_blockbadwords_on"),
                        InlineKeyboardButton(f"❌ Disable{' ✅' if not status else ''}", callback_data="mod_toggle_blockbadwords_off"),
                    ],
                    [
                        InlineKeyboardButton(bbw_label("off", "✖", "Off"), callback_data="bbw_p_off"),
                        InlineKeyboardButton(bbw_label("kick", "❗", "Kick"), callback_data="bbw_p_kick"),
                    ],
                    [
                        InlineKeyboardButton(bbw_label("mute", "🔇", "Mute"), callback_data="bbw_p_mute"),
                        InlineKeyboardButton(bbw_label("ban", "🚫", "Ban"), callback_data="bbw_p_ban"),
                    ],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
                ]
                await safe_edit_message(callback_query.message, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="html")
                await callback_query.answer()

            elif data == "mod_cmd_resetwarnings":
                chat_id = callback_query.message.chat.id
                warned_users = []
                if warnings_col is not None:
                    cursor = warnings_col.find({"chat_id": chat_id, "count": {"$gt": 0}})
                    for doc in cursor:
                        warned_users.append({"user_id": doc.get("user_id"), "count": doc.get("count", 0)})
                
                if not warned_users:
                    text = (
                        "🔄 <b>Reset Warnings</b>\n\n"
                        "No warned users in this group.\n\n"
                        "Use this menu to reset warnings for all users or use:\n"
                        "<code>/resetwarnings @username</code> to reset a specific user."
                    )
                    keyboard = [
                        [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
                    ]
                else:
                    lines = ["🔄 <b>Reset Warnings</b>\n\n📋 <b>Warned Users:</b>\n"]
                    for wu in warned_users[:20]:
                        lines.append(f"• User <code>{wu['user_id']}</code> — {wu['count']} warning(s)")
                    lines.append(f"\n<b>Total:</b> {len(warned_users)} user(s)")
                    lines.append("\nUse <code>/resetwarnings @username</code> to reset a specific user.")
                    text = "\n".join(lines)
                    keyboard = [
                        [InlineKeyboardButton("🗑 Reset ALL Warnings", callback_data="mod_reset_all_warnings")],
                        [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
                    ]
                await safe_edit_message(callback_query.message, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="html")
                await callback_query.answer()

            elif data == "mod_cmd_enablemod":
                chat_id = callback_query.message.chat.id
                mc = moderation_config.get(chat_id) or load_moderation_config(chat_id)
                mc["enabled"] = True
                moderation_config[chat_id] = mc
                save_moderation_config(chat_id)
                await callback_query.answer("✅ Moderation Enabled!", show_alert=True)
                # Re-render moderation menu
                callback_query.data = "moderation"
                await safe_edit_message(
                    callback_query.message,
                    "🛡️ **Content Moderation**\n\n"
                    "✅ **Moderation is ENABLED**\n\n"
                    "Add bot as admin in your group, then use buttons below to configure.\n\n"
                    "🔞 **Sex Content Filter:**\n"
                    "Block Bad Words to auto-delete inappropriate content\n\n"
                    "⚠️ **Warning System:**\n"
                    "• Set punishment: Off/Kick/Mute/Ban\n"
                    "• Set max warnings before punishment\n"
                    "• Admins are exempt from all filters",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Enable Mod ✅", callback_data="mod_cmd_enablemod"), InlineKeyboardButton("❌ Disable Mod", callback_data="mod_cmd_disablemod")],
                        [InlineKeyboardButton("🚫 Block Forward", callback_data="mod_cmd_blockforward"), InlineKeyboardButton("🔗 Block Links", callback_data="mod_cmd_blocklinks")],
                        [InlineKeyboardButton("🔞 Block Bad Words", callback_data="mod_cmd_blockbadwords"), InlineKeyboardButton("👁 Mod Status", callback_data="mod_cmd_modstatus")],
                        [InlineKeyboardButton("⚠️ Warnings", callback_data="mod_cmd_warnings"), InlineKeyboardButton("🔄 Reset Warnings", callback_data="mod_cmd_resetwarnings")],
                        [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                    ])
                )
            elif data == "mod_cmd_disablemod":
                chat_id = callback_query.message.chat.id
                mc = moderation_config.get(chat_id) or load_moderation_config(chat_id)
                mc["enabled"] = False
                moderation_config[chat_id] = mc
                save_moderation_config(chat_id)
                await callback_query.answer("❌ Moderation Disabled!", show_alert=True)
                await safe_edit_message(
                    callback_query.message,
                    "🛡️ **Content Moderation**\n\n"
                    "❌ **Moderation is DISABLED**\n\n"
                    "Enable moderation to start protecting your group.\n\n"
                    "Use buttons below to configure and enable.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Enable Mod", callback_data="mod_cmd_enablemod"), InlineKeyboardButton("❌ Disable Mod ✅", callback_data="mod_cmd_disablemod")],
                        [InlineKeyboardButton("🚫 Block Forward", callback_data="mod_cmd_blockforward"), InlineKeyboardButton("🔗 Block Links", callback_data="mod_cmd_blocklinks")],
                        [InlineKeyboardButton("🔞 Block Bad Words", callback_data="mod_cmd_blockbadwords"), InlineKeyboardButton("👁 Mod Status", callback_data="mod_cmd_modstatus")],
                        [InlineKeyboardButton("⚠️ Warnings", callback_data="mod_cmd_warnings"), InlineKeyboardButton("🔄 Reset Warnings", callback_data="mod_cmd_resetwarnings")],
                        [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                    ])
                )
            elif data == "mod_cmd_modstatus":
                chat_id = callback_query.message.chat.id
                mc = moderation_config.get(chat_id) or load_moderation_config(chat_id)
                wc = get_warning_config(chat_id)
                mod_enabled = "✅ ON" if mc.get("enabled") else "❌ OFF"
                bf = "✅" if mc.get("block_forward") else "❌"
                bl = "✅" if mc.get("block_links") else "❌"
                bbw = "✅" if mc.get("block_badwords") else "❌"
                bm = "✅" if mc.get("block_mentions") else "❌"
                ad = "✅" if mc.get("auto_delete_2min") else "❌"
                punishment = wc.get("punishment", "mute").title()
                max_w = wc.get("max_warns", 3)
                mute_dur = wc.get("mute_duration", 3)
                text = (
                    f"👁 <b>Moderation Status</b>\n\n"
                    f"<b>Moderation:</b> {mod_enabled}\n"
                    f"<b>Block Forward:</b> {bf}\n"
                    f"<b>Block Links:</b> {bl}\n"
                    f"<b>Block Bad Words:</b> {bbw}\n"
                    f"<b>Block Mentions:</b> {bm}\n"
                    f"<b>Auto Delete 2min:</b> {ad}\n\n"
                    f"<b>Punishment:</b> {punishment}\n"
                    f"<b>Max Warnings:</b> {max_w}\n"
                    f"<b>Mute Duration:</b> {mute_dur}h"
                )
                await safe_edit_message(
                    callback_query.message, text,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]),
                    parse_mode="html"
                )
                await callback_query.answer()
        elif data.startswith("warn_p_"):
            try:
                chat_id = callback_query.message.chat.id
                p_type = data.replace("warn_p_", "")
                print(f"[WARN_P] chat_id={chat_id} p_type={p_type}", flush=True)
                if p_type in ("off", "kick", "mute", "ban"):
                    wc = get_warning_config(chat_id)
                    wc["punishment"] = p_type
                    warning_config[chat_id] = wc
                    save_warning_config(chat_id)
                    await callback_query.answer(f"✅ Punishment set to: {p_type.title()}", show_alert=True)
                    text, markup = build_warnings_menu(chat_id)
                    await safe_edit_message(callback_query.message, text, reply_markup=markup, parse_mode="html")
                else:
                    await callback_query.answer(f"❌ Unknown punishment: {p_type}", show_alert=True)
            except Exception as e:
                print(f"[WARN_P ERROR] {e}", flush=True)
                await callback_query.answer(f"❌ Error: {e}", show_alert=True)
        elif data.startswith("warn_maxw_"):
            try:
                chat_id = callback_query.message.chat.id
                num = int(data.replace("warn_maxw_", ""))
                print(f"[WARN_MAXW] chat_id={chat_id} num={num}", flush=True)
                if num < 2:
                    num = 2
                if num > 6:
                    num = 6
                wc = get_warning_config(chat_id)
                wc["max_warns"] = num
                warning_config[chat_id] = wc
                save_warning_config(chat_id)
                await callback_query.answer(f"✅ Max warns set to: {num}", show_alert=True)
                text, markup = build_warnings_menu(chat_id)
                await safe_edit_message(callback_query.message, text, reply_markup=markup, parse_mode="html")
            except Exception as e:
                print(f"[WARN_MAXW ERROR] {e}", flush=True)
                await callback_query.answer(f"❌ Error: {e}", show_alert=True)
        # (duplicate warn_maxw_ removed)
        elif data == "warn_list":
            try:
                # Show warned users list for this chat
                chat_id = callback_query.message.chat.id
                print(f"[WARN_LIST] chat_id={chat_id}", flush=True)
                warned_users = []
                if warnings_col is not None:
                    cursor = warnings_col.find({"chat_id": chat_id, "count": {"$gt": 0}})
                    for doc in cursor:
                        warned_users.append({"user_id": doc.get("user_id"), "count": doc.get("count", 0)})
                if not warned_users:
                    await callback_query.answer("📋 No warned users in this group!", show_alert=True)
                else:
                    lines = ["📋 <b>Warned Users:</b>\n"]
                    for wu in warned_users[:20]:
                        lines.append(f"• User <code>{wu['user_id']}</code> — {wu['count']} warning(s)")
                    text = "\n".join(lines)
                    await safe_edit_message(
                        callback_query.message, text,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="mod_cmd_warnings")]]),
                        parse_mode="html"
                    )
                    await callback_query.answer()
            except Exception as e:
                print(f"[WARN_LIST ERROR] {e}", flush=True)
                await callback_query.answer(f"❌ Error: {e}", show_alert=True)
        elif data.startswith("mod_toggle_blockforward_"):
            chat_id = callback_query.message.chat.id
            enable = data.endswith("_on")
            mc = moderation_config.get(chat_id) or load_moderation_config(chat_id)
            mc["block_forward"] = enable
            if enable:
                mc["enabled"] = True
            moderation_config[chat_id] = mc
            save_moderation_config(chat_id)
            await callback_query.answer(f"{'✅ Block Forward Enabled!' if enable else '❌ Block Forward Disabled!'}", show_alert=True)
            # Re-render with punishment buttons
            callback_query.data = "mod_cmd_blockforward"
            status = mc.get("block_forward", False)
            status_text = "✅ Enabled" if status else "❌ Disabled"
            bf_p = mc.get("bf_punishment", "mute")
            def bf_label2(key, emoji, lbl):
                return f"{emoji} {lbl} ✅" if bf_p == key else f"{emoji} {lbl}"
            text = (
                "🚫 <b>Block Forward</b>\n\n"
                "When enabled, all forwarded messages from other channels/groups will be <b>automatically deleted</b>.\n\n"
                f"<b>Current Status:</b> {status_text}\n"
                f"<b>Punishment:</b> {bf_p.title()}"
            )
            keyboard = [
                [
                    InlineKeyboardButton(f"✅ Enable{' ✅' if status else ''}", callback_data="mod_toggle_blockforward_on"),
                    InlineKeyboardButton(f"❌ Disable{' ✅' if not status else ''}", callback_data="mod_toggle_blockforward_off"),
                ],
                [
                    InlineKeyboardButton(bf_label2("off", "✖", "Off"), callback_data="bf_p_off"),
                    InlineKeyboardButton(bf_label2("kick", "❗", "Kick"), callback_data="bf_p_kick"),
                ],
                [
                    InlineKeyboardButton(bf_label2("mute", "🔇", "Mute"), callback_data="bf_p_mute"),
                    InlineKeyboardButton(bf_label2("ban", "🚫", "Ban"), callback_data="bf_p_ban"),
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
            ]
            await safe_edit_message(callback_query.message, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="html")

        elif data.startswith("mod_toggle_blocklinks_"):
            chat_id = callback_query.message.chat.id
            enable = data.endswith("_on")
            mc = moderation_config.get(chat_id) or load_moderation_config(chat_id)
            mc["block_links"] = enable
            if enable:
                mc["enabled"] = True
            moderation_config[chat_id] = mc
            save_moderation_config(chat_id)
            await callback_query.answer(f"{'✅ Block Links Enabled!' if enable else '❌ Block Links Disabled!'}", show_alert=True)
            status = mc.get("block_links", False)
            status_text = "✅ Enabled" if status else "❌ Disabled"
            bl_p = mc.get("bl_punishment", "mute")
            def bl_label2(key, emoji, lbl):
                return f"{emoji} {lbl} ✅" if bl_p == key else f"{emoji} {lbl}"
            text = (
                "🔗 <b>Block Links</b>\n\n"
                "When enabled, messages containing <b>URLs, Telegram links</b> (t.me) will be <b>automatically deleted</b>.\n\n"
                f"<b>Current Status:</b> {status_text}\n"
                f"<b>Punishment:</b> {bl_p.title()}"
            )
            keyboard = [
                [
                    InlineKeyboardButton(f"✅ Enable{' ✅' if status else ''}", callback_data="mod_toggle_blocklinks_on"),
                    InlineKeyboardButton(f"❌ Disable{' ✅' if not status else ''}", callback_data="mod_toggle_blocklinks_off"),
                ],
                [
                    InlineKeyboardButton(bl_label2("off", "✖", "Off"), callback_data="bl_p_off"),
                    InlineKeyboardButton(bl_label2("kick", "❗", "Kick"), callback_data="bl_p_kick"),
                ],
                [
                    InlineKeyboardButton(bl_label2("mute", "🔇", "Mute"), callback_data="bl_p_mute"),
                    InlineKeyboardButton(bl_label2("ban", "🚫", "Ban"), callback_data="bl_p_ban"),
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
            ]
            await safe_edit_message(callback_query.message, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="html")

        elif data.startswith("mod_toggle_blockbadwords_"):
            chat_id = callback_query.message.chat.id
            enable = data.endswith("_on")
            mc = moderation_config.get(chat_id) or load_moderation_config(chat_id)
            mc["block_badwords"] = enable
            if enable:
                mc["enabled"] = True
            moderation_config[chat_id] = mc
            save_moderation_config(chat_id)
            await callback_query.answer(f"{'✅ Block Bad Words Enabled!' if enable else '❌ Block Bad Words Disabled!'}", show_alert=True)
            status = mc.get("block_badwords", False)
            status_text = "✅ Enabled" if status else "❌ Disabled"
            bbw_p = mc.get("bbw_punishment", "mute")
            def bbw_label2(key, emoji, lbl):
                return f"{emoji} {lbl} ✅" if bbw_p == key else f"{emoji} {lbl}"
            text = (
                "🔞 <b>Block Bad Words</b>\n\n"
                "When enabled, messages containing <b>inappropriate/abusive words</b> (Hindi + English) will be <b>automatically deleted</b>.\n\n"
                f"<b>Current Status:</b> {status_text}\n"
                f"<b>Punishment:</b> {bbw_p.title()}"
            )
            keyboard = [
                [
                    InlineKeyboardButton(f"✅ Enable{' ✅' if status else ''}", callback_data="mod_toggle_blockbadwords_on"),
                    InlineKeyboardButton(f"❌ Disable{' ✅' if not status else ''}", callback_data="mod_toggle_blockbadwords_off"),
                ],
                [
                    InlineKeyboardButton(bbw_label2("off", "✖", "Off"), callback_data="bbw_p_off"),
                    InlineKeyboardButton(bbw_label2("kick", "❗", "Kick"), callback_data="bbw_p_kick"),
                ],
                [
                    InlineKeyboardButton(bbw_label2("mute", "🔇", "Mute"), callback_data="bbw_p_mute"),
                    InlineKeyboardButton(bbw_label2("ban", "🚫", "Ban"), callback_data="bbw_p_ban"),
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
            ]
            await safe_edit_message(callback_query.message, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="html")

        # Per-feature punishment handlers
        elif data.startswith("bf_p_") or data.startswith("bl_p_") or data.startswith("bbw_p_"):
            try:
                chat_id = callback_query.message.chat.id
                mc = moderation_config.get(chat_id) or load_moderation_config(chat_id)
                if data.startswith("bf_p_"):
                    p_type = data.replace("bf_p_", "")
                    config_key = "bf_punishment"
                    re_render = "mod_cmd_blockforward"
                    feature_name = "Block Forward"
                elif data.startswith("bl_p_"):
                    p_type = data.replace("bl_p_", "")
                    config_key = "bl_punishment"
                    re_render = "mod_cmd_blocklinks"
                    feature_name = "Block Links"
                else:
                    p_type = data.replace("bbw_p_", "")
                    config_key = "bbw_punishment"
                    re_render = "mod_cmd_blockbadwords"
                    feature_name = "Block Bad Words"
                
                if p_type in ("off", "kick", "mute", "ban"):
                    mc[config_key] = p_type
                    moderation_config[chat_id] = mc
                    save_moderation_config(chat_id)
                    await callback_query.answer(f"✅ {feature_name} punishment: {p_type.title()}", show_alert=True)
                    # Re-render the submenu
                    callback_query.data = re_render
                    # Trigger re-render by calling handler logic inline
                    # We just re-set data and let it fall through next time; for now, re-build inline
                    if re_render == "mod_cmd_blockforward":
                        status = mc.get("block_forward", False)
                        status_text = "✅ Enabled" if status else "❌ Disabled"
                        bf_p = mc.get("bf_punishment", "mute")
                        def bf_lbl(key, emoji, lbl):
                            return f"{emoji} {lbl} ✅" if bf_p == key else f"{emoji} {lbl}"
                        text = (
                            "🚫 <b>Block Forward</b>\n\n"
                            "When enabled, all forwarded messages from other channels/groups will be <b>automatically deleted</b>.\n\n"
                            f"<b>Current Status:</b> {status_text}\n"
                            f"<b>Punishment:</b> {bf_p.title()}"
                        )
                        keyboard = [
                            [InlineKeyboardButton(f"✅ Enable{' ✅' if status else ''}", callback_data="mod_toggle_blockforward_on"), InlineKeyboardButton(f"❌ Disable{' ✅' if not status else ''}", callback_data="mod_toggle_blockforward_off")],
                            [InlineKeyboardButton(bf_lbl("off", "✖", "Off"), callback_data="bf_p_off"), InlineKeyboardButton(bf_lbl("kick", "❗", "Kick"), callback_data="bf_p_kick")],
                            [InlineKeyboardButton(bf_lbl("mute", "🔇", "Mute"), callback_data="bf_p_mute"), InlineKeyboardButton(bf_lbl("ban", "🚫", "Ban"), callback_data="bf_p_ban")],
                            [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
                        ]
                    elif re_render == "mod_cmd_blocklinks":
                        status = mc.get("block_links", False)
                        status_text = "✅ Enabled" if status else "❌ Disabled"
                        bl_p = mc.get("bl_punishment", "mute")
                        def bl_lbl(key, emoji, lbl):
                            return f"{emoji} {lbl} ✅" if bl_p == key else f"{emoji} {lbl}"
                        text = (
                            "🔗 <b>Block Links</b>\n\n"
                            "When enabled, messages containing <b>URLs, Telegram links</b> (t.me) will be <b>automatically deleted</b>.\n\n"
                            f"<b>Current Status:</b> {status_text}\n"
                            f"<b>Punishment:</b> {bl_p.title()}"
                        )
                        keyboard = [
                            [InlineKeyboardButton(f"✅ Enable{' ✅' if status else ''}", callback_data="mod_toggle_blocklinks_on"), InlineKeyboardButton(f"❌ Disable{' ✅' if not status else ''}", callback_data="mod_toggle_blocklinks_off")],
                            [InlineKeyboardButton(bl_lbl("off", "✖", "Off"), callback_data="bl_p_off"), InlineKeyboardButton(bl_lbl("kick", "❗", "Kick"), callback_data="bl_p_kick")],
                            [InlineKeyboardButton(bl_lbl("mute", "🔇", "Mute"), callback_data="bl_p_mute"), InlineKeyboardButton(bl_lbl("ban", "🚫", "Ban"), callback_data="bl_p_ban")],
                            [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
                        ]
                    else:
                        status = mc.get("block_badwords", False)
                        status_text = "✅ Enabled" if status else "❌ Disabled"
                        bbw_p = mc.get("bbw_punishment", "mute")
                        def bbw_lbl(key, emoji, lbl):
                            return f"{emoji} {lbl} ✅" if bbw_p == key else f"{emoji} {lbl}"
                        text = (
                            "🔞 <b>Block Bad Words</b>\n\n"
                            "When enabled, messages containing <b>inappropriate/abusive words</b> (Hindi + English) will be <b>automatically deleted</b>.\n\n"
                            f"<b>Current Status:</b> {status_text}\n"
                            f"<b>Punishment:</b> {bbw_p.title()}"
                        )
                        keyboard = [
                            [InlineKeyboardButton(f"✅ Enable{' ✅' if status else ''}", callback_data="mod_toggle_blockbadwords_on"), InlineKeyboardButton(f"❌ Disable{' ✅' if not status else ''}", callback_data="mod_toggle_blockbadwords_off")],
                            [InlineKeyboardButton(bbw_lbl("off", "✖", "Off"), callback_data="bbw_p_off"), InlineKeyboardButton(bbw_lbl("kick", "❗", "Kick"), callback_data="bbw_p_kick")],
                            [InlineKeyboardButton(bbw_lbl("mute", "🔇", "Mute"), callback_data="bbw_p_mute"), InlineKeyboardButton(bbw_lbl("ban", "🚫", "Ban"), callback_data="bbw_p_ban")],
                            [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
                        ]
                    await safe_edit_message(callback_query.message, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="html")
                else:
                    await callback_query.answer(f"❌ Unknown punishment: {p_type}", show_alert=True)
            except Exception as e:
                print(f"[FEATURE_PUNISHMENT ERROR] {e}", flush=True)
                await callback_query.answer(f"❌ Error: {e}", show_alert=True)

        elif data == "mod_reset_all_warnings":
            chat_id = callback_query.message.chat.id
            if warnings_col is not None:
                warnings_col.delete_many({"chat_id": chat_id})
            # Also clear in-memory
            keys_to_remove = [k for k in user_warnings if k[0] == chat_id]
            for k in keys_to_remove:
                del user_warnings[k]
            await callback_query.answer("✅ All warnings have been reset!", show_alert=True)
            # Re-render reset warnings menu
            text = (
                "🔄 <b>Reset Warnings</b>\n\n"
                "✅ All warnings have been cleared!\n\n"
                "No warned users in this group."
            )
            keyboard = [
                [InlineKeyboardButton("🔙 Back", callback_data="moderation")],
            ]
            await safe_edit_message(callback_query.message, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="html")

        elif data == "warn_mute_dur_menu":
            try:
                chat_id = callback_query.message.chat.id
                print(f"[WARN_MUTE_DUR_MENU] chat_id={chat_id}", flush=True)
                wc = get_warning_config(chat_id)
                current_dur = wc.get("mute_duration", 3)
                text = (
                    "🔇⏱ <b>Set Mute Duration</b>\n\n"
                    "Select how long a user will be muted when they exceed the maximum warnings.\n\n"
                    f"<b>Current Duration:</b> {current_dur} hour{'s' if current_dur != 1 else ''}"
                )
                durations = [1, 2, 3, 6, 12, 24]
                dur_buttons_row1 = []
                dur_buttons_row2 = []
                for d in durations[:3]:
                    label = f"{d}h ✅" if d == current_dur else f"{d}h"
                    dur_buttons_row1.append(InlineKeyboardButton(label, callback_data=f"warn_mute_dur_{d}"))
                for d in durations[3:]:
                    label = f"{d}h ✅" if d == current_dur else f"{d}h"
                    dur_buttons_row2.append(InlineKeyboardButton(label, callback_data=f"warn_mute_dur_{d}"))
                keyboard = [
                    dur_buttons_row1,
                    dur_buttons_row2,
                    [InlineKeyboardButton("🔙 Back", callback_data="mod_cmd_warnings")],
                ]
                await safe_edit_message(callback_query.message, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="html")
                await callback_query.answer()
            except Exception as e:
                print(f"[WARN_MUTE_DUR_MENU ERROR] {e}", flush=True)
                await callback_query.answer(f"❌ Error: {e}", show_alert=True)
        elif data.startswith("warn_mute_dur_"):
            try:
                chat_id = callback_query.message.chat.id
                dur = int(data.replace("warn_mute_dur_", ""))
                print(f"[WARN_MUTE_DUR] chat_id={chat_id} dur={dur}", flush=True)
                if dur not in (1, 2, 3, 6, 12, 24):
                    dur = 3
                wc = get_warning_config(chat_id)
                wc["mute_duration"] = dur
                warning_config[chat_id] = wc
                save_warning_config(chat_id)
                await callback_query.answer(f"✅ Mute duration set to: {dur}h", show_alert=True)
                text = (
                    "🔇⏱ <b>Set Mute Duration</b>\n\n"
                    "Select how long a user will be muted when they exceed the maximum warnings.\n\n"
                    f"<b>Current Duration:</b> {dur} hour{'s' if dur != 1 else ''}"
                )
                durations = [1, 2, 3, 6, 12, 24]
                dur_buttons_row1 = []
                dur_buttons_row2 = []
                for d in durations[:3]:
                    label = f"{d}h ✅" if d == dur else f"{d}h"
                    dur_buttons_row1.append(InlineKeyboardButton(label, callback_data=f"warn_mute_dur_{d}"))
                for d in durations[3:]:
                    label = f"{d}h ✅" if d == dur else f"{d}h"
                    dur_buttons_row2.append(InlineKeyboardButton(label, callback_data=f"warn_mute_dur_{d}"))
                keyboard = [
                    dur_buttons_row1,
                    dur_buttons_row2,
                    [InlineKeyboardButton("🔙 Back", callback_data="mod_cmd_warnings")],
                ]
                await safe_edit_message(callback_query.message, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="html")
            except Exception as e:
                print(f"[WARN_MUTE_DUR ERROR] {e}", flush=True)
                await callback_query.answer(f"❌ Error: {e}", show_alert=True)
        elif data == "admin":
            # Verify user access
            if not await verify_user_access(callback_query, client):
                return
            
            await safe_edit_message(
                callback_query.message,
                "🆘 <b>Admin Controls</b>\n\n"
                "👇 Button press karke command dekho:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🔒 Set Force Join", callback_data="acmd_setforcejoin"),
                        InlineKeyboardButton("❌ Remove Force Join", callback_data="acmd_removeforcejoin")
                    ],
                    [
                        InlineKeyboardButton("ℹ️ Force Join Info", callback_data="acmd_forcejoininfo"),
                        InlineKeyboardButton("👥 Set Join Wait", callback_data="acmd_setjoinwait")
                    ],
                    [
                        InlineKeyboardButton("🚫 Remove Join Wait", callback_data="acmd_removejoinwait"),
                        InlineKeyboardButton("📊 Join Wait Status", callback_data="acmd_joinwaitstatus")
                    ],
                    [
                        InlineKeyboardButton("🔗 Block Mention", callback_data="acmd_blockmention"),
                        InlineKeyboardButton("⏱ Auto Delete 2min", callback_data="acmd_autodelete2min")
                    ],
                    [
                        InlineKeyboardButton("✅ Enable Mod", callback_data="acmd_enablemod"),
                        InlineKeyboardButton("👁 Mod Status", callback_data="acmd_modstatus")
                    ],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                ]),
                parse_mode="html"
            )
            await callback_query.answer()
        elif data.startswith("acmd_"):
            acmd_map = {
                "acmd_setforcejoin": "/setforcejoin @channel|Name|Link",
                "acmd_removeforcejoin": "/removeforcejoin",
                "acmd_forcejoininfo": "/forcejoininfo",
                "acmd_setjoinwait": "/setjoinwait <number>",
                "acmd_removejoinwait": "/removejoinwait",
                "acmd_joinwaitstatus": "/joinwaitstatus",
                "acmd_blockmention": "/blockmention",
                "acmd_autodelete2min": "/autodelete2min",
                "acmd_enablemod": "/enablemod",
                "acmd_modstatus": "/modstatus",
            }
            cmd_text = acmd_map.get(data, "")
            if cmd_text:
                await callback_query.answer(f"📋 Command: {cmd_text}", show_alert=True)
        elif data == "join_request":
            # Verify user access
            if not await verify_user_access(callback_query, client):
                return
            
            channels_list = "\n".join([f"• `{ch}`" for ch in auto_approve_channels]) if auto_approve_channels else "None"
            await safe_edit_message(
                callback_query.message,
                "📥 <b>Join Request Auto-Approve</b>\n\n"
                f"<b>Status:</b> {'🟢 Active' if auto_approve_channels else '🔴 Inactive'}\n"
                f"<b>Total:</b> {len(auto_approve_channels)}\n"
                f"✅ Approved: {auto_approve_stats['approved']}\n"
                f"❌ Failed: {auto_approve_stats['failed']}\n\n"
                "👇 Button press karke command dekho:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Auto Approve", callback_data="jcmd_autoapprove"),
                        InlineKeyboardButton("🛑 Stop Approve", callback_data="jcmd_stopapprove")
                    ],
                    [
                        InlineKeyboardButton("📋 Approve All", callback_data="jcmd_approveall"),
                        InlineKeyboardButton("📜 Approve List", callback_data="jcmd_approvelist")
                    ],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                ]),
                parse_mode="html"
            )
            await callback_query.answer()
        elif data.startswith("jcmd_"):
            jcmd_map = {
                "jcmd_autoapprove": "/autoapprove <channel/group>",
                "jcmd_stopapprove": "/stopapprove <channel/group>",
                "jcmd_approveall": "/approveall <channel/group>",
                "jcmd_approvelist": "/approvelist",
            }
            cmd_text = jcmd_map.get(data, "")
            if cmd_text:
                await callback_query.answer(f"📋 Command: {cmd_text}", show_alert=True)
        elif data == "file_logo":
            # Verify user access
            if not await verify_user_access(callback_query, client):
                return
            
            await safe_edit_message(
                callback_query.message,
                "🖼️ <b>File Logo / Watermark</b>\n\n"
                f"<b>Status:</b> {'🟢 Enabled' if logo_config.get('enabled') else '🔴 Disabled'}\n"
                f"<b>Logo:</b> {'✅ Set' if logo_config.get('logo_file_id') else '❌ Not set'}\n"
                f"<b>Position:</b> {logo_config.get('position', 'bottom-right')}\n"
                f"<b>Size:</b> {logo_config.get('size', 20)}%\n\n"
                "👇 Button press karke command dekho:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🖼 Set Logo", callback_data="lcmd_setlogo"),
                        InlineKeyboardButton("📝 Set Logo Text", callback_data="lcmd_setlogotext")
                    ],
                    [
                        InlineKeyboardButton("📍 Position", callback_data="lcmd_logoposition"),
                        InlineKeyboardButton("📏 Size", callback_data="lcmd_logosize")
                    ],
                    [
                        InlineKeyboardButton("🔆 Opacity", callback_data="lcmd_logoopacity"),
                        InlineKeyboardButton("ℹ️ Logo Info", callback_data="lcmd_logoinfo")
                    ],
                    [
                        InlineKeyboardButton("✅ Enable Logo", callback_data="lcmd_enablelogo"),
                        InlineKeyboardButton("❌ Disable Logo", callback_data="lcmd_disablelogo")
                    ],
                    [
                        InlineKeyboardButton("🗑 Remove Logo", callback_data="lcmd_removelogo")
                    ],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                ]),
                parse_mode="html"
            )
            await callback_query.answer()
        elif data.startswith("lcmd_"):
            lcmd_map = {
                "lcmd_setlogo": "/setlogo (reply to image)",
                "lcmd_setlogotext": "/setlogotext <text>",
                "lcmd_logoposition": "/logoposition <pos>",
                "lcmd_logosize": "/logosize <1-50>",
                "lcmd_logoopacity": "/logoopacity <0-255>",
                "lcmd_logoinfo": "/logoinfo",
                "lcmd_enablelogo": "/enablelogo",
                "lcmd_disablelogo": "/disablelogo",
                "lcmd_removelogo": "/removelogo",
            }
            cmd_text = lcmd_map.get(data, "")
            if cmd_text:
                await callback_query.answer(f"📋 Command: {cmd_text}", show_alert=True)
        elif data == "languages":
            lang_buttons = [
                [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
                [
                    InlineKeyboardButton("🇮🇹 Italiano", callback_data="lang_it"),
                    InlineKeyboardButton("🇪🇸 Español", callback_data="lang_es")
                ],
                [
                    InlineKeyboardButton("🇧🇷🇵🇹 Português", callback_data="lang_pt"),
                    InlineKeyboardButton("🇩🇪 Deutsch", callback_data="lang_de")
                ],
                [
                    InlineKeyboardButton("🇫🇷 Français", callback_data="lang_fr"),
                    InlineKeyboardButton("🇷🇴 Română", callback_data="lang_ro")
                ],
                [
                    InlineKeyboardButton("🇳🇱 Nederlands", callback_data="lang_nl"),
                    InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr")
                ],
                [
                    InlineKeyboardButton("🇨🇳 简体中文", callback_data="lang_zh"),
                    InlineKeyboardButton("🇨🇳 繁體中文", callback_data="lang_zt")
                ],
                [
                    InlineKeyboardButton("🇺🇦 Українська", callback_data="lang_uk"),
                    InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")
                ],
                [
                    InlineKeyboardButton("🇰🇿 Қазақ", callback_data="lang_kk"),
                    InlineKeyboardButton("🇮🇩 Indonesia", callback_data="lang_id")
                ],
                [
                    InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang_uz"),
                    InlineKeyboardButton("🇺🇿 Ўзбекча", callback_data="lang_uzc")
                ],
                [
                    InlineKeyboardButton("🇦🇿 Azərbaycanca", callback_data="lang_az"),
                    InlineKeyboardButton("🇲🇾 Melayu", callback_data="lang_ms")
                ],
                [
                    InlineKeyboardButton("🇸🇴 Soomaali", callback_data="lang_so"),
                    InlineKeyboardButton("🇦🇱 Shqipe", callback_data="lang_sq")
                ],
                [
                    InlineKeyboardButton("🇷🇸 Srpski", callback_data="lang_sr"),
                    InlineKeyboardButton("🇪🇹 Amharic", callback_data="lang_am")
                ],
                [
                    InlineKeyboardButton("🇬🇷 Ελληνικά", callback_data="lang_el"),
                    InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar")
                ],
                [
                    InlineKeyboardButton("🇰🇷 한국어", callback_data="lang_ko"),
                    InlineKeyboardButton("🇮🇷 پارسی", callback_data="lang_fa")
                ],
                [
                    InlineKeyboardButton("☀️ کوردی", callback_data="lang_ku"),
                    InlineKeyboardButton("🇮🇳 हिन्दी", callback_data="lang_hi")
                ],
                [
                    InlineKeyboardButton("🇱🇰 සිංහල", callback_data="lang_si"),
                    InlineKeyboardButton("🇧🇩 বাংলা", callback_data="lang_bn")
                ],
                [
                    InlineKeyboardButton("🇵🇰 اردو", callback_data="lang_ur"),
                    InlineKeyboardButton("🇮🇱 עברית", callback_data="lang_he")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
            ]
            
            user_id = callback_query.from_user.id
            if user_id not in user_languages:
                load_user_language(user_id)
            header = t(user_id, "choose_language") or "🌍 <b>Choose your language</b>"
            
            await safe_edit_message(
                callback_query.message,
                header,
                reply_markup=InlineKeyboardMarkup(lang_buttons),
                parse_mode="html"
            )
            await callback_query.answer()
        elif data.startswith("lang_"):
            lang_map = {
                "lang_en": ("🇬🇧 English", "en"),
                "lang_it": ("🇮🇹 Italiano", "it"),
                "lang_es": ("🇪🇸 Español", "es"),
                "lang_pt": ("🇧🇷 Português", "pt"),
                "lang_de": ("🇩🇪 Deutsch", "de"),
                "lang_fr": ("🇫🇷 Français", "fr"),
                "lang_ro": ("🇷🇴 Română", "ro"),
                "lang_nl": ("🇳🇱 Nederlands", "nl"),
                "lang_tr": ("🇹🇷 Türkçe", "tr"),
                "lang_zh": ("🇨🇳 简体中文", "zh"),
                "lang_zt": ("🇨🇳 繁體中文", "zt"),
                "lang_uk": ("🇺🇦 Українська", "uk"),
                "lang_ru": ("🇷🇺 Русский", "ru"),
                "lang_kk": ("🇰🇿 Қазақ", "kk"),
                "lang_id": ("🇮🇩 Indonesia", "id"),
                "lang_uz": ("🇺🇿 O'zbekcha", "uz"),
                "lang_uzc": ("🇺🇿 Ўзбекча", "uzc"),
                "lang_az": ("🇦🇿 Azərbaycanca", "az"),
                "lang_ms": ("🇲🇾 Melayu", "ms"),
                "lang_so": ("🇸🇴 Soomaali", "so"),
                "lang_sq": ("🇦🇱 Shqipe", "sq"),
                "lang_sr": ("🇷🇸 Srpski", "sr"),
                "lang_am": ("🇪🇹 Amharic", "am"),
                "lang_el": ("🇬🇷 Ελληνικά", "el"),
                "lang_ar": ("🇸🇦 العربية", "ar"),
                "lang_ko": ("🇰🇷 한국어", "ko"),
                "lang_fa": ("🇮🇷 پارسی", "fa"),
                "lang_ku": ("☀️ کوردی", "ku"),
                "lang_hi": ("🇮🇳 हिन्दी", "hi"),
                "lang_si": ("🇱🇰 සිංහල", "si"),
                "lang_bn": ("🇧🇩 বাংলা", "bn"),
                "lang_ur": ("🇵🇰 اردو", "ur"),
                "lang_he": ("🇮🇱 עברית", "he"),
            }
            info = lang_map.get(data)
            if info:
                lang_name, lang_code = info
                user_id = callback_query.from_user.id
                save_user_language(user_id, lang_code)
                
                # Show confirmation message with back button in selected language
                confirm_text = t(user_id, "lang_selected")
                back_btn = InlineKeyboardButton(t(user_id, "btn_back"), callback_data="back_to_start")
                
                await safe_edit_message(
                    callback_query.message,
                    confirm_text,
                    reply_markup=InlineKeyboardMarkup([[back_btn]])
                )
                await callback_query.answer()
        elif data.startswith("hcmd_"):
            hcmd_map = {
                "hcmd_start": "/start",
                "hcmd_forward": "/forward",
                "hcmd_stop": "/stop",
                "hcmd_progress": "/progress",
                "hcmd_status": "/status",
                "hcmd_resume": "/resume",
                "hcmd_setconfig": "/setconfig",
                "hcmd_accounts": "/accounts",
            }
            cmd_text = hcmd_map.get(data, "")
            if cmd_text:
                await callback_query.answer(f"📋 Command: {cmd_text}", show_alert=True)
        elif data == "filters_menu":
            # Verify user access
            if not await verify_user_access(callback_query, client):
                return
            
            # Show filter management menu
            user_id = callback_query.from_user.id
            
            filter_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎬 Video Filter", callback_data="filter_info_video")],
                [InlineKeyboardButton("🖼️ Photo Filter", callback_data="filter_info_photo")],
                [InlineKeyboardButton("📁 File Filter", callback_data="filter_info_file")],
                [InlineKeyboardButton("🎵 Audio Filter", callback_data="filter_info_audio")],
                [InlineKeyboardButton("🎭 Sticker Filter", callback_data="filter_info_sticker")],
                [InlineKeyboardButton("📝 Text Filter", callback_data="filter_info_text")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
            ])
            
            await safe_edit_message(
                callback_query.message,
                "🔍 **Forwarding Filters**\n\n"
                "You can skip specific content types during forwarding:\n\n"
                "• **🎬 Video Filter** - Skip videos, GIFs, video notes\n"
                "• **🖼️ Photo Filter** - Skip photos/images\n"
                "• **📁 File Filter** - Skip documents/files\n"
                "• **🎵 Audio Filter** - Skip audio, voice messages\n"
                "• **🎭 Sticker Filter** - Skip stickers\n"
                "• **📝 Text Filter** - Skip text-only messages\n\n"
                "⚡ **How to use:**\n"
                "1. Click **📤 Forward** button\n"
                "2. Set source channel\n"
                "3. Enter skip number\n"
                "4. **Select filters** to skip content types\n"
                "5. Select destination channel\n"
                "6. Forwarding starts!\n\n"
                "✅ = Content will be SKIPPED\n"
                "❌ = Content will be forwarded",
                reply_markup=filter_keyboard
            )
            await callback_query.answer()
        elif data.startswith("filter_info_"):
            filter_type = data.replace("filter_info_", "")
            
            filter_info = {
                "video": ("🎬 Video Filter", "Videos, GIFs (animations), Video notes/circles", "Movies, clips, animated content"),
                "photo": ("🖼️ Photo Filter", "Photos, Images, Pictures", "All image content"),
                "file": ("📁 File Filter", "Documents, PDFs, ZIPs, any file attachments", "All document types"),
                "audio": ("🎵 Audio Filter", "Audio files, Voice messages, Music", "MP3, voice notes, audio content"),
                "sticker": ("🎭 Sticker Filter", "Stickers, Animated stickers", "All sticker types"),
                "text": ("📝 Text Filter", "Text-only messages (no media attached)", "Plain text messages")
            }
            
            info = filter_info.get(filter_type, ("Unknown", "Unknown", "Unknown"))
            
            back_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Filters", callback_data="filters_menu")]
            ])
            
            await safe_edit_message(
                callback_query.message,
                f"**{info[0]}**\n\n"
                f"📋 **What it filters:**\n{info[1]}\n\n"
                f"📌 **Examples:**\n{info[2]}\n\n"
                f"⚡ **To use this filter:**\n"
                f"Start forwarding → Select this filter → ✅",
                reply_markup=back_keyboard
            )
            await callback_query.answer()
        elif data == "cancel_forward":
            user_id = callback_query.from_user.id
            forward_wizard_state.pop(user_id, None)
            await safe_edit_message(
                callback_query.message,
                "❌ Forwarding cancelled!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                ])
            )
            await callback_query.answer()
        elif data.startswith("select_dest_"):
            # User selected a destination channel
            user_id = callback_query.from_user.id
            channel_idx = int(data.replace("select_dest_", ""))
            
            if user_id not in forward_wizard_state:
                await safe_edit_message(
                    callback_query.message,
                    "❌ Session expired. Please start again.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                    ])
                )
                await callback_query.answer()
                return
            
            # Get user's channels
            user_channels = []
            if user_channels_col is not None:
                saved = user_channels_col.find({"user_id": user_id})
                user_channels = [c.get("channel") for c in saved if c.get("channel")]
            
            if channel_idx >= len(user_channels):
                await safe_edit_message(
                    callback_query.message,
                    "❌ Invalid channel!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                    ])
                )
                await callback_query.answer()
                return
            
            dest_channel = user_channels[channel_idx]
            wizard = forward_wizard_state[user_id]
            wizard["dest_channel"] = dest_channel
            wizard["dest_title"] = dest_channel
            wizard["state"] = "forwarding"
            
            # Initialize progress tracking for this user
            user_forward_progress[user_id] = {
                "fetched_msg": wizard["last_message_id"],
                "success_fwd": 0,
                "duplicate_msg": 0,
                "skipped_msg": wizard["skip_number"],
                "filtered_msg": 0,
                "status": "Starting",
                "percentage": 0,
                "elapsed": 0,
                "eta": "Calculating...",
                "is_active": True,
                "started_at": time.time(),
                "status_message_id": None
            }
            
            # Send initial status message
            cancel_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("• CANCEL", callback_data="cancel_fwd_active")]
            ])
            
            status_msg = await callback_query.message.reply(
                format_forward_status(user_id),
                reply_markup=cancel_keyboard
            )
            
            user_forward_progress[user_id]["status_message_id"] = status_msg.id
            user_forward_progress[user_id]["chat_id"] = callback_query.message.chat.id
            
            # Start forwarding in background
            asyncio.create_task(wizard_forward_messages(
                user_id,
                wizard["source_channel"],
                dest_channel,
                wizard["skip_number"],
                wizard["last_message_id"],
                wizard.get("filters", {}),
                client
            ))
        elif data.startswith("toggle_filter_"):
            # Toggle a filter option
            user_id = callback_query.from_user.id
            filter_name = data.replace("toggle_filter_", "")
            
            if user_id not in forward_wizard_state:
                await safe_edit_message(
                    callback_query.message,
                    "❌ Session expired. Please start again.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                    ])
                )
                await callback_query.answer()
                return
            
            wizard = forward_wizard_state[user_id]
            if "filters" not in wizard:
                wizard["filters"] = {
                    "skip_videos": False,
                    "skip_photos": False,
                    "skip_files": False,
                    "skip_audio": False,
                    "skip_stickers": False,
                    "skip_text": False
                }
            
            # Toggle the filter
            wizard["filters"][filter_name] = not wizard["filters"].get(filter_name, False)
            
            # Update the filter selection message
            filters = wizard["filters"]
            filter_buttons = [
                [
                    InlineKeyboardButton(
                        f"{'✅' if filters.get('skip_videos') else '❌'} Skip Videos",
                        callback_data="toggle_filter_skip_videos"
                    ),
                    InlineKeyboardButton(
                        f"{'✅' if filters.get('skip_photos') else '❌'} Skip Photos",
                        callback_data="toggle_filter_skip_photos"
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"{'✅' if filters.get('skip_files') else '❌'} Skip Files",
                        callback_data="toggle_filter_skip_files"
                    ),
                    InlineKeyboardButton(
                        f"{'✅' if filters.get('skip_audio') else '❌'} Skip Audio",
                        callback_data="toggle_filter_skip_audio"
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"{'✅' if filters.get('skip_stickers') else '❌'} Skip Stickers",
                        callback_data="toggle_filter_skip_stickers"
                    ),
                    InlineKeyboardButton(
                        f"{'✅' if filters.get('skip_text') else '❌'} Skip Text Only",
                        callback_data="toggle_filter_skip_text"
                    )
                ],
                [InlineKeyboardButton("✅ Continue", callback_data="filters_done")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_forward")]
            ]
            
            try:
                await callback_query.message.edit_reply_markup(
                    reply_markup=InlineKeyboardMarkup(filter_buttons)
                )
            except:
                pass
            await callback_query.answer()
        elif data == "filters_done":
            # User finished selecting filters, show destination channels
            user_id = callback_query.from_user.id
            
            if user_id not in forward_wizard_state:
                await safe_edit_message(
                    callback_query.message,
                    "❌ Session expired. Please start again.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                    ])
                )
                await callback_query.answer()
                return
            
            wizard = forward_wizard_state[user_id]
            wizard["state"] = "waiting_dest"
            
            # Get user's saved channels
            user_channels = []
            if user_channels_col is not None:
                saved = user_channels_col.find({"user_id": user_id})
                user_channels = [c.get("channel") for c in saved if c.get("channel")]
            
            if not user_channels:
                await safe_edit_message(
                    callback_query.message,
                    "❌ No destination channels saved!\n\n"
                    "Please add channels first using:\n"
                    "/start → 📢 Channel → Add Channel",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📢 Add Channel", callback_data="add_channel")],
                        [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                    ])
                )
                forward_wizard_state.pop(user_id, None)
                await callback_query.answer()
                return
            
            # Create buttons for each channel
            buttons = [[InlineKeyboardButton(f"📁 {ch}", callback_data=f"select_dest_{i}")] for i, ch in enumerate(user_channels[:10])]
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_forward")])
            
            await safe_edit_message(
                callback_query.message,
                f"**( SELECT DESTINATION CHAT )**\n\n"
                f"Select a channel from your saved channels:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            await callback_query.answer()
        elif data == "cancel_fwd_active":
            user_id = callback_query.from_user.id
            if user_id in user_forward_progress:
                user_forward_progress[user_id]["is_active"] = False
                user_forward_progress[user_id]["status"] = "Cancelled"
            forward_wizard_state.pop(user_id, None)
            await safe_edit_message(
                callback_query.message,
                "🛑 Forwarding cancelled!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
                ])
            )
            await callback_query.answer()
        
        await callback_query.answer()
    
    @bot_client.on_message(filters.command("accounts"))
    async def accounts_handler(client, message):
        # Admin only command
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("❌ This command is only for admins!")
            return
        
        if not user_clients:
            await message.reply("❌ No accounts connected!")
            return
        
        account_list = "\n".join([f"✅ {name}" for name, _ in user_clients])
        expected_speed = len(user_clients) * 30
        
        await message.reply(
            f"👥 **Connected Accounts ({len(user_clients)})**\n\n"
            f"{account_list}\n\n"
            f"⚡ Expected speed: ~{expected_speed}/min"
        )
    
    @bot_client.on_message(filters.command("setconfig"))
    async def setconfig_handler(client, message):
        # Admin only command
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("❌ This command is only for admins!")
            return
        
        try:
            parts = message.text.split()
            if len(parts) != 3:
                await message.reply("Usage: /setconfig <source_channel> <dest_channel>")
                return
            
            source = parts[1]
            dest = parts[2]
            save_config(source, dest)
            await message.reply(f"✅ Config saved!\nSource: {source}\nDest: {dest}")
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
    
    @bot_client.on_message(filters.command("forward"))
    async def forward_handler(client, message):
        global is_forwarding
        
        if is_forwarding:
            await message.reply("⚠️ Forwarding already in progress!")
            return
        
        if not user_clients:
            await message.reply("❌ No user accounts connected! Add SESSION_STRING to environment.")
            return
        
        try:
            parts = message.text.split()
            if len(parts) != 3:
                await message.reply("Usage: /forward <start_id> <end_id>")
                return
            
            start_id = int(parts[1])
            end_id = int(parts[2])
            
            config = get_config()
            if not config.get("source_channel") or not config.get("dest_channel"):
                await message.reply("❌ Please set config first: /setconfig")
                return
            
            num_accounts = len(user_clients)
            expected_speed = num_accounts * 30
            
            await message.reply(
                f"🚀 Starting forward: {start_id} to {end_id}\n"
                f"👥 Using {num_accounts} account(s)\n"
                f"⚡ Expected speed: ~{expected_speed}/min"
            )
            
            # Start forwarding in background
            asyncio.create_task(forward_messages(
                config["source_channel"],
                config["dest_channel"],
                start_id,
                end_id
            ))
            
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
    
    @bot_client.on_message(filters.command("resume"))
    async def resume_handler(client, message):
        # Admin only command
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("❌ This command is only for admins!")
            return
        
        global is_forwarding
        
        if is_forwarding:
            await message.reply("⚠️ Forwarding already in progress!")
            return
        
        if not user_clients:
            await message.reply("❌ No user accounts connected!")
            return
        
        load_progress()
        
        if current_progress["current_id"] == 0:
            await message.reply("❌ No previous progress found")
            return
        
        config = get_config()
        if not config.get("source_channel"):
            await message.reply("❌ No config found")
            return
        
        num_accounts = len(user_clients)
        
        await message.reply(
            f"🔄 Resuming from ID: {current_progress['current_id']}\n"
            f"👥 Using {num_accounts} account(s)"
        )
        
        asyncio.create_task(forward_messages(
            config["source_channel"],
            config["dest_channel"],
            current_progress["current_id"],
            current_progress["end_id"],
            is_resume=True
        ))
    
    @bot_client.on_message(filters.command("stop"))
    async def stop_handler(client, message):
        # Admin only command
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("❌ This command is only for admins!")
            return
        
        global stop_requested
        stop_requested = True
        await message.reply("🛑 Stop requested...")
    
    @bot_client.on_message(filters.command("progress"))
    async def progress_handler(client, message):
        load_progress()
        
        total = current_progress["total_count"]
        done = current_progress["success_count"] + current_progress["failed_count"] + current_progress["skipped_count"]
        pct = round((done / total * 100), 1) if total > 0 else 0
        
        # Show account info only to admins
        if message.from_user.id in ADMIN_IDS:
            await message.reply(
                f"📊 **Progress**\n\n"
                f"✅ Success: {current_progress['success_count']}\n"
                f"❌ Failed: {current_progress['failed_count']}\n"
                f"⏭️ Skipped: {current_progress['skipped_count']}\n"
                f"📈 Total: {done}/{total} ({pct}%)\n"
                f"⚡ Speed: {current_progress['speed']}/min\n"
                f"👥 Accounts: {current_progress.get('active_accounts', 1)}\n"
                f"🔄 Active: {'Yes' if current_progress['is_active'] else 'No'}\n"
                f"⚠️ Rate limits: {current_progress['rate_limit_hits']}"
            )
        else:
            await message.reply(
                f"📊 **Progress**\n\n"
                f"✅ Success: {current_progress['success_count']}\n"
                f"❌ Failed: {current_progress['failed_count']}\n"
                f"⏭️ Skipped: {current_progress['skipped_count']}\n"
                f"📈 Total: {done}/{total} ({pct}%)\n"
                f"🔄 Active: {'Yes' if current_progress['is_active'] else 'No'}"
            )
    
    @bot_client.on_message(filters.command("status"))
    async def status_handler(client, message):
        config = get_config()
        num_accounts = len(user_clients)
        expected_speed = num_accounts * 30 if num_accounts else 0
        
        # Show account info only to admins
        if message.from_user.id in ADMIN_IDS:
            await message.reply(
                f"📡 **Status**\n\n"
                f"Source: {config.get('source_channel', 'Not set')}\n"
                f"Dest: {config.get('dest_channel', 'Not set')}\n"
                f"👥 Connected accounts: {num_accounts}\n"
                f"⚡ Expected speed: ~{expected_speed}/min\n"
                f"Forwarding: {'🟢 Active' if is_forwarding else '⚪ Idle'}\n"
                f"📥 Auto-approve: {len(auto_approve_channels)} channels\n"
                f"🖼️ Watermark: {'🟢 On' if logo_config.get('enabled') else '⚪ Off'}"
            )
        else:
            await message.reply(
                f"📡 **Status**\n\n"
                f"Source: {config.get('source_channel', 'Not set')}\n"
                f"Dest: {config.get('dest_channel', 'Not set')}\n"
                f"Forwarding: {'🟢 Active' if is_forwarding else '⚪ Idle'}\n"
                f"📥 Auto-approve: {len(auto_approve_channels)} channels\n"
                f"🖼️ Watermark: {'🟢 On' if logo_config.get('enabled') else '⚪ Off'}"
            )
    
    # ============ LOGO / WATERMARK HANDLERS ============
    
    @bot_client.on_message(filters.command("setlogo"))
    async def setlogo_handler(client, message):
        """Set logo image by replying to a photo"""
        global logo_config
        
        if not message.reply_to_message or not message.reply_to_message.photo:
            await message.reply(
                "❌ Please reply to a photo to set it as logo.\n\n"
                "Usage: Reply to a photo with /setlogo"
            )
            return
        
        try:
            # Get file_id of the photo
            file_id = message.reply_to_message.photo.file_id
            logo_config["logo_file_id"] = file_id
            logo_config["enabled"] = True
            save_logo_config()
            
            await message.reply(
                "✅ **Logo set successfully!**\n\n"
                f"🖼️ Watermark is now **enabled**\n"
                f"📍 Position: {logo_config.get('position', 'bottom-right')}\n"
                f"📏 Size: {logo_config.get('size', 20)}%\n\n"
                "All forwarded photos will now have this watermark!"
            )
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
    
    @bot_client.on_message(filters.command("setlogotext"))
    async def setlogotext_handler(client, message):
        """Set text watermark"""
        global logo_config
        
        try:
            text = message.text.replace("/setlogotext", "").strip()
            if not text:
                await message.reply("Usage: /setlogotext <your text>\nExample: /setlogotext @MyChannel")
                return
            
            logo_config["text"] = text
            logo_config["enabled"] = True
            save_logo_config()
            
            await message.reply(
                f"✅ **Text watermark set!**\n\n"
                f"📝 Text: `{text}`\n"
                f"🖼️ Watermark is now **enabled**"
            )
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
    
    @bot_client.on_message(filters.command("logoposition"))
    async def logoposition_handler(client, message):
        """Set watermark position"""
        global logo_config
        
        try:
            parts = message.text.split()
            if len(parts) != 2:
                await message.reply(
                    "Usage: /logoposition <position>\n\n"
                    "Positions:\n"
                    "• top-left\n"
                    "• top-right\n"
                    "• bottom-left\n"
                    "• bottom-right\n"
                    "• center"
                )
                return
            
            position = parts[1].lower()
            valid_positions = ["top-left", "top-right", "bottom-left", "bottom-right", "center"]
            
            if position not in valid_positions:
                await message.reply(f"❌ Invalid position. Use: {', '.join(valid_positions)}")
                return
            
            logo_config["position"] = position
            save_logo_config()
            await message.reply(f"✅ Logo position set to: **{position}**")
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
    
    @bot_client.on_message(filters.command("logosize"))
    async def logosize_handler(client, message):
        """Set watermark size (percentage of image)"""
        global logo_config
        
        try:
            parts = message.text.split()
            if len(parts) != 2:
                await message.reply("Usage: /logosize <1-50>\nExample: /logosize 20")
                return
            
            size = int(parts[1])
            if size < 1 or size > 50:
                await message.reply("❌ Size must be between 1 and 50")
                return
            
            logo_config["size"] = size
            save_logo_config()
            await message.reply(f"✅ Logo size set to: **{size}%**")
        except ValueError:
            await message.reply("❌ Invalid number")
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
    
    @bot_client.on_message(filters.command("logoopacity"))
    async def logoopacity_handler(client, message):
        """Set watermark opacity (0-255)"""
        global logo_config
        
        try:
            parts = message.text.split()
            if len(parts) != 2:
                await message.reply("Usage: /logoopacity <0-255>\nExample: /logoopacity 128")
                return
            
            opacity = int(parts[1])
            if opacity < 0 or opacity > 255:
                await message.reply("❌ Opacity must be between 0 and 255")
                return
            
            logo_config["opacity"] = opacity
            save_logo_config()
            await message.reply(f"✅ Logo opacity set to: **{opacity}/255**")
        except ValueError:
            await message.reply("❌ Invalid number")
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
    
    @bot_client.on_message(filters.command("enablelogo"))
    async def enablelogo_handler(client, message):
        """Enable watermark"""
        global logo_config
        
        if not logo_config.get("logo_file_id") and not logo_config.get("text"):
            await message.reply("❌ No logo or text set. Use /setlogo or /setlogotext first.")
            return
        
        logo_config["enabled"] = True
        save_logo_config()
        await message.reply("✅ **Watermark enabled!**\n\nAll forwarded photos will now have watermark.")
    
    @bot_client.on_message(filters.command("disablelogo"))
    async def disablelogo_handler(client, message):
        """Disable watermark"""
        global logo_config
        
        logo_config["enabled"] = False
        save_logo_config()
        await message.reply("🔴 **Watermark disabled!**\n\nPhotos will be forwarded without watermark.")
    
    @bot_client.on_message(filters.command("removelogo"))
    async def removelogo_handler(client, message):
        """Remove logo and text"""
        global logo_config
        
        logo_config = {
            "enabled": False,
            "logo_file_id": None,
            "text": None,
            "position": "bottom-right",
            "opacity": 128,
            "size": 20
        }
        save_logo_config()
        await message.reply("✅ **Logo removed!**\n\nAll watermark settings cleared.")
    
    @bot_client.on_message(filters.command("logoinfo"))
    async def logoinfo_handler(client, message):
        """Show current logo settings"""
        await message.reply(
            "🖼️ **Logo / Watermark Settings**\n\n"
            f"**Status:** {'🟢 Enabled' if logo_config.get('enabled') else '🔴 Disabled'}\n"
            f"**Logo Image:** {'✅ Set' if logo_config.get('logo_file_id') else '❌ Not set'}\n"
            f"**Text:** `{logo_config.get('text') or 'Not set'}`\n"
            f"**Position:** {logo_config.get('position', 'bottom-right')}\n"
            f"**Opacity:** {logo_config.get('opacity', 128)}/255\n"
            f"**Size:** {logo_config.get('size', 20)}%\n\n"
            f"📊 **Stats:**\n"
            f"✅ Watermarked: {logo_stats['watermarked']}\n"
            f"❌ Failed: {logo_stats['failed']}"
        )
    
    # ============ CONTENT MODERATION HANDLERS ============
    
    @bot_client.on_message(filters.command("enablemod") & GROUP_CHAT)
    async def enablemod_handler(client, message):
        """Enable content moderation in this group"""
        global moderation_config
        
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None

        # Allow if:
        # 1) User is in bot ADMIN_IDS
        # 2) User is a group admin
        # 3) Message is sent as the group itself (anonymous admin mode)
        is_bot_admin = bool(user_id and user_id in ADMIN_IDS)
        is_group_admin = False

        if message.sender_chat and message.sender_chat.id == chat_id:
            # Anonymous admin mode: only group admins can send messages as the group
            is_group_admin = True
        elif user_id:
            try:
                member = await client.get_chat_member(chat_id, user_id)
                if member.status in ["administrator", "creator"]:
                    is_group_admin = True
            except Exception:
                pass

        if not is_bot_admin and not is_group_admin:
            await message.reply(
                "❌ Only admins can enable moderation!\n\n"
                f"Debug: user_id={user_id}, bot_admin={'YES' if is_bot_admin else 'NO'}, sender_chat={'YES' if message.sender_chat else 'NO'}."
            )
            return
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        moderation_config[chat_id]["enabled"] = True
        save_moderation_config(chat_id)
        
        await message.reply(
            "✅ **Content Moderation Enabled!**\n\n"
            "Commands:\n"
            "• /blockforward - Block forwarded messages\n"
            "• /blocklinks - Block links/URLs\n"
            "• /blockbadwords - Block inappropriate content\n"
            "• /modstatus - View moderation settings\n"
            "• /disablemod - Disable moderation"
        )
    
    @bot_client.on_message(filters.command("disablemod") & GROUP_CHAT)
    async def disablemod_handler(client, message):
        """Disable content moderation"""
        global moderation_config
        
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None

        is_bot_admin = bool(user_id and user_id in ADMIN_IDS)
        is_group_admin = False

        if message.sender_chat and message.sender_chat.id == chat_id:
            is_group_admin = True
        elif user_id:
            try:
                member = await client.get_chat_member(chat_id, user_id)
                if member.status in ["administrator", "creator"]:
                    is_group_admin = True
            except Exception:
                pass

        if not is_bot_admin and not is_group_admin:
            await message.reply("❌ Only admins can disable moderation!")
            return
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        moderation_config[chat_id]["enabled"] = False
        save_moderation_config(chat_id)
        
        await message.reply("🔴 **Content Moderation Disabled!**")
    
    @bot_client.on_message(filters.command("blockforward") & GROUP_CHAT)
    async def blockforward_handler(client, message):
        """Toggle blocking forwarded messages"""
        global moderation_config
        
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None

        is_bot_admin = bool(user_id and user_id in ADMIN_IDS)
        is_group_admin = False

        if message.sender_chat and message.sender_chat.id == chat_id:
            is_group_admin = True
        elif user_id:
            try:
                member = await client.get_chat_member(chat_id, user_id)
                if member.status in ["administrator", "creator"]:
                    is_group_admin = True
            except Exception:
                pass

        if not is_bot_admin and not is_group_admin:
            await message.reply("❌ Only admins can change this!")
            return
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        current = moderation_config[chat_id].get("block_forward", False)
        moderation_config[chat_id]["block_forward"] = not current
        moderation_config[chat_id]["enabled"] = True
        save_moderation_config(chat_id)
        
        status = "🟢 ON" if not current else "🔴 OFF"
        await message.reply(f"📨 **Block Forwarded Messages:** {status}")
    
    @bot_client.on_message(filters.command("blocklinks") & GROUP_CHAT)
    async def blocklinks_handler(client, message):
        """Toggle blocking messages with links"""
        global moderation_config
        
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None

        is_bot_admin = bool(user_id and user_id in ADMIN_IDS)
        is_group_admin = False

        if message.sender_chat and message.sender_chat.id == chat_id:
            is_group_admin = True
        elif user_id:
            try:
                member = await client.get_chat_member(chat_id, user_id)
                if member.status in ["administrator", "creator"]:
                    is_group_admin = True
            except Exception:
                pass

        if not is_bot_admin and not is_group_admin:
            await message.reply("❌ Only admins can change this!")
            return
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        current = moderation_config[chat_id].get("block_links", False)
        moderation_config[chat_id]["block_links"] = not current
        moderation_config[chat_id]["enabled"] = True
        save_moderation_config(chat_id)
        
        status = "🟢 ON" if not current else "🔴 OFF"
        await message.reply(f"🔗 **Block Links/URLs:** {status}")
    
    @bot_client.on_message(filters.command("blockbadwords") & GROUP_CHAT)
    async def blockbadwords_handler(client, message):
        """Toggle blocking inappropriate content"""
        global moderation_config
        
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None

        is_bot_admin = bool(user_id and user_id in ADMIN_IDS)
        is_group_admin = False

        if message.sender_chat and message.sender_chat.id == chat_id:
            is_group_admin = True
        elif user_id:
            try:
                member = await client.get_chat_member(chat_id, user_id)
                if member.status in ["administrator", "creator"]:
                    is_group_admin = True
            except Exception:
                pass

        if not is_bot_admin and not is_group_admin:
            await message.reply("❌ Only admins can change this!")
            return
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        current = moderation_config[chat_id].get("block_badwords", False)
        moderation_config[chat_id]["block_badwords"] = not current
        moderation_config[chat_id]["enabled"] = True
        save_moderation_config(chat_id)
        
        status = "🟢 ON" if not current else "🔴 OFF"
        await message.reply(f"🚫 **Block Inappropriate Content:** {status}")
    
    @bot_client.on_message(filters.command("blockmention") & GROUP_CHAT)
    async def blockmention_handler(client, message):
        """Toggle blocking @mentions"""
        global moderation_config
        
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None

        is_bot_admin = bool(user_id and user_id in ADMIN_IDS)
        is_group_admin = False

        if message.sender_chat and message.sender_chat.id == chat_id:
            is_group_admin = True
        elif user_id:
            try:
                member = await client.get_chat_member(chat_id, user_id)
                if member.status in ["administrator", "creator"]:
                    is_group_admin = True
            except Exception:
                pass

        if not is_bot_admin and not is_group_admin:
            await message.reply("❌ Only admins can change this!")
            return
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        current = moderation_config[chat_id].get("block_mentions", False)
        moderation_config[chat_id]["block_mentions"] = not current
        moderation_config[chat_id]["enabled"] = True
        save_moderation_config(chat_id)
        
        status = "🟢 ON" if not current else "🔴 OFF"
        await message.reply(f"📛 **Block @Mentions:** {status}\n\nAll @username, @bot, @channel mentions will be deleted!")
    
    @bot_client.on_message(filters.command("autodelete2min") & GROUP_CHAT)
    async def autodelete2min_handler(client, message):
        """Toggle auto-delete messages after 2 minutes"""
        global moderation_config, auto_delete_queue
        
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None

        is_bot_admin = bool(user_id and user_id in ADMIN_IDS)
        is_group_admin = False

        if message.sender_chat and message.sender_chat.id == chat_id:
            is_group_admin = True
        elif user_id:
            try:
                member = await client.get_chat_member(chat_id, user_id)
                if member.status in ["administrator", "creator"]:
                    is_group_admin = True
            except Exception:
                pass

        if not is_bot_admin and not is_group_admin:
            await message.reply("❌ Only admins can change this!")
            return
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        current = moderation_config[chat_id].get("auto_delete_2min", False)
        moderation_config[chat_id]["auto_delete_2min"] = not current
        moderation_config[chat_id]["enabled"] = True
        save_moderation_config(chat_id)
        
        # Initialize queue for this chat if enabling
        if not current:
            auto_delete_queue[chat_id] = []
        
        status = "🟢 ON" if not current else "🔴 OFF"
        await message.reply(f"🗑️ **Auto-Delete 2min:** {status}\n\nAll messages in this group will be deleted after 2 minutes!")
    
    # ============ FORCE JOIN HANDLERS ============
    
    def load_group_forcejoin(chat_id):
        """Load force join config for a group from database"""
        global group_forcejoin_config
        if group_forcejoin_col is not None:
            saved = group_forcejoin_col.find_one({"chat_id": chat_id})
            if saved:
                group_forcejoin_config[chat_id] = {
                    "enabled": saved.get("enabled", False),
                    "channel_id": saved.get("channel_id"),
                    "channel_name": saved.get("channel_name", "Channel"),
                    "invite_link": saved.get("invite_link", "")
                }
                return group_forcejoin_config[chat_id]
        return {"enabled": False, "channel_id": None, "channel_name": "", "invite_link": ""}
    
    def save_group_forcejoin(chat_id):
        """Save force join config for a group to database"""
        if group_forcejoin_col is not None and chat_id in group_forcejoin_config:
            group_forcejoin_col.update_one(
                {"chat_id": chat_id},
                {"$set": {
                    **group_forcejoin_config[chat_id],
                    "chat_id": chat_id,
                    "updated_at": datetime.utcnow()
                }},
                upsert=True
            )
    
    # ============ JOIN WAIT (PER-USER INVITES) ==========
    
    # In-memory fallback if MongoDB is not configured
    joinwait_invites_cache = {}  # {(chat_id, user_id): count}
    
    def load_new_member_wait(chat_id):
        """Load join-wait config from database"""
        global new_member_wait_config
        if new_member_wait_col is not None:
            saved = new_member_wait_col.find_one({"chat_id": chat_id})
            if saved:
                new_member_wait_config[chat_id] = {
                    "enabled": saved.get("enabled", False),
                    "required_adds": int(saved.get("required_adds", saved.get("required_joins", 3) or 3)),
                }
                return new_member_wait_config[chat_id]
        return {"enabled": False, "required_adds": 3}
    
    def save_new_member_wait(chat_id):
        """Save join-wait config to database"""
        if new_member_wait_col is not None and chat_id in new_member_wait_config:
            new_member_wait_col.update_one(
                {"chat_id": chat_id},
                {"$set": {
                    **new_member_wait_config[chat_id],
                    "chat_id": chat_id,
                    "updated_at": datetime.utcnow(),
                }},
                upsert=True,
            )
    
    def get_user_added_count(chat_id: int, user_id: int) -> int:
        """How many members this user has added to this group"""
        if joinwait_invites_col is not None:
            doc = joinwait_invites_col.find_one({"chat_id": chat_id, "user_id": user_id})
            return int(doc.get("count", 0)) if doc else 0
        return int(joinwait_invites_cache.get((chat_id, user_id), 0))
    
    def increment_user_added_count(chat_id: int, user_id: int, delta: int) -> int:
        """Increment invite/add count for a user; returns new count."""
        if delta <= 0:
            return get_user_added_count(chat_id, user_id)
        if joinwait_invites_col is not None:
            joinwait_invites_col.update_one(
                {"chat_id": chat_id, "user_id": user_id},
                {"$inc": {"count": int(delta)}, "$set": {"updated_at": datetime.utcnow()}},
                upsert=True,
            )
            return get_user_added_count(chat_id, user_id)
        key = (chat_id, user_id)
        joinwait_invites_cache[key] = int(joinwait_invites_cache.get(key, 0)) + int(delta)
        return int(joinwait_invites_cache[key])
    
    def reset_joinwait_chat_counts(chat_id: int):
        """Clear all per-user counts for a chat (used when disabling feature)."""
        if joinwait_invites_col is not None:
            joinwait_invites_col.delete_many({"chat_id": chat_id})
        else:
            for k in list(joinwait_invites_cache.keys()):
                if k[0] == chat_id:
                    joinwait_invites_cache.pop(k, None)
    
    async def mute_user_for_joinwait(client, chat_id: int, user_id: int):
        """Mute/restrict a user for 1 minute so they cannot send any messages (blocks all bots from responding)."""
        try:
            from datetime import datetime, timedelta
            # Mute for 1 minute only
            until_time = datetime.now() + timedelta(minutes=1)
            await client.restrict_chat_member(
                chat_id,
                user_id,
                ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                    can_invite_users=True,  # Allow inviting so they can add members
                ),
                until_date=until_time
            )
            print(f"🔇 JoinWait: Muted user {user_id} in chat {chat_id} for 1 minute")
            return True
        except Exception as e:
            print(f"⚠️ JoinWait mute failed for {user_id} in {chat_id}: {e}")
            return False
    
    async def unmute_user_for_joinwait(client, chat_id: int, user_id: int):
        """Unmute/unrestrict a user after they've added enough members."""
        try:
            await client.restrict_chat_member(
                chat_id,
                user_id,
                ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_invite_users=True,
                    can_change_info=False,
                    can_pin_messages=False,
                )
            )
            print(f"🔊 JoinWait: Unmuted user {user_id} in chat {chat_id}")
            return True
        except Exception as e:
            print(f"⚠️ JoinWait unmute failed for {user_id} in {chat_id}: {e}")
            return False

    async def joinwait_mute_existing_members(client, chat_id: int, required: int):
        """When /setjoinwait is enabled, immediately mute all existing non-admin users.
        This prevents other bots from reacting to their first message before we delete it."""
        scan_limit = int(os.getenv("JOINWAIT_MUTE_SCAN_LIMIT", "5000"))
        muted = 0
        scanned = 0

        try:
            async for m in client.get_chat_members(chat_id):
                scanned += 1
                if scanned > scan_limit:
                    break

                u = getattr(m, "user", None)
                if not u or u.is_bot:
                    continue

                uid = u.id
                if uid in ADMIN_IDS:
                    continue

                # Skip admins (cached check is safest)
                try:
                    if await is_group_admin_cached(client, chat_id, uid):
                        continue
                except Exception:
                    pass

                # If they already satisfied requirement, don't mute
                try:
                    if get_user_added_count(chat_id, uid) >= required:
                        continue
                except Exception:
                    pass

                try:
                    ok = await mute_user_for_joinwait(client, chat_id, uid)
                    if ok:
                        muted += 1
                except FloodWait as e:
                    await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
                except Exception:
                    pass
        except Exception as e:
            print(f"⚠️ JoinWait initial mute scan failed in {chat_id}: {e}")

        print(f"✅ JoinWait initial scan done | chat={chat_id} scanned={scanned} muted={muted}")

    def is_group_or_bot_admin(chat_id: int, user_id: int, member_obj=None) -> bool:
        if user_id in ADMIN_IDS:
            return True
        if member_obj is None:
            return False
        try:
            cls_name = member_obj.__class__.__name__ if member_obj else ""
            if "Owner" in cls_name or "Administrator" in cls_name or "Admin" in cls_name:
                return True
            if hasattr(member_obj, "privileges") and member_obj.privileges is not None:
                return True
            if hasattr(member_obj, "status"):
                status = member_obj.status
                status_str = str(status.value if hasattr(status, "value") else status).lower()
                if any(x in status_str for x in ["creator", "owner", "admin", "administrator"]):
                    return True
        except Exception:
            pass
        return False

    # ============ NEW MEMBER WAIT COMMANDS ============

    async def _safe_group_reply(client, message, text: str):
        """Reply in group; if bot can't write in group, try DM the user with instructions."""
        try:
            return await message.reply(text)
        except (ChatWriteForbidden, Forbidden) as e:
            print(f"⚠️ JoinWait reply failed in chat {message.chat.id}: {e}")
            if message.from_user:
                try:
                    await client.send_message(
                        message.from_user.id,
                        "⚠️ Bot group me reply nahi kar pa raha (send permission/mute issue).\n"
                        "Please bot ko group me **Send Messages** permission do ya **Admin** banao, phir command dobara run karo.\n\n"
                        f"Group: {message.chat.title or message.chat.id}\n\n" + text,
                    )
                except Exception as e2:
                    print(f"⚠️ DM also failed for JoinWait: {e2}")
            return None


    @bot_client.on_message(filters.regex(r"^/setjoinwait(?:@[A-Za-z0-9_]+)?(?:\s|$)") & GROUP_CHAT)
    async def setjoinwait_handler(client, message):
        """Set how many members must join before new users can message"""
        global new_member_wait_config
        
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None
        
        # Check if admin
        is_bot_admin = bool(user_id and user_id in ADMIN_IDS)
        is_group_admin = False
        
        if message.sender_chat and message.sender_chat.id == chat_id:
            is_group_admin = True
        elif user_id:
            try:
                member = await client.get_chat_member(chat_id, user_id)
                cls_name = member.__class__.__name__ if member else ""
                if "Owner" in cls_name or "Administrator" in cls_name or "Admin" in cls_name:
                    is_group_admin = True
                elif hasattr(member, "status"):
                    status = member.status
                    status_str = str(status.value if hasattr(status, "value") else status).lower()
                    if any(x in status_str for x in ["creator", "owner", "admin", "administrator"]):
                        is_group_admin = True
            except Exception:
                pass
        
        if not is_bot_admin and not is_group_admin:
            await _safe_group_reply(client, message, "❌ Only admins can use this command!")
            return
        
        # Parse command: /setjoinwait 3
        text = message.text or ""
        parts = text.split()
        
        if len(parts) < 2:
            await _safe_group_reply(
                client,
                message,
                "❌ **Usage:**\n"
                "`/setjoinwait <number>`\n\n"
                "**Example:**\n"
                "`/setjoinwait 3` - Har user ko 3 members add karne honge tabhi message bhej sakta hai\n\n"
                "Use `/removejoinwait` to disable this feature.",
            )
            return
        
        try:
            required_joins = int(parts[1])
            if required_joins < 1 or required_joins > 100:
                await _safe_group_reply(client, message, "❌ Number must be between 1 and 100!")
                return
        except ValueError:
            await _safe_group_reply(client, message, "❌ Please provide a valid number!")
            return
        
        # Save config
        new_member_wait_config[chat_id] = {
            "enabled": True,
            "required_adds": required_joins,
        }
        save_new_member_wait(chat_id)
        reset_joinwait_chat_counts(chat_id)

        # IMPORTANT: Immediately mute existing members so other bots can't reply before our delete/mute kicks in
        asyncio.create_task(joinwait_mute_existing_members(client, chat_id, required_joins))

        await _safe_group_reply(
            client,
            message,
            f"✅ **Join Wait Enabled!**\n\n"
            f"📊 Required Adds (per user): **{required_joins}**\n\n"
            f"⚠️ Ab se **har user** ko group me **{required_joins} members add** karne honge, tabhi woh message bhej sakta hai.\n\n"
            f"Use `/removejoinwait` to disable.\n"
            f"Use `/joinwaitstatus` to check status.",
        )
    
    @bot_client.on_message(filters.regex(r"^/removejoinwait(?:@[A-Za-z0-9_]+)?(?:\s|$)") & GROUP_CHAT)
    async def removejoinwait_handler(client, message):
        """Remove join wait restriction and unmute all restricted users"""
        global new_member_wait_config
        
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None
        
        # Check if admin
        is_bot_admin = bool(user_id and user_id in ADMIN_IDS)
        is_group_admin = False
        
        if message.sender_chat and message.sender_chat.id == chat_id:
            is_group_admin = True
        elif user_id:
            try:
                member = await client.get_chat_member(chat_id, user_id)
                cls_name = member.__class__.__name__ if member else ""
                if "Owner" in cls_name or "Administrator" in cls_name or "Admin" in cls_name:
                    is_group_admin = True
                elif hasattr(member, "status"):
                    status = member.status
                    status_str = str(status.value if hasattr(status, "value") else status).lower()
                    if any(x in status_str for x in ["creator", "owner", "admin", "administrator"]):
                        is_group_admin = True
            except Exception:
                pass
        
        if not is_bot_admin and not is_group_admin:
            await _safe_group_reply(client, message, "❌ Only admins can use this command!")
            return
        
        # Unmute all users who were muted due to joinwait
        if joinwait_invites_col is not None:
            muted_users = list(joinwait_invites_col.find({"chat_id": chat_id}))
            unmute_count = 0
            for doc in muted_users:
                muted_user_id = doc.get("user_id")
                if muted_user_id:
                    try:
                        await unmute_user_for_joinwait(client, chat_id, muted_user_id)
                        unmute_count += 1
                    except Exception as e:
                        print(f"Failed to unmute {muted_user_id}: {e}")
        
        # Disable
        if chat_id in new_member_wait_config:
            new_member_wait_config[chat_id]["enabled"] = False
            save_new_member_wait(chat_id)
        
        reset_joinwait_chat_counts(chat_id)
        
        if new_member_wait_col is not None:
            new_member_wait_col.delete_one({"chat_id": chat_id})
        
        await _safe_group_reply(client, message, "🔴 **Join Wait Disabled!**\n\n✅ All muted users have been unmuted.\n🔓 Everyone can now send messages freely.")
    
    @bot_client.on_message(filters.regex(r"^/joinwaitstatus(?:@[A-Za-z0-9_]+)?(?:\s|$)") & GROUP_CHAT)
    async def joinwaitstatus_handler(client, message):
        """Show join wait status"""
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None
        
        if chat_id not in new_member_wait_config:
            new_member_wait_config[chat_id] = load_new_member_wait(chat_id)
        
        config = new_member_wait_config.get(chat_id, {})
        
        if config.get("enabled"):
            required = int(config.get("required_adds", 3))
            mine = get_user_added_count(chat_id, user_id) if user_id else 0
            remaining = max(0, required - mine)
            
            await _safe_group_reply(
                client,
                message,
                f"⏳ **Join Wait Status**\n\n"
                f"**Status:** 🟢 Enabled\n"
                f"📊 Required Adds (per user): **{required}**\n"
                f"👤 Your Adds: **{mine}/{required}**\n"
                f"⏰ Remaining: **{remaining}**\n\n"
                + ("✅ Ab aap message bhej sakte ho." if remaining == 0 else f"⚠️ Pehle {remaining} members add karo, tabhi message bhej paoge."),
            )
        else:
            await _safe_group_reply(
                client,
                message,
                "⏳ **Join Wait Status**\n\n"
                "**Status:** 🔴 Disabled\n\n"
                "Use `/setjoinwait <number>` to enable.",
            )
    
    # ============ NEW MEMBER JOIN HANDLER ============
    
    @bot_client.on_message(filters.new_chat_members)
    async def new_member_handler(client, message):
        """Track who added new members (used for join-wait feature). 
        Also mutes new joiners and unmutes inviters when they reach target."""
        global new_member_wait_config
        
        chat_id = message.chat.id
        inviter_id = message.from_user.id if message.from_user else None
        
        # Load config if not in memory
        if chat_id not in new_member_wait_config:
            new_member_wait_config[chat_id] = load_new_member_wait(chat_id)
        
        config = new_member_wait_config.get(chat_id, {})
        if not config.get("enabled"):
            return
        
        required = int(config.get("required_adds", 3))
        
        # Mute new members who join (not bots, not admins)
        for new_member in (message.new_chat_members or []):
            if not new_member or new_member.is_bot:
                continue
            
            new_user_id = new_member.id
            
            # Skip if already has enough adds (returning member)
            existing_count = get_user_added_count(chat_id, new_user_id)
            if existing_count >= required:
                continue
            
            # Skip if user is admin
            try:
                if await is_group_admin_cached(client, chat_id, new_user_id):
                    continue
            except Exception:
                pass
            
            # Skip if user is bot admin
            if new_user_id in ADMIN_IDS:
                continue
            
            # Mute the new user immediately
            await mute_user_for_joinwait(client, chat_id, new_user_id)
            
            # Send welcome/restriction message
            async def _send_joinwait_welcome(uid=new_user_id, uname=new_member.first_name):
                try:
                    user_mention = f"[{uname}](tg://user?id={uid})"
                    
                    # Get invite link
                    try:
                        chat_info = await client.get_chat(chat_id)
                        invite_link = chat_info.invite_link
                        if not invite_link:
                            invite_link = (await client.create_chat_invite_link(chat_id)).invite_link
                    except Exception:
                        invite_link = None
                    
                    buttons = []
                    if invite_link:
                        buttons.append([
                            InlineKeyboardButton(
                                "👥 Add Member",
                                url=f"https://t.me/share/url?url={invite_link}&text=Join%20our%20group!",
                            )
                        ])
                    buttons.append([
                        InlineKeyboardButton(
                            "📊 How many users have I added?",
                            callback_data=f"joinwait_check_{chat_id}_{uid}",
                        )
                    ])
                    
                    welcome_msg = await client.send_message(
                        chat_id,
                        f"👋 **Welcome** {user_mention}!\n\n"
                        f"🔒 You are **muted** until you add **{required}** members to this group.\n\n"
                        f"👥 Your Progress: **0/{required}**\n"
                        f"⏰ Add {required} members to unlock messaging!",
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )
                    asyncio.create_task(auto_delete_message(welcome_msg, 30))
                except Exception as e:
                    print(f"Failed to send joinwait welcome: {e}")
            
            asyncio.create_task(_send_joinwait_welcome())
        
        if not inviter_id:
            return
        
        # Count only real "added" members. If someone joined via link, Telegram often sets from_user = the joiner.
        credited = 0
        for new_member in (message.new_chat_members or []):
            if not new_member or new_member.is_bot:
                continue
            if new_member.id == inviter_id:
                # joined by link / self join - no credit
                continue
            credited += 1
        
        if credited <= 0:
            return
        
        # Increment the inviter's count
        old_count = get_user_added_count(chat_id, inviter_id)
        new_count = increment_user_added_count(chat_id, inviter_id, credited)
        
        # Check if inviter has now reached the required count -> unmute them
        if old_count < required and new_count >= required:
            await unmute_user_for_joinwait(client, chat_id, inviter_id)
            
            # Notify the user they're unmuted
            try:
                inviter = message.from_user
                inviter_mention = f"[{inviter.first_name}](tg://user?id={inviter_id})"
                unlock_msg = await client.send_message(
                    chat_id,
                    f"🎉 **Congratulations** {inviter_mention}!\n\n"
                    f"✅ You have added **{new_count}** members!\n"
                    f"🔓 You are now **unmuted** and can send messages freely!",
                )
                asyncio.create_task(auto_delete_message(unlock_msg, 15))
            except Exception as e:
                print(f"Failed to send unmute notification: {e}")
    
    # ============ NEW MEMBER MESSAGE FILTER ============

    async def is_group_admin_cached(client, chat_id: int, user_id: int) -> bool:
        """Fast admin check using a short TTL cache (avoids get_chat_member per message)."""
        now = time.monotonic()
        entry = GROUP_ADMIN_CACHE.get(chat_id)
        if entry and (now - entry["ts"]) < GROUP_ADMIN_CACHE_TTL:
            return user_id in entry["ids"]

        admin_ids: set[int] = set()
        try:
            async for m in client.get_chat_members(
                chat_id,
                filter=pyrogram.enums.ChatMembersFilter.ADMINISTRATORS,
            ):
                if m and getattr(m, "user", None):
                    admin_ids.add(m.user.id)
        except Exception:
            # Fallback: check only this user (slower, but rare)
            try:
                member = await client.get_chat_member(chat_id, user_id)
                status_str = str(getattr(member, "status", "")).lower()
                return any(x in status_str for x in ["creator", "administrator", "admin", "owner"])
            except Exception:
                return False

        GROUP_ADMIN_CACHE[chat_id] = {"ts": now, "ids": admin_ids}
        return user_id in admin_ids

    from pyrogram.raw import functions, types as raw_types
    from pyrogram.handlers import RawUpdateHandler
    
    # Cache for blocked users to avoid repeated DB lookups
    JOINWAIT_BLOCK_CACHE = {}  # {(chat_id, user_id): {"blocked": bool, "ts": timestamp}}
    JOINWAIT_BLOCK_CACHE_TTL = 5  # seconds
    
    async def raw_joinwait_handler(client, update, users, chats):
        """RAW UPDATE HANDLER - Runs before ALL other handlers to block messages from locked users.
        This ensures movie bots NEVER see messages from users who haven't added enough members."""
        global new_member_wait_config
        
        # Only handle new messages in groups
        if not isinstance(update, (raw_types.UpdateNewMessage, raw_types.UpdateNewChannelMessage)):
            return
        
        message = getattr(update, "message", None)
        if not message:
            return
        
        # Get chat ID
        peer_id = getattr(message, "peer_id", None)
        if not peer_id:
            return
        
        # Only handle groups/supergroups
        if isinstance(peer_id, raw_types.PeerChannel):
            chat_id = -1000000000000 - peer_id.channel_id
        elif isinstance(peer_id, raw_types.PeerChat):
            chat_id = -peer_id.chat_id
        else:
            return  # Not a group
        
        # Get user ID
        from_id = getattr(message, "from_id", None)
        if isinstance(from_id, raw_types.PeerUser):
            user_id = from_id.user_id
        elif hasattr(message, "from_id") and isinstance(message.from_id, int):
            user_id = message.from_id
        else:
            return  # No user
        
        # Skip commands (let them through to command handlers)
        msg_text = getattr(message, "message", "") or ""
        if msg_text.startswith("/"):
            return
        
        # Check cache first for speed
        cache_key = (chat_id, user_id)
        now = time.time()
        cached = JOINWAIT_BLOCK_CACHE.get(cache_key)
        if cached and (now - cached["ts"]) < JOINWAIT_BLOCK_CACHE_TTL:
            if not cached["blocked"]:
                return  # User is allowed
        else:
            # Cache miss - check config
            if chat_id not in new_member_wait_config:
                new_member_wait_config[chat_id] = load_new_member_wait(chat_id)
            
            config = new_member_wait_config.get(chat_id, {})
            
            if not config.get("enabled"):
                JOINWAIT_BLOCK_CACHE[cache_key] = {"blocked": False, "ts": now}
                return
            
            # Check if user is admin (using cache)
            try:
                if user_id in ADMIN_IDS:
                    JOINWAIT_BLOCK_CACHE[cache_key] = {"blocked": False, "ts": now}
                    return
            except Exception:
                pass
            
            required = int(config.get("required_adds", 3))
            current = get_user_added_count(chat_id, user_id)
            
            if current >= required:
                JOINWAIT_BLOCK_CACHE[cache_key] = {"blocked": False, "ts": now}
                return
            
            # User should be blocked
            JOINWAIT_BLOCK_CACHE[cache_key] = {"blocked": True, "ts": now}
        
        # ====== USER IS BLOCKED - DELETE MESSAGE IMMEDIATELY ======
        msg_id = getattr(message, "id", None)
        if msg_id:
            try:
                # Use raw API to delete message (fastest possible method)
                if isinstance(peer_id, raw_types.PeerChannel):
                    await client.invoke(
                        functions.channels.DeleteMessages(
                            channel=await client.resolve_peer(chat_id),
                            id=[msg_id]
                        )
                    )
                else:
                    await client.invoke(
                        functions.messages.DeleteMessages(
                            id=[msg_id],
                            revoke=True
                        )
                    )
                print(f"🔇 JoinWait RAW: Deleted message {msg_id} from user {user_id} in {chat_id}")
            except Exception as e:
                print(f"⚠️ JoinWait RAW delete failed: {e}")
        
        # Mute user (async, non-blocking)
        asyncio.create_task(mute_user_for_joinwait(client, chat_id, user_id))
        
        # Send notification (async, non-blocking)
        async def _send_raw_notify():
            try:
                if chat_id not in new_member_wait_config:
                    new_member_wait_config[chat_id] = load_new_member_wait(chat_id)
                config = new_member_wait_config.get(chat_id, {})
                required = int(config.get("required_adds", 3))
                current = get_user_added_count(chat_id, user_id)
                remaining = max(0, required - current)
                
                # Get user info
                try:
                    user = await client.get_users(user_id)
                    user_name = user.first_name
                except Exception:
                    user_name = "User"
                user_mention = f"[{user_name}](tg://user?id={user_id})"
                
                # Get invite link
                try:
                    chat_info = await client.get_chat(chat_id)
                    invite_link = chat_info.invite_link
                    if not invite_link:
                        invite_link = (await client.create_chat_invite_link(chat_id)).invite_link
                except Exception:
                    invite_link = None
                
                buttons = []
                if invite_link:
                    buttons.append([
                        InlineKeyboardButton(
                            "👥 Add Member",
                            url=f"https://t.me/share/url?url={invite_link}&text=Join%20our%20group!",
                        )
                    ])
                buttons.append([
                    InlineKeyboardButton(
                        "📊 How many users have I added?",
                        callback_data=f"joinwait_check_{chat_id}_{user_id}",
                    )
                ])
                
                notify_msg = await client.send_message(
                    chat_id,
                    f"🔇 **You are muted!**\n"
                    f"👈 Dear {user_mention}\n"
                    f"You need to add **{required}** members to the group to unlock messaging!\n\n"
                    f"👥 Your Progress: **{current}/{required}**\n"
                    f"⏰ Remaining: **{remaining}**",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                asyncio.create_task(auto_delete_message(notify_msg, 15))
            except Exception as e:
                print(f"JoinWait notify failed: {e}")
        
        asyncio.create_task(_send_raw_notify())
        
        # CRITICAL: Raise StopPropagation to prevent ALL other handlers from seeing this update
        raise StopPropagation
    
    # Register the raw handler with highest priority (group=-9999)
    bot_client.add_handler(RawUpdateHandler(raw_joinwait_handler), group=-9999)
    print("✅ JoinWait RAW handler registered with priority -9999")
    
    @bot_client.on_message(
        GROUP_CHAT
        & ~filters.regex(
            r"^/(?:[A-Za-z0-9_]{1,32})(?:@[A-Za-z0-9_]+)?(?:\s|$)"
        ),
        group=-999,
    )
    async def new_member_wait_filter(client, message):
        """Fallback filter for JoinWait (RAW handler should catch most messages first)"""
        global new_member_wait_config

        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None

        if not user_id:
            return

        if getattr(message, "sender_chat", None) is not None:
            return

        if chat_id not in new_member_wait_config:
            new_member_wait_config[chat_id] = load_new_member_wait(chat_id)

        config = new_member_wait_config.get(chat_id, {})

        if not config.get("enabled"):
            return

        try:
            if await is_group_admin_cached(client, chat_id, user_id):
                return
        except Exception:
            pass
        if user_id in ADMIN_IDS:
            return
        
        required = int(config.get("required_adds", 3))
        current = get_user_added_count(chat_id, user_id)
        
        if current >= required:
            return
        
        # Delete and mute (fallback if RAW handler missed it)
        try:
            await message.delete()
        except Exception:
            pass

        asyncio.create_task(mute_user_for_joinwait(client, chat_id, user_id))

        try:
            message.stop_propagation()
        except Exception:
            pass

        # RAW handler handles notifications, fallback just deletes and stops propagation
        return
    
    # ============ JOINWAIT CALLBACK HANDLER ============
    
    @bot_client.on_callback_query(filters.regex(r"^joinwait_check_"))
    async def joinwait_check_callback(client, callback_query):
        """Handle 'How many users have I added?' button click"""
        try:
            data = callback_query.data
            parts = data.split("_")
            # joinwait_check_{chat_id}_{user_id}
            if len(parts) < 4:
                await callback_query.answer("❌ Invalid request", show_alert=True)
                return
            
            chat_id = int(parts[2])
            original_user_id = int(parts[3])
            clicker_id = callback_query.from_user.id
            
            # Load config
            if chat_id not in new_member_wait_config:
                new_member_wait_config[chat_id] = load_new_member_wait(chat_id)
            
            config = new_member_wait_config.get(chat_id, {})
            required = int(config.get("required_adds", 3))
            
            # Get clicker's own count
            clicker_count = get_user_added_count(chat_id, clicker_id)
            remaining = max(0, required - clicker_count)
            
            if remaining == 0:
                await callback_query.answer(
                    f"✅ You have added {clicker_count} users!\nYou can now send messages.",
                    show_alert=True
                )
            else:
                await callback_query.answer(
                    f"You have added {clicker_count} users so far and you need to add {remaining} more users",
                    show_alert=True
                )
        except Exception as e:
            print(f"Error in joinwait_check_callback: {e}")
            await callback_query.answer("❌ Error checking status", show_alert=True)
    
    # ============ @ADMIN TAG COMMAND ============
    
    @bot_client.on_message(filters.regex(r"@admin") & GROUP_CHAT)
    async def admin_tag_handler(client, message):
        """Handle @admin tag - notify all group admins"""
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None
        user_name = message.from_user.first_name if message.from_user else "Someone"
        
        try:
            # Get all admins of the group
            admins = []
            async for member in client.get_chat_members(chat_id, filter=pyrogram.enums.ChatMembersFilter.ADMINISTRATORS):
                if member.user and not member.user.is_bot:
                    admins.append(member.user)
            
            if not admins:
                await message.reply("❌ No admins found in this group!")
                return
            
            # Build admin mentions
            admin_mentions = []
            for admin in admins:
                mention = f"[{admin.first_name}](tg://user?id={admin.id})"
                admin_mentions.append(mention)
            
            admin_list = " | ".join(admin_mentions)
            
            # Get reply context if any
            reply_text = ""
            if message.reply_to_message:
                reply_text = "\n\n📝 **Regarding message:** " + (message.reply_to_message.text or message.reply_to_message.caption or "[Media]")[:100]
            
            # Get user's message (remove @admin from it)
            user_msg = message.text or ""
            user_msg = user_msg.replace("@admin", "").strip()
            reason = f"\n\n💬 **Reason:** {user_msg}" if user_msg else ""
            
            await message.reply(
                f"🚨 **Admin Alert!**\n\n"
                f"👤 **Called by:** [{user_name}](tg://user?id={user_id})\n"
                f"👮 **Admins:** {admin_list}"
                f"{reason}"
                f"{reply_text}",
                disable_web_page_preview=True
            )
            
        except Exception as e:
            print(f"Error in @admin handler: {e}")
            await message.reply("❌ Failed to notify admins. Make sure the bot is admin!")
    
    @bot_client.on_message(filters.command("setforcejoin") & GROUP_CHAT)
    async def setforcejoin_handler(client, message):
        """Set force join channel for this group"""
        global group_forcejoin_config
        
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None
        
        # Check if admin
        is_bot_admin = bool(user_id and user_id in ADMIN_IDS)
        is_group_admin = False
        
        if message.sender_chat and message.sender_chat.id == chat_id:
            is_group_admin = True
        elif user_id:
            try:
                member = await client.get_chat_member(chat_id, user_id)
                if member.status in ["administrator", "creator"]:
                    is_group_admin = True
            except Exception:
                pass
        
        if not is_bot_admin and not is_group_admin:
            await message.reply("❌ Only admins can set force join!")
            return
        
        # Parse command: /setforcejoin @channel|Channel Name|https://t.me/+invite
        text = message.text or ""
        parts = text.split(maxsplit=1)
        
        if len(parts) < 2:
            await message.reply(
                "❌ **Usage:** `/setforcejoin @channel_or_group|Name|https://t.me/+invitelink`\n\n"
                "**Examples:**\n"
                "• Channel: `/setforcejoin @MyChannel|My Channel|https://t.me/+abc123`\n"
                "• Group: `/setforcejoin -1001234567890|My Group|https://t.me/+xyz789`\n\n"
                "💡 **Note:** For private groups, use chat ID with invite link!"
            )
            return
        
        channel_data = parts[1].strip()
        channel_parts = channel_data.split("|")
        
        target_id = channel_parts[0].strip()
        target_name = channel_parts[1].strip() if len(channel_parts) > 1 else target_id
        invite_link = channel_parts[2].strip() if len(channel_parts) > 2 else ""
        
        # Determine if it's a group or channel
        target_type = "channel"  # default
        
        # Check if it's a numeric ID (group/supergroup)
        if target_id.lstrip("-").isdigit():
            # It's a chat ID - could be group or channel
            target_type = "group_or_channel"
        elif target_id.startswith("@"):
            target_type = "username"
        else:
            target_id = "@" + target_id
            target_type = "username"
        
        # Try to get chat info and generate invite link if needed
        try:
            if target_id.lstrip("-").isdigit():
                check_chat = await client.get_chat(int(target_id))
            else:
                check_chat = await client.get_chat(target_id)
            
            # Get proper name if not provided
            if target_name == target_id:
                target_name = check_chat.title or target_name
            
            # Determine actual type
            if check_chat.type in [ChatType.CHANNEL]:
                target_type = "channel"
            else:
                target_type = "group"
            
            # Get or generate invite link for private chats
            if not invite_link:
                if check_chat.username:
                    invite_link = f"https://t.me/{check_chat.username}"
                elif check_chat.invite_link:
                    invite_link = check_chat.invite_link
                else:
                    # Try to create invite link (bot must be admin)
                    try:
                        new_link = await client.create_chat_invite_link(check_chat.id)
                        invite_link = new_link.invite_link
                    except Exception:
                        invite_link = ""
            
            # Store the numeric ID for reliable checking
            target_id = str(check_chat.id)
            
        except Exception as e:
            # Continue with provided info even if we can't fetch chat
            print(f"Could not fetch target chat info: {e}")
        
        if not invite_link:
            await message.reply(
                f"⚠️ **Warning:** No invite link found!\n\n"
                f"Please provide invite link manually:\n"
                f"`/setforcejoin {target_id}|{target_name}|https://t.me/+YOUR_LINK`"
            )
            return
        
        # Save config with type info
        group_forcejoin_config[chat_id] = {
            "enabled": True,
            "channel_id": target_id,
            "channel_name": target_name,
            "invite_link": invite_link,
            "target_type": target_type  # "channel" or "group"
        }
        save_group_forcejoin(chat_id)
        
        type_emoji = "📢" if target_type == "channel" else "👥"
        type_label = "Channel" if target_type == "channel" else "Group"
        
        await message.reply(
            f"✅ **Force Join Enabled!**\n\n"
            f"{type_emoji} {type_label}: `{target_id}`\n"
            f"📛 Name: **{target_name}**\n"
            f"🔗 Link: {invite_link}\n\n"
            f"⚠️ Users must join this {type_label.lower()} to send messages.\n"
            f"Messages from non-members will be deleted with a join button!"
        )
    
    @bot_client.on_message(filters.command("removeforcejoin") & GROUP_CHAT)
    async def removeforcejoin_handler(client, message):
        """Remove force join for this group"""
        global group_forcejoin_config
        
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None
        
        # Check if admin
        is_bot_admin = bool(user_id and user_id in ADMIN_IDS)
        is_group_admin = False
        
        if message.sender_chat and message.sender_chat.id == chat_id:
            is_group_admin = True
        elif user_id:
            try:
                member = await client.get_chat_member(chat_id, user_id)
                if member.status in ["administrator", "creator"]:
                    is_group_admin = True
            except Exception:
                pass
        
        if not is_bot_admin and not is_group_admin:
            await message.reply("❌ Only admins can remove force join!")
            return
        
        # Disable force join
        if chat_id in group_forcejoin_config:
            group_forcejoin_config[chat_id]["enabled"] = False
            save_group_forcejoin(chat_id)
        
        if group_forcejoin_col is not None:
            group_forcejoin_col.delete_one({"chat_id": chat_id})
        
        await message.reply("🔴 **Force Join Disabled!**\n\nAll users can now send messages without joining any channel.")
    
    @bot_client.on_message(filters.command("forcejoininfo") & GROUP_CHAT)
    async def forcejoininfo_handler(client, message):
        """Show force join status"""
        chat_id = message.chat.id
        
        if chat_id not in group_forcejoin_config:
            group_forcejoin_config[chat_id] = load_group_forcejoin(chat_id)
        
        config = group_forcejoin_config.get(chat_id, {})
        
        if config.get("enabled") and config.get("channel_id"):
            target_type = config.get("target_type", "channel")
            type_emoji = "📢" if target_type == "channel" else "👥"
            type_label = "Channel" if target_type == "channel" else "Group"
            
            await message.reply(
                f"🔐 **Force Join Status**\n\n"
                f"**Status:** 🟢 Enabled\n"
                f"{type_emoji} {type_label}: `{config.get('channel_id')}`\n"
                f"📛 Name: **{config.get('channel_name', 'N/A')}**\n"
                f"🔗 Link: {config.get('invite_link', 'N/A')}\n\n"
                f"⚠️ Users must join to send messages!"
            )
        else:
            await message.reply(
                f"🔐 **Force Join Status**\n\n"
                f"**Status:** 🔴 Disabled\n\n"
                f"Use `/setforcejoin @channel` to enable."
            )
    
    # ============ FORCE JOIN MESSAGE FILTER ============
    
    @bot_client.on_message(GROUP_CHAT & ~filters.command(["setforcejoin", "removeforcejoin", "forcejoininfo", "enablemod", "disablemod", "blockforward", "blocklinks", "blockbadwords", "blockmention", "autodelete2min", "modstatus", "warnings", "resetwarnings"]), group=1)
    async def forcejoin_filter_handler(client, message):
        """Delete messages from users who haven't joined the force join channel"""
        global group_forcejoin_config
        
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else None
        
        if not user_id:
            return
        
        # Load config if not in memory
        if chat_id not in group_forcejoin_config:
            group_forcejoin_config[chat_id] = load_group_forcejoin(chat_id)
        
        config = group_forcejoin_config.get(chat_id, {})
        
        # Skip if force join is disabled
        if not config.get("enabled") or not config.get("channel_id"):
            return
        
        # Skip if user is admin
        try:
            member = await client.get_chat_member(chat_id, user_id)
            if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                return
        except:
            pass
        
        # Check if user has joined the target channel/group
        target_id = config.get("channel_id")
        target_type = config.get("target_type", "channel")  # "channel" or "group"
        
        try:
            # Parse target ID
            if str(target_id).lstrip("-").isdigit():
                check_chat_id = int(target_id)
            elif str(target_id).startswith("@"):
                check_chat_id = target_id
            else:
                check_chat_id = "@" + target_id
            
            # Check membership
            target_member = await client.get_chat_member(check_chat_id, user_id)
            
            # For both groups and channels, check if user is a member
            if target_member.status not in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
                # User is a member, allow message
                return
                
        except Exception as e:
            # If we can't check membership, log and assume not joined
            error_str = str(e).lower()
            # If user is not found in chat = not a member
            if "user not found" in error_str or "user_not_participant" in error_str:
                pass  # Continue to delete message
            else:
                # Other errors (bot not admin in target, etc.) - log but don't block
                print(f"Force join check error for {target_id}: {e}")
                # Don't block user if we can't verify (graceful degradation)
                return
        
        # User hasn't joined - delete message and send join button
        try:
            await message.delete()
        except:
            pass
        
        # Send join button message
        user_name = message.from_user.first_name if message.from_user else "User"
        target_name = config.get("channel_name", "Channel/Group")
        invite_link = config.get("invite_link", "")
        
        type_emoji = "📢" if target_type == "channel" else "👥"
        type_label = "channel" if target_type == "channel" else "group"
        
        join_button = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{type_emoji} Join {target_name}", url=invite_link)],
            [InlineKeyboardButton("✅ I've Joined", callback_data=f"check_forcejoin_{chat_id}_{user_id}")]
        ])
        
        try:
            warn_msg = await client.send_message(
                chat_id,
                f"👋 **{user_name}**, आप यहाँ message नहीं भेज सकते!\n\n"
                f"{type_emoji} पहले **{target_name}** join करो, फिर message करो!\n\n"
                f"⬇️ नीचे button पर click करके join करो:",
                reply_markup=join_button
            )
            # Auto-delete warning after 30 seconds
            asyncio.create_task(auto_delete_message(warn_msg, 30))
        except Exception as e:
            print(f"Failed to send force join warning: {e}")
    
    # ============ FORCE JOIN CALLBACK HANDLER ============
    
    @bot_client.on_callback_query(filters.regex(r"^check_forcejoin_"))
    async def check_forcejoin_callback(client, callback_query):
        """Handle 'I've Joined' button click"""
        data = callback_query.data
        parts = data.split("_")
        
        if len(parts) < 4:
            await callback_query.answer("❌ Invalid request!", show_alert=True)
            return
        
        chat_id = int(parts[2])
        target_user_id = int(parts[3])
        clicker_user_id = callback_query.from_user.id
        
        # Only the mentioned user can click this button
        if clicker_user_id != target_user_id:
            await callback_query.answer("❌ यह button सिर्फ उस user के लिए है!", show_alert=True)
            return
        
        # Get config
        if chat_id not in group_forcejoin_config:
            group_forcejoin_config[chat_id] = load_group_forcejoin(chat_id)
        
        config = group_forcejoin_config.get(chat_id, {})
        channel_id = config.get("channel_id")
        
        if not channel_id:
            await callback_query.answer("✅ Force join disabled!", show_alert=True)
            try:
                await callback_query.message.delete()
            except:
                pass
            return
        
        # Check if user has joined now
        target_id = config.get("channel_id")
        target_type = config.get("target_type", "channel")
        
        try:
            # Parse target ID
            if str(target_id).lstrip("-").isdigit():
                check_chat_id = int(target_id)
            elif str(target_id).startswith("@"):
                check_chat_id = target_id
            else:
                check_chat_id = "@" + target_id
            
            target_member = await client.get_chat_member(check_chat_id, clicker_user_id)
            if target_member.status not in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
                # User has joined!
                await callback_query.answer("✅ धन्यवाद! अब आप message भेज सकते हैं!", show_alert=True)
                try:
                    await callback_query.message.delete()
                except:
                    pass
                return
        except Exception as e:
            pass
        
        # Still not joined
        await callback_query.answer("❌ आपने अभी तक channel join नहीं किया! पहले join करो!", show_alert=True)
    
    # ============ AUTO-DELETE 2MIN MESSAGE HANDLER ============
    
    @bot_client.on_message(GROUP_CHAT & ~filters.command(["setforcejoin", "removeforcejoin", "forcejoininfo", "enablemod", "disablemod", "blockforward", "blocklinks", "blockbadwords", "blockmention", "autodelete2min", "modstatus", "warnings", "resetwarnings"]), group=99)
    async def auto_delete_message_handler(client, message):
        """Queue messages for auto-deletion after 2 minutes"""
        global auto_delete_queue
        
        chat_id = message.chat.id
        
        # Check if auto-delete is enabled for this chat
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        config = moderation_config.get(chat_id, {})
        if not config.get("auto_delete_2min"):
            return
        
        # Queue the message for deletion
        message_id = message.id
        
        # Create task to delete after 2 minutes
        async def delete_after_2min():
            try:
                await asyncio.sleep(120)  # 2 minutes = 120 seconds
                await client.delete_messages(chat_id, message_id)
                moderation_stats["auto_deleted"] += 1
            except Exception as e:
                print(f"Failed to auto-delete message {message_id}: {e}")
        
        asyncio.create_task(delete_after_2min())
    
    @bot_client.on_message(filters.command("modstatus") & GROUP_CHAT)
    async def modstatus_handler(client, message):
        """Show moderation status"""
        chat_id = message.chat.id
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        config = moderation_config.get(chat_id, {})
        
        await message.reply(
            "🛡️ **Content Moderation Status**\n\n"
            f"**Moderation:** {'🟢 Enabled' if config.get('enabled') else '🔴 Disabled'}\n"
            f"**Block Forwards:** {'🟢 ON' if config.get('block_forward') else '🔴 OFF'}\n"
            f"**Block Links:** {'🟢 ON' if config.get('block_links') else '🔴 OFF'}\n"
            f"**Block Bad Words:** {'🟢 ON' if config.get('block_badwords') else '🔴 OFF'}\n"
            f"**Block @Mentions:** {'🟢 ON' if config.get('block_mentions') else '🔴 OFF'}\n"
            f"**Auto-Delete 2min:** {'🟢 ON' if config.get('auto_delete_2min') else '🔴 OFF'}\n\n"
            f"📊 **Stats:**\n"
            f"📨 Deleted forwards: {moderation_stats['deleted_forward']}\n"
            f"🔗 Deleted links: {moderation_stats['deleted_links']}\n"
            f"🚫 Deleted bad words: {moderation_stats['deleted_badwords']}\n"
            f"📛 Deleted mentions: {moderation_stats['deleted_mentions']}\n"
            f"🗑️ Auto-deleted: {moderation_stats['auto_deleted']}\n"
            f"⚠️ Total warnings: {moderation_stats['warnings']}\n"
            f"🔨 Auto-bans: {moderation_stats['bans']}"
        )
    
    # ============ PRIVATE MESSAGE HANDLER FOR CHANNEL INPUT ============
    
    @bot_client.on_message(filters.private & ~filters.command(["start", "setconfig", "forward", "stop", "progress", "status", "setlogo", "setlogotext", "logoposition", "logosize", "logoopacity", "enablelogo", "disablelogo", "removelogo", "logoinfo", "autoapprove", "stopapprove", "approvelist", "approveall", "debugjoin", "rawtest", "version", "cancel"]))
    async def private_message_handler(client, message):
        """Handle private messages for channel input and forward wizard"""
        user_id = message.from_user.id
        
        # ====== FORWARD WIZARD HANDLERS ======
        wizard = forward_wizard_state.get(user_id)
        if wizard:
            state = wizard.get("state")
            
            # Handle "waiting_source" - User forwards a message from source channel
            if state == "waiting_source":
                source_channel = None
                source_title = "Unknown"
                last_message_id = 0
                
                # Check if it's a forwarded message
                if message.forward_from_chat:
                    source_channel = message.forward_from_chat.id
                    source_title = message.forward_from_chat.title or str(source_channel)
                    last_message_id = message.forward_from_message_id or 0
                elif message.text:
                    # Try to extract from t.me link
                    import re
                    link_match = re.search(r't\.me/([a-zA-Z0-9_]+)/(\d+)', message.text)
                    if link_match:
                        source_channel = "@" + link_match.group(1)
                        source_title = source_channel
                        last_message_id = int(link_match.group(2))
                
                if not source_channel or not last_message_id:
                    await message.reply(
                        "❌ Could not detect source channel.\n\n"
                        "Please forward a message from the source channel or send a message link."
                    )
                    return
                
                wizard["source_channel"] = source_channel
                wizard["source_title"] = source_title
                wizard["last_message_id"] = last_message_id
                wizard["state"] = "waiting_skip"
                
                cancel_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_forward")]
                ])
                
                await message.reply(
                    f"**( SET MESSAGE SKIPPING NUMBER )**\n\n"
                    f"Skip the message as much as you enter the number and the rest of the message will be forwarded\n"
                    f"Default Skip Number = 0\n"
                    f"eg: You enter 0 = 0 message skiped\n"
                    f"You enter 5 = 5 message skiped\n"
                    f"/cancel - cancel this process",
                    reply_markup=cancel_keyboard
                )
                return
            
            # Handle "waiting_skip" - User enters skip number, show filter options
            elif state == "waiting_skip":
                try:
                    skip_number = int(message.text.strip())
                    if skip_number < 0:
                        skip_number = 0
                except:
                    skip_number = 0
                
                wizard["skip_number"] = skip_number
                wizard["state"] = "waiting_filters"
                
                # Show filter selection
                filters = wizard.get("filters", {})
                filter_buttons = [
                    [
                        InlineKeyboardButton(
                            f"{'✅' if filters.get('skip_videos') else '❌'} Skip Videos",
                            callback_data="toggle_filter_skip_videos"
                        ),
                        InlineKeyboardButton(
                            f"{'✅' if filters.get('skip_photos') else '❌'} Skip Photos",
                            callback_data="toggle_filter_skip_photos"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            f"{'✅' if filters.get('skip_files') else '❌'} Skip Files",
                            callback_data="toggle_filter_skip_files"
                        ),
                        InlineKeyboardButton(
                            f"{'✅' if filters.get('skip_audio') else '❌'} Skip Audio",
                            callback_data="toggle_filter_skip_audio"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            f"{'✅' if filters.get('skip_stickers') else '❌'} Skip Stickers",
                            callback_data="toggle_filter_skip_stickers"
                        ),
                        InlineKeyboardButton(
                            f"{'✅' if filters.get('skip_text') else '❌'} Skip Text Only",
                            callback_data="toggle_filter_skip_text"
                        )
                    ],
                    [InlineKeyboardButton("✅ Continue", callback_data="filters_done")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_forward")]
                ]
                
                await message.reply(
                    f"**( SELECT FILTERS )**\n\n"
                    f"Select content types to **SKIP** (not forward):\n\n"
                    f"• Click to toggle ✅/❌\n"
                    f"• ✅ = Will be SKIPPED\n"
                    f"• ❌ = Will be forwarded\n\n"
                    f"Click **Continue** when done.",
                    reply_markup=InlineKeyboardMarkup(filter_buttons)
                )
                return
        
        # ====== CHANNEL INPUT HANDLER ======
        if user_channel_state.get(user_id) == "waiting_add_channel":
            channel_input = message.text.strip() if message.text else ""
            
            # Validate and clean channel input
            if channel_input.startswith("https://t.me/"):
                channel_input = "@" + channel_input.replace("https://t.me/", "").split("/")[0]
            elif channel_input.startswith("t.me/"):
                channel_input = "@" + channel_input.replace("t.me/", "").split("/")[0]
            elif not channel_input.startswith("@") and not channel_input.startswith("-"):
                channel_input = "@" + channel_input
            
            # Save to database
            if user_channels_col is not None:
                # Check if channel already exists for this user
                existing = user_channels_col.find_one({"user_id": user_id, "channel": channel_input})
                if existing:
                    await message.reply(f"⚠️ Channel `{channel_input}` is already added!")
                else:
                    user_channels_col.insert_one({
                        "user_id": user_id,
                        "channel": channel_input,
                        "added_at": datetime.utcnow()
                    })
                    await message.reply(
                        f"✅ **Channel Added!**\n\n"
                        f"Channel: `{channel_input}`\n\n"
                        "Use /start → Channel to see all your channels."
                    )
            else:
                await message.reply(f"✅ Channel `{channel_input}` noted! (DB not connected)")
            
            # Clear state
            user_channel_state.pop(user_id, None)
            return
    
    # ============ CANCEL COMMAND ============
    
    @bot_client.on_message(filters.private & filters.command("cancel"))
    async def cancel_handler(client, message):
        """Cancel any active wizard"""
        user_id = message.from_user.id
        
        cancelled = False
        if user_id in forward_wizard_state:
            forward_wizard_state.pop(user_id)
            cancelled = True
        if user_id in user_channel_state:
            user_channel_state.pop(user_id)
            cancelled = True
        if user_id in user_forward_progress:
            user_forward_progress[user_id]["is_active"] = False
            cancelled = True
        
        if cancelled:
            await message.reply("❌ Process cancelled!")
        else:
            await message.reply("No active process to cancel.")
    
    # ============ AUTO DELETE HELPER ============
    
    async def auto_delete_message(msg, delay_seconds=10):
        """Delete a message after specified delay"""
        try:
            await asyncio.sleep(delay_seconds)
            await msg.delete()
        except Exception as e:
            print(f"Failed to auto-delete message: {e}")
    
    # ============ CONTENT MODERATION MESSAGE FILTER ============
    
    @bot_client.on_message(GROUP_CHAT & ~filters.command(["setforcejoin", "removeforcejoin", "forcejoininfo", "enablemod", "disablemod", "blockforward", "blocklinks", "blockbadwords", "blockmention", "modstatus", "warnings", "resetwarnings"]))
    async def moderation_filter_handler(client, message):
        """Filter and delete inappropriate messages with warning system"""
        global moderation_stats, user_warnings
        
        chat_id = message.chat.id
        
        # If message is sent via sender_chat (anonymous admin / channel identity), skip moderation
        # Admins/owners can post as the group/channel; treating these as exempt avoids false warnings.
        if getattr(message, "sender_chat", None) is not None:
            return

        # If we can't identify a user, do not moderate
        if message.from_user is None:
            return

        user_id = message.from_user.id
        
        # Load config if not in memory
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        config = moderation_config.get(chat_id, {})
        
        # Skip if moderation is disabled
        if not config.get("enabled"):
            return
        
        # Skip if user is a bot admin first
        if user_id in ADMIN_IDS:
            return
        
        # Skip if user is admin/owner (admins are exempt from moderation)
        try:
            member = await client.get_chat_member(chat_id, user_id)
            
            # Check if user is admin or owner using multiple methods
            is_admin_or_owner = False
            
            # Method 1: Check class name (ChatMemberOwner, ChatMemberAdministrator)
            cls_name = member.__class__.__name__ if member else ""
            if "Owner" in cls_name or "Administrator" in cls_name or "Admin" in cls_name:
                is_admin_or_owner = True
            
            # Method 2: Check if privileges exist (admins have this)
            if not is_admin_or_owner and hasattr(member, "privileges") and member.privileges is not None:
                is_admin_or_owner = True
            
            # Method 3: Check status attribute (covers various Pyrogram versions)
            if not is_admin_or_owner and hasattr(member, "status"):
                status = member.status
                # Handle both enum and string status
                status_str = str(status.value if hasattr(status, "value") else status).lower()
                if any(x in status_str for x in ["creator", "owner", "admin", "administrator"]):
                    is_admin_or_owner = True
            
            # Method 4: Direct ChatMember type check using pyrogram types
            if not is_admin_or_owner:
                try:
                    from pyrogram.types import ChatMemberOwner, ChatMemberAdministrator
                    if isinstance(member, (ChatMemberOwner, ChatMemberAdministrator)):
                        is_admin_or_owner = True
                except ImportError:
                    pass
            
            if is_admin_or_owner:
                return
            
            # Debug log for non-exempt users
            print(f"[DEBUG] moderation: user_id={user_id} cls={cls_name} is_admin={is_admin_or_owner}", flush=True)
            
        except Exception as e:
            # If we can't verify role, skip moderation to avoid false warnings
            print(f"[DEBUG] Cannot verify admin status for {user_id}: {e}", flush=True)
            return
        
        async def add_warning_and_check_ban(reason):
            """Add warning to user and punish if exceeded limit"""
            global user_warnings, moderation_stats
            
            key = (chat_id, user_id)
            wc = get_warning_config(chat_id)
            max_warns = wc.get("max_warns", 3)
            punishment = wc.get("punishment", "mute")
            
            # If punishment is off, skip warnings entirely
            if punishment == "off":
                return
            
            # Load from DB if not in memory
            if key not in user_warnings and warnings_col is not None:
                saved = warnings_col.find_one({"chat_id": chat_id, "user_id": user_id})
                user_warnings[key] = saved.get("count", 0) if saved else 0
            
            # Increment warning
            user_warnings[key] = user_warnings.get(key, 0) + 1
            current_warnings = user_warnings[key]
            moderation_stats["warnings"] += 1
            
            # Save to DB
            if warnings_col is not None:
                warnings_col.update_one(
                    {"chat_id": chat_id, "user_id": user_id},
                    {"$set": {"count": current_warnings, "last_reason": reason, "updated_at": datetime.utcnow()}},
                    upsert=True
                )
            
            user_name = message.from_user.first_name
            
            # Check if should punish
            if current_warnings >= max_warns:
                try:
                    if punishment == "ban":
                        await client.ban_chat_member(chat_id, user_id)
                        moderation_stats["bans"] += 1
                        action_text = "🚫 Auto-Ban"
                    elif punishment == "kick":
                        await client.ban_chat_member(chat_id, user_id)
                        # Unban immediately so they can rejoin (kick behavior)
                        await client.unban_chat_member(chat_id, user_id)
                        action_text = "👢 Auto-Kick"
                    elif punishment == "mute":
                        mute_dur = wc.get("mute_duration", 3)
                        from datetime import timedelta
                        until = datetime.utcnow() + timedelta(hours=mute_dur)
                        await client.restrict_chat_member(
                            chat_id, user_id,
                            ChatPermissions(),  # No permissions = muted
                            until_date=until
                        )
                        action_text = f"🔇 Auto-Mute ({mute_dur}h)"
                    else:
                        action_text = "⚠️ Punished"
                    
                    p_msg = await client.send_message(
                        chat_id,
                        f"{action_text}: {user_name}\n"
                        f"Reason: {max_warns} warnings exceeded\n"
                        f"Last violation: {reason}"
                    )
                    asyncio.create_task(auto_delete_message(p_msg, 10))
                    # Reset warnings
                    user_warnings[key] = 0
                    if warnings_col is not None:
                        warnings_col.update_one(
                            {"chat_id": chat_id, "user_id": user_id},
                            {"$set": {"count": 0, "punished": True}}
                        )
                    print(f"🔨 {action_text} {user_name} after {max_warns} warnings")
                except Exception as e:
                    print(f"Failed to punish user: {e}")
            else:
                # Send warning message
                remaining = max_warns - current_warnings
                warn_msg = await client.send_message(
                    chat_id,
                    f"⚠️ **Warning {current_warnings}/{max_warns}:** {user_name}\n"
                    f"Reason: {reason}\n"
                    f"⛔ {remaining} more warning{'s' if remaining > 1 else ''} = {punishment.title()}!"
                )
                asyncio.create_task(auto_delete_message(warn_msg, 10))
        
        async def apply_feature_punishment(feature_punishment, reason):
            """Apply per-feature punishment (instant ban/kick/mute, no warning system)"""
            if feature_punishment == "off":
                return  # Just delete, no punishment
            user_name = message.from_user.first_name
            try:
                if feature_punishment == "ban":
                    await client.ban_chat_member(chat_id, user_id)
                    moderation_stats["bans"] += 1
                    action_text = "🚫 Banned"
                elif feature_punishment == "kick":
                    await client.ban_chat_member(chat_id, user_id)
                    await client.unban_chat_member(chat_id, user_id)
                    action_text = "👢 Kicked"
                elif feature_punishment == "mute":
                    from datetime import timedelta
                    wc = get_warning_config(chat_id)
                    mute_dur = wc.get("mute_duration", 3)
                    until = datetime.utcnow() + timedelta(hours=mute_dur)
                    await client.restrict_chat_member(
                        chat_id, user_id,
                        ChatPermissions(),
                        until_date=until
                    )
                    action_text = f"🔇 Muted ({mute_dur}h)"
                else:
                    action_text = "⚠️ Punished"
                p_msg = await client.send_message(
                    chat_id,
                    f"{action_text}: {user_name}\n"
                    f"Reason: {reason}"
                )
                asyncio.create_task(auto_delete_message(p_msg, 10))
                print(f"🔨 {action_text} {user_name} for {reason}")
            except Exception as e:
                print(f"Failed to punish user: {e}")

        try:
            # Check for forwarded messages
            if config.get("block_forward") and message.forward_date:
                await message.delete()
                moderation_stats["deleted_forward"] += 1
                bf_p = config.get("bf_punishment", "mute")
                await apply_feature_punishment(bf_p, "Forwarded message")
                return
            
            # Get message text
            text = message.text or message.caption or ""
            
            # Check for links
            if config.get("block_links") and text and contains_link(text):
                await message.delete()
                moderation_stats["deleted_links"] += 1
                bl_p = config.get("bl_punishment", "mute")
                await apply_feature_punishment(bl_p, "Link/URL not allowed")
                return
            
            # Check for bad words
            if config.get("block_badwords") and text and contains_bad_words(text):
                await message.delete()
                moderation_stats["deleted_badwords"] += 1
                bbw_p = config.get("bbw_punishment", "mute")
                await apply_feature_punishment(bbw_p, "Inappropriate/sexual content")
                return
            
            # Check for @mentions
            if config.get("block_mentions") and text and contains_mention(text):
                await message.delete()
                moderation_stats["deleted_mentions"] += 1
                await add_warning_and_check_ban("@mentions not allowed")
                return
                
        except Exception as e:
            print(f"Moderation error: {e}")
    
    @bot_client.on_message(filters.command("warnings") & GROUP_CHAT)
    async def check_warnings_handler(client, message):
        """Check warnings for a user"""
        chat_id = message.chat.id
        
        # Check if replying to someone
        if message.reply_to_message:
            target_user = message.reply_to_message.from_user
        else:
            target_user = message.from_user
        
        key = (chat_id, target_user.id)
        
        # Load from DB
        if key not in user_warnings and warnings_col is not None:
            saved = warnings_col.find_one({"chat_id": chat_id, "user_id": target_user.id})
            user_warnings[key] = saved.get("count", 0) if saved else 0
        
        count = user_warnings.get(key, 0)
        await message.reply(
            f"⚠️ **Warnings for {target_user.first_name}:** {count}/{MAX_WARNINGS}\n"
            f"{'🔴 Next violation = BAN!' if count == MAX_WARNINGS - 1 else ''}"
        )
    
    @bot_client.on_message(filters.command("resetwarnings") & GROUP_CHAT)
    async def reset_warnings_handler(client, message):
        """Reset warnings for a user (admin only)"""
        chat_id = message.chat.id
        
        # Check if user is admin
        try:
            member = await client.get_chat_member(chat_id, message.from_user.id)
            if member.status not in ["administrator", "creator"]:
                await message.reply("❌ Only admins can reset warnings!")
                return
        except:
            pass
        
        if not message.reply_to_message:
            await message.reply("❌ Reply to a user's message to reset their warnings")
            return
        
        target_user = message.reply_to_message.from_user
        key = (chat_id, target_user.id)
        
        user_warnings[key] = 0
        if warnings_col is not None:
            warnings_col.update_one(
                {"chat_id": chat_id, "user_id": target_user.id},
                {"$set": {"count": 0}},
                upsert=True
            )
        
        await message.reply(f"✅ Warnings reset for {target_user.first_name}")
    
    # ============ JOIN REQUEST AUTO-APPROVE HANDLERS ============
    
    @bot_client.on_message(filters.command("autoapprove"))
    async def autoapprove_handler(client, message):
        """Enable auto-approve for a channel/group"""
        try:
            parts = message.text.split()
            if len(parts) != 2:
                await message.reply(
                    "Usage: /autoapprove <channel/group>\n\n"
                    "Examples:\n"
                    "• /autoapprove @mychannel\n"
                    "• /autoapprove @mygroup\n"
                    "• /autoapprove -1001234567890"
                )
                return
            
            channel = parts[1]
            auto_approve_channels.add(channel)
            
            # Save to database
            if autoapprove_col is not None:
                autoapprove_col.update_one(
                    {"channel": channel},
                    {"$set": {"channel": channel, "enabled": True, "updated_at": datetime.utcnow()}},
                    upsert=True
                )
            
            await message.reply(
                f"✅ Auto-approve enabled for: {channel}\n\n"
                f"📢 Works for both Channels & Groups!\n"
                f"All join requests will be automatically approved!\n"
                f"Use /stopapprove {channel} to disable."
            )
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
    
    @bot_client.on_message(filters.command("stopapprove"))
    async def stopapprove_handler(client, message):
        """Disable auto-approve for a channel/group"""
        try:
            parts = message.text.split()
            if len(parts) != 2:
                await message.reply("Usage: /stopapprove <channel/group>")
                return
            
            channel = parts[1]
            auto_approve_channels.discard(channel)
            
            # Update database
            if autoapprove_col is not None:
                autoapprove_col.update_one(
                    {"channel": channel},
                    {"$set": {"enabled": False, "updated_at": datetime.utcnow()}}
                )
            
            await message.reply(f"🛑 Auto-approve disabled for: {channel}")
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
    
    @bot_client.on_message(filters.command("approvelist"))
    async def approvelist_handler(client, message):
        """List all auto-approve channels/groups"""
        if not auto_approve_channels:
            await message.reply("📥 No auto-approve channels/groups configured.\n\nUse /autoapprove <channel/group> to enable.")
            return
        
        channels_list = "\n".join([f"• {ch}" for ch in auto_approve_channels])
        await message.reply(
            f"📥 **Auto-Approve Channels/Groups ({len(auto_approve_channels)})**\n\n"
            f"{channels_list}\n\n"
            f"✅ Approved: {auto_approve_stats['approved']}\n"
            f"❌ Failed: {auto_approve_stats['failed']}"
        )
    
    @bot_client.on_message(filters.command("debugjoin"))
    async def debugjoin_handler(client, message):
        """Debug join-request approval access for a channel/group"""
        import re
        import aiohttp

        try:
            text = (message.text or "").strip()
            m = re.match(r"^/debugjoin(?:@\w+)?\s*(.*)$", text)
            arg = (m.group(1).strip() if m else "")

            if not arg:
                await message.reply(
                    "Usage: /debugjoin <channel/group>\n\n"
                    "Examples:\n"
                    "• /debugjoin @mychannel\n"
                    "• /debugjoin -1001234567890"
                )
                return

            if not BOT_TOKEN:
                await message.reply("❌ BOT_TOKEN / TELEGRAM_BOT_TOKEN missing in environment.")
                return

            base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"

            async with aiohttp.ClientSession() as session:
                async def _get(path: str, params: dict | None = None):
                    async with session.get(f"{base_url}/{path}", params=params) as resp:
                        try:
                            data = await resp.json()
                        except Exception:
                            data = {"ok": False, "error_code": resp.status, "description": "Non-JSON response"}
                        return resp.status, data

                status_code, me = await _get("getMe")
                if not me.get("ok"):
                    await message.reply(f"❌ getMe failed ({status_code}): {me.get('description')}")
                    return

                bot_id = (me.get("result") or {}).get("id")

                # Resolve chat_id
                chat_id = None
                if isinstance(arg, str) and arg.lstrip("-").isdigit():
                    # Many users paste channel id without the leading "-".
                    # Supergroup/channel ids are negative in Bot API (usually start with -100...).
                    if not arg.startswith("-") and len(arg) >= 10:
                        chat_id = -int(arg)
                    else:
                        chat_id = int(arg)

                    # getChat needs bot to already be in the chat
                    _, chat = await _get("getChat", {"chat_id": chat_id})
                    if not chat.get("ok"):
                        await message.reply(
                            "❌ getChat failed: "
                            f"{chat.get('description')} (code: {chat.get('error_code')})\n\n"
                            "Ye usually tab hota hai jab bot chat me add nahi hai / access nahi hai."
                        )
                        return
                    chat_id = (chat.get("result") or {}).get("id")
                else:
                    # Resolve username / invite link style
                    _, chat = await _get("getChat", {"chat_id": arg})
                    if not chat.get("ok"):
                        await message.reply(
                            "❌ getChat failed: "
                            f"{chat.get('description')} (code: {chat.get('error_code')})\n\n"
                            "Ye usually tab hota hai jab bot chat me add nahi hai / access nahi hai."
                        )
                        return
                    chat_id = (chat.get("result") or {}).get("id")


                # Check bot membership/rights in that chat
                _, member = await _get("getChatMember", {"chat_id": chat_id, "user_id": bot_id})

                # Check join request visibility
                _, jr = await _get("getChatJoinRequests", {"chat_id": chat_id, "limit": 1})

                def fmt(x):
                    if not x or not isinstance(x, dict):
                        return "(no response)"
                    if x.get("ok"):
                        return "ok"
                    return f"{x.get('description')} (code: {x.get('error_code')})"

                await message.reply(
                    "🧪 **Join Request Debug**\n\n"
                    f"Chat: `{arg}` → `{chat_id}`\n"
                    f"Bot member: {fmt(member)}\n"
                    f"JoinRequests: {fmt(jr)}\n\n"
                    "Agar JoinRequests me error aa raha hai to usi error se exact reason pata chalega."
                )

        except Exception as e:
            await message.reply(f"❌ debug error: {e}")

    @bot_client.on_message(filters.regex(r"^/chatid(?:@\w+)?(?:\s+|$)"))
    async def chatid_handler(client, message):
        """Reply with current chat id/title/type so admins can target /approveall correctly"""
        try:
            chat = message.chat
            title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or "(no title)"
            username = f"@{chat.username}" if getattr(chat, "username", None) else "(no username)"
            await message.reply(
                "🆔 **Chat Info**\n\n"
                f"Title: {title}\n"
                f"Type: {chat.type}\n"
                f"ID: `{chat.id}`\n"
                f"Username: {username}\n\n"
                "Use: `/approveall` (same chat me) ya `/approveall -100...`"
            )
        except Exception as e:
            await message.reply(f"❌ chatid error: {e}")

    @bot_client.on_message(filters.regex(r"^/approveall(?:@\w+)?(?:\s+|-|$)"))
    async def approveall_handler(client, message):
        """Approve all pending join requests for a channel/group using BOT"""
        global auto_approve_stats
        import re
        
        # Check if user is bot admin or group admin
        user_id = message.from_user.id if message.from_user else None

        # If command is sent *as a channel itself* (no from_user), allow it.
        # In Telegram, only channel admins can post as a channel.
        # This can happen in:
        # - a channel (chat.type == CHANNEL)
        # - a group/supergroup when "Send as channel" is used (message.sender_chat is a channel)
        sender_chat = getattr(message, "sender_chat", None)
        is_channel_post = (
            user_id is None
            and sender_chat is not None
            and getattr(sender_chat, "type", None) == ChatType.CHANNEL
        )

        if not is_channel_post and user_id not in BOT_ADMINS:
            # Allow chat admins (group/supergroup/channel) to run it inside their own chat
            if user_id is not None and message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL]:
                member = await client.get_chat_member(message.chat.id, user_id)
                if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                    await message.reply("❌ Only admins can use this command!")
                    return
            else:
                await message.reply("❌ Only bot admins can use this command!")
                return
        
        try:
            text = (message.text or "").strip()
            m = re.match(r"^/approveall(?:@\w+)?\s*(.*)$", text)
            arg = (m.group(1).strip() if m else "")

            # Support both styles:
            # /approveall -100...
            # /approveall-100...
            if not arg:
                # If command is executed INSIDE a group/channel, default to current chat
                if message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL]:
                    channel = str(message.chat.id)
                else:
                    await message.reply(
                        "Usage: /approveall <channel/group>\n\n"
                        "Examples:\n"
                        "• /approveall @mychannel\n"
                        "• /approveall @mygroup\n"
                        "• /approveall -1001234567890\n"
                        "• /approveall-1001234567890\n\n"
                        "Tip: jis chat me join requests dikh rahe hain, waha /chatid run karke id lo.\n"
                        "⚠️ Bot must be admin with 'Add Members' permission!"
                    )
                    return
            else:
                channel = arg

            status_msg = await message.reply(f"🔄 Approving all pending requests for {channel}...\n⏳ Please wait...")
            
            approved = 0
            failed = 0

            # Diagnostics: how many pending requests each method could SEE
            found_userbot = None  # type: ignore
            found_api = None      # type: ignore
            found_db = None       # type: ignore
            try:
                # Resolve chat_id
                chat_id = None
                chat_title = None

                # If user passed a numeric id (e.g. -100...), use it directly
                if isinstance(channel, str) and channel.lstrip("-").isdigit():
                    try:
                        chat_id = int(channel)
                    except Exception:
                        chat_id = None

                # Otherwise, try resolving via Pyrogram client
                if chat_id is None:
                    chat = await client.get_chat(channel)
                    chat_id = chat.id
                    chat_title = getattr(chat, "title", None)
                else:
                    try:
                        chat = await client.get_chat(chat_id)
                        chat_title = getattr(chat, "title", None)
                    except Exception:
                        pass

                # Hard requirement for approving OLD requests: userbot session must be connected
                if not user_clients:
                    await status_msg.edit(
                        "❌ **Userbot not connected**\n\n"
                        "Old pending join requests approve karne ke liye user account (SESSION_STRING) zaroori hai.\n\n"
                        "Fix (Koyeb env):\n"
                        "• API_ID\n"
                        "• API_HASH\n"
                        "• SESSION_STRING (generate_session.py se)\n\n"
                        "Phir redeploy karke /approveall dobara chalao."
                    )
                    return
                
                # BATCH SIZE for parallel processing
                BATCH_SIZE = 20
                
                # METHOD 1: Try USERBOT client (SESSION_STRING) - only user accounts can list join requests
                userbot_worked = False
                if user_clients:
                    userbot_name, userbot = user_clients[0]  # Use first userbot
                    try:
                        await status_msg.edit(f"🔄 Method 1: Userbot ({userbot_name})...\n{channel}\n⚡ Batch mode: {BATCH_SIZE} at once")
                        
                        # Collect all pending requests first
                        pending_users = []
                        async for join_request in userbot.get_chat_join_requests(chat_id):
                            pending_users.append(join_request.user.id)

                        found_userbot = len(pending_users)
                        if pending_users:
                            await status_msg.edit(f"🔄 Found {len(pending_users)} pending requests\n⚡ Processing in batches of {BATCH_SIZE}...")
                            
                            # Process in batches
                            async def approve_user(uid):
                                try:
                                    await userbot.approve_chat_join_request(chat_id, uid)
                                    return ("success", uid)
                                except Exception as e:
                                    return ("failed", uid, str(e))
                            
                            for i in range(0, len(pending_users), BATCH_SIZE):
                                batch = pending_users[i:i + BATCH_SIZE]
                                results = await asyncio.gather(*[approve_user(uid) for uid in batch], return_exceptions=True)
                                
                                for r in results:
                                    if isinstance(r, tuple) and r[0] == "success":
                                        approved += 1
                                        auto_approve_stats["approved"] += 1
                                    else:
                                        failed += 1
                                        auto_approve_stats["failed"] += 1
                                
                                try:
                                    await status_msg.edit(f"🔄 Approving (Userbot)...\n✅ {approved} | ❌ {failed}\n📊 {approved + failed}/{len(pending_users)}")
                                except:
                                    pass
                                await asyncio.sleep(0.5)  # Small delay between batches to avoid rate limits
                        
                        userbot_worked = True
                    except Exception as e:
                        print(f"Userbot get_chat_join_requests failed: {e}")
                else:
                    print("No userbot available (SESSION_STRING not set)")

                # METHOD 2: If Pyrogram didn't work or found nothing, try raw Bot API
                if not userbot_worked or (approved == 0 and failed == 0):
                    import aiohttp
                    bot_token = BOT_TOKEN
                    if bot_token:
                        base_url = f"https://api.telegram.org/bot{bot_token}"
                        try:
                            await status_msg.edit(f"🔄 Method 2: Bot API...\n{channel}\n⚡ Batch mode: {BATCH_SIZE} at once")
                            async with aiohttp.ClientSession() as session:
                                # First collect all pending user IDs
                                all_pending_users = []
                                offset_date = None
                                offset_user_id = None
                                api_worked = False

                                while True:
                                    params = {"chat_id": chat_id, "limit": 100}
                                    if offset_date:
                                        params["offset_date"] = offset_date
                                    if offset_user_id:
                                        params["offset_user_id"] = offset_user_id

                                    async with session.get(f"{base_url}/getChatJoinRequests", params=params) as resp:
                                        data = await resp.json()
                                        if not data.get("ok"):
                                            break  # API not available, try fallback
                                        
                                        api_worked = True
                                        requests = data.get("result") or []
                                        if not requests:
                                            break

                                        for req in requests:
                                            uid = req.get("user", {}).get("id")
                                            if uid:
                                                all_pending_users.append(uid)

                                        if len(requests) < 100:
                                            break
                                        last_req = requests[-1]
                                        offset_date = last_req.get("date")
                                        offset_user_id = (last_req.get("user") or {}).get("id")

                                found_api = len(all_pending_users) if api_worked else None

                                if api_worked and all_pending_users:
                                    await status_msg.edit(f"🔄 Found {len(all_pending_users)} pending requests\n⚡ Processing in batches of {BATCH_SIZE}...")
                                    
                                    # Process in batches
                                    async def approve_user_api(uid):
                                        try:
                                            async with session.post(f"{base_url}/approveChatJoinRequest", data={"chat_id": chat_id, "user_id": uid}) as ar:
                                                ad = await ar.json()
                                                if ad.get("ok"):
                                                    return ("success", uid)
                                                return ("failed", uid)
                                        except:
                                            return ("failed", uid)
                                    
                                    for i in range(0, len(all_pending_users), BATCH_SIZE):
                                        batch = all_pending_users[i:i + BATCH_SIZE]
                                        results = await asyncio.gather(*[approve_user_api(uid) for uid in batch], return_exceptions=True)
                                        
                                        for r in results:
                                            if isinstance(r, tuple) and r[0] == "success":
                                                approved += 1
                                                auto_approve_stats["approved"] += 1
                                            else:
                                                failed += 1
                                        
                                        try:
                                            await status_msg.edit(f"🔄 Approving (API)...\n✅ {approved} | ❌ {failed}\n📊 {approved + failed}/{len(all_pending_users)}")
                                        except:
                                            pass
                                        await asyncio.sleep(0.5)  # Small delay between batches

                                if not api_worked:
                                    raise Exception("Bot API getChatJoinRequests not available")
                        except Exception as e:
                            print(f"Bot API method failed: {e}")

                # METHOD 3: Fallback - approve from stored pending requests in DB
                if approved == 0 and failed == 0 and pending_join_requests_col is not None:
                    try:
                        await status_msg.edit(f"🔄 Method 3: DB fallback...\n{channel}\n⚡ Batch mode: {BATCH_SIZE} at once")
                        pending = list(pending_join_requests_col.find({"chat_id": str(chat_id), "approved": False}).limit(500))
                        found_db = len(pending)
                        if pending:
                            await status_msg.edit(f"🔄 Found {len(pending)} pending requests in DB\n⚡ Processing in batches of {BATCH_SIZE}...")
                            
                            async def approve_user_db(doc):
                                uid = doc.get("user_id")
                                if not uid:
                                    return ("skip", None)
                                try:
                                    await client.approve_chat_join_request(chat_id, uid)
                                    pending_join_requests_col.update_one(
                                        {"chat_id": str(chat_id), "user_id": uid},
                                        {"$set": {"approved": True, "approved_at": datetime.utcnow()}}
                                    )
                                    return ("success", uid)
                                except Exception as e:
                                    pending_join_requests_col.update_one(
                                        {"chat_id": str(chat_id), "user_id": uid},
                                        {"$set": {"approved": True, "error": str(e)}}
                                    )
                                    return ("failed", uid)
                            
                            for i in range(0, len(pending), BATCH_SIZE):
                                batch = pending[i:i + BATCH_SIZE]
                                results = await asyncio.gather(*[approve_user_db(doc) for doc in batch], return_exceptions=True)
                                
                                for r in results:
                                    if isinstance(r, tuple):
                                        if r[0] == "success":
                                            approved += 1
                                            auto_approve_stats["approved"] += 1
                                        elif r[0] == "failed":
                                            failed += 1
                                            auto_approve_stats["failed"] += 1
                                
                                try:
                                    await status_msg.edit(f"🔄 Approving (DB)...\n✅ {approved} | ❌ {failed}\n📊 {approved + failed}/{len(pending)}")
                                except:
                                    pass
                                await asyncio.sleep(0.5)
                    except Exception as e:
                        print(f"DB fallback failed: {e}")

                # Final result
                if approved == 0 and failed == 0:
                    await status_msg.edit(
                        f"ℹ️ **No pending requests found**\n\n"
                        f"Channel: {chat_title or channel}\n\n"
                        "Diagnostics (pending visible):\n"
                        f"• Userbot: {found_userbot if found_userbot is not None else 'N/A'}\n"
                        f"• Bot API: {found_api if found_api is not None else 'N/A'}\n"
                        f"• DB: {found_db if found_db is not None else 'N/A'}\n\n"
                        "Fix checklist:\n"
                        "• Channel invite link must be **Request to Join** (approval required)\n"
                        "• Abhi koi user ne request bheji ho (pending requests actually exist)\n"
                        "• Userbot account (SESSION_STRING) should be **Admin** in the channel\n"
                        "• Try: /debugjoin -100... (for permission check)"
                    )
                else:
                    await status_msg.edit(
                        f"✅ **Approval Complete!**\n\n"
                        f"📢 Channel: {chat_title or channel}\n"
                        f"✅ Approved: {approved}\n"
                        f"❌ Failed: {failed}"
                    )
            except Exception as e:
                error_msg = str(e)
                if "CHAT_ADMIN_REQUIRED" in error_msg or "not enough rights" in error_msg.lower():
                    await status_msg.edit(
                        f"❌ **Bot needs admin permissions!**\n\n"
                        f"Make the bot admin in {channel} with:\n"
                        f"• ✅ Invite Users via Link"
                    )
                else:
                    await status_msg.edit(f"❌ Error: {e}")
        
        except Exception as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                await message.reply(
                    "❌ **Error: Not Found**\n\n"
                    "Iska matlab: bot ko is channel/group ka access nahi hai (bot add/admin nahi hai) ya chat id galat hai.\n\n"
                    "Fix:\n"
                    "1) Bot ko us channel/group me ADD karo\n"
                    "2) Bot ko ADMIN banao\n"
                    "3) Join Requests ON rakho (Approval required)\n"
                    "4) Phir /approveall -100... dobara chalao"
                )
                return
            await message.reply(f"❌ Error: {e}")
    
    # ============ CHAT JOIN REQUEST HANDLER (Auto-approve) ============
    
    @bot_client.on_chat_join_request()
    async def join_request_handler(client, chat_join_request):
        """Automatically approve join requests for enabled channels"""
        global auto_approve_stats
        
        chat_id = str(chat_join_request.chat.id)
        chat_username = f"@{chat_join_request.chat.username}" if chat_join_request.chat.username else chat_id
        
        # Check if auto-approve is enabled for this channel
        should_approve = (
            chat_id in auto_approve_channels or
            chat_username in auto_approve_channels or
            chat_join_request.chat.username in auto_approve_channels
        )
        
        if not should_approve:
            return
        
        try:
            # Save request so /approveall can work even if Telegram doesn't allow listing join requests
            if pending_join_requests_col is not None:
                pending_join_requests_col.update_one(
                    {"chat_id": str(chat_join_request.chat.id), "user_id": chat_join_request.from_user.id},
                    {
                        "$set": {
                            "chat_id": str(chat_join_request.chat.id),
                            "chat_username": chat_join_request.chat.username,
                            "user_id": chat_join_request.from_user.id,
                            "user_name": chat_join_request.from_user.first_name,
                            "requested_at": datetime.utcnow(),
                            "approved": False,
                        }
                    },
                    upsert=True,
                )

            # Use user client if available, otherwise bot client
            if user_clients:
                user_client = user_clients[0][1]
                await user_client.approve_chat_join_request(
                    chat_join_request.chat.id,
                    chat_join_request.from_user.id
                )
            else:
                await client.approve_chat_join_request(
                    chat_join_request.chat.id,
                    chat_join_request.from_user.id
                )

            if pending_join_requests_col is not None:
                pending_join_requests_col.update_one(
                    {"chat_id": str(chat_join_request.chat.id), "user_id": chat_join_request.from_user.id},
                    {"$set": {"approved": True, "approved_at": datetime.utcnow()}},
                )

            auto_approve_stats["approved"] += 1
            print(f"✅ Auto-approved: {chat_join_request.from_user.first_name} for {chat_username}")
        
        except Exception as e:
            auto_approve_stats["failed"] += 1
            print(f"❌ Failed to auto-approve: {e}")

    @bot_client.on_message(filters.command("rawtest"))
    async def rawtest_handler(client, message):
        """Raw API test for debugging - shows exact responses"""
        import aiohttp
        try:
            parts = (message.text or "").split()
            if len(parts) < 2:
                await message.reply("Usage: /rawtest <chat_id>\nExample: /rawtest -1002926855756")
                return

            chat_id = parts[1]
            if not BOT_TOKEN:
                await message.reply("❌ BOT_TOKEN missing!")
                return

            base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
            results = []

            async with aiohttp.ClientSession() as session:
                # 1. getMe
                async with session.get(f"{base_url}/getMe") as r:
                    me_status = r.status
                    try:
                        me_data = await r.json()
                    except:
                        me_data = {"raw": (await r.text())[:200]}
                results.append(f"**1. getMe** (status={me_status}):\n```{str(me_data)[:300]}```")

                bot_id = (me_data.get("result") or {}).get("id") if isinstance(me_data, dict) else None

                # 2. getChat
                async with session.get(f"{base_url}/getChat", params={"chat_id": chat_id}) as r:
                    gc_status = r.status
                    try:
                        gc_data = await r.json()
                    except:
                        gc_data = {"raw": (await r.text())[:200]}
                results.append(f"**2. getChat** (status={gc_status}):\n```{str(gc_data)[:400]}```")

                # 3. getChatMember (bot)
                if bot_id:
                    async with session.get(f"{base_url}/getChatMember", params={"chat_id": chat_id, "user_id": bot_id}) as r:
                        gm_status = r.status
                        try:
                            gm_data = await r.json()
                        except:
                            gm_data = {"raw": (await r.text())[:200]}
                    results.append(f"**3. getChatMember(bot)** (status={gm_status}):\n```{str(gm_data)[:400]}```")

                # 4. getChatJoinRequests
                async with session.get(f"{base_url}/getChatJoinRequests", params={"chat_id": chat_id, "limit": 5}) as r:
                    jr_status = r.status
                    try:
                        jr_data = await r.json()
                    except:
                        jr_data = {"raw": (await r.text())[:200]}
                results.append(f"**4. getChatJoinRequests** (status={jr_status}):\n```{str(jr_data)[:400]}```")

            await message.reply("🔬 Raw API Test Results\n\n" + "\n\n".join(results))

        except Exception as e:
            await message.reply(f"❌ rawtest error: {e}")

    @bot_client.on_message(filters.command("version"))
    async def version_handler(client, message):
        """Print running build/version to confirm deployment"""
        from datetime import datetime
        try:
            await message.reply(
                "✅ Running build is updated.\n"
                f"Build time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                "Commands: /version /rawtest /debugjoin"
            )
        except Exception as e:
            await message.reply(f"❌ version error: {e}")

    # ==================== BROADCAST FUNCTIONALITY ====================
    
    async def save_user_for_broadcast(user_id: int, username: str = None, first_name: str = None):
        """Save user to broadcast list"""
        if broadcast_users_col is None:
            return
        try:
            broadcast_users_col.update_one(
                {"user_id": user_id},
                {"$set": {
                    "user_id": user_id,
                    "username": username,
                    "first_name": first_name,
                    "updated_at": datetime.utcnow()
                }},
                upsert=True
            )
        except Exception as e:
            print(f"Error saving user for broadcast: {e}")

    def _candidate_chat_ids(chat_id: int) -> list[int]:
        """Return possible chat_id variants.

        Telegram IDs differ by client/library:
        - Basic groups: negative id like -123456
        - Supergroups/channels: often -1001234567890
        Some environments store/return the "missing -100" form, which can cause PeerIdInvalid.
        """
        ids: list[int] = []
        try:
            ids.append(int(chat_id))
        except Exception:
            return []

        s = str(ids[0])
        if s.startswith("-100"):
            # Try the non -100 variant too
            try:
                ids.append(int("-" + s[4:]))
            except Exception:
                pass
        elif s.startswith("-") and len(s) >= 10:
            # Try adding -100 prefix (supergroup/channel form)
            try:
                ids.append(int("-100" + s[1:]))
            except Exception:
                pass

        # De-dup while preserving order
        seen = set()
        out: list[int] = []
        for x in ids:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    async def bot_api_is_admin(chat_id: int) -> bool:
        """Fallback admin check via raw Bot API.
        Helps when Pyrogram raises PeerIdInvalid due to missing peer cache."""
        try:
            if not BOT_TOKEN:
                return False
            import aiohttp

            base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                me = await bot_client.get_me()
                for cid in _candidate_chat_ids(chat_id):
                    try:
                        async with session.get(
                            f"{base_url}/getChatMember",
                            params={"chat_id": str(cid), "user_id": str(me.id)},
                        ) as r:
                            data = await r.json()
                        if isinstance(data, dict) and data.get("ok"):
                            status = ((data.get("result") or {}).get("status") or "").lower()
                            return status in {"administrator", "creator"}
                    except Exception:
                        continue
            return False
        except Exception:
            return False

    async def bot_api_can_invite_users(chat_id: int) -> bool | None:
        """Return can_invite_users for the bot in a chat via Bot API.

        Returns:
          - True/False when available
          - None when it can't be determined (e.g. token missing / API error)
        """
        try:
            if not BOT_TOKEN:
                return None
            import aiohttp

            base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                me = await bot_client.get_me()
                for cid in _candidate_chat_ids(chat_id):
                    try:
                        async with session.get(
                            f"{base_url}/getChatMember",
                            params={"chat_id": str(cid), "user_id": str(me.id)},
                        ) as r:
                            data = await r.json()
                        if not isinstance(data, dict) or not data.get("ok"):
                            continue
                        result = data.get("result") or {}
                        val = result.get("can_invite_users")
                        return bool(val) if val is not None else None
                    except Exception:
                        continue
            return None
        except Exception:
            return None

    async def save_group_for_broadcast(chat_id: int, chat_title: str = None):
        """Save group to broadcast list (only if bot is admin)"""
        if broadcast_groups_col is None:
            return False
        try:
            # Check if bot is admin in this group
            try:
                bot_me = await bot_client.get_me()

                member = None
                last_err = None
                for cid in _candidate_chat_ids(chat_id):
                    try:
                        # Some hosts/clients can throw PeerIdInvalid even for valid ids
                        # if the peer isn't cached yet. Force-resolve once and retry.
                        try:
                            member = await bot_client.get_chat_member(cid, bot_me.id)
                        except PeerIdInvalid:
                            try:
                                await bot_client.get_chat(cid)  # resolve/cache peer
                            except Exception:
                                pass
                            member = await bot_client.get_chat_member(cid, bot_me.id)

                        chat_id = cid  # use the working id going forward
                        break
                    except Exception as e:
                        last_err = e
                        member = None
                        continue

                if member is None:
                    # Final fallback: raw Bot API (doesn't depend on local peer cache)
                    if not await bot_api_is_admin(chat_id):
                        return False

                if member is not None and member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                    return False  # Not admin, don't save
            except Exception:
                # If pyrogram path fails, try raw Bot API before giving up
                if not await bot_api_is_admin(chat_id):
                    return False

            broadcast_groups_col.update_one(
                {"chat_id": chat_id},
                {"$set": {
                    "chat_id": chat_id,
                    "chat_title": chat_title,
                    "updated_at": datetime.utcnow()
                }},
                upsert=True
            )
            return True
        except Exception as e:
            print(f"Error saving group for broadcast: {e}")
            return False

    async def refresh_broadcast_groups() -> dict:
        """Scan all dialogs and rebuild broadcast group list (admin-only command).
        Also syncs admin_groups_col for /admingroups command."""
        if broadcast_groups_col is None:
            return {"total_seen": 0, "saved": 0, "removed": 0, "errors": 0}

        total_seen = saved = removed = errors = 0
        last_error = None
        current_group_ids = set()

        scan_client = get_next_client() or (user_clients[0][1] if user_clients else None)

        # Bots cannot scan dialogs (messages.GetDialogs). If no user session is configured,
        # fallback to syncing whatever is already stored in broadcast_groups_col.
        if scan_client is None:
            try:
                if bot_client is None:
                    return {
                        "total_seen": 0,
                        "saved": 0,
                        "removed": 0,
                        "errors": 1,
                        "last_error": "Bot not started yet.",
                    }

                me = await bot_client.get_me()
                docs = list(broadcast_groups_col.find({}))
                total_seen = len(docs)

                for d in docs:
                    try:
                        chat_id = int(d.get("chat_id"))
                        chat = await bot_client.get_chat(chat_id)
                        chat_title = getattr(chat, "title", None) or d.get("chat_title") or str(chat_id)
                        chat_type = "supergroup" if getattr(chat, "type", None) == ChatType.SUPERGROUP else "group"
                        member_count = getattr(chat, "members_count", 0) or 0

                        member = await bot_client.get_chat_member(chat_id, me.id)
                        if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                            # Not admin anymore
                            broadcast_groups_col.delete_one({"chat_id": chat_id})
                            if admin_groups_col is not None:
                                admin_groups_col.delete_one({"chat_id": chat_id})
                            removed += 1
                            continue

                        permissions = {
                            "is_owner": member.status == ChatMemberStatus.OWNER,
                            "can_delete_messages": getattr(member.privileges, "can_delete_messages", False) if hasattr(member, "privileges") else False,
                            "can_restrict_members": getattr(member.privileges, "can_restrict_members", False) if hasattr(member, "privileges") else False,
                            "can_promote_members": getattr(member.privileges, "can_promote_members", False) if hasattr(member, "privileges") else False,
                            "can_change_info": getattr(member.privileges, "can_change_info", False) if hasattr(member, "privileges") else False,
                            "can_invite_users": getattr(member.privileges, "can_invite_users", False) if hasattr(member, "privileges") else False,
                            "can_pin_messages": getattr(member.privileges, "can_pin_messages", False) if hasattr(member, "privileges") else False,
                            "can_manage_chat": getattr(member.privileges, "can_manage_chat", False) if hasattr(member, "privileges") else False,
                        }

                        await save_admin_group(chat_id, chat_title, chat_type, member_count, permissions)

                        username = getattr(chat, "username", "") or ""
                        if username:
                            invite_link = f"https://t.me/{username}"
                        else:
                            chat_id_str = str(chat_id).replace("-100", "")
                            invite_link = f"https://t.me/c/{chat_id_str}"

                        if admin_groups_col is not None:
                            admin_groups_col.update_one(
                                {"chat_id": chat_id},
                                {"$set": {"invite_link": invite_link, "username": username}},
                            )

                        saved += 1
                    except Exception as e:
                        errors += 1
                        last_error = str(e)

                return {
                    "total_seen": total_seen,
                    "saved": saved,
                    "removed": removed,
                    "errors": errors,
                    "last_error": last_error or "No user session configured; synced from stored DB only.",
                }
            except Exception as e:
                return {
                    "total_seen": 0,
                    "saved": 0,
                    "removed": 0,
                    "errors": 1,
                    "last_error": f"No user session configured and fallback failed: {e}",
                }
        
        try:
            async for dialog in scan_client.get_dialogs():
                chat = getattr(dialog, "chat", None)
                if chat is None:
                    continue

                if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
                    continue

                total_seen += 1
                chat_id = chat.id
                chat_title = getattr(chat, "title", "Unknown Group")
                chat_type = "supergroup" if chat.type == ChatType.SUPERGROUP else "group"
                member_count = getattr(chat, "members_count", 0) or 0
                current_group_ids.add(chat_id)

                try:
                    ok = await save_group_for_broadcast(chat_id, chat_title)

                    # If Pyrogram couldn't verify admin due to peer-cache issues,
                    # fallback to raw Bot API so we still store the group.
                    if not ok:
                        if await bot_api_is_admin(chat_id):
                            try:
                                broadcast_groups_col.update_one(
                                    {"chat_id": chat_id},
                                    {"$set": {"chat_id": chat_id, "chat_title": chat_title, "updated_at": datetime.utcnow()}},
                                    upsert=True,
                                )
                                ok = True
                            except Exception:
                                ok = False

                    if ok:
                        saved += 1
                        # Also save to admin_groups_col with permissions (for /admingroups)
                        try:
                            bot_me = await bot_client.get_me()
                            try:
                                member = await bot_client.get_chat_member(chat_id, bot_me.id)
                            except PeerIdInvalid:
                                try:
                                    await bot_client.get_chat(chat_id)
                                except Exception:
                                    pass
                                member = await bot_client.get_chat_member(chat_id, bot_me.id)

                            if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                                permissions = {
                                    "is_owner": member.status == ChatMemberStatus.OWNER,
                                    "can_delete_messages": getattr(member.privileges, "can_delete_messages", False) if hasattr(member, "privileges") else False,
                                    "can_restrict_members": getattr(member.privileges, "can_restrict_members", False) if hasattr(member, "privileges") else False,
                                    "can_promote_members": getattr(member.privileges, "can_promote_members", False) if hasattr(member, "privileges") else False,
                                    "can_change_info": getattr(member.privileges, "can_change_info", False) if hasattr(member, "privileges") else False,
                                    "can_invite_users": getattr(member.privileges, "can_invite_users", False) if hasattr(member, "privileges") else False,
                                    "can_pin_messages": getattr(member.privileges, "can_pin_messages", False) if hasattr(member, "privileges") else False,
                                    "can_manage_chat": getattr(member.privileges, "can_manage_chat", False) if hasattr(member, "privileges") else False,
                                }
                            else:
                                permissions = None

                            if permissions is not None:
                                await save_admin_group(chat_id, chat_title, chat_type, member_count, permissions)

                                # Store link info so /admingroups can show clickable links
                                username = getattr(chat, "username", "") or ""
                                if username:
                                    invite_link = f"https://t.me/{username}"
                                elif getattr(chat, "invite_link", None):
                                    invite_link = chat.invite_link
                                else:
                                    chat_id_str = str(chat_id).replace("-100", "")
                                    invite_link = f"https://t.me/c/{chat_id_str}"

                                if admin_groups_col is not None:
                                    admin_groups_col.update_one(
                                        {"chat_id": chat_id},
                                        {"$set": {"invite_link": invite_link, "username": username}},
                                    )
                        except Exception:
                            # As a last resort, still list the group in /admingroups (without permissions)
                            try:
                                if admin_groups_col is not None:
                                    await save_admin_group(
                                        chat_id,
                                        chat_title,
                                        chat_type,
                                        member_count,
                                        {
                                            "is_owner": False,
                                            "can_delete_messages": False,
                                            "can_restrict_members": False,
                                            "can_promote_members": False,
                                            "can_change_info": False,
                                            "can_invite_users": False,
                                            "can_pin_messages": False,
                                            "can_manage_chat": False,
                                        },
                                    )
                            except Exception:
                                pass
                    else:
                        # If it's in DB but we are not admin anymore, remove it
                        try:
                            if broadcast_groups_col.find_one({"chat_id": chat_id}):
                                broadcast_groups_col.delete_one({"chat_id": chat_id})
                                removed += 1
                            if admin_groups_col is not None:
                                admin_groups_col.delete_one({"chat_id": chat_id})
                        except Exception:
                            pass

                except Exception as e:
                    errors += 1
                    last_error = str(e)

        except Exception as e:
            errors += 1
            last_error = str(e)

        # Remove groups from admin_groups_col that are no longer in dialogs
        try:
            if admin_groups_col is not None:
                existing_groups = list(admin_groups_col.find({}, {"chat_id": 1}))
                for group in existing_groups:
                    if group["chat_id"] not in current_group_ids:
                        admin_groups_col.delete_one({"chat_id": group["chat_id"]})
        except Exception:
            pass

        return {
            "total_seen": total_seen,
            "saved": saved,
            "removed": removed,
            "errors": errors,
            "last_error": last_error,
        }


    async def remove_group_from_broadcast(chat_id: int):
        """Remove group from broadcast list"""
        if broadcast_groups_col is None:
            return
        try:
            broadcast_groups_col.delete_one({"chat_id": chat_id})
        except Exception as e:
            print(f"Error removing group from broadcast: {e}")

    async def remove_user_from_broadcast(user_id: int):
        """Remove user from broadcast list"""
        if broadcast_users_col is None:
            return
        try:
            broadcast_users_col.delete_one({"user_id": user_id})
        except Exception as e:
            print(f"Error removing user from broadcast: {e}")

    # ==================== ADMIN GROUPS MANAGEMENT ====================
    # Note: Primary save_admin_group is defined earlier. This is a fallback reference.
    # The main function is at line ~1321 with username/invite_link support.

    async def remove_admin_group(chat_id: int):
        """Remove group from admin groups list"""
        if admin_groups_col is None:
            return
        try:
            admin_groups_col.delete_one({"chat_id": chat_id})
        except Exception as e:
            print(f"Error removing admin group: {e}")

    async def get_all_admin_groups():
        """Get all groups where bot is admin"""
        if admin_groups_col is None:
            return []
        try:
            groups = list(admin_groups_col.find({}).sort("updated_at", -1))
            return groups
        except Exception as e:
            print(f"Error getting admin groups: {e}")
            return []

    async def _get_best_join_link(chat_id: int, chat_obj=None):
        """Return (username, invite_link) for a chat.

        For private groups without username, only an invite link is clickable.
        Requires bot admin right: "Invite users".
        """
        print(f"📎 _get_best_join_link START for chat_id={chat_id}")
        username = ""
        invite_link = ""

        # Try multiple possible id variants to avoid PeerIdInvalid / "chat not found" issues.
        chat = chat_obj
        if chat is None:
            print(f"📎 chat_obj is None, trying to fetch...")
            for cid in _candidate_chat_ids(chat_id):
                try:
                    chat = await bot_client.get_chat(cid)
                    chat_id = cid
                    print(f"📎 got chat via cid={cid}")
                    break
                except PeerIdInvalid:
                    try:
                        await bot_client.get_chat(cid)  # force resolve/cache
                    except Exception:
                        pass
                    try:
                        chat = await bot_client.get_chat(cid)
                        chat_id = cid
                        break
                    except Exception:
                        chat = None
                        continue
                except Exception as e:
                    print(f"📎 get_chat({cid}) failed: {type(e).__name__}: {e}")
                    chat = None
                    continue

        if chat is not None:
            username = (getattr(chat, "username", "") or "").strip().lstrip("@")
            if username:
                print(f"📎 Found username: {username}")
                return username, ""

            invite_link = (getattr(chat, "invite_link", "") or "").strip()
            if invite_link:
                print(f"📎 Found existing invite_link on chat object: {invite_link}")
                return "", invite_link
        else:
            print(f"📎 Could not fetch chat object for {chat_id}")

        # Pyrogram export (works when bot can invite)
        print(f"📎 Trying export_chat_invite_link for {chat_id}...")
        try:
            invite_link = (await bot_client.export_chat_invite_link(chat_id)).strip()
            if invite_link:
                print(f"📎 export_chat_invite_link SUCCESS: {invite_link}")
                return "", invite_link
        except Exception as e:
            print(f"⚠️ export_chat_invite_link failed for {chat_id}: {type(e).__name__}: {e}")

        # Bot API fallback: first try exportChatInviteLink (primary link), then createChatInviteLink (additional link).
        # Some chats/settings may allow export but not creation.
        if not BOT_TOKEN:
            print("⚠️ BOT_TOKEN missing; cannot create invite links via Bot API")
            return "", ""

        print(f"📎 Trying Bot API fallback for {chat_id}...")
        try:
            import aiohttp

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as sess:
                base = f"https://api.telegram.org/bot{BOT_TOKEN}"

                for cid in _candidate_chat_ids(chat_id):
                    # 1) exportChatInviteLink
                    try:
                        async with sess.post(f"{base}/exportChatInviteLink", json={"chat_id": str(cid)}) as resp:
                            data = await resp.json()
                        print(f"📎 Bot API exportChatInviteLink({cid}) response: {data}")
                        if data.get("ok") and data.get("result"):
                            link = (data.get("result") or "").strip()
                            if link:
                                print(f"📎 Bot API exportChatInviteLink SUCCESS: {link}")
                                return "", link
                    except Exception as e:
                        print(f"📎 exportChatInviteLink({cid}) exception: {type(e).__name__}: {e}")

                    # 2) createChatInviteLink
                    try:
                        async with sess.post(f"{base}/createChatInviteLink", json={"chat_id": str(cid)}) as resp:
                            data = await resp.json()
                        print(f"📎 Bot API createChatInviteLink({cid}) response: {data}")
                        if data.get("ok") and data.get("result"):
                            link = (data["result"].get("invite_link") or "").strip()
                            if link:
                                print(f"📎 Bot API createChatInviteLink SUCCESS: {link}")
                                return "", link
                    except Exception as e:
                        print(f"📎 createChatInviteLink({cid}) exception: {type(e).__name__}: {e}")
        except Exception as e:
            print(f"⚠️ Bot API invite-link fallback failed for {chat_id}: {type(e).__name__}: {e}")

        print(f"📎 _get_best_join_link END for {chat_id} - NO LINK FOUND")
        return "", ""

    async def refresh_admin_groups() -> dict:
        """Scan all dialogs and update admin groups with permissions + join links"""
        if admin_groups_col is None:
            return {"total_seen": 0, "saved": 0, "removed": 0, "errors": 0}

        total_seen = saved = removed = errors = 0
        current_group_ids = set()

        try:
            async for dialog in bot_client.get_dialogs():
                chat = getattr(dialog, "chat", None)
                if chat is None:
                    continue

                if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
                    continue

                total_seen += 1
                chat_id = chat.id
                current_group_ids.add(chat_id)
                chat_title = getattr(chat, "title", "Unknown Group")
                chat_type = "supergroup" if chat.type == ChatType.SUPERGROUP else "group"
                member_count = getattr(chat, "members_count", 0) or 0

                try:
                    bot_me = await bot_client.get_me()

                    member = None
                    try:
                        member = await bot_client.get_chat_member(chat_id, bot_me.id)
                    except PeerIdInvalid:
                        # Try to resolve/cache peer and retry once
                        try:
                            await bot_client.get_chat(chat_id)
                        except Exception:
                            pass
                        try:
                            member = await bot_client.get_chat_member(chat_id, bot_me.id)
                        except PeerIdInvalid:
                            member = None

                    is_admin = False
                    if member is not None:
                        is_admin = member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
                    else:
                        # Final fallback: raw Bot API (doesn't depend on peer cache)
                        is_admin = await bot_api_is_admin(chat_id)

                    if is_admin:
                        can_invite_fallback = await bot_api_can_invite_users(chat_id)
                        permissions = {
                            "is_owner": (member.status == ChatMemberStatus.OWNER) if member is not None else False,
                            "can_delete_messages": getattr(member.privileges, "can_delete_messages", False) if (member is not None and hasattr(member, "privileges")) else False,
                            "can_restrict_members": getattr(member.privileges, "can_restrict_members", False) if (member is not None and hasattr(member, "privileges")) else False,
                            "can_promote_members": getattr(member.privileges, "can_promote_members", False) if (member is not None and hasattr(member, "privileges")) else False,
                            "can_change_info": getattr(member.privileges, "can_change_info", False) if (member is not None and hasattr(member, "privileges")) else False,
                            "can_invite_users": (
                                getattr(member.privileges, "can_invite_users", False)
                                if (member is not None and hasattr(member, "privileges"))
                                else bool(can_invite_fallback) if can_invite_fallback is not None else False
                            ),
                            "can_pin_messages": getattr(member.privileges, "can_pin_messages", False) if (member is not None and hasattr(member, "privileges")) else False,
                            "can_manage_chat": getattr(member.privileges, "can_manage_chat", False) if (member is not None and hasattr(member, "privileges")) else False,
                        }

                        # Fetch link FIRST before saving
                        username, invite_link = await _get_best_join_link(chat_id, chat_obj=chat)
                        print(f"🔗 refresh_admin_groups: {chat_title} | username={username} | invite_link={invite_link}")

                        ok = await save_admin_group(chat_id, chat_title, chat_type, member_count, permissions, username=username, invite_link=invite_link)
                        if ok:
                            saved += 1

                    else:
                        await remove_admin_group(chat_id)
                        removed += 1

                except Exception as e:
                    errors += 1
                    print(f"Error checking admin status for {chat_id}: {e}")

            # Remove groups that are no longer in dialogs
            try:
                existing_groups = list(admin_groups_col.find({}, {"chat_id": 1}))
                for group in existing_groups:
                    if group.get("chat_id") not in current_group_ids:
                        admin_groups_col.delete_one({"chat_id": group.get("chat_id")})
                        removed += 1
            except Exception:
                pass

        except Exception as e:
            errors += 1
            print(f"Error refreshing admin groups: {e}")

        return {"total_seen": total_seen, "saved": saved, "removed": removed, "errors": errors}

    # Track users on /start
    @bot_client.on_message(filters.command("start") & filters.private, group=-100)
    async def track_user_broadcast(client, message):
        """Track user for broadcast when they start bot"""
        try:
            await save_user_for_broadcast(
                message.from_user.id,
                message.from_user.username,
                message.from_user.first_name
            )
        except Exception:
            pass

    # Track groups when bot is added OR when a command is used in the group.
    # Note: With Bot Privacy ON, bots may not receive normal messages. Service updates are reliable.
    @bot_client.on_message((GROUP_CHAT & (filters.new_chat_members | filters.left_chat_member)), group=-100)
    async def track_group_broadcast(client, message):
        """Track group for broadcast"""
        try:
            if not (message.chat and message.chat.id):
                return

            chat_id = message.chat.id

            # If bot was removed from group, clean up
            try:
                if getattr(message, "left_chat_member", None) is not None:
                    me = await bot_client.get_me()
                    if message.left_chat_member.id == me.id:
                        await remove_group_from_broadcast(chat_id)
                        return
            except Exception:
                pass

            await save_group_for_broadcast(chat_id, message.chat.title)
        except Exception:
            pass

    @bot_client.on_message(filters.command("broadcast") & filters.private)
    async def broadcast_command(client, message):
        """Broadcast message to all users - Admin only"""
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("❌ Only admins can use broadcast.")
            return
        
        # Check if reply to a message
        if not message.reply_to_message:
            await message.reply(
                "📢 **Broadcast to Users**\n\n"
                "Reply to any message (text/photo/video/document) with:\n"
                "`/broadcast` - Send to all users\n\n"
                "Example: Reply to a message and send /broadcast"
            )
            return
        
        status_msg = await message.reply("📢 Starting broadcast to users...")
        
        if broadcast_users_col is None:
            await status_msg.edit("❌ Database not connected.")
            return
        
        users = list(broadcast_users_col.find({}))
        total = len(users)
        success = 0
        failed = 0
        blocked = 0
        
        await status_msg.edit(f"📢 Broadcasting to {total} users...")
        
        for user in users:
            try:
                user_id = user.get("user_id")
                if not user_id:
                    continue
                
                # Copy the replied message to user
                await message.reply_to_message.copy(user_id)
                success += 1
                
                # Update status every 50 users
                if success % 50 == 0:
                    await status_msg.edit(
                        f"📢 Broadcasting...\n"
                        f"✅ Sent: {success}/{total}\n"
                        f"❌ Failed: {failed}\n"
                        f"🚫 Blocked: {blocked}"
                    )
                
                await asyncio.sleep(0.05)  # Rate limit protection
                
            except Exception as e:
                err_str = str(e).lower()
                if "blocked" in err_str or "deactivated" in err_str or "user is deactivated" in err_str:
                    blocked += 1
                    await remove_user_from_broadcast(user_id)
                else:
                    failed += 1
        
        await status_msg.edit(
            f"✅ **Broadcast Complete!**\n\n"
            f"📊 Total users: {total}\n"
            f"✅ Sent: {success}\n"
            f"❌ Failed: {failed}\n"
            f"🚫 Blocked (removed): {blocked}"
        )

    @bot_client.on_message(filters.command("gbroadcast") & filters.private)
    async def group_broadcast_command(client, message):
        """Broadcast message to all groups - Admin only"""
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("❌ Only admins can use group broadcast.")
            return
        
        # Check if reply to a message
        if not message.reply_to_message:
            await message.reply(
                "📢 **Broadcast to Groups**\n\n"
                "Reply to any message (text/photo/video/document) with:\n"
                "`/gbroadcast` - Send to all groups where bot is admin\n\n"
                "Example: Reply to a message and send /gbroadcast"
            )
            return
        
        status_msg = await message.reply("📢 Starting broadcast to groups...")
        
        if broadcast_groups_col is None:
            await status_msg.edit("❌ Database not connected.")
            return
        
        groups = list(broadcast_groups_col.find({}))
        total = len(groups)
        success = 0
        failed = 0
        removed = 0
        
        await status_msg.edit(f"📢 Broadcasting to {total} groups...")
        
        for group in groups:
            try:
                chat_id = group.get("chat_id")
                if not chat_id:
                    continue
                
                # Copy the replied message to group
                await message.reply_to_message.copy(chat_id)
                success += 1
                
                # Update status every 20 groups
                if success % 20 == 0:
                    await status_msg.edit(
                        f"📢 Broadcasting to groups...\n"
                        f"✅ Sent: {success}/{total}\n"
                        f"❌ Failed: {failed}\n"
                        f"🗑️ Removed: {removed}"
                    )
                
                await asyncio.sleep(0.1)  # Rate limit protection
                
            except Exception as e:
                err_str = str(e).lower()
                if "forbidden" in err_str or "not a member" in err_str or "chat not found" in err_str or "kicked" in err_str:
                    removed += 1
                    await remove_group_from_broadcast(chat_id)
                else:
                    failed += 1
        
        await status_msg.edit(
            f"✅ **Group Broadcast Complete!**\n\n"
            f"📊 Total groups: {total}\n"
            f"✅ Sent: {success}\n"
            f"❌ Failed: {failed}\n"
            f"🗑️ Removed (left/kicked): {removed}"
        )

    @bot_client.on_message(filters.command("broadcaststats") & filters.private)
    async def broadcast_stats_command(client, message):
        """Show broadcast statistics - Admin only"""
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("❌ Only admins can view broadcast stats.")
            return
        
        user_count = 0
        group_count = 0
        
        if broadcast_users_col is not None:
            user_count = broadcast_users_col.count_documents({})
        
        if broadcast_groups_col is not None:
            group_count = broadcast_groups_col.count_documents({})
        
        await message.reply(
            f"📊 **Broadcast Statistics**\n\n"
            f"👤 Total Users: {user_count}\n"
            f"👥 Total Groups: {group_count}\n\n"
            f"**Commands:**\n"
            f"`/broadcast` - Send to all users (reply to message)\n"
            f"`/gbroadcast` - Send to all groups (reply to message)\n"
            f"`/broadcaststats` - View this stats"
        )


# Flask routes for health checks
@flask_app.route("/")
def home():
    num_accounts = len(user_clients)
    return jsonify({
        "status": "ok",
        "message": "Telegram Forwarder Bot (Multi-Account MTProto)",
        "accounts": num_accounts,
        "expected_speed": f"{num_accounts * 30}/min"
    })


@flask_app.route("/webhook", methods=["GET", "POST"])
@flask_app.route("/webhook/", methods=["GET", "POST"])
def webhook():
    """Handle Telegram webhook requests (we use polling, so just acknowledge)."""
    try:
        # Helpful for Koyeb logs: shows if Telegram (or anything) is still hitting webhook
        print(f"🌐 /webhook hit: method={request.method} content_type={request.content_type}")
    except Exception:
        pass

    # We use Pyrogram polling, not webhook mode.
    # This route exists to prevent 404 errors if webhook is accidentally set.
    return jsonify({"ok": True, "message": "Bot uses polling mode, not webhook"})


@flask_app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "user_clients": len(user_clients),
        "bot_client": bot_client is not None,
        "is_forwarding": is_forwarding
    })


@flask_app.route("/progress")
def get_progress():
    load_progress()
    return jsonify(current_progress)


@flask_app.route("/accounts")
def get_accounts():
    return jsonify({
        "count": len(user_clients),
        "accounts": [name for name, _ in user_clients],
        "expected_speed": f"{len(user_clients) * 30}/min"
    })


@flask_app.route("/admin-groups")
def get_admin_groups():
    """Get all groups where bot is admin"""
    try:
        if admin_groups_col is None:
            return jsonify({"error": "Database not connected", "groups": []})
        
        groups = list(admin_groups_col.find({}).sort("updated_at", -1))
        
        # Convert ObjectId to string for JSON serialization
        for group in groups:
            group["_id"] = str(group["_id"])
            if "updated_at" in group:
                group["updated_at"] = group["updated_at"].isoformat() if group["updated_at"] else None
        
        return jsonify({
            "count": len(groups),
            "groups": groups
        })
    except Exception as e:
        return jsonify({"error": str(e), "groups": []})


@flask_app.route("/refresh-admin-groups", methods=["POST"])
def trigger_refresh_admin_groups():
    """Trigger refresh of admin groups list"""
    try:
        # Run async function in the event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def do_refresh():
            return await refresh_admin_groups()

        result = loop.run_until_complete(do_refresh())
        loop.close()

        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@flask_app.route("/generate-invite-link", methods=["POST"])
def generate_invite_link():
    """Generate (or fetch) a clickable join link for a specific group.

    Notes:
    - For private groups, Telegram only provides clickable access via invite link.
    - This requires the bot admin right: "Invite users".
    """
    try:
        payload = request.get_json(silent=True) or {}
        chat_id_raw = payload.get("chat_id")
        try:
            chat_id = int(chat_id_raw)
        except Exception:
            chat_id = 0

        if not chat_id:
            return jsonify({"success": False, "error": "chat_id is required"}), 400

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def do_generate():
            username, invite_link = await _get_best_join_link(chat_id)

            # Also report current permission (helps UI explain why link is missing)
            can_invite = None
            try:
                bot_me = await bot_client.get_me()
                member = await bot_client.get_chat_member(chat_id, bot_me.id)
                can_invite = bool(getattr(getattr(member, "privileges", None), "can_invite_users", False))
            except Exception:
                pass

            # Persist so the dashboard can show it immediately
            if admin_groups_col is not None:
                try:
                    admin_groups_col.update_one(
                        {"chat_id": chat_id},
                        {"$set": {"username": username or None, "invite_link": invite_link or None, "updated_at": datetime.utcnow()}},
                        upsert=True,
                    )
                except Exception:
                    pass

            link = invite_link or (f"https://t.me/{username}" if username else "")
            return {
                "username": username or None,
                "invite_link": invite_link or None,
                "link": link or None,
                "can_invite_users": can_invite,
                "success": bool(link),
            }

        result = loop.run_until_complete(do_generate())
        loop.close()

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})



def run_flask():
    """Run Flask in a separate thread"""
    port = int(os.getenv("PORT", 8000))
    flask_app.run(host="0.0.0.0", port=port, debug=False)


async def bot_watchdog():
    """Keep the bot reliably receiving updates.

    On some hosts, multiple instances or brief network drops can cause polling to stop.
    This task periodically checks connection state and tries to self-recover.
    """
    global bot_client

    while True:
        await asyncio.sleep(25)

        if bot_client is None:
            continue

        try:
            # If disconnected, try a clean restart
            if not getattr(bot_client, "is_connected", False):
                print("⚠️ bot_client disconnected — restarting...")
                try:
                    await bot_client.stop()
                except Exception:
                    pass
                await bot_client.start()
                try:
                    await bot_client.delete_webhook(drop_pending_updates=False)
                except Exception:
                    pass
                print("✅ bot_client restarted")
            else:
                # Light touch keepalive
                await bot_client.get_me()
        except Exception as e:
            print(f"⚠️ bot_watchdog error: {e} — attempting recovery")
            try:
                await bot_client.stop()
            except Exception:
                pass
            try:
                await asyncio.sleep(3)
                await bot_client.start()
                try:
                    await bot_client.delete_webhook(drop_pending_updates=False)
                except Exception:
                    pass
                print("✅ bot_client recovered")
            except Exception as e2:
                print(f"❌ bot_client recovery failed: {e2}")


async def shutdown_clients():
    """Gracefully stop all clients (prevents AUTH_KEY_DUPLICATED on quick redeploys)."""
    global user_clients, bot_client, bot_watchdog_task

    # Stop watchdog first
    if bot_watchdog_task is not None:
        try:
            bot_watchdog_task.cancel()
        except Exception:
            pass
        bot_watchdog_task = None

    # Stop user clients
    for name, c in list(user_clients):
        try:
            await c.stop()
            print(f"🛑 Stopped {name}")
        except Exception as e:
            print(f"⚠️ Could not stop {name}: {e}")

    user_clients = []

    # Stop bot client
    if bot_client is not None:
        try:
            await bot_client.stop()
            print("🛑 Stopped bot client")
        except Exception as e:
            print(f"⚠️ Could not stop bot client: {e}")
        finally:
            bot_client = None


async def main():
    """Main entry point"""
    print("=" * 50)
    print("🚀 Telegram Forwarder Bot (Multi-Account MTProto)")
    print("=" * 50)

    # Start Flask FIRST so health check passes immediately
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"🌐 Flask server started on port {os.getenv('PORT', 8000)}")

    # Small delay to ensure Flask is listening before Koyeb health check
    import time
    time.sleep(2)

    # Load saved progress
    load_progress()

    # Initialize clients (this can take time, but Flask is already up)
    await init_clients()

    # Start bot watchdog (auto-recovers if polling stops)
    global bot_watchdog_task
    if bot_client is not None and bot_watchdog_task is None:
        bot_watchdog_task = asyncio.create_task(bot_watchdog())
        print("🛡️ Bot watchdog enabled")

    # Webhook clearing is handled via bot_client.delete_webhook() during init_clients()
    # (keeps dependencies minimal and avoids silent failures)

    print("\n✅ Bot is running!")
    print(f"👥 Total accounts: {len(user_clients)}")
    print(f"⚡ Expected speed: ~{len(user_clients) * 30}/min")
    print("=" * 50)

    # Ensure graceful disconnect on redeploy/termination
    loop = asyncio.get_running_loop()

    def _on_term(_sig, _frame):
        loop.create_task(shutdown_clients())

    try:
        signal.signal(signal.SIGTERM, _on_term)
        signal.signal(signal.SIGINT, _on_term)
    except Exception:
        pass

    try:
        # Use Pyrogram's idle to keep bot running and processing updates
        await idle()
    finally:
        await shutdown_clients()


if __name__ == "__main__":
    asyncio.run(main())
