"""
analytics.py - Performance analytics calculations for DUYS Bot.
All computations are pure Python - no external dependencies.
"""

import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class AnalyticsResult:
    period_days:       int
    total_trades:      int
    wins:              int
    losses:            int
    win_rate_pct:      float
    profit_factor:     float   # sum(wins) / abs(sum(losses)); 'N/A' if no losses
    total_pnl_usdt:    float
    total_pnl_pct:     float
    sharpe_ratio:      float
    max_drawdown_pct:  float
    max_drawdown_date: str
    avg_trade_duration_hrs: float
    best_symbol:       str
    worst_symbol:      str
    best_pnl:          float
    worst_pnl:         float
    equity_curve:      list   # [(week_label, weekly_pnl), ...]
    monthly_breakdown: list   # [(month_label, pnl, trades), ...]
    raw_trades:        list = field(default_factory=list)

    def format_telegram(self) -> str:
        wr = f"{self.win_rate_pct:.1f}%"
        pf = f"{self.profit_factor:.2f}" if isinstance(self.profit_factor, float) else "N/A"
        sr = f"{self.sharpe_ratio:.2f}" if not math.isnan(self.sharpe_ratio) else "N/A"
        tp = f"${self.total_pnl_usdt:+.2f}"
        dd = f"{self.max_drawdown_pct:.2f}%"

        lines = [
            f"📊 <b>Performance Analytics — {self.period_days}d</b>\n",
            f"  Trades:          <code>{self.total_trades}</code>  "
            f"({self.wins}W / {self.losses}L)",
            f"  Win rate:        <code>{wr}</code>",
            f"  Profit factor:   <code>{pf}</code>",
            f"  Total PnL:       <code>{tp}  ({self.total_pnl_pct:+.2f}%)</code>",
            f"  Sharpe ratio:    <code>{sr}</code>",
            f"  Max drawdown:    <code>{dd}</code>",
            f"  Avg hold:        <code>{self.avg_trade_duration_hrs:.1f}h</code>",
            f"  Best symbol:     <code>{self.best_symbol}</code>  "
            f"(${self.best_pnl:+.2f})",
            f"  Worst symbol:    <code>{self.worst_symbol}</code>  "
            f"(${self.worst_pnl:+.2f})",
        ]

        if self.equity_curve:
            lines.append("\n📈 <b>Weekly PnL (last 12 weeks)</b>")
            # ASCII bar chart
            vals  = [v for _, v in self.equity_curve[-12:]]
            scale = max(abs(v) for v in vals) if vals else 1
            for label, val in self.equity_curve[-12:]:
                bar_len  = int(abs(val) / scale * 8) if scale else 0
                bar      = ("▓" if val >= 0 else "░") * bar_len
                sign     = "+" if val >= 0 else ""
                lines.append(f"  <code>{label:>6}  {bar:<8}  {sign}{val:.1f}</code>")

        if self.monthly_breakdown:
            lines.append("\n🗓 <b>Monthly Breakdown</b>")
            for month, pnl, cnt in self.monthly_breakdown[-6:]:
                sign = "+" if pnl >= 0 else ""
                lines.append(
                    f"  <code>{month:>8}  {sign}{pnl:>8.2f}  ({cnt} trades)</code>"
                )

        return "\n".join(lines)


def compute_analytics(trades: list, period_days: int, starting_balance: float = 1000.0) -> AnalyticsResult:
    """
    Compute full performance analytics from a list of closed trade dicts.
    trades must have: pnl, pnl_pct, symbol, opened_at, closed_at.
    """
    if not trades:
        return AnalyticsResult(
            period_days=period_days, total_trades=0, wins=0, losses=0,
            win_rate_pct=0.0, profit_factor=0.0, total_pnl_usdt=0.0,
            total_pnl_pct=0.0, sharpe_ratio=float("nan"),
            max_drawdown_pct=0.0, max_drawdown_date="",
            avg_trade_duration_hrs=0.0, best_symbol="N/A",
            worst_symbol="N/A", best_pnl=0.0, worst_pnl=0.0,
            equity_curve=[], monthly_breakdown=[], raw_trades=[]
        )

    wins      = [t for t in trades if float(t.get("pnl", 0) or 0) > 0]
    losses    = [t for t in trades if float(t.get("pnl", 0) or 0) <= 0]
    total_pnl = sum(float(t.get("pnl", 0) or 0) for t in trades)
    pnl_pct   = total_pnl / starting_balance * 100

    # Profit factor
    gross_profit = sum(float(t["pnl"]) for t in wins)
    gross_loss   = abs(sum(float(t["pnl"]) for t in losses))
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else float("inf")

    # Sharpe ratio
    daily_pnl: dict = {}
    for t in trades:
        d = (t.get("closed_at") or "")[:10]
        daily_pnl[d] = daily_pnl.get(d, 0.0) + float(t.get("pnl", 0) or 0)
    daily_rets = list(daily_pnl.values())
    if len(daily_rets) > 1:
        m = sum(daily_rets) / len(daily_rets)
        std = math.sqrt(sum((r - m) ** 2 for r in daily_rets) / len(daily_rets))
        sharpe = (m / std * math.sqrt(252) / starting_balance * 100) if std > 0 else 0.0
    else:
        sharpe = float("nan")

    # Max drawdown
    cum = starting_balance
    peak = cum
    max_dd = 0.0
    max_dd_date = ""
    for t in trades:
        cum  += float(t.get("pnl", 0) or 0)
        peak  = max(peak, cum)
        dd    = (peak - cum) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd      = dd
            max_dd_date = (t.get("closed_at") or "")[:10]

    # Avg duration
    durations = []
    for t in trades:
        try:
            op = datetime.fromisoformat(t["opened_at"])
            cl = datetime.fromisoformat(t["closed_at"])
            durations.append((cl - op).total_seconds() / 3600)
        except Exception:
            pass
    avg_dur = sum(durations) / len(durations) if durations else 0.0

    # Best/worst symbol
    sym_pnl: dict = {}
    for t in trades:
        s = t.get("symbol", "?")
        sym_pnl[s] = sym_pnl.get(s, 0.0) + float(t.get("pnl", 0) or 0)

    best_sym  = max(sym_pnl, key=sym_pnl.get) if sym_pnl else "N/A"
    worst_sym = min(sym_pnl, key=sym_pnl.get) if sym_pnl else "N/A"

    # Weekly equity curve (last 12 weeks)
    now = datetime.utcnow()
    weekly: dict = {}
    for t in trades:
        try:
            d   = datetime.fromisoformat(t["closed_at"])
            wk  = int((now - d).days / 7)
            lbl = f"W-{wk}" if wk > 0 else "This"
            weekly[lbl] = weekly.get(lbl, 0.0) + float(t.get("pnl", 0) or 0)
        except Exception:
            pass
    equity_curve = sorted(weekly.items(), key=lambda x: x[0], reverse=True)[:12][::-1]

    # Monthly breakdown
    monthly: dict = {}
    for t in trades:
        m = (t.get("closed_at") or "")[:7]   # "YYYY-MM"
        if m:
            if m not in monthly:
                monthly[m] = [0.0, 0]
            monthly[m][0] += float(t.get("pnl", 0) or 0)
            monthly[m][1] += 1
    monthly_breakdown = [
        (m, round(v[0], 4), v[1])
        for m, v in sorted(monthly.items())
    ]

    return AnalyticsResult(
        period_days=period_days,
        total_trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
        profit_factor=profit_factor if isinstance(profit_factor, float) else float("inf"),
        total_pnl_usdt=round(total_pnl, 4),
        total_pnl_pct=round(pnl_pct, 3),
        sharpe_ratio=round(sharpe, 3) if not math.isnan(sharpe) else float("nan"),
        max_drawdown_pct=round(max_dd, 3),
        max_drawdown_date=max_dd_date,
        avg_trade_duration_hrs=round(avg_dur, 2),
        best_symbol=best_sym,
        worst_symbol=worst_sym,
        best_pnl=round(sym_pnl.get(best_sym, 0), 4),
        worst_pnl=round(sym_pnl.get(worst_sym, 0), 4),
        equity_curve=equity_curve,
        monthly_breakdown=monthly_breakdown,
        raw_trades=trades,
    )
