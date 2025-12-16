# Telegram Forwarder Bot - Koyeb Deployment

## 🚀 Quick Deploy to Koyeb

### Step 1: GitHub पर Upload करें

1. इस `koyeb-bot` folder को GitHub repository में push करें
2. या नया repository बनाएं सिर्फ इस folder के साथ

### Step 2: Koyeb पर Deploy करें

1. [Koyeb Dashboard](https://app.koyeb.com) पर जाएं
2. **Create Service** → **GitHub** select करें
3. अपना repository select करें
4. Settings configure करें:
   - **Branch**: main
   - **Root directory**: `koyeb-bot` (अगर main repo में है)
   - **Builder**: Docker
   - **Port**: 8000

### Step 3: Environment Variables Set करें

Koyeb dashboard में ये environment variables add करें:

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | @BotFather से मिला token |
| `SUPABASE_URL` | `https://wqspxhsjujakaldaxhvm.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key |
| `WEBHOOK_URL` | Koyeb app URL (deploy के बाद मिलेगा) |

### Step 4: Webhook Set करें

Deploy होने के बाद:

1. Koyeb से आपका app URL copy करें (जैसे: `https://your-app-xxxxx.koyeb.app`)
2. Browser में जाएं: `https://your-app-xxxxx.koyeb.app/set-webhook`
3. `{"ok":true}` response आना चाहिए

### Step 5: Bot Test करें

Telegram में अपने bot को `/start` command भेजें!

---

## 🔧 Local Development

```bash
cd koyeb-bot
npm install
cp .env.example .env
# Edit .env with your values
npm run dev
```

---

## 📁 File Structure

```
koyeb-bot/
├── src/
│   └── index.ts      # Main bot code
├── package.json
├── tsconfig.json
├── Dockerfile
├── .env.example
└── README.md
```

---

## 🔗 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/health` | GET | Health status |
| `/webhook` | POST | Telegram webhook |
| `/set-webhook` | GET | Set Telegram webhook |
| `/delete-webhook` | GET | Remove webhook |

---

## ⚠️ Important Notes

1. **Supabase tables**: Same database tables use होंगे (forwarding_progress, user_sessions, etc.)
2. **Service Role Key**: Koyeb में Supabase SERVICE_ROLE key use करें, anon key नहीं
3. **Webhook URL**: Deploy के बाद WEBHOOK_URL update करना न भूलें
