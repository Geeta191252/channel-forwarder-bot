import os
import asyncio
import time
from datetime import datetime
from flask import Flask, request, jsonify
from pyrogram import Client, filters, idle
from pyrogram.errors import FloodWait, SlowmodeWait, ChatAdminRequired, ChannelPrivate
from pymongo import MongoClient
from dotenv import load_dotenv
import threading

load_dotenv()

# Flask app for health checks
flask_app = Flask(__name__)

# MongoDB setup
MONGO_URI = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or ""
mongo_client = MongoClient(MONGO_URI) if MONGO_URI else None
db = mongo_client["telegram_forwarder"] if mongo_client else None

# Collections
sessions_col = db["user_sessions"] if db else None
progress_col = db["forwarding_progress"] if db else None
forwarded_col = db["forwarded_messages"] if db else None
config_col = db["bot_config"] if db else None

# User account credentials (MTProto)
API_ID = os.getenv("API_ID", "")
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")  # For user account
# Support common env var names used in deploy dashboards
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""  # For bot commands


# Speed settings - MTProto allows much faster speeds
BATCH_SIZE = 10  # Messages per batch
DELAY_BETWEEN_BATCHES = 2  # Seconds - gives ~300/min with 1 account
DELAY_BETWEEN_MESSAGES = 0.2  # 200ms between individual messages

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
    "rate_limit_hits": 0
}

# Pyrogram clients
user_client = None  # User account for forwarding (fast)
bot_client = None   # Bot for commands/UI


def get_config():
    """Get bot configuration from database"""
    if config_col:
        return config_col.find_one({}) or {}
    return {}


def save_config(source_channel, dest_channel):
    """Save bot configuration to database"""
    if config_col:
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
    if progress_col:
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
    if progress_col:
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
                "rate_limit_hits": saved.get("rate_limit_hits", 0)
            })


def is_message_forwarded(source_channel, message_id):
    """Check if message was already forwarded"""
    if forwarded_col:
        return forwarded_col.find_one({
            "source_channel": source_channel,
            "source_message_id": message_id
        }) is not None
    return False


def mark_message_forwarded(source_channel, dest_channel, message_id):
    """Mark message as forwarded"""
    if forwarded_col:
        forwarded_col.insert_one({
            "source_channel": source_channel,
            "dest_channel": dest_channel,
            "source_message_id": message_id,
            "forwarded_at": datetime.utcnow()
        })


async def forward_messages(source_channel, dest_channel, start_id, end_id, is_resume=False):
    """Forward messages using MTProto (user account) - FAST!"""
    global is_forwarding, stop_requested, current_progress, user_client
    
    if not user_client:
        print("User client not initialized!")
        return
    
    is_forwarding = True
    stop_requested = False
    
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
            "rate_limit_hits": 0
        }
    else:
        current_progress["is_active"] = True
    
    save_progress()
    
    current_id = current_progress["current_id"] if is_resume else start_id
    batch_start_time = time.time()
    batch_count = 0
    
    print(f"Starting forward: {source_channel} -> {dest_channel}, IDs: {current_id} to {end_id}")
    
    try:
        while current_id <= end_id and not stop_requested:
            # Process batch
            batch_ids = list(range(current_id, min(current_id + BATCH_SIZE, end_id + 1)))
            
            for msg_id in batch_ids:
                if stop_requested:
                    break
                
                # Check if already forwarded
                if is_message_forwarded(source_channel, msg_id):
                    current_progress["skipped_count"] += 1
                    current_progress["current_id"] = msg_id
                    continue
                
                try:
                    # Copy message using user account (MTProto)
                    await user_client.copy_message(
                        chat_id=dest_channel,
                        from_chat_id=source_channel,
                        message_id=msg_id
                    )
                    
                    current_progress["success_count"] += 1
                    mark_message_forwarded(source_channel, dest_channel, msg_id)
                    batch_count += 1
                    
                except FloodWait as e:
                    # Handle rate limit
                    print(f"FloodWait: sleeping {e.value} seconds")
                    current_progress["rate_limit_hits"] += 1
                    save_progress()
                    await asyncio.sleep(e.value)
                    
                    # Retry this message
                    try:
                        await user_client.copy_message(
                            chat_id=dest_channel,
                            from_chat_id=source_channel,
                            message_id=msg_id
                        )
                        current_progress["success_count"] += 1
                        mark_message_forwarded(source_channel, dest_channel, msg_id)
                        batch_count += 1
                    except Exception as retry_err:
                        print(f"Retry failed for {msg_id}: {retry_err}")
                        current_progress["failed_count"] += 1
                        
                except Exception as e:
                    error_msg = str(e).lower()
                    if "message" in error_msg and ("not found" in error_msg or "empty" in error_msg or "deleted" in error_msg):
                        current_progress["skipped_count"] += 1
                    else:
                        print(f"Error forwarding {msg_id}: {e}")
                        current_progress["failed_count"] += 1
                
                current_progress["current_id"] = msg_id
                
                # Small delay between messages
                await asyncio.sleep(DELAY_BETWEEN_MESSAGES)
            
            # Calculate speed
            elapsed = time.time() - batch_start_time
            if elapsed > 0:
                current_progress["speed"] = round((batch_count / elapsed) * 60, 1)  # msgs/min
            
            # Save progress after each batch
            save_progress()
            
            # Move to next batch
            current_id += BATCH_SIZE
            
            # Delay between batches
            await asyncio.sleep(DELAY_BETWEEN_BATCHES)
            
            print(f"Progress: {current_progress['success_count']}/{current_progress['total_count']} @ {current_progress['speed']}/min")
    
    except Exception as e:
        print(f"Forward error: {e}")
    
    finally:
        is_forwarding = False
        current_progress["is_active"] = False
        save_progress()
        print("Forwarding completed!")


async def init_clients():
    """Initialize Pyrogram clients"""
    global user_client, bot_client
    
    # User client for fast forwarding (MTProto)
    if SESSION_STRING and API_ID and API_HASH:
        user_client = Client(
            "user_session",
            api_id=int(API_ID),
            api_hash=API_HASH,
            session_string=SESSION_STRING
        )
        await user_client.start()
        print("User client started (MTProto ready)")
    
    # Bot client for commands
    if BOT_TOKEN and API_ID and API_HASH:
        bot_client = Client(
            "bot_session",
            api_id=int(API_ID),
            api_hash=API_HASH,
            bot_token=BOT_TOKEN
        )
        await bot_client.start()
        print("Bot client started")
        
        # Register handlers
        register_bot_handlers()


def register_bot_handlers():
    """Register bot command handlers"""
    
    @bot_client.on_message(filters.command("start"))
    async def start_handler(client, message):
        await message.reply(
            "🚀 **Telegram Forwarder Bot (MTProto)**\n\n"
            "⚡ High-speed forwarding: 250-300/min\n\n"
            "Commands:\n"
            "/setconfig <source> <dest> - Set channels\n"
            "/forward <start_id> <end_id> - Start forwarding\n"
            "/resume - Resume forwarding\n"
            "/stop - Stop forwarding\n"
            "/progress - Show progress\n"
            "/status - Show status"
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
            
            await message.reply(f"🚀 Starting forward: {start_id} to {end_id}\n⚡ Speed: ~300/min")
            
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
        
        load_progress()
        
        if current_progress["current_id"] == 0:
            await message.reply("❌ No previous progress found")
            return
        
        config = get_config()
        if not config.get("source_channel"):
            await message.reply("❌ No config found")
            return
        
        await message.reply(f"🔄 Resuming from ID: {current_progress['current_id']}")
        
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
            f"🔄 Active: {'Yes' if current_progress['is_active'] else 'No'}\n"
            f"⚠️ Rate limits: {current_progress['rate_limit_hits']}"
        )
    
    @bot_client.on_message(filters.command("status"))
    async def status_handler(client, message):
        config = get_config()
        
        await message.reply(
            f"📡 **Status**\n\n"
            f"Source: {config.get('source_channel', 'Not set')}\n"
            f"Dest: {config.get('dest_channel', 'Not set')}\n"
            f"User Client: {'✅ Connected' if user_client else '❌ Not connected'}\n"
            f"Forwarding: {'🟢 Active' if is_forwarding else '⚪ Idle'}"
        )


# Flask routes for health checks
@flask_app.route("/")
def home():
    return jsonify({"status": "ok", "message": "Telegram Forwarder Bot (MTProto)"})


@flask_app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "user_client": user_client is not None,
        "bot_client": bot_client is not None,
        "is_forwarding": is_forwarding
    })


@flask_app.route("/progress")
def get_progress():
    load_progress()
    return jsonify(current_progress)


def run_flask():
    """Run Flask in a separate thread"""
    port = int(os.getenv("PORT", 8000))
    flask_app.run(host="0.0.0.0", port=port, debug=False)


async def main():
    """Main entry point"""
    print("Starting Telegram Forwarder Bot (MTProto)...")
    
    # Load saved progress
    load_progress()
    
    # Initialize clients
    await init_clients()
    
    # Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print("Bot is running!")
    
    # Use Pyrogram's idle to keep bot running and processing updates
    await idle()


if __name__ == "__main__":
    asyncio.run(main())
