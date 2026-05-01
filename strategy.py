"""
strategy.py - Trading signal engine
Uses: RSI, EMA crossover, MACD, volume spike + optional news sentiment
Compatible with numpy>=2.0.0 and Python 3.13+
"""

import logging
import requests
from config import CRYPTOCOMPARE_API_KEY, NEWSAPI_KEY

logger = logging.getLogger(__name__)


# ── Technical Indicators (pure Python — no numpy dependency for indicators) ───

def compute_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0.0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0.0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_ema(closes: list, period: int) -> float:
    if len(closes) < period:
        return closes[-1]
    k = 2 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 6)


def compute_macd(closes: list):
    if len(closes) < 26:
        return 0, 0, 0
    ema12 = compute_ema(closes, 12)
    ema26 = compute_ema(closes, 26)
    macd_line   = ema12 - ema26
    signal_line = macd_line * 0.9   # simplified 9-period EMA of MACD
    histogram   = macd_line - signal_line
    return round(macd_line, 6), round(signal_line, 6), round(histogram, 6)


def compute_bollinger(closes: list, period: int = 20):
    if len(closes) < period:
        return closes[-1], closes[-1], closes[-1]
    window = closes[-period:]
    mid    = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std    = variance ** 0.5
    return round(mid + 2 * std, 4), round(mid, 4), round(mid - 2 * std, 4)


def _mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


# ── News Sentiment (optional, graceful fallback) ───────────────────────────────

def get_news_sentiment(symbol: str) -> float:
    """
    Returns sentiment score: +1 bullish, -1 bearish, 0 neutral.
    Uses CryptoCompare news API (free tier).
    """
    base_coin = symbol.split("/")[0]
    try:
        if not CRYPTOCOMPARE_API_KEY:
            return 0.0
        url = "https://min-api.cryptocompare.com/data/v2/news/"
        params = {"categories": base_coin, "api_key": CRYPTOCOMPARE_API_KEY, "lang": "EN"}
        resp = requests.get(url, params=params, timeout=5)
        data = resp.json().get("Data", [])[:10]

        positive_words = ["surge", "rally", "bull", "gain", "rise", "high", "pump",
                          "breakout", "adoption", "bullish"]
        negative_words = ["crash", "drop", "bear", "loss", "fall", "low", "dump",
                          "fear", "ban", "bearish", "hack"]

        score = 0
        for article in data:
            title = (article.get("title", "") + " " + article.get("body", "")).lower()
            score += sum(1 for w in positive_words if w in title)
            score -= sum(1 for w in negative_words if w in title)

        return max(-1.0, min(1.0, score / max(len(data), 1)))
    except Exception as e:
        logger.warning(f"News sentiment fetch failed: {e}")
        return 0.0


# ── Main Signal Generator ─────────────────────────────────────────────────────

def generate_signal(ohlcv: list, symbol: str = "") -> dict:
    """
    Analyses OHLCV data and returns a trading signal dict:
    {
        "action":     "BUY" | "SELL" | "HOLD",
        "confidence": 0-100,
        "reason":     str,
        "indicators": {...}
    }
    """
    if len(ohlcv) < 30:
        return {"action": "HOLD", "confidence": 0, "reason": "Insufficient data", "indicators": {}}

    closes  = [c[4] for c in ohlcv]
    volumes = [c[5] for c in ohlcv]

    rsi             = compute_rsi(closes)
    ema9            = compute_ema(closes, 9)
    ema21           = compute_ema(closes, 21)
    macd, sig, hist = compute_macd(closes)
    bb_up, bb_mid, bb_low = compute_bollinger(closes)
    current_price   = closes[-1]

    avg_volume   = _mean(volumes[-20:])
    last_volume  = volumes[-1]
    volume_spike = last_volume > avg_volume * 1.5

    news_score = get_news_sentiment(symbol) if symbol else 0.0

    score   = 0
    reasons = []

    # RSI
    if rsi < 30:
        score += 30
        reasons.append(f"RSI oversold ({rsi})")
    elif rsi > 70:
        score -= 30
        reasons.append(f"RSI overbought ({rsi})")
    else:
        reasons.append(f"RSI neutral ({rsi})")

    # EMA crossover
    if ema9 > ema21:
        score += 20
        reasons.append("EMA9 > EMA21 (bullish cross)")
    else:
        score -= 20
        reasons.append("EMA9 < EMA21 (bearish cross)")

    # MACD
    if hist > 0:
        score += 15
        reasons.append("MACD histogram positive")
    else:
        score -= 15
        reasons.append("MACD histogram negative")

    # Bollinger Bands
    if current_price < bb_low:
        score += 20
        reasons.append("Price below lower Bollinger Band")
    elif current_price > bb_up:
        score -= 20
        reasons.append("Price above upper Bollinger Band")

    # Volume spike
    if volume_spike:
        score = int(score * 1.2)
        reasons.append("Volume spike detected")

    # News sentiment
    if news_score > 0.3:
        score += 10
        reasons.append(f"Positive news sentiment ({news_score:.2f})")
    elif news_score < -0.3:
        score -= 10
        reasons.append(f"Negative news sentiment ({news_score:.2f})")

    confidence = min(abs(score), 100)

    if score >= 30:
        action = "BUY"
    elif score <= -30:
        action = "SELL"
    else:
        action = "HOLD"

    return {
        "action":     action,
        "confidence": confidence,
        "reason":     " | ".join(reasons),
        "indicators": {
            "rsi":    rsi,
            "ema9":   ema9,
            "ema21":  ema21,
            "macd":   macd,
            "price":  current_price,
            "bb_up":  bb_up,
            "bb_low": bb_low,
            "news":   round(news_score, 2),
        }
    }