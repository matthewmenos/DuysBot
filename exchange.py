"""
exchange.py - Unified exchange connector via ccxt
Supports: Binance, Bybit, OKX, MEXC, KuCoin
"""

import ccxt
import logging

logger = logging.getLogger(__name__)

SUPPORTED_EXCHANGES = {
    "binance": ccxt.binance,
    "bybit":   ccxt.bybit,
    "okx":     ccxt.okx,
    "mexc":    ccxt.mexc,
    "kucoin":  ccxt.kucoin,
}

EXCHANGE_LABELS = {
    "binance": "Binance 🟡",
    "bybit":   "Bybit 🔵",
    "okx":     "OKX ⚫",
    "mexc":    "MEXC 🟢",
    "kucoin":  "KuCoin 🟠",
}

# Exchanges requiring a passphrase in addition to key + secret
PASSPHRASE_EXCHANGES = {"okx", "kucoin"}


def get_exchange(exchange_id: str, api_key: str, api_secret: str, api_pass: str = "") -> ccxt.Exchange:
    """Instantiate a ccxt exchange object with credentials."""
    if exchange_id not in SUPPORTED_EXCHANGES:
        raise ValueError(f"Unsupported exchange: {exchange_id}")
    cls    = SUPPORTED_EXCHANGES[exchange_id]
    params = {
        "apiKey":          api_key,
        "secret":          api_secret,
        "enableRateLimit": True,
        "options":         {"defaultType": "spot"},
    }
    if exchange_id in PASSPHRASE_EXCHANGES and api_pass:
        params["password"] = api_pass
    return cls(params)


def check_key_format(exchange_id: str, api_key: str, api_secret: str, api_pass: str = "") -> dict:
    """
    Fast format-only check — no network call, never freezes the bot.
    Returns {"valid": True} or {"valid": False, "error": "reason"}.
    Real connectivity is confirmed lazily on first /balance call.
    """
    api_key    = (api_key    or "").strip()
    api_secret = (api_secret or "").strip()
    api_pass   = (api_pass   or "").strip()

    if not api_key or not api_secret:
        return {"valid": False, "error": "API key and secret cannot be empty."}

    if len(api_key) < 8 or len(api_secret) < 8:
        return {"valid": False, "error": "API key or secret is too short — please double-check."}

    if exchange_id in PASSPHRASE_EXCHANGES and not api_pass:
        label = EXCHANGE_LABELS.get(exchange_id, exchange_id)
        return {"valid": False, "error": f"{label} requires a passphrase in addition to key and secret."}

    return {"valid": True}


# ── Exchange data helpers (synchronous — called from scheduler threads) ───────

def fetch_balance(exchange: ccxt.Exchange) -> dict:
    """Return non-zero coin balances."""
    try:
        bal    = exchange.fetch_balance()
        result = {}
        for coin in ["USDT", "BTC", "ETH", "BNB", "SOL", "XRP", "USDC"]:
            free  = float((bal.get(coin) or {}).get("free",  0) or 0)
            total = float((bal.get(coin) or {}).get("total", 0) or 0)
            if total > 0:
                result[coin] = {"free": round(free, 6), "total": round(total, 6)}
        return result
    except Exception as e:
        logger.error(f"fetch_balance error: {e}")
        raise


def fetch_usdt_balance(exchange: ccxt.Exchange) -> float:
    """Return free USDT balance — used for trade amount validation."""
    try:
        bal = exchange.fetch_balance()
        return float((bal.get("USDT") or {}).get("free", 0) or 0)
    except Exception as e:
        logger.error(f"fetch_usdt_balance error: {e}")
        raise


def fetch_ticker(exchange: ccxt.Exchange, symbol: str) -> dict:
    try:
        t = exchange.fetch_ticker(symbol)
        return {
            "last":       t["last"],
            "change_pct": round(t.get("percentage", 0) or 0, 2),
            "high":       t["high"],
            "low":        t["low"],
            "volume":     t.get("quoteVolume", 0),
        }
    except Exception as e:
        logger.error(f"fetch_ticker error: {e}")
        raise


def fetch_ohlcv(exchange: ccxt.Exchange, symbol: str, timeframe: str = "1h", limit: int = 100) -> list:
    try:
        return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except Exception as e:
        logger.error(f"fetch_ohlcv error: {e}")
        raise


def place_market_order(exchange: ccxt.Exchange, symbol: str, side: str, amount_usdt: float) -> dict:
    try:
        ticker = exchange.fetch_ticker(symbol)
        price  = ticker["last"]
        qty    = exchange.amount_to_precision(symbol, amount_usdt / price)
        if side == "buy":
            order = exchange.create_market_buy_order(symbol, float(qty))
        else:
            order = exchange.create_market_sell_order(symbol, float(qty))
        logger.info(f"Order placed: {side} {qty} {symbol} @ ~{price}")
        return order
    except Exception as e:
        logger.error(f"place_market_order error: {e}")
        raise


def close_all_positions(exchange: ccxt.Exchange, open_trades: list) -> list:
    results = []
    for trade in open_trades:
        try:
            ticker = exchange.fetch_ticker(trade["symbol"])
            price  = ticker["last"]
            qty    = trade["amount"] / trade["entry_price"]
            exchange.create_market_sell_order(trade["symbol"], qty)
            results.append({"symbol": trade["symbol"], "status": "closed", "price": price})
        except Exception as e:
            results.append({"symbol": trade["symbol"], "status": f"error: {e}"})
    return results
