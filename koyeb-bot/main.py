import os
import asyncio
import time
import io
from datetime import datetime
from flask import Flask, request, jsonify
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, SlowmodeWait, ChatAdminRequired, ChannelPrivate
from pymongo import MongoClient
from dotenv import load_dotenv
import threading
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

# Flask app for health checks
flask_app = Flask(__name__)

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
logo_col = db["logo_config"] if db is not None else None
moderation_col = db["group_moderation"] if db is not None else None
warnings_col = db["user_warnings"] if db is not None else None
user_channels_col = db["user_channels"] if db is not None else None
force_sub_col = db["force_subscribe"] if db is not None else None

# User state for channel input
user_channel_state = {}  # {user_id: "waiting_add_channel"}

# Forward wizard state
forward_wizard_state = {}  # {user_id: {"state": "...", "source_channel": "", "source_title": "", "skip_number": 0, "last_message_id": 0}}

# Active forwarding progress per user
user_forward_progress = {}  # {user_id: {progress data...}}

# Force subscribe channels list (loaded from DB)
force_subscribe_channels = []  # [{"channel_id": "", "channel_name": "", "invite_link": ""}]

# User account credentials (MTProto)
API_ID = os.getenv("API_ID", "")
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""


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
moderation_config = {}  # {chat_id: {block_forward, block_links, block_badwords, block_mentions, enabled}}
moderation_stats = {"deleted_forward": 0, "deleted_links": 0, "deleted_badwords": 0, "deleted_mentions": 0, "warnings": 0, "bans": 0}
user_warnings = {}  # {(chat_id, user_id): warning_count}
MAX_WARNINGS = 3  # Auto-ban after this many warnings

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
                "block_mentions": saved.get("block_mentions", False)
            }
            return moderation_config[chat_id]
    return {"enabled": False, "block_forward": False, "block_links": False, "block_badwords": False, "block_mentions": False}


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
    """Load force subscribe channels from database"""
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
    return False


def remove_force_subscribe(channel_id):
    """Remove a force subscribe channel"""
    global force_subscribe_channels
    if force_sub_col is not None:
        force_sub_col.delete_one({"channel_id": str(channel_id)})
        force_subscribe_channels = [ch for ch in force_subscribe_channels if ch["channel_id"] != str(channel_id)]
        return True
    return False


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
            else:
                chat_id = channel_id
            
            member = await client.get_chat_member(chat_id, user_id)
            if member.status in ["left", "kicked", "banned"]:
                not_joined.append(channel)
        except Exception as e:
            # If we can't check, assume not joined
            not_joined.append(channel)
    
    return len(not_joined) == 0, not_joined


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


async def init_clients():
    """Initialize Pyrogram clients - supports unlimited accounts!"""
    global user_clients, bot_client, auto_approve_channels
    
    # Load auto-approve channels from database
    if autoapprove_col is not None:
        enabled_channels = autoapprove_col.find({"enabled": True})
        for doc in enabled_channels:
            auto_approve_channels.add(doc["channel"])
        print(f"📥 Loaded {len(auto_approve_channels)} auto-approve channels")
    
    # Load logo config from database
    load_logo_config()
    if logo_config.get("enabled"):
        print(f"🖼️ Logo watermark enabled")
    
    # Get all session strings from environment
    session_strings = get_all_session_strings()
    
    print(f"🔍 Found {len(session_strings)} session string(s)")
    
    # Initialize user clients for fast forwarding (MTProto)
    if session_strings and API_ID and API_HASH:
        for idx, (name, session_string) in enumerate(session_strings):
            try:
                client = Client(
                    f"user_session_{idx}",
                    api_id=int(API_ID),
                    api_hash=API_HASH,
                    session_string=session_string
                )
                await client.start()
                user_clients.append((name, client))
                print(f"✅ {name} connected!")
            except Exception as e:
                print(f"❌ Failed to start {name}: {e}")
    
    print(f"🚀 Total active accounts: {len(user_clients)}")
    
    # Calculate expected speed
    if user_clients:
        expected_speed = len(user_clients) * 30  # ~30 msgs/min per account
        print(f"⚡ Expected forwarding speed: ~{expected_speed}/min")
    
    # Bot client for commands
    if BOT_TOKEN and API_ID and API_HASH:
        bot_client = Client(
            "bot_session",
            api_id=int(API_ID),
            api_hash=API_HASH,
            bot_token=BOT_TOKEN
        )
        await bot_client.start()
        print("🤖 Bot client started")
        
        # Register handlers
        register_bot_handlers()


def register_bot_handlers():
    """Register bot command handlers"""
    
    # Load force subscribe channels on startup
    load_force_subscribe()
    
    @bot_client.on_message(filters.command("start"))
    async def start_handler(client, message):
        user_id = message.from_user.id
        
        # Check force subscribe
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
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                return
        
        # User has joined all or no force subscribe
        num_accounts = len(user_clients)
        expected_speed = num_accounts * 30 if num_accounts else 0
        
        # Inline keyboard buttons
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📤 Forward", callback_data="forward"),
                InlineKeyboardButton("📢 Channel", callback_data="channel")
            ],
            [
                InlineKeyboardButton("🔍 Filters", callback_data="filters_menu"),
                InlineKeyboardButton("🛡️ Moderation", callback_data="moderation")
            ],
            [
                InlineKeyboardButton("🆘 @Admin", callback_data="admin"),
                InlineKeyboardButton("📥 Join Request", callback_data="join_request")
            ],
            [
                InlineKeyboardButton("📁 File Logo", callback_data="file_logo"),
                InlineKeyboardButton("❓ Help", callback_data="help")
            ]
        ])
        
        await message.reply(
            f"🚀 **Telegram Forwarder Bot (Multi-Account MTProto)**\n\n"
            f"👥 Active accounts: {num_accounts}\n"
            f"⚡ Expected speed: ~{expected_speed}/min\n\n"
            f"Select an option below or use commands:",
            reply_markup=keyboard
        )
    
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
        
        # Handle force subscribe verification
        if data == "check_joined":
            user_id = callback_query.from_user.id
            is_joined, not_joined = await check_user_joined(client, user_id)
            
            if is_joined:
                # User has joined all channels, show main menu
                num_accounts = len(user_clients)
                expected_speed = num_accounts * 30 if num_accounts else 0
                
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📤 Forward", callback_data="forward"),
                        InlineKeyboardButton("📢 Channel", callback_data="channel")
                    ],
                    [
                        InlineKeyboardButton("🔍 Filters", callback_data="filters_menu"),
                        InlineKeyboardButton("🛡️ Moderation", callback_data="moderation")
                    ],
                    [
                        InlineKeyboardButton("🆘 @Admin", callback_data="admin"),
                        InlineKeyboardButton("📥 Join Request", callback_data="join_request")
                    ],
                    [
                        InlineKeyboardButton("📁 File Logo", callback_data="file_logo"),
                        InlineKeyboardButton("❓ Help", callback_data="help")
                    ]
                ])
                
                await callback_query.message.edit_text(
                    f"✅ **Verification Successful!**\n\n"
                    f"🚀 **Telegram Forwarder Bot**\n\n"
                    f"👥 Active accounts: {num_accounts}\n"
                    f"⚡ Expected speed: ~{expected_speed}/min\n\n"
                    f"Select an option below:",
                    reply_markup=keyboard
                )
            else:
                # Still not joined
                buttons = []
                for channel in not_joined:
                    link = channel.get("invite_link") or f"https://t.me/{channel['channel_id'].replace('@', '').replace('-', '')}"
                    buttons.append([InlineKeyboardButton(f"📢 Join {channel['channel_name']}", url=link)])
                
                buttons.append([InlineKeyboardButton("✅ Joined All - Verify", callback_data="check_joined")])
                
                await callback_query.message.edit_text(
                    "❌ **Not Joined Yet!**\n\n"
                    f"You still need to join **{len(not_joined)}** channel(s):\n\n"
                    "👇 Click below to join, then click **Verify** again:",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
            
            await callback_query.answer()
            return
        
        if data == "forward":
            user_id = callback_query.from_user.id
            
            # Check if user has accounts connected
            if not user_clients:
                await callback_query.message.reply("❌ No user accounts connected!")
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
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_forward")]
            ])
            
            await callback_query.message.reply(
                "**( SET SOURCE CHAT )**\n\n"
                "Forward the last message or last message link of source chat.\n"
                "/cancel - cancel this process",
                reply_markup=cancel_keyboard
            )
        elif data == "channel":
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
                [InlineKeyboardButton("back", callback_data="back_main")]
            ])
            
            await callback_query.message.reply(
                f"📢 **My Channels**\n\n"
                f"you can manage your target chats in here\n\n"
                f"**Your Channels ({len(user_channels)}):**\n{channels_text}",
                reply_markup=channel_keyboard
            )
        elif data == "add_channel":
            user_id = callback_query.from_user.id
            user_channel_state[user_id] = "waiting_add_channel"
            await callback_query.message.reply(
                "📢 **Add Channel**\n\n"
                "Send me the channel/chat username or link:\n\n"
                "Examples:\n"
                "• @channelname\n"
                "• https://t.me/channelname\n"
                "• -1001234567890\n\n"
                "Just send the message below 👇"
            )
        elif data == "remove_channel":
            user_id = callback_query.from_user.id
            user_channels = []
            if user_channels_col is not None:
                saved = user_channels_col.find({"user_id": user_id})
                user_channels = [c.get("channel") for c in saved if c.get("channel")]
            
            if not user_channels:
                await callback_query.message.reply("❌ No channels to remove!")
                return
            
            # Create buttons for each channel to remove
            buttons = [[InlineKeyboardButton(f"🗑️ {ch}", callback_data=f"del_ch_{ch}")] for ch in user_channels[:10]]
            buttons.append([InlineKeyboardButton("back", callback_data="channel")])
            
            await callback_query.message.reply(
                "🗑️ **Remove Channel**\n\n"
                "Select a channel to remove:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        elif data.startswith("del_ch_"):
            channel_to_delete = data.replace("del_ch_", "")
            user_id = callback_query.from_user.id
            
            if user_channels_col is not None:
                user_channels_col.delete_one({"user_id": user_id, "channel": channel_to_delete})
            
            await callback_query.message.reply(f"✅ Channel `{channel_to_delete}` removed!")
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
                    InlineKeyboardButton("🛡️ Moderation", callback_data="moderation")
                ],
                [
                    InlineKeyboardButton("🆘 @Admin", callback_data="admin"),
                    InlineKeyboardButton("📥 Join Request", callback_data="join_request")
                ],
                [
                    InlineKeyboardButton("📁 File Logo", callback_data="file_logo"),
                    InlineKeyboardButton("❓ Help", callback_data="help")
                ]
            ])
            
            await callback_query.message.reply(
                f"🚀 **Telegram Forwarder Bot**\n\n"
                f"👥 Connected accounts: {num_accounts}\n"
                f"⚡ Expected speed: ~{expected_speed} msg/min\n\n"
                "Select an option below:",
                reply_markup=keyboard
            )
        elif data == "moderation":
            await callback_query.message.reply(
                "🛡️ **Content Moderation**\n\n"
                "Add bot as admin in your group, then use:\n\n"
                "**Commands (in group):**\n"
                "/enablemod - Enable moderation\n"
                "/disablemod - Disable moderation\n"
                "/blockforward - Block forwarded messages\n"
                "/blocklinks - Block links/URLs/usernames\n"
                "/blockbadwords - 🔞 Block sex/adult content\n"
                "/modstatus - View moderation settings\n"
                "/warnings - Check user warnings\n"
                "/resetwarnings - Reset user warnings (admin)\n\n"
                "🔞 **Sex Content Filter:**\n"
                "Use /blockbadwords to auto-delete:\n"
                "• Sex messages (sex, porn, xxx, etc.)\n"
                "• Adult content & Abusive words\n\n"
                "⚠️ **Auto-Ban System:**\n"
                "• 3 warnings = Automatic BAN\n"
                "• Warning given on each violation\n"
                "• Admins are exempt from all filters"
            )
        elif data == "admin":
            await callback_query.message.reply(
                "🆘 **Admin Controls - Block @Mentions**\n\n"
                "Block @username, @bot, @channel mentions in group!\n\n"
                "**Commands (in group):**\n"
                "/blockmention - Toggle @mention blocking\n"
                "/enablemod - Enable moderation first\n"
                "/modstatus - View all settings\n\n"
                "⚡ When enabled, any message with @username will be deleted!\n"
                "👮 Admins are exempt from this filter."
            )
        elif data == "join_request":
            channels_list = "\n".join([f"• `{ch}`" for ch in auto_approve_channels]) if auto_approve_channels else "None"
            await callback_query.message.reply(
                "📥 **Join Request Auto-Approve**\n\n"
                "📢 Works for both **Channels & Groups**!\n\n"
                f"**Status:** {'🟢 Active' if auto_approve_channels else '🔴 Inactive'}\n"
                f"**Total:** {len(auto_approve_channels)}\n"
                f"✅ Approved: {auto_approve_stats['approved']}\n"
                f"❌ Failed: {auto_approve_stats['failed']}\n\n"
                f"**Active Channels/Groups:**\n{channels_list}\n\n"
                "**Commands:**\n"
                "/autoapprove <channel/group> - Enable\n"
                "/stopapprove <channel/group> - Disable\n"
                "/approveall <channel/group> - Accept all pending\n"
                "/approvelist - Show all enabled"
            )
        elif data == "file_logo":
            await callback_query.message.reply(
                "🖼️ **File Logo / Watermark**\n\n"
                f"**Status:** {'🟢 Enabled' if logo_config.get('enabled') else '🔴 Disabled'}\n"
                f"**Logo:** {'✅ Set' if logo_config.get('logo_file_id') else '❌ Not set'}\n"
                f"**Text:** {logo_config.get('text') or 'Not set'}\n"
                f"**Position:** {logo_config.get('position', 'bottom-right')}\n"
                f"**Opacity:** {logo_config.get('opacity', 128)}/255\n"
                f"**Size:** {logo_config.get('size', 20)}%\n\n"
                f"📊 **Stats:**\n"
                f"✅ Watermarked: {logo_stats['watermarked']}\n"
                f"❌ Failed: {logo_stats['failed']}\n\n"
                "**Commands:**\n"
                "/setlogo - Reply to image to set logo\n"
                "/setlogotext <text> - Set text watermark\n"
                "/logoposition <pos> - Set position\n"
                "/logosize <1-50> - Set size %\n"
                "/logoopacity <0-255> - Set opacity\n"
                "/enablelogo - Enable watermark\n"
                "/disablelogo - Disable watermark\n"
                "/removelogo - Remove logo\n"
                "/logoinfo - Show logo settings"
            )
        elif data == "help":
            await callback_query.message.reply(
                "❓ **Help Menu**\n\n"
                "**📤 Forwarding:**\n"
                "/start - Show main menu\n"
                "/setconfig - Set channels\n"
                "/forward - Start forwarding\n"
                "/resume - Resume forwarding\n"
                "/stop - Stop forwarding\n"
                "/progress - Show progress\n"
                "/status - Show status\n"
                "/accounts - Show accounts\n\n"
                "**🛡️ Moderation (in groups):**\n"
                "/enablemod - Enable moderation\n"
                "/blockforward - Block forwards\n"
                "/blocklinks - Block links\n"
                "/blockbadwords - Block bad content\n"
                "/modstatus - View settings"
            )
        elif data == "filters_menu":
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
            
            await callback_query.message.reply(
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
            
            await callback_query.message.reply(
                f"**{info[0]}**\n\n"
                f"📋 **What it filters:**\n{info[1]}\n\n"
                f"📌 **Examples:**\n{info[2]}\n\n"
                f"⚡ **To use this filter:**\n"
                f"Start forwarding → Select this filter → ✅",
                reply_markup=back_keyboard
            )
        elif data == "cancel_forward":
            user_id = callback_query.from_user.id
            forward_wizard_state.pop(user_id, None)
            await callback_query.message.reply("❌ Forwarding cancelled!")
        elif data.startswith("select_dest_"):
            # User selected a destination channel
            user_id = callback_query.from_user.id
            channel_idx = int(data.replace("select_dest_", ""))
            
            if user_id not in forward_wizard_state:
                await callback_query.message.reply("❌ Session expired. Please start again.")
                return
            
            # Get user's channels
            user_channels = []
            if user_channels_col is not None:
                saved = user_channels_col.find({"user_id": user_id})
                user_channels = [c.get("channel") for c in saved if c.get("channel")]
            
            if channel_idx >= len(user_channels):
                await callback_query.message.reply("❌ Invalid channel!")
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
                await callback_query.message.reply("❌ Session expired. Please start again.")
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
        elif data == "filters_done":
            # User finished selecting filters, show destination channels
            user_id = callback_query.from_user.id
            
            if user_id not in forward_wizard_state:
                await callback_query.message.reply("❌ Session expired. Please start again.")
                return
            
            wizard = forward_wizard_state[user_id]
            wizard["state"] = "waiting_dest"
            
            # Get user's saved channels
            user_channels = []
            if user_channels_col is not None:
                saved = user_channels_col.find({"user_id": user_id})
                user_channels = [c.get("channel") for c in saved if c.get("channel")]
            
            if not user_channels:
                await callback_query.message.reply(
                    "❌ No destination channels saved!\n\n"
                    "Please add channels first using:\n"
                    "/start → 📢 Channel → Add Channel"
                )
                forward_wizard_state.pop(user_id, None)
                return
            
            # Create buttons for each channel
            buttons = [[InlineKeyboardButton(f"📁 {ch}", callback_data=f"select_dest_{i}")] for i, ch in enumerate(user_channels[:10])]
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_forward")])
            
            await callback_query.message.reply(
                f"**( SELECT DESTINATION CHAT )**\n\n"
                f"Select a channel from your saved channels:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        elif data == "cancel_fwd_active":
            user_id = callback_query.from_user.id
            if user_id in user_forward_progress:
                user_forward_progress[user_id]["is_active"] = False
                user_forward_progress[user_id]["status"] = "Cancelled"
            forward_wizard_state.pop(user_id, None)
            await callback_query.message.reply("🛑 Forwarding cancelled!")
        
        await callback_query.answer()
    
    @bot_client.on_message(filters.command("accounts"))
    async def accounts_handler(client, message):
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
        global stop_requested
        stop_requested = True
        await message.reply("🛑 Stop requested...")
    
    @bot_client.on_message(filters.command("progress"))
    async def progress_handler(client, message):
        load_progress()
        
        total = current_progress["total_count"]
        done = current_progress["success_count"] + current_progress["failed_count"] + current_progress["skipped_count"]
        pct = round((done / total * 100), 1) if total > 0 else 0
        
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
    
    @bot_client.on_message(filters.command("status"))
    async def status_handler(client, message):
        config = get_config()
        num_accounts = len(user_clients)
        expected_speed = num_accounts * 30 if num_accounts else 0
        
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
    
    @bot_client.on_message(filters.command("enablemod") & filters.group)
    async def enablemod_handler(client, message):
        """Enable content moderation in this group"""
        global moderation_config
        
        chat_id = message.chat.id
        
        # Check if user is admin
        try:
            member = await client.get_chat_member(chat_id, message.from_user.id)
            if member.status not in ["administrator", "creator"]:
                await message.reply("❌ Only admins can enable moderation!")
                return
        except:
            pass
        
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
    
    @bot_client.on_message(filters.command("disablemod") & filters.group)
    async def disablemod_handler(client, message):
        """Disable content moderation"""
        global moderation_config
        
        chat_id = message.chat.id
        
        # Check if user is admin
        try:
            member = await client.get_chat_member(chat_id, message.from_user.id)
            if member.status not in ["administrator", "creator"]:
                await message.reply("❌ Only admins can disable moderation!")
                return
        except:
            pass
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        moderation_config[chat_id]["enabled"] = False
        save_moderation_config(chat_id)
        
        await message.reply("🔴 **Content Moderation Disabled!**")
    
    @bot_client.on_message(filters.command("blockforward") & filters.group)
    async def blockforward_handler(client, message):
        """Toggle blocking forwarded messages"""
        global moderation_config
        
        chat_id = message.chat.id
        
        # Check if user is admin
        try:
            member = await client.get_chat_member(chat_id, message.from_user.id)
            if member.status not in ["administrator", "creator"]:
                await message.reply("❌ Only admins can change this!")
                return
        except:
            pass
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        current = moderation_config[chat_id].get("block_forward", False)
        moderation_config[chat_id]["block_forward"] = not current
        moderation_config[chat_id]["enabled"] = True
        save_moderation_config(chat_id)
        
        status = "🟢 ON" if not current else "🔴 OFF"
        await message.reply(f"📨 **Block Forwarded Messages:** {status}")
    
    @bot_client.on_message(filters.command("blocklinks") & filters.group)
    async def blocklinks_handler(client, message):
        """Toggle blocking messages with links"""
        global moderation_config
        
        chat_id = message.chat.id
        
        # Check if user is admin
        try:
            member = await client.get_chat_member(chat_id, message.from_user.id)
            if member.status not in ["administrator", "creator"]:
                await message.reply("❌ Only admins can change this!")
                return
        except:
            pass
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        current = moderation_config[chat_id].get("block_links", False)
        moderation_config[chat_id]["block_links"] = not current
        moderation_config[chat_id]["enabled"] = True
        save_moderation_config(chat_id)
        
        status = "🟢 ON" if not current else "🔴 OFF"
        await message.reply(f"🔗 **Block Links/URLs:** {status}")
    
    @bot_client.on_message(filters.command("blockbadwords") & filters.group)
    async def blockbadwords_handler(client, message):
        """Toggle blocking inappropriate content"""
        global moderation_config
        
        chat_id = message.chat.id
        
        # Check if user is admin
        try:
            member = await client.get_chat_member(chat_id, message.from_user.id)
            if member.status not in ["administrator", "creator"]:
                await message.reply("❌ Only admins can change this!")
                return
        except:
            pass
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        current = moderation_config[chat_id].get("block_badwords", False)
        moderation_config[chat_id]["block_badwords"] = not current
        moderation_config[chat_id]["enabled"] = True
        save_moderation_config(chat_id)
        
        status = "🟢 ON" if not current else "🔴 OFF"
        await message.reply(f"🚫 **Block Inappropriate Content:** {status}")
    
    @bot_client.on_message(filters.command("blockmention") & filters.group)
    async def blockmention_handler(client, message):
        """Toggle blocking @mentions"""
        global moderation_config
        
        chat_id = message.chat.id
        
        # Check if user is admin
        try:
            member = await client.get_chat_member(chat_id, message.from_user.id)
            if member.status not in ["administrator", "creator"]:
                await message.reply("❌ Only admins can change this!")
                return
        except:
            pass
        
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        current = moderation_config[chat_id].get("block_mentions", False)
        moderation_config[chat_id]["block_mentions"] = not current
        moderation_config[chat_id]["enabled"] = True
        save_moderation_config(chat_id)
        
        status = "🟢 ON" if not current else "🔴 OFF"
        await message.reply(f"📛 **Block @Mentions:** {status}\n\nAll @username, @bot, @channel mentions will be deleted!")
    
    @bot_client.on_message(filters.command("modstatus") & filters.group)
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
            f"**Block @Mentions:** {'🟢 ON' if config.get('block_mentions') else '🔴 OFF'}\n\n"
            f"📊 **Stats:**\n"
            f"📨 Deleted forwards: {moderation_stats['deleted_forward']}\n"
            f"🔗 Deleted links: {moderation_stats['deleted_links']}\n"
            f"🚫 Deleted bad words: {moderation_stats['deleted_badwords']}\n"
            f"📛 Deleted mentions: {moderation_stats['deleted_mentions']}\n"
            f"⚠️ Total warnings: {moderation_stats['warnings']}\n"
            f"🔨 Auto-bans: {moderation_stats['bans']}"
        )
    
    # ============ PRIVATE MESSAGE HANDLER FOR CHANNEL INPUT ============
    
    @bot_client.on_message(filters.private & ~filters.command(["start", "setconfig", "forward", "stop", "progress", "status", "setlogo", "setlogotext", "logoposition", "logosize", "logoopacity", "enablelogo", "disablelogo", "removelogo", "logoinfo", "autoapprove", "stopapprove", "approvelist", "approveall", "cancel"]))
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
    
    # ============ CONTENT MODERATION MESSAGE FILTER ============
    
    @bot_client.on_message(filters.group & ~filters.command(["enablemod", "disablemod", "blockforward", "blocklinks", "blockbadwords", "blockmention", "modstatus", "warnings", "resetwarnings"]))
    async def moderation_filter_handler(client, message):
        """Filter and delete inappropriate messages with warning system"""
        global moderation_stats, user_warnings
        
        chat_id = message.chat.id
        user_id = message.from_user.id
        
        # Load config if not in memory
        if chat_id not in moderation_config:
            moderation_config[chat_id] = load_moderation_config(chat_id)
        
        config = moderation_config.get(chat_id, {})
        
        # Skip if moderation is disabled
        if not config.get("enabled"):
            return
        
        # Skip if user is admin
        try:
            member = await client.get_chat_member(chat_id, user_id)
            if member.status in ["administrator", "creator"]:
                return
        except:
            pass
        
        async def add_warning_and_check_ban(reason):
            """Add warning to user and ban if exceeded limit"""
            global user_warnings, moderation_stats
            
            key = (chat_id, user_id)
            
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
            
            # Check if should ban
            if current_warnings >= MAX_WARNINGS:
                try:
                    await client.ban_chat_member(chat_id, user_id)
                    moderation_stats["bans"] += 1
                    await client.send_message(
                        chat_id,
                        f"🚫 **Auto-Ban:** {user_name}\n"
                        f"Reason: {MAX_WARNINGS} warnings exceeded\n"
                        f"Last violation: {reason}"
                    )
                    # Reset warnings after ban
                    user_warnings[key] = 0
                    if warnings_col is not None:
                        warnings_col.update_one(
                            {"chat_id": chat_id, "user_id": user_id},
                            {"$set": {"count": 0, "banned": True}}
                        )
                    print(f"🔨 Auto-banned {user_name} after {MAX_WARNINGS} warnings")
                except Exception as e:
                    print(f"Failed to ban user: {e}")
            else:
                # Send warning message
                remaining = MAX_WARNINGS - current_warnings
                await client.send_message(
                    chat_id,
                    f"⚠️ **Warning {current_warnings}/{MAX_WARNINGS}:** {user_name}\n"
                    f"Reason: {reason}\n"
                    f"⛔ {remaining} more warning{'s' if remaining > 1 else ''} = Auto-Ban!"
                )
        
        try:
            # Check for forwarded messages
            if config.get("block_forward") and message.forward_date:
                await message.delete()
                moderation_stats["deleted_forward"] += 1
                await add_warning_and_check_ban("Forwarded message")
                return
            
            # Get message text
            text = message.text or message.caption or ""
            
            # Check for links
            if config.get("block_links") and text and contains_link(text):
                await message.delete()
                moderation_stats["deleted_links"] += 1
                await add_warning_and_check_ban("Link/URL not allowed")
                return
            
            # Check for bad words
            if config.get("block_badwords") and text and contains_bad_words(text):
                await message.delete()
                moderation_stats["deleted_badwords"] += 1
                await add_warning_and_check_ban("Inappropriate/sexual content")
                return
            
            # Check for @mentions
            if config.get("block_mentions") and text and contains_mention(text):
                await message.delete()
                moderation_stats["deleted_mentions"] += 1
                await add_warning_and_check_ban("@mentions not allowed")
                return
                
        except Exception as e:
            print(f"Moderation error: {e}")
    
    @bot_client.on_message(filters.command("warnings") & filters.group)
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
    
    @bot_client.on_message(filters.command("resetwarnings") & filters.group)
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
    
    @bot_client.on_message(filters.command("approveall"))
    async def approveall_handler(client, message):
        """Approve all pending join requests for a channel/group"""
        global auto_approve_stats
        
        if not user_clients:
            await message.reply("❌ No user accounts connected!")
            return
        
        try:
            parts = message.text.split()
            if len(parts) != 2:
                await message.reply(
                    "Usage: /approveall <channel/group>\n\n"
                    "Examples:\n"
                    "• /approveall @mychannel\n"
                    "• /approveall @mygroup\n"
                    "• /approveall -1001234567890"
                )
                return
            
            channel = parts[1]
            user_client = user_clients[0][1]  # Use first user client
            
            await message.reply(f"🔄 Approving all pending requests for {channel}...")
            
            approved = 0
            failed = 0
            
            try:
                # Get chat to get chat_id
                chat = await user_client.get_chat(channel)
                
                # Iterate through pending join requests
                async for request in user_client.get_chat_join_requests(chat.id):
                    try:
                        await user_client.approve_chat_join_request(chat.id, request.user.id)
                        approved += 1
                        auto_approve_stats["approved"] += 1
                        
                        # Small delay to avoid rate limits
                        await asyncio.sleep(0.5)
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                        try:
                            await user_client.approve_chat_join_request(chat.id, request.user.id)
                            approved += 1
                            auto_approve_stats["approved"] += 1
                        except:
                            failed += 1
                            auto_approve_stats["failed"] += 1
                    except Exception as e:
                        failed += 1
                        auto_approve_stats["failed"] += 1
                        print(f"Failed to approve {request.user.id}: {e}")
                
                await message.reply(
                    f"✅ **Approval Complete!**\n\n"
                    f"Channel: {channel}\n"
                    f"✅ Approved: {approved}\n"
                    f"❌ Failed: {failed}"
                )
            except Exception as e:
                await message.reply(f"❌ Error accessing channel: {e}")
        
        except Exception as e:
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
            
            auto_approve_stats["approved"] += 1
            print(f"✅ Auto-approved: {chat_join_request.from_user.first_name} for {chat_username}")
        
        except Exception as e:
            auto_approve_stats["failed"] += 1
            print(f"❌ Failed to auto-approve: {e}")


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


def run_flask():
    """Run Flask in a separate thread"""
    port = int(os.getenv("PORT", 8000))
    flask_app.run(host="0.0.0.0", port=port, debug=False)


async def main():
    """Main entry point"""
    print("=" * 50)
    print("🚀 Telegram Forwarder Bot (Multi-Account MTProto)")
    print("=" * 50)
    
    # Load saved progress
    load_progress()
    
    # Initialize clients
    await init_clients()
    
    # Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print("\n✅ Bot is running!")
    print(f"👥 Total accounts: {len(user_clients)}")
    print(f"⚡ Expected speed: ~{len(user_clients) * 30}/min")
    print("=" * 50)
    
    # Use Pyrogram's idle to keep bot running and processing updates
    await idle()


if __name__ == "__main__":
    asyncio.run(main())
