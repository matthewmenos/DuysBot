"""
scheduler.py - Auto-trading loop + price alert monitor
Runs every 60 seconds via job_queue.
"""

import logging
from telegram.ext import ContextTypes
from database import (
    get_all_trading_users, get_open_trades, open_trade, close_trade,
    get_all_alert_users, get_active_alerts, mark_alert_triggered
)
from exchange import get_exchange, fetch_ohlcv, fetch_ticker, place_market_order
from strategy import generate_signal

logger = logging.getLogger(__name__)


async def start_scheduler(context: ContextTypes.DEFAULT_TYPE):
    """Called by job_queue periodically. Runs trading loop + alert monitor."""

    # 1. Auto-trading loop
    for user in get_all_trading_users():
        try:
            await process_user(context, user)
        except Exception as e:
            logger.error(f"Scheduler trading error for user {user['user_id']}: {e}")

    # 2. Price alert monitor
    try:
        await check_price_alerts(context)
    except Exception as e:
        logger.error(f"Scheduler alert error: {e}")


# ── Auto-trading ──────────────────────────────────────────────────────────────

async def process_user(context, user):
    user_id   = user["user_id"]
    exchange  = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
    symbol    = user["symbol"]
    tp_pct    = user["take_profit"] / 100
    sl_pct    = user["stop_loss"] / 100
    trade_amt = user["trade_amount"]

    open_trades   = get_open_trades(user_id)
    ticker        = fetch_ticker(exchange, symbol)
    current_price = ticker["last"]

    # ── Check open trades for TP / SL ────────────────────────────────────────
    for trade in open_trades:
        if trade["symbol"] != symbol:
            continue

        entry   = trade["entry_price"]
        pnl_pct = (
            (current_price - entry) / entry
            if trade["side"] == "buy"
            else (entry - current_price) / entry
        )

        hit_tp = pnl_pct >= tp_pct
        hit_sl = pnl_pct <= -sl_pct

        if hit_tp or hit_sl:
            pnl_usdt = trade["amount"] * pnl_pct
            close_trade(trade["id"], current_price, round(pnl_usdt, 4), round(pnl_pct * 100, 2))

            emoji  = "✅" if hit_tp else "🛑"
            reason = "Take Profit" if hit_tp else "Stop Loss"
            msg = (
                f"{emoji} <b>{reason} Hit!</b>\n\n"
                f"Symbol:  <code>{symbol}</code>\n"
                f"Entry:   <code>${entry:,.6f}</code>\n"
                f"Exit:    <code>${current_price:,.6f}</code>\n"
                f"PnL:     <code>{'+'if pnl_usdt>=0 else ''}{pnl_usdt:.4f} USDT "
                f"({pnl_pct*100:+.2f}%)</code>"
            )
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="HTML")
            logger.info(f"Trade {trade['id']} closed ({reason}) for user {user_id}")
            return

    # ── No open trade → look for signal ──────────────────────────────────────
    if open_trades:
        return

    ohlcv  = fetch_ohlcv(exchange, symbol, timeframe="1h", limit=100)
    signal = generate_signal(ohlcv, symbol)

    if signal["action"] == "BUY" and signal["confidence"] >= 50:
        try:
            order    = place_market_order(exchange, symbol, "buy", trade_amt)
            order_id = str(order.get("id", "N/A"))
            open_trade(
                user_id, symbol, "buy", current_price,
                trade_amt, user["exchange"], order_id, signal["reason"]
            )
            msg = (
                f"🚀 <b>BUY Signal Executed</b>\n\n"
                f"Symbol:     <code>{symbol}</code>\n"
                f"Price:      <code>${current_price:,.6f}</code>\n"
                f"Amount:     <code>{trade_amt} USDT</code>\n"
                f"Confidence: <code>{signal['confidence']}%</code>\n"
                f"📊 Reason: {signal['reason'][:200]}"
            )
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Buy order failed for {user_id}: {e}")
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ Buy order failed: <code>{e}</code>",
                parse_mode="HTML"
            )


# ── Price Alert Monitor ───────────────────────────────────────────────────────

async def check_price_alerts(context: ContextTypes.DEFAULT_TYPE):
    """
    Check all active alerts every cycle.
    Fetches prices per user (using their connected exchange).
    Each alert fires once, then is marked as triggered.
    """
    alert_users = get_all_alert_users()
    if not alert_users:
        return

    for user in alert_users:
        user_id = user["user_id"]
        alerts  = get_active_alerts(user_id)
        if not alerts:
            continue

        try:
            exch = get_exchange(
                user["exchange"], user["api_key"],
                user["api_secret"], user["api_pass"]
            )
        except Exception as e:
            logger.warning(f"Alert: could not connect exchange for user {user_id}: {e}")
            continue

        # Cache prices per symbol to avoid duplicate API calls
        price_cache: dict = {}

        for alert in alerts:
            symbol = alert["symbol"]

            if symbol not in price_cache:
                try:
                    ticker = fetch_ticker(exch, symbol)
                    price_cache[symbol] = ticker["last"]
                except Exception as e:
                    logger.warning(f"Alert: could not fetch {symbol} for user {user_id}: {e}")
                    continue

            current_price = price_cache[symbol]
            target        = alert["target_price"]
            condition     = alert["condition"]   # 'above' or 'below'

            triggered = (
                (condition == "above" and current_price >= target) or
                (condition == "below" and current_price <= target)
            )

            if triggered:
                mark_alert_triggered(alert["id"])
                await _send_alert_notification(context, user_id, alert, current_price)
                logger.info(
                    f"Alert #{alert['id']} triggered for user {user_id}: "
                    f"{symbol} {condition} ${target} (now ${current_price})"
                )


async def _send_alert_notification(context, user_id: int, alert, current_price: float):
    """Send a rich, formatted alert notification to the user."""
    symbol    = alert["symbol"]
    target    = alert["target_price"]
    condition = alert["condition"]
    note      = alert["note"]

    direction = "risen above" if condition == "above" else "fallen below"
    arrow     = "📈" if condition == "above" else "📉"
    coin      = symbol.split("/")[0]

    # Calculate % distance from target
    pct_diff = ((current_price - target) / target) * 100

    text = (
        f"🔔 <b>Price Alert Triggered!</b>\n\n"
        f"{arrow} <b>{coin}</b> has {direction} your target\n\n"
        f"Symbol:  <code>{symbol}</code>\n"
        f"Target:  <code>${target:,.6f}</code>\n"
        f"Current: <code>${current_price:,.6f}</code>  "
        f"(<code>{pct_diff:+.2f}%</code> from target)\n"
    )

    if note:
        text += f"\n📝 <i>{note}</i>\n"

    text += (
        f"\n⚡ This alert has been removed.\n"
        f"Use /setalert to set a new target."
    )

    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send alert notification to {user_id}: {e}")
