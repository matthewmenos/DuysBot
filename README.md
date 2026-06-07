# DUYS Trading Bot 🤖

A production-ready automated crypto trading bot for Telegram. Connects directly to your exchange, trades using real-time market signals, and manages the full user lifecycle — from onboarding and subscriptions to risk management and admin tools.

---

## Feature Overview

### Trading
| Feature | Details |
|---|---|
| 🤖 Auto Trade | Bot scans signals every 60s and places orders automatically |
| 👆 Manual Trade | You tap **Start Now** to buy; TP/SL still close automatically |
| 📈 Signal Engine | RSI, EMA crossover, MACD, Bollinger Bands, volume spike, news sentiment, CoinMarketCap |
| 🪙 Multi-symbol | Trade up to 3 symbols simultaneously |
| 🎯 TP/SL modes | Percentage or fixed price — configurable per user |
| 📉 Trailing Stop | Stop loss rises with profit to lock in gains |
| ✅ Trade Confirmation | Optional: approve each trade before it executes (30s timeout) |
| 🔁 Trade Retry | Errors surface as messages, never fail silently |
| 🔒 Deduplication | Prevents duplicate orders from button double-taps |
| ⚠️ SL Warning | Alerts user when a trade reaches 80% of stop loss distance |

### Exchanges
| Exchange | Notes |
|---|---|
| Binance 🟡 | Standard key + secret |
| Bybit 🔵 | Standard key + secret |
| OKX ⚫ | Key + secret + passphrase |
| MEXC 🟢 | Standard key + secret (90-day expiry — bot warns automatically) |
| KuCoin 🟠 | Key + secret + passphrase |

Keys are stored **encrypted** (Fernet AES-256) in the database. Messages containing API keys are **deleted immediately** after entry.

### Payments & Access
| Method | Details |
|---|---|
| 🆓 Free Trial | 7-day trial, one per account, verified by Telegram ID |
| 💳 Paystack | Card, mobile money, bank transfer — auto-activates via webhook |
| 🪙 USDT Crypto | Aptos, TRON (TRC-20), BSC (BEP-20) — verified on-chain |
| 🔑 Admin Grant | Lifetime access granted by admin |
| 🔗 Referral | Refer users, earn free months per successful subscription (configurable) |

Plans are **fully configurable via env vars** — no code changes needed (`PLAN_PRICE_1M`, `PLAN_PRICE_3M`, `PLAN_PRICE_6M`). Defaults: **1 month $12** · **3 months $34** · **6 months $65**

---

## Quick Start

### Prerequisites
- Python 3.13+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A Paystack account at [paystack.com](https://paystack.com)
- API keys from at least one exchange
- A Linux VPS with a public IP (for Paystack webhooks)

### 1. Install

```bash
git clone <your-repo>
cd trading_bot
pip install -r requirements.txt
```

### 2. Generate encryption key

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output — you'll need it in step 3.

### 3. Configure

```bash
cp .env.example .env
nano .env
```

**Minimum required to start:**
```env
BOT_TOKEN=your_telegram_bot_token
ADMIN_IDS=your_telegram_user_id
ENCRYPTION_KEY=your_generated_fernet_key

PAYSTACK_SECRET_KEY=sk_live_...
PAYSTACK_PUBLIC_KEY=pk_live_...
PAYSTACK_WEBHOOK_SECRET=your_webhook_secret
BOT_WEBHOOK_URL=https://yourdomain.com
```

### 4. Configure Paystack webhook

In your Paystack dashboard → **Settings → API Keys & Webhooks**:
- Webhook URL: `https://yourdomain.com/paystack/webhook`
- Copy the webhook secret to `PAYSTACK_WEBHOOK_SECRET` in `.env`

### 5. Configure crypto wallets (optional)

Set any or all wallet addresses in `.env` to accept USDT payments:
```env
USDT_APTOS_ADDRESS=0xYourAptosAddress
USDT_TRON_ADDRESS=TYourTronAddress
USDT_BSC_ADDRESS=0xYourBscAddress

TRONGRID_API_KEY=your_key     # free at trongrid.io
BSCSCAN_API_KEY=your_key      # free at bscscan.com/apis
```

Networks with no address configured are hidden from the payment menu.

### 6. Run

```bash
python main.py
```

This starts the Telegram bot, the Paystack webhook HTTP server (port 8080), and all background schedulers.

---

## Commands

### User Commands
| Command | Description |
|---|---|
| `/start` | Welcome screen with payment options or main menu |
| `/dashboard` | Full overview: balance, trades, PnL, settings in one screen |
| `/balance` | Live exchange balance with decimal formatting |
| `/start_trade` | Choose Auto or Manual mode and activate trading |
| `/stop_trade` | Stop trading (open positions stay open) |
| `/positions` | Live open positions with unrealised PnL |
| `/health` | Monitor open trades with entry, current price, PnL |
| `/chart` | Live price + all technical indicators + current signal |
| `/pnl` | Full profit & loss summary |
| `/history` | Last 10 closed trades |
| `/summary` | Trade cycle summary |
| `/export` | Download full trade history as CSV |
| `/signals` | Last 10 signals with confidence and outcome |
| `/settings` | Configure everything: exchange, symbol, TP/SL, toggles |
| `/exchanges` | List all supported exchanges |
| `/setalert SYMBOL above\|below PRICE` | Set a price alert |
| `/myalerts` | View active price alerts |
| `/delalert <id>` | Delete a price alert |
| `/subscribe` | Subscribe or start your free trial |
| `/mystatus` | Subscription status and payment history |
| `/referral` | Get your referral link (earn free months) |
| `/support <message>` | Contact the support team |
| `/timezone` | Set your timezone for daily reports |
| `/status` | Bot and platform status |
| `/panic` | Emergency close ALL your own open trades |

### Admin Commands
| Command | Description |
|---|---|
| `/grant <user_id>` | Grant lifetime access |
| `/user <user_id>` | Full user profile lookup |
| `/reply <user_id> <message>` | Reply directly to a user |
| `/broadcast <message>` | Send announcement to all subscribers |
| `/subscribers` | List all active subscribers |
| `/close` | Emergency close ALL trades across ALL users |
| `/status` | Full platform stats including backup info |

---

## Settings Toggles

All accessible via `/settings`:

| Toggle | Description |
|---|---|
| 🎯 Take Profit | Set value + toggle between % or fixed price mode |
| 🛑 Stop Loss | Set value + toggle between % or fixed price mode |
| 🔁 Trailing Stop | Stop loss moves up as profit grows |
| ✅ Trade Confirmation | Approve each signal before it executes |
| 📡 Signal Alerts | Receive suggestions for high-confidence signals on other coins |
| 🤖 / 👆 Trade Mode | Switch between Auto and Manual without restarting |

---

## Trading Strategy

Signals are generated by combining:

1. **RSI (14)** — Oversold < 30 triggers bullish score; overbought > 70 triggers bearish
2. **EMA Crossover (9/21)** — Golden cross adds score; death cross subtracts
3. **MACD Histogram** — Positive adds score; negative subtracts
4. **Bollinger Bands (20)** — Price below lower band is bullish; above upper is bearish
5. **Volume Spike** — Volume 1.5× above average amplifies the composite score by 20%
6. **News Sentiment** — CryptoCompare news API scored for positive/negative keywords
7. **CoinMarketCap** — Rank, 24h/7d change, and market cap factor added to score

A **BUY** signal is generated when composite score ≥ 30 with ≥ 50% confidence.
Trades close automatically when **Take Profit** or **Stop Loss** is hit.

---

## Access & Onboarding Flow

```
User sends /start
     │
     ├─ Active subscription → Main menu
     │
     └─ No access → Payment screen shown
           │
           ├─ 🆓 Free Trial (7 days, once per Telegram ID)
           │        └─ Guided onboarding: Exchange → Symbol → TP/SL → Trade Mode
           │
           ├─ 💳 Paystack (card / mobile money / bank transfer)
           │        └─ Webhook auto-activates subscription
           │
           ├─ 🪙 USDT Crypto (Aptos / TRON / BSC)
           │        └─ User pastes TX hash → verified on-chain → activated
           │
           └─ 🔑 Admin /grant <user_id> → Lifetime access
```

---

## Background Schedulers

All run automatically inside the bot process:

| Task | Interval | Description |
|---|---|---|
| Auto-trade loop | 60s | Scans signals, manages TP/SL, trailing stop; enforces circuit breaker |
| Price alert check | 60s | Fires alerts when targets are hit |
| Signal suggestions | 5 min | Scans 12 top pairs, notifies on ≥70% confidence |
| Daily PnL report | Daily 8 AM | Sends wins/losses/PnL summary to each user |
| Renewal reminders | Every 10 min | Notifies users 3 days and 1 day before expiry |
| DB backup | Every 24h | Backs up `bot_data.db` to `backups/`, keeps last 7 |
| API key expiry | Every 10 min | Warns MEXC users 14, 7, 3, 1 day before 90-day expiry |
| SL warning | Every 5 min | Alerts user when trade reaches 80% of stop loss |
| Confirm timeout | 60s | Auto-skips pending trade confirmations after 30s |

---

## Project Structure

```
trading_bot/
├── main.py              Entry point — registers commands, starts bot + webhook + scheduler
├── config.py            All environment variables and constants
├── database.py          SQLite layer — all DB queries and helpers
├── exchange.py          ccxt connector — Binance, Bybit, OKX, MEXC, KuCoin
├── strategy.py          Signal engine — RSI, EMA, MACD, BB, volume, news, CMC
├── scheduler.py         All background tasks
├── handlers.py          All Telegram command and callback handlers
├── alerts_handlers.py   Price alert commands (/setalert, /myalerts, /delalert)
├── onboarding.py        Guided 4-step setup flow for new users
├── paystack.py          Paystack API — payment links and verification
├── crypto_payment.py    On-chain USDT verification (Aptos, TRON, BSC)
├── webhook_server.py    HTTP server for Paystack payment webhooks
├── encryption.py        Fernet AES-256 encryption for API keys
├── logger_setup.py      Structured logging + admin error reporting
├── rate_limiter.py      Per-user command cooldowns and trade deduplication
├── backup.py            SQLite backup management
├── referral.py          Referral link generation, tracking, and rewards
├── utils.py             Shared decorators (require_granted, require_creds)
├── requirements.txt
├── .env.example
├── bot_data.db          Created automatically on first run
├── bot.log              General bot log
├── trades.log           Dedicated trade activity log
└── backups/             Daily DB backups (auto-created)
```

---

## Deployment on Linux VPS

### systemd service

```bash
sudo nano /etc/systemd/system/duysbot.service
```

```ini
[Unit]
Description=DUYS Trading Bot
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
sudo systemctl enable duysbot
sudo systemctl start duysbot
sudo journalctl -u duysbot -f
```

### nginx reverse proxy (for Paystack webhooks)

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location /paystack/ {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
    }
}
```

Get a free SSL cert: `sudo certbot --nginx -d yourdomain.com`

---

## Security

| Area | Implementation |
|---|---|
| API key storage | Fernet AES-256 encrypted in SQLite |
| DB backups | Encrypted with the same Fernet key — never stored plaintext |
| API key entry | Messages deleted from chat immediately after input |
| Paystack webhooks | HMAC-SHA512 signature verified on every event |
| TradingView webhooks | Per-user HMAC tokens; symbol/amount/exchange validated on every call |
| Trade scope | Spot only — no leverage, no margin |
| Per-user keys | No shared credentials between users |
| Access gate | Expired subscriptions lose trading access immediately |
| Error messages | Raw exception strings never shown to users |
| Trade logs | Order IDs and exact amounts omitted from `trades.log` |
| Circuit breaker | Auto-halts trading after configurable daily loss % (`MAX_DAILY_LOSS_PCT`) |
| Race conditions | Per-(user, symbol) async lock prevents duplicate orders |
| Referral codes | Cryptographically random (`secrets.token_urlsafe`), not enumerable |
| TLS | Startup warning if `BEHIND_TLS_PROXY` is not set |
| Error reporting | All errors forwarded to admin Telegram IDs in real time |

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `ENCRYPTION_KEY` | **Yes** | — | Fernet AES-256 key. Generate once and back up securely. Bot refuses to start without it. |
| `TV_WEBHOOK_SECRET_SALT` | Recommended | random/session | Salt for TradingView webhook tokens. If unset, tokens reset on every restart. |
| `BEHIND_TLS_PROXY` | Recommended | — | Set to `1` when nginx/Caddy TLS is in place. Suppresses HTTP security warning. |
| `BOT_TOKEN` | **Yes** | — | Telegram bot token from @BotFather. |
| `ADMIN_IDS` | **Yes** | — | Comma-separated Telegram user IDs with admin access. |
| `SUPPORT_CHANNEL_ID` | No | — | Private channel/group for support messages (bot must be admin). |
| `PAYSTACK_SECRET_KEY` | Yes* | — | `sk_live_...` or `sk_test_...` from Paystack dashboard. |
| `PAYSTACK_PUBLIC_KEY` | Yes* | — | `pk_live_...` or `pk_test_...` from Paystack dashboard. |
| `PAYSTACK_WEBHOOK_SECRET` | Yes* | — | Webhook signing secret from Paystack dashboard. |
| `BOT_WEBHOOK_URL` | Yes* | — | Public HTTPS URL of your server (e.g. `https://yourdomain.com`). |
| `WEBHOOK_PORT` | No | `8080` | Port for Paystack webhook HTTP server. TradingView uses `WEBHOOK_PORT + 1`. |
| `PLAN_PRICE_1M` | No | `12.00` | 1-month subscription price (USD). |
| `PLAN_PRICE_3M` | No | `34.00` | 3-month subscription price (USD). |
| `PLAN_PRICE_6M` | No | `65.00` | 6-month subscription price (USD). |
| `FREE_TRIAL_DAYS` | No | `7` | Free trial length in days (once per account). |
| `REFERRAL_REWARD_MONTHS` | No | `1` | Free months given to referrer per successful conversion. |
| `MAX_DAILY_LOSS_PCT` | No | `5.0` | Circuit breaker: halt trading when daily loss exceeds this %. Set `0` to disable. |
| `USDT_APTOS_ADDRESS` | No | — | Your Aptos wallet for USDT (LayerZero) payments. |
| `USDT_TRON_ADDRESS` | No | — | Your TRON wallet for USDT TRC-20 payments. |
| `USDT_BSC_ADDRESS` | No | — | Your BSC wallet for USDT BEP-20 payments. |
| `TRONGRID_API_KEY` | No | — | TronGrid API key for TRON payment verification (free at trongrid.io). |
| `BSCSCAN_API_KEY` | No | — | BscScan API key for BSC payment verification (free at bscscan.com/apis). |
| `ARB_MIN_PROFIT_PCT` | No | `0.3` | Minimum net arb profit % (after all fees) to flag an opportunity. |
| `ARB_WITHDRAWAL_FEE_USDT` | No | `1.5` | Flat cross-exchange withdrawal cost assumed per leg (USDT). |
| `ARB_FEE_BINANCE` | No | `0.001` | Binance taker fee fraction (override if your tier differs). |
| `ARB_FEE_BYBIT` | No | `0.001` | Bybit taker fee fraction. |
| `ARB_FEE_OKX` | No | `0.001` | OKX taker fee fraction. |
| `ARB_FEE_MEXC` | No | `0.002` | MEXC taker fee fraction. |
| `ARB_FEE_KUCOIN` | No | `0.001` | KuCoin taker fee fraction. |
| `ARB_FEE_COINBASE` | No | `0.006` | Coinbase Advanced taker fee fraction. |
| `ARB_FEE_BINGX` | No | `0.001` | BingX taker fee fraction. |
| `ARB_FEE_GATEIO` | No | `0.002` | Gate.io taker fee fraction. |
| `ARB_FEE_DEFAULT` | No | `0.001` | Default taker fee for unlisted exchanges. |
| `MAX_DCA_PLANS` | No | `3` | Max simultaneous DCA plans per user. |
| `MAX_GRID_PLANS` | No | `2` | Max simultaneous grid trading plans per user. |
| `MAX_SMART_ORDERS` | No | `5` | Max active smart orders (TWAP/Iceberg/OCO) per user. |
| `CORRELATION_THRESHOLD` | No | `0.85` | Block co-holding pairs with correlation above this value (0–1). |
| `CRYPTOCOMPARE_API_KEY` | No | — | News sentiment scoring (cryptocompare.com, free tier). |
| `COINMARKETCAP_API_KEY` | No | — | CMC rank and market cap signals (coinmarketcap.com/api, free). |
| `NEWSAPI_KEY` | No | — | General crypto headlines (newsapi.org, free tier). |
| `DB_PATH` | No | `bot_data.db` | SQLite database file path. |
| `PERSISTENCE_FILE` | No | `bot_persistence.pickle` | PTB PicklePersistence file for wizard state and cooldowns. |
| `PAPER_DEFAULT_BALANCE` | No | `1000.0` | Starting paper-trading balance for new users (USDT). |

*Required only if accepting Paystack payments.
