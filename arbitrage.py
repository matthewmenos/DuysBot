"""
arbitrage.py — Arbitrage engine for DuysBot
============================================

Supports two strategies:

1. CROSS-EXCHANGE ARBITRAGE
   Buy an asset on Exchange A (lower ask) and sell on Exchange B (higher bid).
   Only viable when net profit after BOTH exchanges' taker fees and a flat
   withdrawal/transfer fee estimate exceeds MIN_PROFIT_PCT.

2. TRIANGULAR ARBITRAGE
   Exploit price discrepancies within a SINGLE exchange across three pairs.
   e.g. USDT → BTC → ETH → USDT
   Fees are deducted per-leg; end_usdt already reflects the true net result.

All profit calculations are performed BEFORE execution.
Execution helpers re-validate live prices; they abort silently when the
opportunity has vanished.  All errors are bubbled up to callers, never silently
swallowed.
"""

import logging
import itertools
import traceback
from dataclasses import dataclass, field
from typing import Optional

import ccxt

logger = logging.getLogger(__name__)


# ── Tunables (all overrideable via .env) ───────────────────────────────────────
from config import (
    ARB_MIN_PROFIT_PCT           as MIN_PROFIT_PCT,
    ARB_WITHDRAWAL_FEE_USDT      as CROSS_EXCHANGE_WITHDRAWAL_FEE_USDT,
    ARB_FEE_BINANCE, ARB_FEE_BYBIT, ARB_FEE_OKX, ARB_FEE_MEXC,
    ARB_FEE_KUCOIN, ARB_FEE_COINBASE, ARB_FEE_BINGX, ARB_FEE_GATEIO,
    ARB_FEE_DEFAULT,
)

# All symbols the engine can scan (superset — user picks a subset)
ALL_SCANNABLE_SYMBOLS: list[str] = [
    "BTC/USDT",  "ETH/USDT",  "SOL/USDT",  "BNB/USDT",
    "XRP/USDT",  "ADA/USDT",  "DOGE/USDT", "AVAX/USDT",
    "LINK/USDT", "DOT/USDT",  "MATIC/USDT","LTC/USDT",
    "UNI/USDT",  "ATOM/USDT", "TRX/USDT",  "NEAR/USDT",
    "APT/USDT",  "OP/USDT",   "ARB/USDT",  "TON/USDT",
    "FIL/USDT",  "INJ/USDT",  "SUI/USDT",  "SEI/USDT",
]

# Default cross-exchange symbol list (used when user hasn't picked any)
DEFAULT_CROSS_EXCHANGE_SYMBOLS: list[str] = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
    "XRP/USDT", "ADA/USDT", "DOGE/USDT", "AVAX/USDT",
    "LINK/USDT", "DOT/USDT", "MATIC/USDT", "LTC/USDT",
]

# Triangular paths (all round-trip through USDT)
# Format: (sym1, sym2, sym3) — sym1 and sym3 must be */USDT
ALL_TRIANGULAR_PATHS: list[tuple[str, str, str]] = [
    ("BTC/USDT",  "ETH/BTC",   "ETH/USDT"),
    ("BTC/USDT",  "BNB/BTC",   "BNB/USDT"),
    ("ETH/USDT",  "BNB/ETH",   "BNB/USDT"),
    ("BTC/USDT",  "SOL/BTC",   "SOL/USDT"),
    ("ETH/USDT",  "SOL/ETH",   "SOL/USDT"),
    ("BTC/USDT",  "XRP/BTC",   "XRP/USDT"),
    ("BTC/USDT",  "LTC/BTC",   "LTC/USDT"),
    ("BTC/USDT",  "DOT/BTC",   "DOT/USDT"),
    ("BTC/USDT",  "LINK/BTC",  "LINK/USDT"),
    ("ETH/USDT",  "LINK/ETH",  "LINK/USDT"),
    ("BTC/USDT",  "ATOM/BTC",  "ATOM/USDT"),
    ("BTC/USDT",  "UNI/BTC",   "UNI/USDT"),
    ("ETH/USDT",  "UNI/ETH",   "UNI/USDT"),
    ("BTC/USDT",  "DOGE/BTC",  "DOGE/USDT"),
]

EXCHANGE_TAKER_FEES: dict[str, float] = {
    "binance":  ARB_FEE_BINANCE,
    "bybit":    ARB_FEE_BYBIT,
    "okx":      ARB_FEE_OKX,
    "mexc":     ARB_FEE_MEXC,
    "kucoin":   ARB_FEE_KUCOIN,
    "coinbase": ARB_FEE_COINBASE,
    "bingx":    ARB_FEE_BINGX,
    "gateio":   ARB_FEE_GATEIO,
}
DEFAULT_TAKER_FEE: float = ARB_FEE_DEFAULT


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class CrossExchangeOpportunity:
    symbol:             str
    buy_exchange:       str
    sell_exchange:      str
    buy_price:          float    # ask on buy side
    sell_price:         float    # bid on sell side
    spread_pct:         float    # gross spread %
    fee_pct:            float    # combined taker fees %
    withdrawal_usdt:    float    # flat transfer cost
    net_profit_pct:     float    # spread_pct - fee_pct
    net_profit_usdt:    float    # dollar estimate at trade_amount_usdt
    viable:             bool
    trade_amount_usdt:  float = 1_000.0

    def summary(self) -> str:
        icon = "✅" if self.viable else "⚠️"
        return (
            f"{icon} *Cross: {self.symbol}*\n"
            f"  Buy  `{self.buy_exchange.upper()}` @ `${self.buy_price:,.4f}`\n"
            f"  Sell `{self.sell_exchange.upper()}` @ `${self.sell_price:,.4f}`\n"
            f"  Spread `{self.spread_pct:.3f}%` · Fees `{self.fee_pct:.3f}%` · "
            f"*Net `{self.net_profit_pct:.3f}%`* (~`${self.net_profit_usdt:.2f}` / $1k)\n"
        )


@dataclass
class TriangularOpportunity:
    exchange:           str
    path:               tuple   # (sym1, sym2, sym3)
    directions:         tuple   # ("buy"|"sell", ...)
    start_usdt:         float
    end_usdt:           float   # fees already deducted inside _simulate_triangular
    net_profit_pct:     float   # (end_usdt - start_usdt) / start_usdt * 100
    net_profit_usdt:    float   # end_usdt - start_usdt
    viable:             bool

    def summary(self) -> str:
        icon = "✅" if self.viable else "⚠️"
        arrow = " → ".join(
            f"{'BUY' if d == 'buy' else 'SELL'} {s}"
            for s, d in zip(self.path, self.directions)
        )
        return (
            f"{icon} *Tri: {self.exchange.upper()}*\n"
            f"  `{arrow}`\n"
            f"  *Net `{self.net_profit_pct:.3f}%`* (~`${self.net_profit_usdt:.2f}` / $1k)\n"
        )


# ── Scan errors (non-fatal per-symbol failures) ────────────────────────────────

@dataclass
class ScanError:
    exchange:   str
    symbol:     str
    error:      str
    tb:         str = ""   # truncated traceback for admin visibility


# ── Price helpers ──────────────────────────────────────────────────────────────

def _best_bid_ask(
    exchange: ccxt.Exchange,
    symbol: str,
) -> Optional[tuple[float, float]]:
    """
    Return (best_bid, best_ask) for symbol.
    Tries L1 order-book first; falls back to ticker.
    Returns None on any error — callers decide whether to raise or skip.
    """
    try:
        if exchange.has.get("fetchOrderBook"):
            ob  = exchange.fetch_order_book(symbol, limit=1)
            bid = ob["bids"][0][0] if ob.get("bids") else None
            ask = ob["asks"][0][0] if ob.get("asks") else None
            if bid and ask and float(bid) > 0 and float(ask) > 0:
                return float(bid), float(ask)
        t   = exchange.fetch_ticker(symbol)
        bid = t.get("bid") or t.get("last")
        ask = t.get("ask") or t.get("last")
        if bid and ask and float(bid) > 0 and float(ask) > 0:
            return float(bid), float(ask)
    except ccxt.BadSymbol:
        logger.debug(f"[ARB] {exchange.id}: symbol {symbol} not listed — skipping")
    except ccxt.NetworkError as e:
        logger.warning(f"[ARB] {exchange.id}/{symbol} network error: {e}")
    except ccxt.ExchangeError as e:
        logger.warning(f"[ARB] {exchange.id}/{symbol} exchange error: {e}")
    except Exception as e:
        logger.debug(f"[ARB] _best_bid_ask {exchange.id}/{symbol}: {e}")
    return None


def _taker_fee(exchange_id: str) -> float:
    return EXCHANGE_TAKER_FEES.get(exchange_id, DEFAULT_TAKER_FEE)


def _filter_paths_for_symbols(
    symbols: list[str],
    paths: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """
    Return only triangular paths where both the start AND end symbol
    (sym1 and sym3, the USDT pairs) appear in the user's chosen symbols list.
    Falls back to all paths if the filter would leave nothing.
    """
    sym_set = set(symbols)
    filtered = [p for p in paths if p[0] in sym_set or p[2] in sym_set]
    return filtered if filtered else paths


# ── Cross-Exchange Arbitrage ───────────────────────────────────────────────────

def scan_cross_exchange(
    exchanges:          dict[str, ccxt.Exchange],
    symbols:            list[str] = None,
    trade_amount_usdt:  float = 1_000.0,
) -> tuple[list[CrossExchangeOpportunity], list[ScanError]]:
    """
    Compare every symbol across every ordered exchange pair.

    Returns (opportunities_sorted, scan_errors).
    Errors are non-fatal; a fetch failure on one symbol/exchange skips that
    pair and records a ScanError rather than crashing the whole scan.
    """
    if symbols is None:
        symbols = DEFAULT_CROSS_EXCHANGE_SYMBOLS

    opportunities: list[CrossExchangeOpportunity] = []
    errors:         list[ScanError]               = []

    for symbol in symbols:
        prices: dict[str, tuple[float, float]] = {}
        for ex_id, ex in exchanges.items():
            ba = _best_bid_ask(ex, symbol)
            if ba:
                prices[ex_id] = ba
            # _best_bid_ask already logs per-symbol failures — no extra error needed

        if len(prices) < 2:
            continue   # need at least two exchanges to compare

        for buy_id, sell_id in itertools.permutations(prices.keys(), 2):
            _,        buy_ask  = prices[buy_id]
            sell_bid, _        = prices[sell_id]

            if buy_ask <= 0 or sell_bid <= 0:
                continue

            spread_pct = (sell_bid - buy_ask) / buy_ask * 100.0
            if spread_pct <= 0:
                continue

            buy_fee_pct    = _taker_fee(buy_id)  * 100
            sell_fee_pct   = _taker_fee(sell_id) * 100
            total_fee_pct  = buy_fee_pct + sell_fee_pct
            net_profit_pct = spread_pct - total_fee_pct

            gross_usdt      = trade_amount_usdt * spread_pct    / 100
            fee_usdt        = trade_amount_usdt * total_fee_pct / 100
            net_profit_usdt = gross_usdt - fee_usdt - CROSS_EXCHANGE_WITHDRAWAL_FEE_USDT

            viable = net_profit_pct > MIN_PROFIT_PCT and net_profit_usdt > 0

            opportunities.append(CrossExchangeOpportunity(
                symbol=symbol,
                buy_exchange=buy_id,
                sell_exchange=sell_id,
                buy_price=buy_ask,
                sell_price=sell_bid,
                spread_pct=round(spread_pct, 4),
                fee_pct=round(total_fee_pct, 4),
                withdrawal_usdt=CROSS_EXCHANGE_WITHDRAWAL_FEE_USDT,
                net_profit_pct=round(net_profit_pct, 4),
                net_profit_usdt=round(net_profit_usdt, 4),
                viable=viable,
                trade_amount_usdt=trade_amount_usdt,
            ))

    opportunities.sort(key=lambda o: (not o.viable, -o.net_profit_pct))
    return opportunities, errors


# ── Triangular Arbitrage ───────────────────────────────────────────────────────

def _simulate_triangular(
    prices:     dict[str, tuple[float, float]],
    path:       tuple[str, str, str],
    fee:        float,
    start_usdt: float = 1_000.0,
) -> Optional[tuple[float, tuple[str, str, str]]]:
    """
    Simulate a triangular path. Returns (end_usdt, directions) or None.

    Fee is applied at each leg: end_usdt already reflects ALL three taker
    charges — callers must NOT subtract fees again.

    dir="buy"  → spend quote to receive base (use ask)
    dir="sell" → spend base to receive quote  (use bid)
    """
    sym1, sym2, sym3 = path
    if not all(s in prices for s in (sym1, sym2, sym3)):
        return None

    _, ask1     = prices[sym1]
    bid2, ask2  = prices[sym2]
    bid3, _     = prices[sym3]

    b1, q1 = sym1.split("/")
    b2, q2 = sym2.split("/")
    b3, q3 = sym3.split("/")

    # Leg 1 — always buy sym1 (spend USDT, get base1)
    if q1 != "USDT" or ask1 <= 0:
        return None
    amount_base1 = (start_usdt / ask1) * (1.0 - fee)
    dir1 = "buy"

    # Leg 2 — convert base1 → intermediate
    intermediate: str
    amount_intermediate: float
    dir2: str

    if b2 == b1 and q2 != "USDT":
        # e.g. sell BTC/ETH: spend BTC (base), receive ETH (quote)
        if bid2 <= 0:
            return None
        amount_intermediate = amount_base1 * bid2 * (1.0 - fee)
        intermediate        = q2
        dir2                = "sell"
    elif q2 == b1:
        # e.g. buy ETH/BTC using BTC (quote), receive ETH (base)
        if ask2 <= 0:
            return None
        amount_intermediate = (amount_base1 / ask2) * (1.0 - fee)
        intermediate        = b2
        dir2                = "buy"
    else:
        return None

    # Leg 3 — always sell sym3 back to USDT
    if b3 != intermediate or q3 != "USDT" or bid3 <= 0:
        return None
    end_usdt = amount_intermediate * bid3 * (1.0 - fee)
    dir3     = "sell"

    return end_usdt, (dir1, dir2, dir3)


def scan_triangular(
    exchange:           ccxt.Exchange,
    symbols:            list[str] = None,
    paths:              list[tuple[str, str, str]] = None,
    start_usdt:         float = 1_000.0,
) -> tuple[list[TriangularOpportunity], list[ScanError]]:
    """
    Scan triangular paths on a single exchange.
    If symbols is provided, only paths whose USDT legs match are checked.

    Returns (opportunities_sorted, scan_errors).
    """
    if paths is None:
        paths = ALL_TRIANGULAR_PATHS
    if symbols:
        paths = _filter_paths_for_symbols(symbols, paths)

    fee = _taker_fee(exchange.id)

    # Bulk-fetch all needed prices
    needed: set[str] = set()
    for path in paths:
        needed.update(path)

    prices:  dict[str, tuple[float, float]] = {}
    errors:  list[ScanError]               = []

    for sym in needed:
        try:
            ba = _best_bid_ask(exchange, sym)
            if ba:
                prices[sym] = ba
        except Exception as e:
            errors.append(ScanError(
                exchange=exchange.id,
                symbol=sym,
                error=str(e),
                tb=traceback.format_exc()[-400:],
            ))

    opportunities: list[TriangularOpportunity] = []

    for path in paths:
        try:
            result = _simulate_triangular(prices, path, fee, start_usdt)
            if result is None:
                continue

            end_usdt, directions = result
            net_profit_usdt = end_usdt - start_usdt
            net_profit_pct  = net_profit_usdt / start_usdt * 100.0
            viable          = net_profit_pct > MIN_PROFIT_PCT

            opportunities.append(TriangularOpportunity(
                exchange=exchange.id,
                path=path,
                directions=directions,
                start_usdt=start_usdt,
                end_usdt=round(end_usdt, 6),
                net_profit_pct=round(net_profit_pct, 4),
                net_profit_usdt=round(net_profit_usdt, 4),
                viable=viable,
            ))
        except Exception as e:
            errors.append(ScanError(
                exchange=exchange.id,
                symbol=str(path),
                error=str(e),
                tb=traceback.format_exc()[-400:],
            ))

    opportunities.sort(key=lambda o: (not o.viable, -o.net_profit_pct))
    return opportunities, errors


# ── Unified scan entry point ───────────────────────────────────────────────────

def run_arbitrage_scan(
    exchanges:          dict[str, ccxt.Exchange],
    trade_amount_usdt:  float = 1_000.0,
    symbols:            list[str] = None,
) -> dict:
    """
    Run both cross-exchange and triangular scans across all provided exchanges.

    :param exchanges:          {exchange_id: ccxt.Exchange instance}
    :param trade_amount_usdt:  notional for profit estimates
    :param symbols:            user-selected token list; None = defaults
    :returns: {
        "cross_exchange": [CrossExchangeOpportunity, ...],
        "triangular":     [TriangularOpportunity, ...],
        "viable_count":   int,
        "summary_lines":  [str, ...],
        "scan_errors":    [ScanError, ...],   ← NEW: surfaced to admin
    }
    """
    logger.info(
        f"[ARB] Scan starting — exchanges: {list(exchanges.keys())}, "
        f"symbols: {symbols or 'defaults'}"
    )
    all_errors: list[ScanError] = []

    # ── Cross-exchange ─────────────────────────────────────────────────────────
    try:
        cross_opps, cross_errs = scan_cross_exchange(
            exchanges,
            symbols=symbols,
            trade_amount_usdt=trade_amount_usdt,
        )
        all_errors.extend(cross_errs)
    except Exception as e:
        logger.error(f"[ARB] Cross-exchange scan crashed: {e}", exc_info=True)
        cross_opps = []
        all_errors.append(ScanError("all", "cross_exchange", str(e), traceback.format_exc()[-400:]))

    viable_cross = [o for o in cross_opps if o.viable]
    logger.info(f"[ARB] Cross-exchange: {len(viable_cross)} viable / {len(cross_opps)} found")

    # ── Triangular (per exchange) ──────────────────────────────────────────────
    tri_opps: list[TriangularOpportunity] = []
    for ex_id, ex in exchanges.items():
        try:
            found, tri_errs = scan_triangular(ex, symbols=symbols, start_usdt=trade_amount_usdt)
            all_errors.extend(tri_errs)
            tri_opps.extend(found)
            viable_tri = [o for o in found if o.viable]
            logger.info(f"[ARB] Triangular {ex_id}: {len(viable_tri)} viable / {len(found)} found")
        except Exception as e:
            logger.error(f"[ARB] Triangular scan crashed on {ex_id}: {e}", exc_info=True)
            all_errors.append(ScanError(ex_id, "triangular", str(e), traceback.format_exc()[-400:]))

    viable_tri   = [o for o in tri_opps if o.viable]
    viable_count = len(viable_cross) + len(viable_tri)

    # ── Summary lines ──────────────────────────────────────────────────────────
    summary_lines: list[str] = []
    if viable_cross:
        summary_lines.append(f"📊 *Cross-Exchange ({len(viable_cross)} found):*")
        for opp in viable_cross[:5]:
            summary_lines.append(opp.summary())
    if viable_tri:
        summary_lines.append(f"🔺 *Triangular ({len(viable_tri)} found):*")
        for opp in viable_tri[:5]:
            summary_lines.append(opp.summary())
    if not summary_lines:
        summary_lines.append("🔍 No viable arbitrage opportunities at this time.")

    # Log non-fatal scan errors at warning level for visibility
    if all_errors:
        logger.warning(f"[ARB] Scan completed with {len(all_errors)} non-fatal error(s).")

    return {
        "cross_exchange": cross_opps,
        "triangular":     tri_opps,
        "viable_count":   viable_count,
        "summary_lines":  summary_lines,
        "scan_errors":    all_errors,
    }


# ── Execution helpers ──────────────────────────────────────────────────────────

def execute_cross_exchange_arb(
    opp:           CrossExchangeOpportunity,
    buy_exchange:  ccxt.Exchange,
    sell_exchange: ccxt.Exchange,
) -> dict:
    """
    Execute a cross-exchange arb opportunity.

    1. Re-validates live prices — aborts if opportunity is gone.
    2. Places a market buy on buy_exchange.
    3. Places a market sell on sell_exchange using the actual filled qty.

    Returns {"buy_order", "sell_order", "error"}.
    Raises nothing — all errors go to result["error"].
    """
    result: dict = {"buy_order": None, "sell_order": None, "error": None}
    try:
        ba = _best_bid_ask(buy_exchange,  opp.symbol)
        sa = _best_bid_ask(sell_exchange, opp.symbol)
        if not ba or not sa:
            result["error"] = "Could not fetch live prices before execution — aborting."
            return result

        live_spread  = (sa[0] - ba[1]) / ba[1] * 100
        total_fee    = (_taker_fee(opp.buy_exchange) + _taker_fee(opp.sell_exchange)) * 100
        live_net_pct = live_spread - total_fee

        if live_net_pct < MIN_PROFIT_PCT:
            result["error"] = (
                f"Opportunity gone — live net {live_net_pct:.3f}% "
                f"< minimum {MIN_PROFIT_PCT}%."
            )
            return result

        qty_base = opp.trade_amount_usdt / ba[1]
        qty_str  = buy_exchange.amount_to_precision(opp.symbol, qty_base)

        buy_order = buy_exchange.create_market_buy_order(opp.symbol, float(qty_str))
        result["buy_order"] = buy_order
        logger.info(f"[ARB-EXEC] BUY  {opp.symbol} on {opp.buy_exchange}: {buy_order.get('id')}")

        filled_base = float(buy_order.get("filled") or qty_base)
        sell_qty    = sell_exchange.amount_to_precision(opp.symbol, filled_base)

        sell_order = sell_exchange.create_market_sell_order(opp.symbol, float(sell_qty))
        result["sell_order"] = sell_order
        logger.info(f"[ARB-EXEC] SELL {opp.symbol} on {opp.sell_exchange}: {sell_order.get('id')}")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"[ARB-EXEC] Execution error: {e}", exc_info=True)

    return result


def execute_triangular_arb(
    opp:        TriangularOpportunity,
    exchange:   ccxt.Exchange,
    start_usdt: float = 1_000.0,
) -> dict:
    """
    Execute a triangular arb on a single exchange (three sequential market orders).

    Re-simulates with live prices before any order. Handles buy/sell qty
    conversion correctly for each leg.

    Returns {"orders": list, "error": str|None}.
    """
    result: dict = {"orders": [], "error": None}
    try:
        sym1, sym2, sym3 = opp.path
        dir1, dir2, dir3 = opp.directions
        fee = _taker_fee(exchange.id)

        p1 = _best_bid_ask(exchange, sym1)
        p2 = _best_bid_ask(exchange, sym2)
        p3 = _best_bid_ask(exchange, sym3)

        if not (p1 and p2 and p3):
            result["error"] = "Live prices unavailable — aborting."
            return result

        sim = _simulate_triangular({sym1: p1, sym2: p2, sym3: p3}, opp.path, fee, start_usdt)
        if not sim:
            result["error"] = "Live simulation failed — path changed, aborting."
            return result

        end_usdt, _ = sim
        live_net_pct = (end_usdt - start_usdt) / start_usdt * 100.0
        if live_net_pct < MIN_PROFIT_PCT:
            result["error"] = f"Opportunity gone — live net {live_net_pct:.3f}% < minimum."
            return result

        # Leg 1: always buy sym1
        _, ask1 = p1
        qty1_base = start_usdt / ask1
        qty1_str  = exchange.amount_to_precision(sym1, qty1_base)
        o1 = exchange.create_market_buy_order(sym1, float(qty1_str))
        result["orders"].append(o1)
        filled1 = float(o1.get("filled") or qty1_base)
        logger.info(f"[TRI-EXEC] Leg1 buy  {sym1}: id={o1.get('id')} filled={filled1}")

        # Leg 2: buy or sell depending on path direction
        bid2, ask2 = p2
        if dir2 == "sell":
            qty2_str = exchange.amount_to_precision(sym2, filled1)
            o2 = exchange.create_market_sell_order(sym2, float(qty2_str))
        else:
            if ask2 <= 0:
                result["error"] = "Leg 2 ask is zero — aborting."
                return result
            qty2_base = filled1 / ask2
            qty2_str  = exchange.amount_to_precision(sym2, qty2_base)
            o2 = exchange.create_market_buy_order(sym2, float(qty2_str))
        result["orders"].append(o2)
        filled2 = float(o2.get("filled") or (float(qty2_str)))
        logger.info(f"[TRI-EXEC] Leg2 {dir2} {sym2}: id={o2.get('id')} filled={filled2}")

        # Leg 3: always sell sym3
        qty3_str = exchange.amount_to_precision(sym3, filled2)
        o3 = exchange.create_market_sell_order(sym3, float(qty3_str))
        result["orders"].append(o3)
        logger.info(f"[TRI-EXEC] Leg3 sell {sym3}: id={o3.get('id')}")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"[TRI-EXEC] Error: {e}", exc_info=True)

    return result


# ── CLI smoke-test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    test_exchanges: dict[str, ccxt.Exchange] = {
        "binance": ccxt.binance({"enableRateLimit": True}),
        "bybit":   ccxt.bybit(  {"enableRateLimit": True}),
        "kucoin":  ccxt.kucoin( {"enableRateLimit": True}),
        "gateio":  ccxt.gateio( {"enableRateLimit": True}),
        "bingx":   ccxt.bingx(  {"enableRateLimit": True}),
    }

    scan_result = run_arbitrage_scan(
        test_exchanges,
        trade_amount_usdt=500.0,
        symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    )

    print(f"\n{'='*60}")
    print(f"Viable: {scan_result['viable_count']}")
    print(f"Errors: {len(scan_result['scan_errors'])}")
    print("=" * 60)
    for line in scan_result["summary_lines"]:
        print(line.replace("*", "").replace("`", ""))
    if scan_result["scan_errors"]:
        print("\nScan errors:")
        for e in scan_result["scan_errors"]:
            print(f"  {e.exchange}/{e.symbol}: {e.error}")
