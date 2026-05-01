"""
exchange.py - Unified exchange connector via ccxt
Supports: Binance, Bybit, OKX, MEXC
"""

import ccxt
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SUPPORTED_EXCHANGES = {
    "binance": ccxt.binance,
    "bybit":   ccxt.bybit,
    "okx":     ccxt.okx,
    "mexc":    ccxt.mexc,
}

EXCHANGE_LABELS = {
    "binance": "Binance 🟡",
    "bybit":   "Bybit 🔵",
    "okx":     "OKX ⚫",
    "mexc":    "MEXC 🟢",
}


def get_exchange(exchange_id: str, api_key: str, api_secret: str, api_pass: str = "") -> ccxt.Exchange:
    """Instantiate a ccxt exchange object with user credentials."""
    if exchange_id not in SUPPORTED_EXCHANGES:
        raise ValueError(f"Unsupported exchange: {exchange_id}")

    cls = SUPPORTED_EXCHANGES[exchange_id]
    params = {
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    }
    if exchange_id == "okx" and api_pass:
        params["password"] = api_pass

    return cls(params)


def fetch_balance(exchange: ccxt.Exchange) -> dict:
    """Return USDT and main coin balances."""
    try:
        bal = exchange.fetch_balance()
        result = {}
        for coin in ["USDT", "BTC", "ETH", "BNB", "SOL", "XRP"]:
            free  = bal.get(coin, {}).get("free", 0) or 0
            total = bal.get(coin, {}).get("total", 0) or 0
            if total > 0:
                result[coin] = {"free": round(free, 6), "total": round(total, 6)}
        return result
    except Exception as e:
        logger.error(f"fetch_balance error: {e}")
        raise


def fetch_ticker(exchange: ccxt.Exchange, symbol: str) -> dict:
    """Return last price, 24h change, high, low."""
    try:
        ticker = exchange.fetch_ticker(symbol)
        return {
            "last":       ticker["last"],
            "change_pct": round(ticker.get("percentage", 0) or 0, 2),
            "high":       ticker["high"],
            "low":        ticker["low"],
            "volume":     ticker.get("quoteVolume", 0),
        }
    except Exception as e:
        logger.error(f"fetch_ticker error: {e}")
        raise


def fetch_ohlcv(exchange: ccxt.Exchange, symbol: str, timeframe: str = "1h", limit: int = 100) -> list:
    """Return OHLCV candles as list of [ts, open, high, low, close, vol]."""
    try:
        return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except Exception as e:
        logger.error(f"fetch_ohlcv error: {e}")
        raise


def place_market_order(exchange: ccxt.Exchange, symbol: str, side: str, amount_usdt: float) -> dict:
    """Place a market order. amount_usdt is in quote currency (USDT)."""
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
    """Emergency close: sell everything in open_trades for this user."""
    results = []
    for trade in open_trades:
        try:
            symbol = trade["symbol"]
            ticker = exchange.fetch_ticker(symbol)
            price  = ticker["last"]
            # Sell the original buy amount
            qty = trade["amount"] / trade["entry_price"]
            order = exchange.create_market_sell_order(symbol, qty)
            results.append({"symbol": symbol, "status": "closed", "price": price})
        except Exception as e:
            results.append({"symbol": trade["symbol"], "status": f"error: {e}"})
    return results
