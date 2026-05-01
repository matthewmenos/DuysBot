# CryptoTradeBot 🤖

A production-ready Telegram crypto trading bot supporting **Binance**, **Bybit**, **OKX**, and **MEXC** with automated signal-based trading, Paystack subscription payments, and admin lifetime grants.

---

## Features

| Feature | Description |
|---|---|
| 📈 Auto-trading | RSI + EMA crossover + MACD + Bollinger Bands + news sentiment |
| 🏦 Multi-exchange | Binance, Bybit, OKX, MEXC via real APIs (ccxt) |
| 🎯 Risk controls | Per-user Take Profit & Stop Loss, configurable trade size |
| 🪙 Any token | 20 popular presets + search/validate any coin live on exchange |
| 💳 Paystack payments | $12/mo, $34/3mo, $65/6mo — card, mobile money, bank transfer |
| 🔒 Dual access | Admin lifetime grant OR paid Paystack subscription |
| 🔐 Auto-activation | Subscription activates instantly on payment via webhook |
| 💾 Persistence | SQLite — trades, history, settings, subscriptions all saved |
| 📊 Analytics | PnL summary, trade health, cycle summaries |

---

## Quick Start

### 1. Prerequisites
- Python 3.10+
- A Telegram Bot token (from [@BotFather](https://t.me/BotFather))
- A Paystack account at [paystack.com](https://paystack.com)
- API keys from your exchange(s)
- A publicly accessible server (for Paystack webhooks)

### 2. Install dependencies
```bash
cd trading_bot
pip install -r requirements.txt
```

### 3. Configure environment
```bash
cp .env.example .env
nano .env
```

**Minimum required:**
```
BOT_TOKEN=your_telegram_bot_token
ADMIN_IDS=your_telegram_user_id
PAYSTACK_SECRET_KEY=sk_live_...
PAYSTACK_PUBLIC_KEY=pk_live_...
PAYSTACK_WEBHOOK_SECRET=your_webhook_secret
BOT_WEBHOOK_URL=https://yourdomain.com
```

### 4. Set up Paystack webhook
In your Paystack dashboard → **Settings → API Keys & Webhooks**:
- Set webhook URL to: `https://yourdomain.com/paystack/webhook`
- Copy the webhook secret into `.env` as `PAYSTACK_WEBHOOK_SECRET`

### 5. Run the bot
```bash
python main.py
```
This starts both the Telegram bot and the webhook server (on port 8080 by default).

---

## Bot Commands

| Command | Who | Description |
|---|---|---|
| `/start` | All | Welcome screen + subscribe button |
| `/subscribe` | All | Choose and pay for a subscription plan |
| `/mystatus` | All | View subscription status + payment history |
| `/balance` | Subscribers | View exchange balance |
| `/start_trade` | Subscribers | Enable auto-trading |
| `/stop_trade` | Subscribers | Disable auto-trading |
| `/settings` | Subscribers | Exchange, TP, SL, symbol, trade amount |
| `/history` | Subscribers | Last 10 trades |
| `/chart` | Subscribers | Live price + signal + indicators |
| `/pnl` | Subscribers | Full profit & loss report |
| `/health` | Subscribers | Monitor open trades live |
| `/summary` | Subscribers | Trade cycle summary |
| `/exchanges` | All | List supported exchanges |
| `/support` | Subscribers | Message admin |
| `/grant <id>` | **Admin** | Grant user lifetime access |
| `/subscribers` | **Admin** | List all subscribers |
| `/panic` | **Admin** | Emergency close ALL trades |

---

## Access Model

```
New user sends /start
       │
       ├─ Already has access? ──► Show main menu
       │
       └─ No access
              │
              ├─ Admin runs /grant <user_id> ──► Lifetime access (free)
              │
              └─ User taps Subscribe ──► Chooses plan ──► Enters email
                       │
                       └─ Paystack payment link ──► User pays
                                │
                                └─ Paystack webhook ──► Bot auto-activates
                                         └─ Telegram notification sent
```

### Subscription Plans
| Plan | Price | Savings |
|---|---|---|
| 1 Month | $12.00 | — |
| 3 Months | $34.00 | Save $2 |
| 6 Months | $65.00 | Save $7 |

Subscriptions stack — paying again extends from the current expiry date.

---

## Trading Strategy

Signals combine:
1. **RSI (14)** — Overbought/oversold detection
2. **EMA Crossover (9/21)** — Trend direction
3. **MACD** — Momentum confirmation
4. **Bollinger Bands (20)** — Price extremes
5. **Volume spike** — Confirms breakouts
6. **News sentiment** — CryptoCompare API (optional, free tier)

A `BUY` executes when composite score ≥ 30 with ≥50% confidence. Trades close automatically on Take Profit or Stop Loss.

---

## Project Structure

```
trading_bot/
├── main.py            # Entry point — registers handlers, starts bot + webhook server
├── config.py          # All environment variables
├── database.py        # SQLite — users, trades, subscriptions, settings
├── exchange.py        # ccxt exchange connector (Binance, Bybit, OKX, MEXC)
├── strategy.py        # Signal engine (RSI, EMA, MACD, Bollinger, news)
├── scheduler.py       # Auto-trade loop (job_queue, every 60s)
├── handlers.py        # All Telegram command & callback handlers
├── paystack.py        # Paystack API — initialize & verify transactions
├── webhook_server.py  # HTTP server for Paystack payment webhooks
├── requirements.txt
├── .env.example
└── bot_data.db        # Created automatically on first run
```

---

## Deployment (Linux VPS)

```bash
sudo nano /etc/systemd/system/cryptobot.service
```

```ini
[Unit]
Description=CryptoTradeBot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/trading_bot
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10
EnvironmentFile=/home/ubuntu/trading_bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable cryptobot
sudo systemctl start cryptobot
sudo journalctl -u cryptobot -f
```

For the webhook to work publicly, use **nginx** as a reverse proxy:
```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    location /paystack/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
    }
}
```

---

## Security Notes
- ⚠️ API keys stored in SQLite — use encrypted storage or a secrets manager in production
- ✅ Paystack webhook signature verified via HMAC-SHA512
- ✅ Spot trading only (no leverage)
- ✅ Per-user exchange credentials — no shared keys
- ✅ Subscriptions expire and auto-gate access — expired users cannot trade
