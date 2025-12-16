# Telegram Forwarder Bot (Koyeb + MongoDB)

Standalone Telegram Forwarder Bot for deployment on Koyeb with MongoDB database.

## Prerequisites

1. **Telegram Bot Token** - Get from [@BotFather](https://t.me/BotFather)
2. **MongoDB Database** - Get free cluster from [MongoDB Atlas](https://www.mongodb.com/atlas)
3. **Koyeb Account** - Sign up at [koyeb.com](https://koyeb.com)

## Setup MongoDB Atlas (Free)

1. Go to [MongoDB Atlas](https://www.mongodb.com/atlas)
2. Create a free account
3. Create a new cluster (Free M0 tier)
4. Create database user with password
5. Whitelist IP `0.0.0.0/0` (allows all IPs for Koyeb)
6. Get connection string: Click "Connect" → "Drivers" → Copy URI
7. Replace `<password>` with your actual password

## Deployment Steps

### 1. Connect GitHub
In Lovable editor: Click **GitHub** → **Connect to GitHub** → **Create Repository**

### 2. Deploy to Koyeb

1. Go to [Koyeb Console](https://app.koyeb.com)
2. Click **Create Service** → **GitHub**
3. Select your repository
4. Configure:
   - **Root directory**: `koyeb-bot`
   - **Builder**: Docker
   - **Port**: 8000

### 3. Environment Variables

Add these in Koyeb service settings:

| Variable | Value |
|----------|-------|
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `MONGODB_URI` | `mongodb+srv://user:pass@cluster.mongodb.net/telegram_forwarder` |
| `PORT` | `8000` |
| `WEBHOOK_URL` | `https://your-app.koyeb.app` (set after first deploy) |

### 4. Set Webhook

After deployment, visit:
```
https://your-app.koyeb.app/set-webhook
```

## Bot Commands

- `/start` - Show main menu
- `/forward` - Start forwarding wizard
- `/setconfig [source] [dest]` - Manual config
- `/resume` - Resume forwarding
- `/stop` - Stop forwarding
- `/progress` - Check progress
- `/status` - Bot status
- `/cancel` - Cancel current process

## MongoDB Collections

The bot automatically creates these collections:
- `user_sessions` - User wizard states
- `bot_config` - Source/destination config
- `forwarding_progress` - Current progress
- `forwarded_messages` - Tracking forwarded messages

## Local Development

```bash
cd koyeb-bot
npm install
cp .env.example .env
# Edit .env with your values
npm run dev
```

## Troubleshooting

- **Bot not responding**: Check webhook is set correctly
- **MongoDB connection error**: Verify connection string and IP whitelist
- **Rate limits**: Bot handles Telegram rate limits automatically
