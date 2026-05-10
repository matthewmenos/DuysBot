"""
strategy.py - Trading signal engine
Sources: Technical indicators + CryptoCompare news + CoinMarketCap data
Pure Python — no numpy required. Compatible with Python 3.13+
"""

import logging
import requests
from config import CRYPTOCOMPARE_API_KEY, COINMARKETCAP_API_KEY

logger = logging.getLogger(__name__)


# ── Technical Indicators ──────────────────────────────────────────────────────

def _mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def compute_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0.0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0.0 for d in deltas[-period:]]
    avg_gain = _mean(gains)
    avg_loss = _mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_ema(closes: list, period: int) -> float:
    if len(closes) < period:
        return closes[-1]
    k   = 2 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 6)


def compute_macd(closes: list):
    if len(closes) < 26:
        return 0.0, 0.0, 0.0
    ema12       = compute_ema(closes, 12)
    ema26       = compute_ema(closes, 26)
    macd_line   = ema12 - ema26
    signal_line = macd_line * 0.9
    histogram   = macd_line - signal_line
    return round(macd_line, 6), round(signal_line, 6), round(histogram, 6)


def compute_bollinger(closes: list, period: int = 20):
    if len(closes) < period:
        p = closes[-1]
        return p, p, p
    window   = closes[-period:]
    mid      = _mean(window)
    variance = _mean([(x - mid) ** 2 for x in window])
    std      = variance ** 0.5
    return round(mid + 2 * std, 4), round(mid, 4), round(mid - 2 * std, 4)


# ── CryptoCompare News Sentiment ──────────────────────────────────────────────

def get_news_sentiment(symbol: str) -> float:
    """Returns -1.0 (bearish) to +1.0 (bullish). Defaults to 0 on failure."""
    base = symbol.split("/")[0]
    try:
        if not CRYPTOCOMPARE_API_KEY:
            return 0.0
        resp = requests.get(
            "https://min-api.cryptocompare.com/data/v2/news/",
            params={"categories": base, "api_key": CRYPTOCOMPARE_API_KEY, "lang": "EN"},
            timeout=5,
        )
        articles = resp.json().get("Data", [])[:10]
        pos = ["surge", "rally", "bull", "gain", "rise", "breakout", "adoption", "bullish", "pump", "high"]
        neg = ["crash", "drop", "bear", "loss", "fall", "ban", "bearish", "hack", "fear", "dump", "low"]
        score = 0
        for a in articles:
            text = (a.get("title", "") + " " + a.get("body", "")).lower()
            score += sum(1 for w in pos if w in text)
            score -= sum(1 for w in neg if w in text)
        return round(max(-1.0, min(1.0, score / max(len(articles), 1))), 2)
    except Exception as e:
        logger.warning(f"CryptoCompare news failed: {e}")
        return 0.0


# ── CoinMarketCap Data ────────────────────────────────────────────────────────

def get_cmc_data(symbol: str) -> dict:
    """
    Fetch market cap, rank, dominance, 24h/7d change from CoinMarketCap.
    Returns a dict with keys: rank, market_cap, dominance, change_24h, change_7d,
    volume_24h, cmc_score (0-100 bullish signal from CMC metrics).
    Returns empty dict on failure or missing key.
    """
    base = symbol.split("/")[0]
    if not COINMARKETCAP_API_KEY:
        return {}
    try:
        resp = requests.get(
            "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest",
            headers={"X-CMC_PRO_API_KEY": COINMARKETCAP_API_KEY, "Accept": "application/json"},
            params={"symbol": base, "convert": "USD"},
            timeout=6,
        )
        data = resp.json()
        if data.get("status", {}).get("error_code", 1) != 0:
            logger.warning(f"CMC API error: {data.get('status', {}).get('error_message')}")
            return {}

        coin      = data["data"].get(base)
        if not coin:
            return {}
        quote     = coin["quote"]["USD"]
        change_24 = quote.get("percent_change_24h") or 0.0
        change_7d = quote.get("percent_change_7d") or 0.0
        vol_24h   = quote.get("volume_24h") or 0.0
        mkt_cap   = quote.get("market_cap") or 0.0
        rank      = coin.get("cmc_rank") or 0
        dominance = coin.get("market_cap_dominance") or 0.0

        # Derive a simple CMC momentum score (-100 to +100)
        cmc_score = 0
        if change_24 > 5:   cmc_score += 30
        elif change_24 > 2: cmc_score += 15
        elif change_24 < -5: cmc_score -= 30
        elif change_24 < -2: cmc_score -= 15

        if change_7d > 10:  cmc_score += 20
        elif change_7d < -10: cmc_score -= 20

        # Top-10 coins get a small confidence boost (more liquid)
        if 1 <= rank <= 10: cmc_score += 10

        return {
            "rank":       rank,
            "market_cap": mkt_cap,
            "dominance":  round(dominance, 2),
            "change_24h": round(change_24, 2),
            "change_7d":  round(change_7d, 2),
            "volume_24h": round(vol_24h, 0),
            "cmc_score":  max(-100, min(100, cmc_score)),
        }
    except Exception as e:
        logger.warning(f"CoinMarketCap fetch failed: {e}")
        return {}


# ── Main Signal Generator ─────────────────────────────────────────────────────

def generate_signal(ohlcv: list, symbol: str = "") -> dict:
    """
    Combines OHLCV technical analysis + CryptoCompare news + CoinMarketCap data.
    Returns:
        {
            "action":     "BUY" | "SELL" | "HOLD",
            "confidence": 0-100,
            "reason":     str,
            "indicators": dict,
            "cmc":        dict,
        }
    """
    if len(ohlcv) < 30:
        return {
            "action": "HOLD", "confidence": 0,
            "reason": "Insufficient OHLCV data", "indicators": {}, "cmc": {}
        }

    closes  = [c[4] for c in ohlcv]
    volumes = [c[5] for c in ohlcv]

    rsi              = compute_rsi(closes)
    ema9             = compute_ema(closes, 9)
    ema21            = compute_ema(closes, 21)
    macd, sig, hist  = compute_macd(closes)
    bb_up, bb_mid, bb_low = compute_bollinger(closes)
    price            = closes[-1]
    avg_vol          = _mean(volumes[-20:])
    vol_spike        = volumes[-1] > avg_vol * 1.5

    # External signals
    news_score = get_news_sentiment(symbol) if symbol else 0.0
    cmc        = get_cmc_data(symbol) if symbol else {}

    score   = 0
    reasons = []

    # ── RSI (max ±30) ──────────────────────────────────────────────────────────
    if rsi < 30:
        score += 30
        reasons.append(f"RSI oversold ({rsi})")
    elif rsi > 70:
        score -= 30
        reasons.append(f"RSI overbought ({rsi})")
    else:
        reasons.append(f"RSI neutral ({rsi})")

    # ── EMA Crossover (max ±20) ────────────────────────────────────────────────
    if ema9 > ema21:
        score += 20
        reasons.append("EMA9 > EMA21 (bullish)")
    else:
        score -= 20
        reasons.append("EMA9 < EMA21 (bearish)")

    # ── MACD (max ±15) ─────────────────────────────────────────────────────────
    if hist > 0:
        score += 15
        reasons.append("MACD positive")
    else:
        score -= 15
        reasons.append("MACD negative")

    # ── Bollinger Bands (max ±20) ──────────────────────────────────────────────
    if price < bb_low:
        score += 20
        reasons.append("Below lower BB (oversold zone)")
    elif price > bb_up:
        score -= 20
        reasons.append("Above upper BB (overbought zone)")

    # ── Volume spike — amplifier ───────────────────────────────────────────────
    if vol_spike:
        score = int(score * 1.2)
        reasons.append("Volume spike confirms move")

    # ── CryptoCompare news (max ±10) ──────────────────────────────────────────
    if news_score > 0.3:
        score += 10
        reasons.append(f"Positive news sentiment ({news_score})")
    elif news_score < -0.3:
        score -= 10
        reasons.append(f"Negative news sentiment ({news_score})")

    # ── CoinMarketCap signals (max ±30) ───────────────────────────────────────
    if cmc:
        cmc_score = cmc.get("cmc_score", 0)
        score    += cmc_score // 2           # blend at 50% weight
        ch24      = cmc.get("change_24h", 0)
        ch7d      = cmc.get("change_7d", 0)
        if cmc_score > 0:
            reasons.append(f"CMC bullish: 24h {ch24:+.1f}%, 7d {ch7d:+.1f}%")
        elif cmc_score < 0:
            reasons.append(f"CMC bearish: 24h {ch24:+.1f}%, 7d {ch7d:+.1f}%")
        else:
            reasons.append(f"CMC neutral: 24h {ch24:+.1f}%")

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
            "price":  price,
            "bb_up":  bb_up,
            "bb_low": bb_low,
            "news":   news_score,
        },
        "cmc": cmc,
    }


def get_signals(symbols: list = None) -> list:
    """
    Get signals for a list of symbols. If no symbols provided, use default top coins.
    Returns list of signal dicts.
    """
    if symbols is None:
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
    
    signals = []
    for symbol in symbols:
        # For demo, generate mock OHLCV data or fetch real
        # In real implementation, fetch from exchange
        # Here, we'll use mock data for simplicity
        import random
        base_price = 50000 if symbol.startswith("BTC") else 3000 if symbol.startswith("ETH") else 100
        ohlcv = []
        for i in range(50):
            price = base_price + random.uniform(-1000, 1000)
            ohlcv.append([0, price, price+10, price-10, price, 1000000])
        
        signal = generate_signal(ohlcv, symbol)
        signal['symbol'] = symbol
        signals.append(signal)
    
    return signals
