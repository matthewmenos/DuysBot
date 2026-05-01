"""
config.py - Bot configuration and environment variables
Copy .env.example to .env and fill in your values
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# ── Exchange API Keys (per user, stored in DB; these are fallback/test keys) ──
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

BYBIT_API_KEY      = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET   = os.getenv("BYBIT_API_SECRET", "")

OKX_API_KEY        = os.getenv("OKX_API_KEY", "")
OKX_API_SECRET     = os.getenv("OKX_API_SECRET", "")
OKX_PASSPHRASE     = os.getenv("OKX_PASSPHRASE", "")

MEXC_API_KEY       = os.getenv("MEXC_API_KEY", "")
MEXC_API_SECRET    = os.getenv("MEXC_API_SECRET", "")

# ── Paystack ──────────────────────────────────────────────────────────────────
PAYSTACK_SECRET_KEY    = os.getenv("PAYSTACK_SECRET_KEY", "")    # sk_live_... or sk_test_...
PAYSTACK_PUBLIC_KEY    = os.getenv("PAYSTACK_PUBLIC_KEY", "")    # pk_live_... or pk_test_...
PAYSTACK_WEBHOOK_SECRET = os.getenv("PAYSTACK_WEBHOOK_SECRET", "") # for HMAC verification
SUBSCRIPTION_PRICE_USD = 12.00          # USD per month
SUBSCRIPTION_PRICE_KES = 1560           # KES fallback (approx $12 at ~130 KES/USD)
WEBHOOK_PORT           = int(os.getenv("WEBHOOK_PORT", "8080"))  # local port for webhook server
BOT_WEBHOOK_URL        = os.getenv("BOT_WEBHOOK_URL", "")       # e.g. https://yourdomain.com

# ── News / Signal APIs ────────────────────────────────────────────────────────
CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY", "")  # Free tier available
NEWSAPI_KEY           = os.getenv("NEWSAPI_KEY", "")            # newsapi.org

# ── Trading Defaults ──────────────────────────────────────────────────────────
DEFAULT_TAKE_PROFIT   = 2.0    # %
DEFAULT_STOP_LOSS     = 1.0    # %
DEFAULT_TRADE_AMOUNT  = 10.0   # USDT per trade
DEFAULT_SYMBOL        = "BTC/USDT"
QUOTE_CURRENCY        = "USDT"  # Quote side for all pairs

# Popular preset symbols shown as quick-pick buttons in /settings
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
TRADE_LOOP_INTERVAL = 60   # seconds between each auto-trade scan
