"""
config.py - Bot configuration and environment variables
Copy .env.example to .env and fill in your values
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
ADMIN_IDS  = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# Private channel/group where support messages are forwarded.
# Bot must be admin of this channel. Use negative ID e.g. -1001234567890
# Get it by forwarding a message from the channel to @userinfobot
SUPPORT_CHANNEL_ID = os.getenv("SUPPORT_CHANNEL_ID", "")

# ── Exchange API Keys (per-user keys stored in DB; these are optional fallbacks) ──
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

BYBIT_API_KEY      = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET   = os.getenv("BYBIT_API_SECRET", "")

OKX_API_KEY        = os.getenv("OKX_API_KEY", "")
OKX_API_SECRET     = os.getenv("OKX_API_SECRET", "")
OKX_PASSPHRASE     = os.getenv("OKX_PASSPHRASE", "")

MEXC_API_KEY       = os.getenv("MEXC_API_KEY", "")
MEXC_API_SECRET    = os.getenv("MEXC_API_SECRET", "")

KUCOIN_API_KEY        = os.getenv("KUCOIN_API_KEY", "")
KUCOIN_API_SECRET     = os.getenv("KUCOIN_API_SECRET", "")
KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "")

# ── Paystack ──────────────────────────────────────────────────────────────────
PAYSTACK_SECRET_KEY     = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY     = os.getenv("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_WEBHOOK_SECRET = os.getenv("PAYSTACK_WEBHOOK_SECRET", "")
SUBSCRIPTION_PRICE_USD  = 12.00
SUBSCRIPTION_PRICE_KES  = 1560
WEBHOOK_PORT            = int(os.getenv("WEBHOOK_PORT", "8080"))
BOT_WEBHOOK_URL         = os.getenv("BOT_WEBHOOK_URL", "")

# ── Signal & News APIs ────────────────────────────────────────────────────────
CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY", "")   # free at cryptocompare.com
COINMARKETCAP_API_KEY = os.getenv("COINMARKETCAP_API_KEY", "")   # free basic at coinmarketcap.com
NEWSAPI_KEY           = os.getenv("NEWSAPI_KEY", "")             # newsapi.org

# ── Trading Defaults ──────────────────────────────────────────────────────────
DEFAULT_TAKE_PROFIT  = 2.0
DEFAULT_STOP_LOSS    = 1.0
DEFAULT_TRADE_AMOUNT = 10.0
DEFAULT_SYMBOL       = "BTC/USDT"
QUOTE_CURRENCY       = "USDT"

POPULAR_SYMBOLS = [
    "BTC/USDT",  "ETH/USDT",  "BNB/USDT",  "SOL/USDT",
    "XRP/USDT",  "ADA/USDT",  "DOGE/USDT", "TON/USDT",
    "AVAX/USDT", "LINK/USDT", "DOT/USDT",  "MATIC/USDT",
    "LTC/USDT",  "UNI/USDT",  "ATOM/USDT", "TRX/USDT",
    "NEAR/USDT", "APT/USDT",  "OP/USDT",   "ARB/USDT",
]

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "bot_data.db")

# ── Scheduler ────────────────────────────────────────────────────────────────
TRADE_LOOP_INTERVAL = 60
