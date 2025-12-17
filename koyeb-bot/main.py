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
    
    @bot_client.on_message(filters.command("start"))
    async def start_handler(client, message):
        num_accounts = len(user_clients)
        expected_speed = num_accounts * 30 if num_accounts else 0
        
        # Inline keyboard buttons
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📤 Forward", callback_data="forward"),
                InlineKeyboardButton("📢 Channel", callback_data="channel")
            ],
            [
                InlineKeyboardButton("🔞 Porn", callback_data="porn"),
                InlineKeyboardButton("🆘 @Admin", callback_data="admin")
            ],
            [
                InlineKeyboardButton("📥 Join Request", callback_data="join_request"),
                InlineKeyboardButton("📁 File Logo", callback_data="file_logo")
            ],
            [
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
    
    @bot_client.on_callback_query()
    async def callback_handler(client, callback_query):
        data = callback_query.data
        
        if data == "forward":
            await callback_query.message.reply(
                "📤 **Forward Messages**\n\n"
                "1️⃣ Set config: /setconfig <source> <dest>\n"
                "2️⃣ Start: /forward <start_id> <end_id>\n"
                "3️⃣ Resume: /resume\n"
                "4️⃣ Stop: /stop"
            )
        elif data == "channel":
            await callback_query.message.reply(
                "📢 **Channel Setup**\n\n"
                "Use /setconfig to set source and destination channels.\n"
                "Example: /setconfig @source_channel @dest_channel"
            )
        elif data == "porn":
            await callback_query.message.reply(
                "🔞 **Adult Content Mode**\n\n"
                "Forward adult content between channels.\n"
                "Make sure destination channel allows such content."
            )
        elif data == "admin":
            await callback_query.message.reply(
                "🆘 **Contact Admin**\n\n"
                "For support, contact: @YourAdminUsername"
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
                "/start - Show main menu\n"
                "/setconfig - Set channels\n"
                "/forward - Start forwarding\n"
                "/resume - Resume forwarding\n"
                "/stop - Stop forwarding\n"
                "/progress - Show progress\n"
                "/status - Show status\n"
                "/accounts - Show connected accounts"
            )
        
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
