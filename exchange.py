"""
exchange.py - Unified exchange connector via ccxt
Supports: Binance, Bybit, OKX, MEXC, KuCoin, Coinbase Advanced, BingX, Gate.io
"""

import ccxt
import logging

logger = logging.getLogger(__name__)

SUPPORTED_EXCHANGES = {
    "binance":  ccxt.binance,
    "bybit":    ccxt.bybit,
    "okx":      ccxt.okx,
    "mexc":     ccxt.mexc,
    "kucoin":   ccxt.kucoin,
    "coinbase": ccxt.coinbase,   # Coinbase Advanced Trade
    "bingx":    ccxt.bingx,
    "gateio":   ccxt.gate,
}

EXCHANGE_LABELS = {
    "binance":  "Binance 🟡",
    "bybit":    "Bybit 🔵",
    "okx":      "OKX ⚫",
    "mexc":     "MEXC 🟢",
    "kucoin":   "KuCoin 🟠",
    "coinbase": "Coinbase 🔵",
    "bingx":    "BingX 🟣",
    "gateio":   "Gate.io 🔴",
    "":         "Not Set ⚠️",
}

# Human-readable setup notes shown during API key onboarding
EXCHANGE_NOTES = {
    "coinbase": (
        "Use <b>Coinbase Advanced Trade</b> API keys (not old Pro keys).\n"
        "Create at: coinbase.com → Settings → API → New API Key.\n"
        "Permissions needed: <b>View</b> + <b>Trade</b>."
    ),
    "bingx": (
        "Create BingX API keys at: bingx.com → Account → API Management.\n"
        "Permissions needed: <b>Read</b> + <b>Spot Trade</b>."
    ),
    "gateio": (
        "Create Gate.io API keys at: gate.io → Account → API Management.\n"
        "Permissions needed: <b>Read only</b> + <b>Spot Trade</b>."
    ),
}


def get_exchange_label(exchange_id: str) -> str:
    """Return display label. Shows 'Not Set' for empty/None exchange."""
    return EXCHANGE_LABELS.get(exchange_id or "", "Not Set ⚠️")


def get_exchange_note(exchange_id: str) -> str:
    """Return extra setup note for exchanges that need special instructions."""
    return EXCHANGE_NOTES.get(exchange_id or "", "")


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


def get_min_trade_amount(exchange: ccxt.Exchange, symbol: str) -> float:
    """
    Return the minimum order size in USDT for the given symbol.
    Returns 0.0 if the exchange doesn't publish limits (safe fallback).
    """
    try:
        markets = exchange.load_markets()
        market  = markets.get(symbol, {})
        limits  = market.get("limits", {})
        amount  = limits.get("cost", {}).get("min") or limits.get("amount", {}).get("min", 0)
        price   = (market.get("info") or {}).get("lastPrice") or 1
        # cost min is in quote (USDT), amount min is in base — convert
        if limits.get("cost", {}).get("min"):
            return float(limits["cost"]["min"])
        elif limits.get("amount", {}).get("min"):
            return float(limits["amount"]["min"]) * float(price)
        return 0.0
    except Exception as e:
        logger.warning(f"get_min_trade_amount error ({symbol}): {e}")
        return 0.0


# ── Paper order simulation ────────────────────────────────────────────────────

def place_paper_order(symbol: str, side: str, amount_usdt: float, current_price: float) -> dict:
    """Simulate a market order without hitting the exchange."""
    import time, uuid
    qty = amount_usdt / current_price if current_price > 0 else 0
    return {
        "id":        str(uuid.uuid4())[:8],
        "symbol":    symbol,
        "side":      side,
        "type":      "market",
        "price":     current_price,
        "amount":    qty,
        "filled":    qty,
        "cost":      amount_usdt,
        "status":    "closed",
        "timestamp": int(time.time() * 1000),
        "paper":     True,
    }


# ── Limit order helpers ───────────────────────────────────────────────────────

def place_limit_order(exchange: ccxt.Exchange, symbol: str, side: str,
                      amount: float, price: float) -> dict:
    """Place a limit order. amount is in base currency units."""
    try:
        qty = exchange.amount_to_precision(symbol, amount)
        pr  = exchange.price_to_precision(symbol, price)
        if side == "buy":
            return exchange.create_limit_buy_order(symbol, float(qty), float(pr))
        return exchange.create_limit_sell_order(symbol, float(qty), float(pr))
    except Exception as e:
        logger.error(f"place_limit_order error {symbol} {side}: {e}", exc_info=True)
        raise


def cancel_order(exchange: ccxt.Exchange, symbol: str, order_id: str) -> dict:
    try:
        return exchange.cancel_order(order_id, symbol)
    except Exception as e:
        logger.warning(f"cancel_order {order_id}: {e}")
        raise


# ── Rate-limit-aware ExchangeQueue ────────────────────────────────────────────

import asyncio
import functools
from typing import Callable, Any

class ExchangeQueue:
    """
    Wraps a ccxt Exchange to add automatic retry on RateLimitExceeded
    with exponential backoff.  NetworkError retries up to 3 times.
    ExchangeError is raised immediately (no retry).

    Usage:
        queue = ExchangeQueue(exchange)
        result = await queue.call(exchange.fetch_ticker, "BTC/USDT")
    """

    MAX_RATE_RETRIES    = 5
    MAX_NETWORK_RETRIES = 3
    BASE_DELAY          = 1.0
    MAX_DELAY           = 60.0
    WARN_AFTER_FAILURES = 5

    def __init__(self, exchange: ccxt.Exchange):
        self.exchange             = exchange
        self._consecutive_failures = 0

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reset_failures(self):
        self._consecutive_failures = 0

    async def call(self, fn: Callable, *args, **kwargs) -> Any:
        rate_attempts    = 0
        network_attempts = 0

        while True:
            try:
                loop   = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, functools.partial(fn, *args, **kwargs)
                )
                self.reset_failures()
                return result

            except ccxt.RateLimitExceeded:
                rate_attempts += 1
                if rate_attempts > self.MAX_RATE_RETRIES:
                    self._consecutive_failures += 1
                    raise
                delay = min(self.BASE_DELAY * (2 ** rate_attempts), self.MAX_DELAY)
                logger.warning(
                    f"[QUEUE] {self.exchange.id} rate-limited; retry {rate_attempts} "
                    f"in {delay:.1f}s"
                )
                await asyncio.sleep(delay)

            except ccxt.NetworkError:
                network_attempts += 1
                if network_attempts > self.MAX_NETWORK_RETRIES:
                    self._consecutive_failures += 1
                    raise
                await asyncio.sleep(2.0 * network_attempts)

            except ccxt.ExchangeError:
                self._consecutive_failures += 1
                raise

            except Exception:
                self._consecutive_failures += 1
                raise


# Module-level queue registry: (user_id, exchange_id) → ExchangeQueue
_queues: dict[tuple, ExchangeQueue] = {}

def get_exchange_queue(user_id: int, exchange: ccxt.Exchange) -> ExchangeQueue:
    """Return (or create) the ExchangeQueue for this user+exchange."""
    key = (user_id, exchange.id)
    if key not in _queues:
        _queues[key] = ExchangeQueue(exchange)
    return _queues[key]
