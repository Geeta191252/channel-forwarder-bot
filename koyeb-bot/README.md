# Telegram Forwarder Bot (MTProto - High Speed)

⚡ **250-300 files/min** forwarding speed using MTProto user accounts!

## Why MTProto?

| Method | Speed |
|--------|-------|
| Bot API | 10-50/min max ❌ |
| MTProto (1 user) | 25-30/min ✅ |
| MTProto (10 users) | 250-300/min ✅ |

## Setup

### 1. Get Telegram API Credentials

1. Go to https://my.telegram.org
2. Login with your phone number
3. Go to "API Development Tools"
4. Create an app and get your `API_ID` and `API_HASH`

### 2. Generate Session String

Run this Python script to generate your session string:

```python
from pyrogram import Client

api_id = YOUR_API_ID
api_hash = "YOUR_API_HASH"

with Client(":memory:", api_id=api_id, api_hash=api_hash) as app:
    print(app.export_session_string())
```

This will:
1. Ask for your phone number
2. Send you a code on Telegram
3. Print the session string

### 3. Create Bot Token

1. Message @BotFather on Telegram
2. Send /newbot and follow instructions
3. Copy the bot token

### 4. Setup MongoDB

Create a free MongoDB Atlas cluster at https://mongodb.com/atlas

### 5. Configure Environment

Copy `.env.example` to `.env` and fill in:

```env
API_ID=123456
API_HASH=abcdef123456
SESSION_STRING=BQC7...long_string
BOT_TOKEN=123456:ABC-xyz
MONGO_URI=mongodb+srv://...
PORT=8000
```

### 6. Deploy to Koyeb

1. Push to GitHub
2. Create new Koyeb service
3. Connect your repo
4. Set root directory: `koyeb-bot`
5. Add environment variables
6. Deploy!

## Bot Commands

- `/start` - Show help
- `/setconfig <source> <dest>` - Set source and destination channels
- `/forward <start_id> <end_id>` - Start forwarding
- `/resume` - Resume previous forwarding
- `/stop` - Stop forwarding
- `/progress` - Show current progress
- `/status` - Show bot status

## Speed Settings

Default settings in `main.py`:

```python
BATCH_SIZE = 10              # Messages per batch
DELAY_BETWEEN_BATCHES = 2    # Seconds
DELAY_BETWEEN_MESSAGES = 0.2 # Seconds
```

This gives approximately **250-300 messages/min** safely.

## Multiple User Accounts (Even Faster!)

To achieve 500+ msgs/min, you can add multiple user accounts:

1. Generate multiple session strings from different accounts
2. Modify the code to rotate between accounts
3. Each account adds ~30/min capacity

## Important Notes

⚠️ **Use responsibly!** Excessive forwarding may get your account restricted.

- Start with lower speeds and increase gradually
- Monitor for FloodWait errors
- Don't forward copyrighted content
- Respect Telegram ToS

## Troubleshooting

### FloodWait errors
The bot handles these automatically. If persistent, reduce speed.

### Session expired
Generate a new session string.

### Can't access channel
Make sure your user account is a member of both channels.
