"""
config.py - Bot configuration and environment variables
Copy .env.example to .env and fill in your values
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Security ─────────────────────────────────────────────────────────────────
# Generate once: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")  # Leave blank to skip encryption (not recommended)

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
SUBSCRIPTION_PRICE_USD  = 12.00
WEBHOOK_PORT            = int(os.getenv("WEBHOOK_PORT", "8080"))
BOT_WEBHOOK_URL         = os.getenv("BOT_WEBHOOK_URL", "")

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
FREE_TRIAL_DAYS = 7  # once per account lifetime

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
DB_PATH = os.getenv("DB_PATH", "bot_data.db")

# ── Scheduler ────────────────────────────────────────────────────────────────
TRADE_LOOP_INTERVAL = 60
