"""
backtest.py - Backtesting engine for DUYS Bot
Walks OHLCV data forward using the signal engine with zero lookahead bias.
"""

import math
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    symbol:           str
    candles_tested:   int
    total_trades:     int
    wins:             int
    losses:           int
    win_rate_pct:     float
    total_pnl_pct:    float
    total_pnl_usdt:   float
    max_drawdown_pct: float
    sharpe_ratio:     float
    avg_hold_candles: float
    best_trade_pct:   float
    worst_trade_pct:  float
    final_balance:    float
    trades:           list = field(default_factory=list)

    def format_telegram(self) -> str:
        wr  = f"{self.win_rate_pct:.1f}%"
        pnl = f"+{self.total_pnl_pct:.2f}%" if self.total_pnl_pct >= 0 else f"{self.total_pnl_pct:.2f}%"
        sr  = f"{self.sharpe_ratio:.2f}" if not math.isnan(self.sharpe_ratio) else "N/A"
        return (
            f"📊 <b>Backtest Results — {self.symbol}</b>\n\n"
            f"  Candles tested:   <code>{self.candles_tested}</code>\n"
            f"  Total trades:     <code>{self.total_trades}</code>\n"
            f"  Win rate:         <code>{wr}</code>\n"
            f"  Wins / Losses:    <code>{self.wins} / {self.losses}</code>\n"
            f"  Total PnL:        <code>{pnl}</code>  (~${self.total_pnl_usdt:+.2f})\n"
            f"  Best trade:       <code>+{self.best_trade_pct:.2f}%</code>\n"
            f"  Worst trade:      <code>{self.worst_trade_pct:.2f}%</code>\n"
            f"  Max drawdown:     <code>{self.max_drawdown_pct:.2f}%</code>\n"
            f"  Sharpe ratio:     <code>{sr}</code>\n"
            f"  Avg hold:         <code>{self.avg_hold_candles:.1f} candles</code>\n"
            f"  Final balance:    <code>${self.final_balance:.2f}</code>\n"
        )


def run_backtest(
    ohlcv:            list,
    take_profit:      float,
    stop_loss:        float,
    tp_mode:          str   = "pct",
    sl_mode:          str   = "pct",
    symbol:           str   = "BTC/USDT",
    starting_balance: float = 1000.0,
    trade_amount:     float = 100.0,
    taker_fee:        float = 0.001,
) -> BacktestResult:
    """
    Walk-forward backtest with zero lookahead bias.
    Signal at candle N uses only candles 0..N.
    Entry is at candle N+1 open (no snooping).
    Exit checks each subsequent candle's high/low: if both TP and SL hit
    in the same candle, SL is assumed (conservative).
    """
    from strategy import generate_signal

    MIN_CANDLES   = 35   # minimum lookback for all indicators
    FEE_PER_TRADE = taker_fee * 2  # entry + exit

    if len(ohlcv) < MIN_CANDLES + 10:
        raise ValueError(
            f"Need at least {MIN_CANDLES + 10} candles; got {len(ohlcv)}. "
            "Run /backtest with a longer period."
        )

    balance      = starting_balance
    open_trade   = None      # {entry_price, tp, sl, entry_idx}
    closed       = []        # list of {entry_price, exit_price, pnl_pct, reason, hold}
    equity_curve = [balance]

    for i in range(MIN_CANDLES, len(ohlcv) - 1):
        candle        = ohlcv[i]
        next_candle   = ohlcv[i + 1]
        # [ts, open, high, low, close, volume]
        next_open  = next_candle[1]
        next_high  = next_candle[2]
        next_low   = next_candle[3]

        # ── Check TP / SL on open trade ─────────────────────────────────────
        if open_trade is not None:
            ep  = open_trade["entry_price"]
            tp  = open_trade["tp"]
            sl  = open_trade["sl"]

            hit_sl = next_low  <= sl
            hit_tp = next_high >= tp

            if hit_sl or hit_tp:
                # Conservative: if both hit, use SL
                if hit_sl:
                    exit_price = sl
                    reason     = "sl"
                else:
                    exit_price = tp
                    reason     = "tp"

                pnl_pct   = (exit_price - ep) / ep - FEE_PER_TRADE
                pnl_usdt  = trade_amount * pnl_pct
                balance  += pnl_usdt
                hold      = i - open_trade["entry_idx"]

                closed.append({
                    "entry_price": ep,
                    "exit_price":  exit_price,
                    "pnl_pct":     pnl_pct * 100,
                    "pnl_usdt":    pnl_usdt,
                    "reason":      reason,
                    "hold":        hold,
                })
                open_trade = None
                equity_curve.append(balance)
                continue

        # ── No open trade — check for signal ─────────────────────────────────
        if open_trade is None:
            window = ohlcv[max(0, i - MIN_CANDLES):i + 1]
            try:
                sig = generate_signal(window, symbol)
            except Exception:
                continue

            if sig.get("action") == "BUY" and sig.get("confidence", 0) >= 50:
                entry = next_open
                if entry <= 0:
                    continue

                # Compute TP/SL levels
                if tp_mode == "pct":
                    tp_price = entry * (1 + take_profit / 100)
                    sl_price = entry * (1 - stop_loss  / 100)
                else:
                    tp_price = take_profit
                    sl_price = stop_loss

                open_trade = {
                    "entry_price": entry,
                    "tp":          tp_price,
                    "sl":          sl_price,
                    "entry_idx":   i,
                }

    # ── Force-close any remaining open position ──────────────────────────────
    if open_trade is not None:
        last_close = ohlcv[-1][4]
        ep         = open_trade["entry_price"]
        pnl_pct    = (last_close - ep) / ep - FEE_PER_TRADE
        pnl_usdt   = trade_amount * pnl_pct
        balance   += pnl_usdt
        hold       = len(ohlcv) - 1 - open_trade["entry_idx"]
        closed.append({
            "entry_price": ep,
            "exit_price":  last_close,
            "pnl_pct":     pnl_pct * 100,
            "pnl_usdt":    pnl_usdt,
            "reason":      "end",
            "hold":        hold,
        })
        equity_curve.append(balance)

    # ── Metrics ───────────────────────────────────────────────────────────────
    total   = len(closed)
    wins    = sum(1 for t in closed if t["pnl_pct"] > 0)
    losses  = total - wins
    wr      = (wins / total * 100) if total else 0.0
    t_pnl_u = sum(t["pnl_usdt"] for t in closed)
    t_pnl_p = (t_pnl_u / starting_balance * 100) if starting_balance else 0.0
    best    = max((t["pnl_pct"] for t in closed), default=0.0)
    worst   = min((t["pnl_pct"] for t in closed), default=0.0)
    avg_h   = (sum(t["hold"] for t in closed) / total) if total else 0.0

    # Max drawdown from equity curve
    peak   = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak   = max(peak, v)
        dd     = (peak - v) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    # Sharpe ratio from per-trade returns
    returns = [t["pnl_pct"] / 100 for t in closed]
    if len(returns) > 1:
        mean_r = sum(returns) / len(returns)
        std_r  = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / len(returns))
        sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0
    else:
        sharpe = float("nan")

    return BacktestResult(
        symbol=symbol,
        candles_tested=len(ohlcv),
        total_trades=total,
        wins=wins,
        losses=losses,
        win_rate_pct=round(wr, 2),
        total_pnl_pct=round(t_pnl_p, 2),
        total_pnl_usdt=round(t_pnl_u, 4),
        max_drawdown_pct=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 3) if not math.isnan(sharpe) else float("nan"),
        avg_hold_candles=round(avg_h, 1),
        best_trade_pct=round(best, 3),
        worst_trade_pct=round(worst, 3),
        final_balance=round(balance, 4),
        trades=closed,
    )
