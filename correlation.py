"""
correlation.py - Pearson correlation between asset returns.
Used to prevent trading highly correlated pairs simultaneously.
"""

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CORRELATION_CACHE_TTL = 4 * 3600   # 4 hours


def compute_correlation(returns_a: list[float], returns_b: list[float]) -> Optional[float]:
    """Pearson correlation coefficient between two equal-length return series."""
    n = min(len(returns_a), len(returns_b))
    if n < 7:
        return None
    a, b = returns_a[-n:], returns_b[-n:]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    num   = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    den_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    den_b = math.sqrt(sum((x - mean_b) ** 2 for x in b))
    if den_a == 0 or den_b == 0:
        return None
    return round(num / (den_a * den_b), 4)


def _ohlcv_to_daily_returns(ohlcv: list) -> list[float]:
    """Convert OHLCV candles to log returns."""
    closes = [c[4] for c in ohlcv if c[4] > 0]
    if len(closes) < 2:
        return []
    return [(closes[i] / closes[i - 1]) - 1.0 for i in range(1, len(closes))]


def get_correlation_matrix(
    exchange,
    symbols:  list[str],
    days:     int = 30,
    bot_data: dict = None,
) -> dict[tuple, float]:
    """
    Fetch 30d daily OHLCV for each symbol and compute pairwise Pearson correlations.
    Results are cached in bot_data with TTL.
    Returns {(sym_a, sym_b): correlation} for all unique pairs.
    """
    import time
    from exchange import fetch_ohlcv

    now         = time.time()
    cache_key   = f"corr_{exchange.id}_{'_'.join(sorted(symbols))}"
    matrix      = {}

    if bot_data is not None:
        cached = bot_data.get(cache_key)
        if cached and now - cached.get("ts", 0) < CORRELATION_CACHE_TTL:
            return cached["data"]

    # Fetch daily candles for each symbol
    returns: dict[str, list[float]] = {}
    for sym in symbols:
        try:
            ohlcv = fetch_ohlcv(exchange, sym, "1d", days + 5)
            returns[sym] = _ohlcv_to_daily_returns(ohlcv)
        except Exception as e:
            logger.debug(f"[CORR] Could not fetch {sym}: {e}")

    for i, sym_a in enumerate(symbols):
        for sym_b in symbols[i + 1:]:
            ra = returns.get(sym_a, [])
            rb = returns.get(sym_b, [])
            c  = compute_correlation(ra, rb)
            if c is not None:
                matrix[(sym_a, sym_b)] = c
                matrix[(sym_b, sym_a)] = c

    if bot_data is not None:
        bot_data[cache_key] = {"ts": now, "data": matrix}

    return matrix


def is_too_correlated(
    exchange,
    new_symbol:   str,
    held_symbols: list[str],
    threshold:    float = 0.85,
    bot_data:     dict  = None,
) -> tuple[bool, Optional[str], Optional[float]]:
    """
    Return (too_correlated, correlated_with_symbol, correlation_value).
    """
    if not held_symbols:
        return False, None, None

    all_syms = list(set([new_symbol] + held_symbols))
    matrix   = get_correlation_matrix(exchange, all_syms, bot_data=bot_data)

    for held in held_symbols:
        corr = matrix.get((new_symbol, held))
        if corr is not None and abs(corr) >= threshold:
            return True, held, corr

    return False, None, None
