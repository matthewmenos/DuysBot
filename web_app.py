"""
web_app.py - Flask web application for DUYS Bot dashboard and chart API.
Routes:
  GET  /                  - Landing page (index.html)
  GET  /dashboard/<token> - Personal analytics dashboard (24h token)
  GET  /api/analytics/<token> - JSON analytics data for dashboard charts
  GET  /health            - JSON health check
"""

import os
import json
import math
import logging
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, jsonify, send_from_directory, abort, render_template_string, request

logger = logging.getLogger(__name__)
app = Flask(__name__, static_folder=".")

# ── Simple in-process rate limiter ───────────────────────────────────────────
# Tracks (ip, endpoint) → list of timestamps within the window.
_rate_store: dict = {}
_rate_lock = threading.Lock()
RATE_LIMIT_MAX   = 60   # requests
RATE_LIMIT_WINDOW = 60  # seconds


def _check_rate_limit(key: str) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    now = time.time()
    with _rate_lock:
        hits = _rate_store.get(key, [])
        hits = [t for t in hits if now - t < RATE_LIMIT_WINDOW]
        if len(hits) >= RATE_LIMIT_MAX:
            _rate_store[key] = hits
            return False
        hits.append(now)
        _rate_store[key] = hits
        return True


# ── Health ────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "ts": datetime.utcnow().isoformat()})


# ── Landing page ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    try:
        return send_from_directory(".", "index.html")
    except Exception:
        return "<h1>DUYS Trading Bot</h1><p>Web dashboard unavailable.</p>", 200


# ── Analytics JSON API ─────────────────────────────────────────────────────────

@app.route("/api/analytics/<token>")
def api_analytics(token: str):
    from database import get_webdash_token_user, get_full_trade_history, get_open_trades
    from analytics import compute_analytics

    ip = request.remote_addr or "unknown"
    if not _check_rate_limit(f"{ip}:analytics"):
        abort(429)

    user_info = get_webdash_token_user(token)
    if not user_info:
        abort(403)

    uid    = user_info["user_id"]
    trades = get_full_trade_history(uid, days=90)
    result = compute_analytics(trades, 90)

    open_t = [dict(t) for t in get_open_trades(uid)]

    return jsonify({
        "total_trades":    result.total_trades,
        "wins":            result.wins,
        "losses":          result.losses,
        "win_rate":        result.win_rate_pct,
        "total_pnl":       result.total_pnl_usdt,
        "total_pnl_pct":   result.total_pnl_pct,
        "sharpe":          result.sharpe_ratio if not math.isnan(result.sharpe_ratio) else None,
        "max_drawdown":    result.max_drawdown_pct,
        "profit_factor":   result.profit_factor if result.profit_factor != float("inf") else None,
        "avg_duration_hrs":result.avg_trade_duration_hrs,
        "best_symbol":     result.best_symbol,
        "worst_symbol":    result.worst_symbol,
        "equity_curve":    result.equity_curve,
        "monthly":         result.monthly_breakdown,
        "open_positions":  open_t,
        "recent_trades":   trades[-20:],
    })


# ── Dashboard HTML ─────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DUYS Bot — Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
:root{--bg:#04060f;--panel:#0b0f1e;--border:#141c32;--accent:#4fffb0;--text:#dce6f0;--muted:#4a5878;--red:#ff4f6d}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);padding:1.5rem}
h1{font-size:1.4rem;margin-bottom:1.5rem;color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem;margin-bottom:2rem}
.card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:1.2rem}
.card-label{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:.4rem}
.card-val{font-size:1.6rem;font-weight:600}
.pos{color:var(--accent)}.neg{color:var(--red)}
.chart-wrap{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:1.5rem;margin-bottom:1.5rem}
.chart-wrap h3{font-size:.9rem;color:var(--muted);margin-bottom:1rem;text-transform:uppercase;letter-spacing:.06em}
table{width:100%;border-collapse:collapse;font-size:.82rem}
th{text-align:left;color:var(--muted);padding:.5rem;border-bottom:1px solid var(--border)}
td{padding:.5rem;border-bottom:1px solid rgba(20,28,50,.6)}
.expired-msg{text-align:center;padding:4rem;font-size:1.1rem;color:var(--muted)}
.loading{text-align:center;padding:4rem;color:var(--muted)}
canvas{max-height:260px}
</style>
</head>
<body>
<h1>⚡ DUYS Bot Dashboard</h1>
<div id="root"><div class="loading">Loading your data…</div></div>
<script>
const TOKEN = "{{ token }}";
async function load() {
  const r = await fetch("/api/analytics/" + TOKEN);
  if (r.status === 403) {
    document.getElementById("root").innerHTML =
      '<div class="expired-msg">🔒 Link expired — generate a new one with /webdash in Telegram.</div>';
    return;
  }
  const d = await r.json();
  render(d);
}

function fmt(n, digits=2) {
  if (n == null) return "N/A";
  return (n >= 0 ? "+" : "") + Number(n).toFixed(digits);
}

function render(d) {
  const pnlClass = d.total_pnl >= 0 ? "pos" : "neg";
  const stats = [
    {l:"Total Trades", v: d.total_trades, c:""},
    {l:"Win Rate",     v: (d.win_rate||0).toFixed(1)+"%", c: d.win_rate>=50?"pos":"neg"},
    {l:"Total PnL",    v: fmt(d.total_pnl)+" USDT", c: pnlClass},
    {l:"Profit Factor",v: d.profit_factor!=null?Number(d.profit_factor).toFixed(2):"N/A", c:""},
    {l:"Sharpe Ratio", v: d.sharpe!=null?Number(d.sharpe).toFixed(2):"N/A", c:""},
    {l:"Max Drawdown", v: (d.max_drawdown||0).toFixed(2)+"%", c:"neg"},
  ];

  let html = '<div class="grid">' +
    stats.map(s => `<div class="card"><div class="card-label">${s.l}</div>
      <div class="card-val ${s.c}">${s.v}</div></div>`).join("") +
    "</div>";

  // Equity curve
  if (d.equity_curve && d.equity_curve.length) {
    html += `<div class="chart-wrap"><h3>Weekly PnL</h3><canvas id="eq"></canvas></div>`;
  }

  // Win/loss pie
  html += `<div class="chart-wrap" style="max-width:300px">
    <h3>Win / Loss</h3><canvas id="pie"></canvas></div>`;

  // Open positions
  if (d.open_positions && d.open_positions.length) {
    html += `<div class="chart-wrap"><h3>Open Positions</h3><table>
      <tr><th>Symbol</th><th>Side</th><th>Entry</th><th>Amount</th><th>Exchange</th></tr>` +
      d.open_positions.map(p =>
        `<tr><td>${p.symbol}</td><td>${p.side}</td>
         <td>$${Number(p.entry_price).toFixed(4)}</td>
         <td>$${Number(p.amount).toFixed(2)}</td>
         <td>${p.exchange||"-"}</td></tr>`
      ).join("") + "</table></div>";
  }

  // Recent trades
  if (d.recent_trades && d.recent_trades.length) {
    html += `<div class="chart-wrap"><h3>Recent Trades</h3><table>
      <tr><th>Symbol</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Closed</th></tr>` +
      d.recent_trades.slice(-15).reverse().map(t => {
        const pnl = Number(t.pnl||0);
        const cls = pnl >= 0 ? "pos" : "neg";
        return `<tr><td>${t.symbol}</td>
          <td>$${Number(t.entry_price).toFixed(4)}</td>
          <td>${t.exit_price ? "$"+Number(t.exit_price).toFixed(4) : "-"}</td>
          <td class="${cls}">${pnl>=0?"+":""}${pnl.toFixed(4)}</td>
          <td>${(t.closed_at||"").substring(0,16)}</td></tr>`;
      }).join("") + "</table></div>";
  }

  document.getElementById("root").innerHTML = html;

  // Chart.js charts
  const chartOpts = {
    plugins:{legend:{labels:{color:"#dce6f0"}}},
    scales:{x:{ticks:{color:"#4a5878"},grid:{color:"#141c32"}},
            y:{ticks:{color:"#4a5878"},grid:{color:"#141c32"}}}
  };

  if (d.equity_curve && d.equity_curve.length) {
    new Chart(document.getElementById("eq"), {
      type: "bar",
      data: {
        labels: d.equity_curve.map(e=>e[0]),
        datasets:[{
          label:"Weekly PnL (USDT)",
          data: d.equity_curve.map(e=>e[1]),
          backgroundColor: d.equity_curve.map(e => e[1]>=0 ? "#4fffb0" : "#ff4f6d"),
        }]
      },
      options: chartOpts,
    });
  }

  new Chart(document.getElementById("pie"), {
    type:"doughnut",
    data:{
      labels:["Wins","Losses"],
      datasets:[{data:[d.wins,d.losses],backgroundColor:["#4fffb0","#ff4f6d"]}]
    },
    options:{plugins:{legend:{labels:{color:"#dce6f0"}}}}
  });
}

load();
</script>
</body>
</html>"""


@app.route("/dashboard/<token>")
def dashboard(token: str):
    from database import get_webdash_token_user
    ip = request.remote_addr or "unknown"
    if not _check_rate_limit(f"{ip}:dashboard"):
        abort(429)
    user_info = get_webdash_token_user(token)
    if not user_info:
        return render_template_string(
            DASHBOARD_HTML.replace("{{ token }}", token)
        ), 200
    return render_template_string(DASHBOARD_HTML, token=token)


def run_web_app(host: str = "0.0.0.0", port: int = 5000):
    app.run(host=host, port=port, debug=False, use_reloader=False)
