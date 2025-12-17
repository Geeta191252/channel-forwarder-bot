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
db = mongo_client["telegram_forwarder"] if mongo_client is not None else None

# Collections
sessions_col = db["user_sessions"] if db is not None else None
progress_col = db["forwarding_progress"] if db is not None else None
forwarded_col = db["forwarded_messages"] if db is not None else None
config_col = db["bot_config"] if db is not None else None

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

# Pyrogram clients - Multiple user accounts for speed
user_clients = []  # List of (name, client) tuples
bot_client = None   # Bot for commands/UI
current_client_index = 0  # For round-robin rotation


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


async def forward_single_message(dest_channel, source_channel, msg_id):
    """Forward a single message using rotating clients"""
    client = get_next_client()
    if not client:
        return False, "No client available"
    
    try:
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
    global user_clients, bot_client
    
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
        
        await message.reply(
            f"🚀 **Telegram Forwarder Bot (Multi-Account MTProto)**\n\n"
            f"👥 Active accounts: {num_accounts}\n"
            f"⚡ Expected speed: ~{expected_speed}/min\n\n"
            f"Commands:\n"
            f"/setconfig <source> <dest> - Set channels\n"
            f"/forward <start_id> <end_id> - Start forwarding\n"
            f"/resume - Resume forwarding\n"
            f"/stop - Stop forwarding\n"
            f"/progress - Show progress\n"
            f"/status - Show status\n"
            f"/accounts - Show connected accounts"
        )
    
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
            f"Forwarding: {'🟢 Active' if is_forwarding else '⚪ Idle'}"
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
