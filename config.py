"""
config.py - Bot configuration and environment variables
Copy .env.example to .env and fill in your values
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Security ─────────────────────────────────────────────────────────────────
# Generate once: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")
if not ENCRYPTION_KEY:
    raise RuntimeError(
        "ENCRYPTION_KEY is not set. Generate one with:\n"
        "  python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
        "Then add it to your .env file as ENCRYPTION_KEY=<value>"
    )

# ── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN          = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
ADMIN_IDS          = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
SUPPORT_CHANNEL_ID = os.getenv("SUPPORT_CHANNEL_ID", "")

# ── Exchange API Keys (per-user keys stored in DB) ────────────────────────────
BINANCE_API_KEY       = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET    = os.getenv("BINANCE_API_SECRET", "")
BYBIT_API_KEY         = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET      = os.getenv("BYBIT_API_SECRET", "")
OKX_API_KEY           = os.getenv("OKX_API_KEY", "")
OKX_API_SECRET        = os.getenv("OKX_API_SECRET", "")
OKX_PASSPHRASE        = os.getenv("OKX_PASSPHRASE", "")
MEXC_API_KEY          = os.getenv("MEXC_API_KEY", "")
MEXC_API_SECRET       = os.getenv("MEXC_API_SECRET", "")
KUCOIN_API_KEY        = os.getenv("KUCOIN_API_KEY", "")
KUCOIN_API_SECRET     = os.getenv("KUCOIN_API_SECRET", "")
KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "")

# Coinbase Advanced Trade
COINBASE_API_KEY      = os.getenv("COINBASE_API_KEY",  "")
COINBASE_API_SECRET   = os.getenv("COINBASE_API_SECRET", "")

# BingX
BINGX_API_KEY         = os.getenv("BINGX_API_KEY",  "")
BINGX_API_SECRET      = os.getenv("BINGX_API_SECRET", "")

# Gate.io
GATEIO_API_KEY        = os.getenv("GATEIO_API_KEY",  "")
GATEIO_API_SECRET     = os.getenv("GATEIO_API_SECRET", "")

# ── MEXC API key expiry (MEXC keys expire 90 days from creation) ──────────────
MEXC_KEY_EXPIRY_DAYS = 90

# ── Paystack ──────────────────────────────────────────────────────────────────
PAYSTACK_SECRET_KEY     = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY     = os.getenv("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_WEBHOOK_SECRET = os.getenv("PAYSTACK_WEBHOOK_SECRET", "")
WEBHOOK_PORT            = int(os.getenv("WEBHOOK_PORT", "8080"))
BOT_WEBHOOK_URL         = os.getenv("BOT_WEBHOOK_URL", "")

# ── Subscription Pricing (USD) ────────────────────────────────────────────────
# Prices shown to users and charged via Paystack / USDT crypto payments.
# Changing these takes effect immediately — no code change required.
PLAN_PRICE_1M  = float(os.getenv("PLAN_PRICE_1M",  "12.00"))   # 1-month plan
PLAN_PRICE_3M  = float(os.getenv("PLAN_PRICE_3M",  "34.00"))   # 3-month plan
PLAN_PRICE_6M  = float(os.getenv("PLAN_PRICE_6M",  "65.00"))   # 6-month plan

# Keep a dict form for easy lookups (months → price)
PLAN_PRICES: dict = {1: PLAN_PRICE_1M, 3: PLAN_PRICE_3M, 6: PLAN_PRICE_6M}

# Backward-compat alias used by paystack.py
SUBSCRIPTION_PRICE_USD = PLAN_PRICE_1M

# ── Crypto Payment — USDT wallet addresses per network ────────────────────────
# Set each address to YOUR wallet on that network.
# Leave blank to disable that network option.

# Aptos — USDT (LayerZero bridged), 6 decimals
USDT_APTOS_ADDRESS  = os.getenv("USDT_APTOS_ADDRESS", "")

# TRON — USDT TRC-20, 6 decimals
USDT_TRON_ADDRESS   = os.getenv("USDT_TRON_ADDRESS", "")

# BSC (BNB Smart Chain) — USDT BEP-20, 18 decimals
USDT_BSC_ADDRESS    = os.getenv("USDT_BSC_ADDRESS", "")

# API keys for on-chain verification (all free)
TRONGRID_API_KEY    = os.getenv("TRONGRID_API_KEY", "")   # trongrid.io
BSCSCAN_API_KEY     = os.getenv("BSCSCAN_API_KEY", "")    # bscscan.com/apis
# Aptos uses a public fullnode — no key needed

# Network metadata used across the codebase
CRYPTO_NETWORKS = {
    "aptos": {
        "label":    "Aptos 🔵",
        "token":    "USDT (LayerZero)",
        "address":  USDT_APTOS_ADDRESS,
        "decimals": 6,
        "note":     "Send USDT (LayerZero) on Aptos mainnet only",
    },
    "tron": {
        "label":    "TRON 🔴",
        "token":    "USDT (TRC-20)",
        "address":  USDT_TRON_ADDRESS,
        "decimals": 6,
        "note":     "Send USDT on the TRON (TRC-20) network only",
    },
    "bsc": {
        "label":    "BSC 🟡",
        "token":    "USDT (BEP-20)",
        "address":  USDT_BSC_ADDRESS,
        "decimals": 18,
        "note":     "Send USDT on BNB Smart Chain (BEP-20) only",
    },
}

# ── Free Trial ────────────────────────────────────────────────────────────────
FREE_TRIAL_DAYS = int(os.getenv("FREE_TRIAL_DAYS", "7"))  # once per account lifetime

# ── Referral Rewards ──────────────────────────────────────────────────────────
# Months of free access awarded to the referrer when a referred user subscribes.
REFERRAL_REWARD_MONTHS = int(os.getenv("REFERRAL_REWARD_MONTHS", "1"))

# ── Signal & News APIs ────────────────────────────────────────────────────────
CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY", "")
COINMARKETCAP_API_KEY = os.getenv("COINMARKETCAP_API_KEY", "")
NEWSAPI_KEY           = os.getenv("NEWSAPI_KEY", "")

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
DB_PATH          = os.getenv("DB_PATH",          "bot_data.db")
PERSISTENCE_FILE = os.getenv("PERSISTENCE_FILE", "bot_persistence.pickle")

# ── Scheduler ────────────────────────────────────────────────────────────────
TRADE_LOOP_INTERVAL = 60

# ── Feature flags & limits ────────────────────────────────────────────────────
MAX_DCA_PLANS         = int(os.getenv("MAX_DCA_PLANS",         "3"))
MAX_GRID_PLANS        = int(os.getenv("MAX_GRID_PLANS",        "2"))
MAX_SMART_ORDERS      = int(os.getenv("MAX_SMART_ORDERS",      "5"))
CORRELATION_THRESHOLD = float(os.getenv("CORRELATION_THRESHOLD","0.85"))
TV_WEBHOOK_SECRET_SALT = os.getenv("TV_WEBHOOK_SECRET_SALT", "")
if not TV_WEBHOOK_SECRET_SALT:
    import secrets as _secrets
    TV_WEBHOOK_SECRET_SALT = _secrets.token_hex(32)
    import warnings
    warnings.warn(
        "TV_WEBHOOK_SECRET_SALT is not set — using a random value for this session. "
        "Set TV_WEBHOOK_SECRET_SALT in your .env to make webhook tokens persistent across restarts.",
        stacklevel=2,
    )

# ── Arbitrage Engine ─────────────────────────────────────────────────────────
# Minimum net profit % after ALL fees to flag an arb opportunity.
ARB_MIN_PROFIT_PCT           = float(os.getenv("ARB_MIN_PROFIT_PCT",            "0.3"))
# Flat cost (USDT) assumed per cross-exchange withdrawal/transfer.
ARB_WITHDRAWAL_FEE_USDT      = float(os.getenv("ARB_WITHDRAWAL_FEE_USDT",       "1.5"))
# Per-exchange taker fees (fraction). Override individually as needed.
ARB_FEE_BINANCE  = float(os.getenv("ARB_FEE_BINANCE",  "0.001"))
ARB_FEE_BYBIT    = float(os.getenv("ARB_FEE_BYBIT",    "0.001"))
ARB_FEE_OKX      = float(os.getenv("ARB_FEE_OKX",      "0.001"))
ARB_FEE_MEXC     = float(os.getenv("ARB_FEE_MEXC",     "0.002"))
ARB_FEE_KUCOIN   = float(os.getenv("ARB_FEE_KUCOIN",   "0.001"))
ARB_FEE_COINBASE = float(os.getenv("ARB_FEE_COINBASE", "0.006"))
ARB_FEE_BINGX    = float(os.getenv("ARB_FEE_BINGX",    "0.001"))
ARB_FEE_GATEIO   = float(os.getenv("ARB_FEE_GATEIO",   "0.002"))
ARB_FEE_DEFAULT  = float(os.getenv("ARB_FEE_DEFAULT",  "0.001"))

# ── Circuit Breaker ───────────────────────────────────────────────────────────
# Halt ALL new auto-trades for a user if their daily loss exceeds this percent.
# Set to 0 to disable. Example: 5.0 = stop trading after -5% daily drawdown.
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "5.0"))

# Paper trading defaults
PAPER_DEFAULT_BALANCE = float(os.getenv("PAPER_DEFAULT_BALANCE","1000.0"))
