# DUYS Trading Bot — Technical Documentation & Whitepaper

**Version 2.0 · May 2026**

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Signal Engine](#3-signal-engine)
4. [Arbitrage Engine](#4-arbitrage-engine)
5. [Exchange Integration](#5-exchange-integration)
6. [Notification System](#6-notification-system)
7. [Risk Management](#7-risk-management)
8. [Access & Payments](#8-access--payments)
9. [Security Model](#9-security-model)
10. [Background Schedulers](#10-background-schedulers)
11. [Database Schema](#11-database-schema)
12. [API Reference](#12-api-reference)
13. [Deployment Guide](#13-deployment-guide)
14. [Environment Variables](#14-environment-variables)
15. [Command Reference](#15-command-reference)
16. [Changelog](#16-changelog)

---

## 1. Overview

DUYS Trading Bot is a production-grade automated cryptocurrency trading system delivered entirely through Telegram. Users connect their exchange API keys, configure trading parameters, and the bot handles the full lifecycle: signal generation, order placement, position management, take-profit/stop-loss execution, arbitrage scanning, and daily reporting.

### Design Principles

- **Non-custodial.** DUYS never holds funds. All orders are placed directly on the user's exchange via their own API keys.
- **Transparent.** Every trade, signal, and balance change is surfaced to the user in real time via Telegram messages.
- **Fail-safe.** Errors are never silently swallowed. All failures surface as user messages or admin alerts.
- **Modular.** Each subsystem (signals, arbitrage, payments, notifications) is independently testable and replaceable.

### Core Capabilities

| Subsystem | Description |
|-----------|-------------|
| Auto-trading | Signal-driven market orders with configurable TP/SL |
| Manual trading | User-triggered entries, bot-managed exits |
| Arbitrage | Cross-exchange and triangular scanning with instant alerts |
| Signal suggestions | Proactive high-confidence signal notifications |
| Price alerts | Custom above/below price triggers on any symbol |
| Daily reports | Automated PnL summaries with win/loss analytics |
| Multi-exchange | 8 exchanges, switchable without credential re-entry |
| Subscription | Paystack (card/mobile money) + USDT on-chain + admin grant |

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Telegram Bot API                          │
└───────────────────────────┬─────────────────────────────────────┘
                            │  python-telegram-bot (PTB)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  main.py — Application entry point                               │
│  • Registers all CommandHandlers and CallbackQueryHandlers        │
│  • Registers post_init hook for Telegram command menu            │
│  • Starts JobQueue scheduler (60-second tick)                    │
│  • Launches webhook_server.py (Paystack HTTP endpoint)           │
└──────┬──────────────────────┬────────────────────────┬──────────┘
       │                      │                        │
       ▼                      ▼                        ▼
┌─────────────┐   ┌──────────────────┐   ┌────────────────────────┐
│ handlers.py │   │  scheduler.py    │   │   webhook_server.py    │
│             │   │                  │   │                        │
│ /start      │   │ auto-trade loop  │   │ POST /paystack/webhook │
│ /arbitrage  │   │ signal scanner   │   │ HMAC-SHA512 verify     │
│ /settings   │   │ arb scanner      │   │ subscription activate  │
│ /balance    │   │ price alerts     │   └────────────────────────┘
│ callbacks   │   │ daily reports    │
└──────┬──────┘   └───────┬──────────┘
       │                  │
       ▼                  ▼
┌─────────────────────────────────────────────────────────────────┐
│  Core Modules                                                    │
│                                                                  │
│  exchange.py    ccxt connector, 8 exchanges, order placement     │
│  strategy.py    7-layer signal engine (RSI/EMA/MACD/BB/Vol/…)   │
│  arbitrage.py   Cross-exchange + triangular scanner              │
│  database.py    SQLite ORM — all queries, migrations             │
│  encryption.py  Fernet AES-256 for API keys                      │
│  rate_limiter.py Per-command cooldowns, trade deduplication      │
│  logger_setup.py Structured logging + admin error forwarding     │
└─────────────────────────────────────────────────────────────────┘
```

### File Map

| File | Role |
|------|------|
| `main.py` | Entry point; wires PTB Application, schedulers, webhook |
| `config.py` | All env vars and constants |
| `database.py` | SQLite layer; all queries, migrations, helpers |
| `exchange.py` | ccxt connector; order placement; balance; OHLCV |
| `strategy.py` | Signal engine; RSI, EMA, MACD, BB, volume, news, CMC |
| `arbitrage.py` | Cross-exchange + triangular scanner; execution helpers |
| `scheduler.py` | All background tasks (auto-trade, alerts, arb, reports) |
| `handlers.py` | All Telegram command and callback handlers |
| `alerts_handlers.py` | Price alert commands |
| `onboarding.py` | Guided 4-step setup for new users |
| `paystack.py` | Paystack API (payment links, verification) |
| `crypto_payment.py` | On-chain USDT verification (Aptos, TRON, BSC) |
| `webhook_server.py` | HTTP server for Paystack webhooks |
| `encryption.py` | Fernet AES-256 API key encryption/decryption |
| `logger_setup.py` | Structured logging + `report_error_to_admin` |
| `rate_limiter.py` | Per-user command cooldowns; trade deduplication |
| `backup.py` | SQLite backup management (daily, 7-day rolling) |
| `referral.py` | Referral link generation, tracking, reward crediting |
| `utils.py` | Shared decorators: `require_granted`, `require_creds` |
| `web_app.py` | Flask app serving chart data API for `index.html` |

---

## 3. Signal Engine

Located in `strategy.py`. Generates a BUY/SELL/HOLD signal with a confidence score from 0–100 given an OHLCV candle array.

### Layers

| # | Layer | Data Source | Weight |
|---|-------|-------------|--------|
| 1 | **RSI (14)** | Last 14 hourly closes | +2 below 30 (bullish), −2 above 70 (bearish) |
| 2 | **EMA Crossover (9/21)** | Last 30 closes | +2 golden cross, −2 death cross |
| 3 | **MACD (12/26/9)** | Last 35 closes | +1 positive histogram, −1 negative |
| 4 | **Bollinger Bands (20, 2σ)** | Last 20 closes | +2 below lower band, −2 above upper |
| 5 | **Volume Spike** | Last 20 volumes | ×1.2 amplifier if volume > 1.5× average |
| 6 | **News Sentiment** | CryptoCompare API | −2 to +2 weighted keyword score |
| 7 | **CoinMarketCap** | CMC API | Rank, 7d change, market cap modifier |

### Signal Thresholds

```
composite_score ≥  3  →  BUY   (confidence = 50 + score × 5, capped at 100)
composite_score ≤ −3  →  SELL  (confidence = 50 + |score| × 5)
−3 < score < 3        →  HOLD
```

A **BUY** triggers an auto-trade only when `confidence ≥ 50` in auto mode. Signal suggestions require `confidence ≥ 70`.

### OHLCV Requirements

The engine requires at least 35 candles (hourly) to compute all indicators reliably. If fewer candles are available it returns HOLD with confidence 0.

---

## 4. Arbitrage Engine

Located in `arbitrage.py`. Scans every 3 minutes in the background; also callable on-demand via `/arbitrage → 🔍 Scan Now`.

### 4.1 Cross-Exchange Arbitrage

**Logic:**
1. For every symbol in the user's selected list, fetch `(best_bid, best_ask)` from every connected exchange using L1 order book (falls back to ticker).
2. For every ordered exchange pair `(buy_on, sell_on)`:
   - Gross spread = `(sell_bid − buy_ask) / buy_ask × 100`
   - Total fees = `taker_fee(buy_on) + taker_fee(sell_on)` (as %)
   - Net profit % = gross spread − total fees
   - Net profit USDT = `notional × gross_spread/100 − notional × fees/100 − withdrawal_fee`
3. Opportunity is **viable** when `net_profit_pct > MIN_PROFIT_PCT` (0.3%) AND `net_profit_usdt > 0`.

**Taker fee table (May 2026):**

| Exchange | Taker Fee |
|----------|-----------|
| Binance | 0.10% |
| Bybit | 0.10% |
| OKX | 0.10% |
| KuCoin | 0.10% |
| MEXC | 0.20% |
| Coinbase Advanced | 0.60% |
| BingX | 0.10% |
| Gate.io | 0.20% |

**Withdrawal buffer:** $1.50 USDT flat per cross-exchange opportunity (transfer/network cost estimate).

### 4.2 Triangular Arbitrage

**Logic:**  
Simulates a round-trip through three pairs, all settling back to USDT. Fee is applied at each leg before computing the next leg's amount.

**Example path:** `USDT → BTC/USDT (buy) → ETH/BTC (sell) → ETH/USDT (sell) → USDT`

```python
leg1 = start_usdt / ask_BTCUSDT * (1 − fee)   # receive BTC
leg2 = leg1 * bid_ETHBTC * (1 − fee)           # receive ETH
leg3 = leg2 * bid_ETHUSDT * (1 − fee)          # receive USDT
net_pct = (leg3 − start_usdt) / start_usdt × 100
```

All 14 built-in paths are filtered to only check paths whose USDT legs appear in the user's chosen token list.

### 4.3 Notification Model

Each opportunity is identified by a **fingerprint**:
- Cross-exchange: `"X:{buy_ex}>{sell_ex}:{symbol}"` e.g. `"X:binance>bingx:ETH/USDT"`
- Triangular: `"T:{exchange}:{sym1}|{sym2}|{sym3}"` e.g. `"T:bybit:BTC/USDT|ETH/BTC|ETH/USDT"`

Fingerprints are tracked per-user in memory with a 10-minute cooldown (`ARB_OPP_COOLDOWN_SECS`).

| Scenario | Behaviour |
|----------|-----------|
| New fingerprint | Sends immediately, regardless of other recent alerts |
| Same fingerprint within 10 min | Suppressed — not re-sent |
| Same fingerprint after 10 min | Re-sent if still viable |
| Send fails | Fingerprint reverted so it retries next tick |

### 4.4 User Controls

From `/arbitrage`:
- **Enable/Disable** — `arb_enabled` setting; disables both background scans and `/arbitrage` scans
- **Auto-Alerts toggle** — `arb_alerts` setting; disables background notifications but allows on-demand scans
- **Token Picker** — 24-token keyboard toggle; saves as JSON in `arb_symbols` setting. Fewer tokens = faster scans
- **Scan Now** — immediate on-demand scan regardless of scheduler timing

### 4.5 Error Handling

- `ccxt.BadSymbol` — silently skipped (exchange doesn't list that symbol)
- `ccxt.NetworkError` — logged at WARNING, scan continues with remaining symbols
- `ccxt.ExchangeError` — logged at WARNING, scan continues
- Exchange-wide failures — forwarded to admin via `report_error_to_admin`
- Per-symbol errors — recorded as `ScanError` dataclass, reported to user as a footnote ("N symbols skipped")

---

## 5. Exchange Integration

Located in `exchange.py`. Uses **ccxt** as the unified adapter.

### Supported Exchanges

| ID | Label | Auth | Notes |
|----|-------|------|-------|
| `binance` | Binance 🟡 | key + secret | |
| `bybit` | Bybit 🔵 | key + secret | |
| `okx` | OKX ⚫ | key + secret + passphrase | |
| `mexc` | MEXC 🟢 | key + secret | Keys expire 90 days after creation |
| `kucoin` | KuCoin 🟠 | key + secret + passphrase | |
| `coinbase` | Coinbase 🔵 | key + secret | Advanced Trade API only |
| `bingx` | BingX 🟣 | key + secret | |
| `gateio` | Gate.io 🔴 | key + secret | |

### Key Functions

```python
get_exchange(exchange_id, api_key, api_secret, api_pass) → ccxt.Exchange
fetch_balance(exchange) → dict
fetch_usdt_balance(exchange) → float
fetch_ohlcv(exchange, symbol, timeframe, limit) → list
fetch_ticker(exchange, symbol) → dict
place_market_order(exchange, symbol, side, amount) → dict
get_min_trade_amount(exchange, symbol) → float
close_all_positions(exchange, open_trades) → list[dict]
```

### MEXC Key Expiry

MEXC API keys expire 90 days after creation. The bot tracks creation date and sends warnings at 14, 7, 3, and 1 day before expiry. `MEXC_KEY_EXPIRY_DAYS = 90` in `config.py`.

---

## 6. Notification System

All notifications are sent via `context.bot.send_message()` using HTML or Markdown parse mode.

### Notification Types

| Type | Trigger | Frequency | Cooldown |
|------|---------|-----------|----------|
| Trade opened | Market order placed | Per trade | None |
| Trade closed (TP/SL) | Position closed | Per trade | None |
| Signal suggestion | ≥70% confidence on unwatched symbol | Per symbol | 4 hours |
| Arbitrage alert | New viable opportunity fingerprint | Per opportunity | 10 min per fingerprint |
| Price alert | Target price crossed | Per alert | Alert deleted after firing |
| SL warning | Trade at 80% of SL distance | Per trade | 5 minutes |
| Daily PnL report | User-configured hour | Daily | Once per day |
| Balance warning | Insufficient balance for trade | Per attempt | None |
| Renewal reminder | 3 days, 1 day before expiry | Per event | None |
| MEXC key expiry | 14/7/3/1 day before 90-day limit | Per milestone | None |
| Admin error alert | Any exception caught by scheduler | Per error | None |

### Signal Notification Format

```
📈 Signal Alert — BUY SOL/USDT

Confidence: 84%  🟩🟩🟩🟩⬜
Reason: RSI oversold + EMA golden cross + volume spike

💡 This is a suggestion — you are not currently trading SOL/USDT.
Add it via /settings → 🔁 Multi-Symbol.

🔕 Disable: /settings → Signal Alerts
```

### Arbitrage Notification Format

```
⚡ 2 New Arbitrage Opportunity(ies) Found!

📊 Cross-Exchange (1 found):
✅ ETH/USDT
  Buy  `BINANCE` @ $3,284.10
  Sell `BINGX`   @ $3,301.80
  Spread 0.539% · Fees 0.200% · Net 0.339% (~$3.39 / $1k)

🔺 Triangular (1 found):
✅ BYBIT
  BUY BTC/USDT → SELL ETH/BTC → SELL ETH/USDT
  Net 0.421% (~$4.21 / $1k)

💡 Estimates based on $1 000 notional, all fees included.
⚠️ Cross-exchange arb requires pre-funded balances on both sides.
👉 /arbitrage to scan on-demand, change tokens, or adjust settings.
```

---

## 7. Risk Management

### Per-Trade Controls

| Control | Default | Configurable |
|---------|---------|--------------|
| Take Profit | 2.0% | Yes (% or fixed price) |
| Stop Loss | 1.0% | Yes (% or fixed price) |
| Trailing Stop | Off | Yes (triggers at 0.5% by default) |
| Trade Amount | $10 USDT | Yes |
| Trade Confirmation | Off | Yes (30-second timeout) |

### Guard Conditions (checked before every buy)

1. **Balance check** — `free_USDT ≥ trade_amount`, else sends warning and aborts
2. **Minimum order size** — `trade_amount ≥ exchange_minimum`, else sends warning with suggested amount
3. **Open trade deduplication** — `has_open_trade_for_symbol(user_id, symbol)` blocks duplicate entries
4. **API key validity** — connection tested at exchange instantiation
5. **Signal confidence threshold** — `confidence ≥ 50` for auto-trade; `confidence ≥ 70` for suggestions

### SL Warning

When a trade reaches 80% of its stop-loss distance (i.e., the position is losing 80% of the maximum allowed loss), the bot sends a warning message. This runs on a 5-minute check with per-trade cooldown.

### Panic Close

`/panic` closes all the calling user's open positions at market price with a single confirmation tap. Admin `/close` closes all trades across all users.

---

## 8. Access & Payments

### Access Tiers

| Tier | Duration | Source |
|------|----------|--------|
| Free Trial | 7 days | One per Telegram ID, automatic |
| Monthly | 30 days | Paystack / USDT payment |
| Quarterly | 90 days | Paystack / USDT payment |
| Semi-Annual | 180 days | Paystack / USDT payment |
| Admin Grant | Lifetime | `/grant <user_id>` by admin |

### Paystack Flow

1. User selects plan → `paystack.py` creates a payment link via Paystack API
2. User completes payment on Paystack checkout
3. Paystack sends POST to `https://yourdomain.com/paystack/webhook`
4. `webhook_server.py` verifies HMAC-SHA512 signature using `PAYSTACK_WEBHOOK_SECRET`
5. On `charge.success` event: `database.py` activates subscription for the user's Telegram ID
6. Bot sends confirmation message to user

### Crypto Payment Flow (USDT)

1. User selects network (Aptos / TRON / BSC) — only networks with configured wallet addresses are shown
2. Bot displays wallet address and expected USDT amount
3. User sends transaction and pastes the TX hash in chat
4. `crypto_payment.py` queries the relevant chain explorer API (Trongrid / BscScan / Aptos fullnode)
5. Verifies: recipient address matches, amount ≥ plan price, transaction confirmed
6. On success: activates subscription in database

### Referral System

- Each user gets a unique referral link via `/referral`
- When a referred user subscribes (paid), the referrer earns 1 free month
- Tracked in `referrals` table; credit applied automatically on webhook receipt

---

## 9. Security Model

### API Key Encryption

API keys are encrypted before any database write using Fernet symmetric encryption (AES-128-CBC with HMAC-SHA256):

```python
from cryptography.fernet import Fernet
cipher = Fernet(ENCRYPTION_KEY)
encrypted_key = cipher.encrypt(raw_api_key.encode()).decode()
```

The `ENCRYPTION_KEY` is loaded from the environment and never stored in the database. Losing this key makes all stored credentials permanently unreadable.

### Chat Hygiene

After a user pastes an API key in chat, the bot immediately calls `context.bot.delete_message()` to remove it from chat history. This prevents credential exposure if chat logs are accessed.

### Webhook Verification

Every Paystack webhook is verified:

```python
sig   = request.headers.get('x-paystack-signature')
hmac_ = hmac.new(SECRET.encode(), request.data, hashlib.sha512).hexdigest()
if not hmac.compare_digest(sig, hmac_):
    abort(400)
```

### Trade Scope

Only **spot** market orders are placed. No leverage, no margin, no futures. Users cannot accidentally open leveraged positions through the bot.

### Rate Limiting

Per-user per-command cooldowns prevent abuse:

| Command | Cooldown |
|---------|----------|
| `/balance` | 10 seconds |
| `/arbitrage` | 30 seconds |
| `/start_trade` | 5 seconds |
| Manual buy | 5 seconds |
| Most commands | 2 seconds |

---

## 10. Background Schedulers

All tasks run on the PTB `JobQueue` inside the bot process. The main ticker fires every 60 seconds; internal counters gate less-frequent tasks.

```
start_scheduler() — called every 60 seconds
├── 1. Auto-trade loop (every tick)
│       process_user() for every trading user
│       ├── Fetch ticker price
│       ├── Check TP/SL on open trades
│       ├── If trailing stop enabled: update stop price
│       └── If no open trade: generate signal → execute or suggest
│
├── 2. Price alert check (every tick)
│       check_price_alerts() — scans active alerts, fires on condition met
│
├── 3. SL warning check (every 5 min)
│       check_sl_warnings() — warns when trade at 80% SL distance
│
├── 4. Signal suggestions (every 5 min)
│       run_signal_suggestions() — scans 12 symbols, sends ≥70% confidence alerts
│
├── 5. Trade confirmation timeout (every tick)
│       expire_pending_confirmations() — rejects unconfirmed trades after 30s
│
├── 6. Daily PnL reports (every 10 min check)
│       send_daily_reports() — sends to users whose report_hour has passed
│
├── 7. Renewal reminders (every 10 min)
│       send_renewal_reminders() — 3-day and 1-day expiry warnings
│
├── 8. API key expiry check (every 10 min)
│       check_api_key_expiry() — MEXC 90-day warnings at 14/7/3/1 days
│
├── 9. DB backup (every 24 hours)
│       run_db_backup() — copies bot_data.db to backups/, keeps last 7
│
└── 10. Arbitrage scan (every 3 min)
        run_arbitrage_notifications() — per-user scan, sends fingerprint-deduped alerts
```

---

## 11. Database Schema

SQLite database at `DB_PATH` (default `bot_data.db`). Migrations run automatically on startup using a safe `ALTER TABLE ... ADD COLUMN` pattern that is idempotent (failures on existing columns are caught and ignored).

### Core Tables

```sql
CREATE TABLE users (
  user_id         INTEGER PRIMARY KEY,
  username        TEXT,
  exchange        TEXT DEFAULT '',
  api_key         TEXT DEFAULT '',    -- Fernet encrypted
  api_secret      TEXT DEFAULT '',    -- Fernet encrypted
  api_pass        TEXT DEFAULT '',    -- Fernet encrypted
  granted         INTEGER DEFAULT 0,  -- 1 = lifetime admin grant
  sub_expiry      TEXT,               -- ISO datetime
  trial_used      INTEGER DEFAULT 0,
  timezone        TEXT DEFAULT 'UTC',
  created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE user_settings (
  user_id          INTEGER PRIMARY KEY,
  take_profit      REAL    DEFAULT 2.0,
  stop_loss        REAL    DEFAULT 1.0,
  tp_mode          TEXT    DEFAULT 'pct',     -- 'pct' | 'fixed'
  sl_mode          TEXT    DEFAULT 'pct',
  trade_amount     REAL    DEFAULT 10.0,
  symbol           TEXT    DEFAULT 'BTC/USDT',
  trading_on       INTEGER DEFAULT 0,
  confirm_trades   INTEGER DEFAULT 0,
  trailing_stop    INTEGER DEFAULT 0,
  trailing_stop_pct REAL   DEFAULT 0.5,
  report_hour      INTEGER DEFAULT 8,
  last_report_date TEXT,
  signal_suggestions INTEGER DEFAULT 1,
  multi_symbols    TEXT    DEFAULT NULL,      -- JSON list
  trade_mode       TEXT    DEFAULT 'auto',   -- 'auto' | 'manual'
  arb_alerts       INTEGER DEFAULT 1,        -- background arb notifications
  arb_enabled      INTEGER DEFAULT 1,        -- arbitrage feature toggle
  arb_symbols      TEXT    DEFAULT NULL      -- JSON list of tokens to scan
);

CREATE TABLE trades (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER,
  symbol       TEXT,
  side         TEXT,           -- 'buy' | 'sell'
  entry_price  REAL,
  exit_price   REAL,
  amount       REAL,           -- USDT notional
  pnl          REAL,
  status       TEXT DEFAULT 'open',   -- 'open' | 'closed'
  exchange     TEXT,
  order_id     TEXT,
  signal       TEXT,           -- reason string
  opened_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  closed_at    TEXT,
  close_reason TEXT            -- 'tp' | 'sl' | 'manual' | 'panic'
);

CREATE TABLE price_alerts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER,
  symbol       TEXT,
  condition    TEXT,           -- 'above' | 'below'
  target_price REAL,
  note         TEXT,
  triggered    INTEGER DEFAULT 0,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE signals (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER,
  symbol       TEXT,
  action       TEXT,           -- 'BUY' | 'SELL' | 'HOLD'
  confidence   REAL,
  reason       TEXT,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE subscriptions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER,
  reference    TEXT,           -- Paystack reference or TX hash
  months       INTEGER,
  amount       REAL,
  currency     TEXT,
  status       TEXT DEFAULT 'pending',  -- 'pending' | 'active' | 'expired'
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE referrals (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  referrer_id    INTEGER,
  referred_id    INTEGER,
  rewarded       INTEGER DEFAULT 0,
  created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE exchange_creds (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER,
  exchange_id TEXT,
  api_key     TEXT,            -- Fernet encrypted
  api_secret  TEXT,            -- Fernet encrypted
  api_pass    TEXT,            -- Fernet encrypted
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, exchange_id)
);

CREATE TABLE trade_confirmations (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER,
  symbol      TEXT,
  side        TEXT,
  price       REAL,
  amount      REAL,
  signal_data TEXT,            -- JSON
  status      TEXT DEFAULT 'pending',  -- 'pending' | 'confirmed' | 'rejected' | 'expired'
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
```

---

## 12. API Reference

### `run_arbitrage_scan(exchanges, trade_amount_usdt, symbols) → dict`

```python
# exchanges:         {exchange_id: ccxt.Exchange}
# trade_amount_usdt: float (default 1000.0)
# symbols:           list[str] | None (None = DEFAULT_CROSS_EXCHANGE_SYMBOLS)

result = {
  "cross_exchange": [CrossExchangeOpportunity, ...],
  "triangular":     [TriangularOpportunity, ...],
  "viable_count":   int,
  "summary_lines":  [str, ...],     # Markdown-formatted
  "scan_errors":    [ScanError, ...]
}
```

### `CrossExchangeOpportunity` fields

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | str | e.g. `"ETH/USDT"` |
| `buy_exchange` | str | Exchange ID to buy on |
| `sell_exchange` | str | Exchange ID to sell on |
| `buy_price` | float | Best ask on buy exchange |
| `sell_price` | float | Best bid on sell exchange |
| `spread_pct` | float | Gross spread % |
| `fee_pct` | float | Combined taker fees % |
| `withdrawal_usdt` | float | Flat transfer cost estimate |
| `net_profit_pct` | float | `spread_pct − fee_pct` |
| `net_profit_usdt` | float | Dollar estimate at notional |
| `viable` | bool | `net_profit_pct > MIN_PROFIT_PCT` |

### `TriangularOpportunity` fields

| Field | Type | Description |
|-------|------|-------------|
| `exchange` | str | Exchange ID |
| `path` | tuple[str,str,str] | Three symbol strings |
| `directions` | tuple[str,str,str] | `"buy"` or `"sell"` per leg |
| `start_usdt` | float | Notional start |
| `end_usdt` | float | End USDT after all 3 legs + fees |
| `net_profit_pct` | float | `(end−start)/start × 100` |
| `net_profit_usdt` | float | `end − start` |
| `viable` | bool | `net_profit_pct > MIN_PROFIT_PCT` |

### `generate_signal(ohlcv, symbol) → dict`

```python
signal = {
  "action":     "BUY" | "SELL" | "HOLD",
  "confidence": 0–100,
  "reason":     str   # human-readable summary
}
```

---

## 13. Deployment Guide

### Requirements

- Python 3.13+
- Linux VPS with public IP (for Paystack webhooks)
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- Paystack account at [paystack.com](https://paystack.com)
- Domain with HTTPS (use Certbot for free Let's Encrypt cert)

### Installation

```bash
# Clone repository
git clone <your-repo>
cd duysbot

# Install dependencies
pip install -r requirements.txt

# Generate encryption key (do this ONCE and store it safely)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Configure environment
cp .env.example .env
nano .env   # fill in all required values

# Run
python main.py
```

### systemd Service

```ini
# /etc/systemd/system/duysbot.service
[Unit]
Description=DUYS Trading Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/duysbot
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10
EnvironmentFile=/home/ubuntu/duysbot/.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable duysbot
sudo systemctl start duysbot
sudo journalctl -u duysbot -f
```

### Nginx Reverse Proxy

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
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

```bash
# Free SSL certificate
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

### Paystack Webhook Configuration

In your Paystack dashboard → Settings → API Keys & Webhooks:
- Webhook URL: `https://yourdomain.com/paystack/webhook`
- Copy the displayed secret to `PAYSTACK_WEBHOOK_SECRET` in `.env`

---

## 14. Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ENCRYPTION_KEY` | ✅ | Fernet key for API key encryption. Generate once; losing it makes all stored keys unreadable |
| `BOT_TOKEN` | ✅ | Telegram bot token from @BotFather |
| `ADMIN_IDS` | ✅ | Comma-separated Telegram user IDs with admin access |
| `SUPPORT_CHANNEL_ID` | — | Telegram channel ID for support message forwarding |
| `PAYSTACK_SECRET_KEY` | ✅ | `sk_live_...` or `sk_test_...` |
| `PAYSTACK_PUBLIC_KEY` | ✅ | `pk_live_...` or `pk_test_...` |
| `PAYSTACK_WEBHOOK_SECRET` | ✅ | From Paystack dashboard |
| `BOT_WEBHOOK_URL` | ✅ | `https://yourdomain.com` |
| `WEBHOOK_PORT` | — | Default `8080` |
| `USDT_APTOS_ADDRESS` | — | Leave blank to disable Aptos payment |
| `USDT_TRON_ADDRESS` | — | Leave blank to disable TRON payment |
| `USDT_BSC_ADDRESS` | — | Leave blank to disable BSC payment |
| `TRONGRID_API_KEY` | — | Free at trongrid.io |
| `BSCSCAN_API_KEY` | — | Free at bscscan.com/apis |
| `BINANCE_API_KEY/SECRET` | — | Optional default account |
| `BYBIT_API_KEY/SECRET` | — | Optional default account |
| `OKX_API_KEY/SECRET/PASSPHRASE` | — | Optional default account |
| `MEXC_API_KEY/SECRET` | — | Optional default account |
| `KUCOIN_API_KEY/SECRET/PASSPHRASE` | — | Optional default account |
| `COINBASE_API_KEY/SECRET` | — | Optional; Advanced Trade API |
| `BINGX_API_KEY/SECRET` | — | Optional |
| `GATEIO_API_KEY/SECRET` | — | Optional |
| `CRYPTOCOMPARE_API_KEY` | — | News sentiment layer. Free tier |
| `COINMARKETCAP_API_KEY` | — | Market data layer. Free basic |
| `NEWSAPI_KEY` | — | Additional news layer. Free tier |
| `DB_PATH` | — | Default `bot_data.db` |

---

## 15. Command Reference

### User Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome screen → free trial or main menu |
| `/dashboard` | Full overview: balance, positions, PnL, settings |
| `/balance` | Live exchange balance |
| `/start_trade` | Choose Auto or Manual mode and activate |
| `/stop_trade` | Deactivate trading (positions stay open) |
| `/positions` | Live open positions with unrealised PnL |
| `/health` | Per-trade health with entry, current price, PnL % |
| `/chart` | Live price + indicators + current signal |
| `/pnl` | Full profit & loss summary |
| `/history` | Last 10 closed trades |
| `/summary` | Trade cycle summary |
| `/export` | Download trade history as CSV |
| `/signals` | Last 10 signals with confidence and reason |
| `/arbitrage` | Arbitrage control panel: enable, token picker, scan now |
| `/settings` | Configure all parameters and toggles |
| `/exchanges` | List and switch connected exchanges |
| `/setalert SYMBOL above\|below PRICE` | Create price alert |
| `/myalerts` | View active price alerts |
| `/delalert <id>` | Remove a price alert |
| `/subscribe` | Subscribe or start free trial |
| `/mystatus` | Subscription status and history |
| `/referral` | Get referral link; view referral stats |
| `/timezone` | Set timezone for daily reports |
| `/status` | Bot and platform status |
| `/panic` | Emergency close all your open trades |
| `/support <message>` | Forward message to support channel |
| `/help` | Full command list with descriptions |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/grant <user_id>` | Grant lifetime access |
| `/user <user_id>` | Full user profile lookup |
| `/reply <user_id> <message>` | Reply to a user directly |
| `/broadcast <message>` | Announcement to all subscribers |
| `/subscribers` | List all active subscribers |
| `/close` | Emergency close ALL trades across ALL users |
| `/status` | Full platform stats including backup info |

### Settings Toggles (via `/settings`)

| Toggle | Setting Key | Default |
|--------|-------------|---------|
| 🎯 Take Profit % or Fixed | `take_profit`, `tp_mode` | 2.0%, pct |
| 🛑 Stop Loss % or Fixed | `stop_loss`, `sl_mode` | 1.0%, pct |
| 📉 Trailing Stop | `trailing_stop` | Off |
| ✅ Trade Confirmation | `confirm_trades` | Off |
| 📡 Signal Alerts | `signal_suggestions` | On |
| ⚡ Arb Alerts | `arb_alerts` | On |
| 🟢/🔴 Arbitrage Enabled | `arb_enabled` | On |
| 🤖/👆 Trade Mode | `trade_mode` | Auto |

---

## 16. Changelog

### v2.0 — May 2026

**New Exchanges**
- Added Coinbase Advanced Trade (0.60% taker fee)
- Added BingX (0.10% taker fee)
- Added Gate.io (0.20% taker fee)
- Setup notes for each new exchange displayed during API key entry

**Arbitrage Engine (major rewrite)**
- Per-opportunity fingerprint deduplication — new opps notify immediately, same opp suppressed 10 min
- User token picker: 24 tokens, toggleable from `/arbitrage → 🪙 Choose Tokens`
- `arb_enabled` toggle (feature on/off) separate from `arb_alerts` (notifications on/off)
- `ScanError` dataclass — non-fatal errors surface to users and admin instead of silent swallowing
- `ccxt.BadSymbol`, `ccxt.NetworkError`, `ccxt.ExchangeError` classified separately
- 4 new triangular paths added (ATOM, UNI, DOGE, LINK/ETH)
- 12 new scannable tokens added to the picker (FIL, INJ, SUI, SEI, etc.)
- `asyncio.to_thread` used for blocking ccxt calls — bot never freezes during scan

**Notifications (critical bug fix)**
- Fixed: `run_signal_suggestions` had broken indentation — log and send code was outside the for-loop, using stale variables. Signals were computed but never delivered.
- Fixed: `is_granted(update.effective_user)` TypeError — now correctly passes `uid: int`
- Fixed: `opp.gross_profit_pct` AttributeError — removed non-existent field references
- Added: actual Telegram `send_message` call with confidence bar, reason, and dismiss tip
- Fixed: `log_signal_to_db` placed outside the symbol loop (referenced undefined `symbol`)
- Cooldown reverted on send failure so notification retries next tick

**main.py**
- Fixed: `ensure_future` in `job_queue.run_once` — replaced with `app.post_init` hook (correct PTB pattern)
- Telegram `/` command menu now registers reliably on every startup

**database.py**
- Added `arb_enabled`, `arb_symbols` columns with migrations
- `get_all_subscribed_users` SELECT includes `arb_alerts` (removed redundant `get_settings()` call in scheduler)

**rate_limiter.py**
- Added `"arbitrage": 30` cooldown (scan is network-heavy)

---

*DUYS Trading Bot is provided for informational and automation purposes. Cryptocurrency trading carries significant financial risk. Past performance of signals or arbitrage detection does not guarantee future results. Always trade with funds you can afford to lose.*
