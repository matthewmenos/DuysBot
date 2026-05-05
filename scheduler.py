"""
scheduler.py - All automated background tasks:
  1. Auto-trading loop (TP/SL + signal scanning)
  2. Price alert monitor
  3. Signal suggestion scanner (every 5 min)
  4. Daily PnL report sender
  5. API key expiry checker (MEXC 90-day)
  6. Trade confirmation timeout handler
"""

import json
import logging
from datetime import datetime, timedelta
from telegram.ext import ContextTypes

from database import (
    get_all_trading_users, get_open_trades, open_trade, close_trade,
    get_all_alert_users, get_active_alerts, mark_alert_triggered,
    get_users_due_for_report, mark_report_sent, get_daily_pnl, get_weekly_pnl,
    get_all_users_for_key_check, get_settings, get_multi_symbols,
    get_pending_confirmation, resolve_trade_confirmation,
    create_trade_confirmation, get_all_subscribed_users,
)
from exchange import get_exchange, fetch_ohlcv, fetch_ticker, place_market_order
from strategy import generate_signal
from logger_setup import (
    log_signal, log_trade_open, log_trade_close, report_error_to_admin
)
from config import ADMIN_IDS, MEXC_KEY_EXPIRY_DAYS

logger = logging.getLogger(__name__)

# ── Scheduler tick counters ───────────────────────────────────────────────────
_suggestion_counter  = 0
_key_check_counter   = 0
SUGGESTION_INTERVAL  = 5    # every 5 ticks (5 min)
KEY_CHECK_INTERVAL   = 10   # every 10 ticks (10 min)
CONFIRM_TIMEOUT_SECS = 30   # trade confirmation expires after 30s

# Candidate symbols scanned for suggestions
SUGGESTION_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
    "XRP/USDT", "ADA/USDT", "AVAX/USDT", "DOGE/USDT",
    "LINK/USDT", "DOT/USDT", "MATIC/USDT", "NEAR/USDT",
]
SUGGESTION_MIN_CONFIDENCE = 70
SUGGESTION_COOLDOWN_HOURS = 4
_recently_suggested: dict = {}

# ── Pending confirmations: {confirm_id: (user_id, expiry_ts)} ─────────────────
_pending_confirms: dict = {}


# ═════════════════════════════════════════════════════════════════════════════
# Main scheduler entry point
# ═════════════════════════════════════════════════════════════════════════════

async def start_scheduler(context: ContextTypes.DEFAULT_TYPE):
    global _suggestion_counter, _key_check_counter
    _suggestion_counter += 1
    _key_check_counter  += 1

    # 1. Auto-trading
    for user in get_all_trading_users():
        try:
            await process_user(context, user)
        except Exception as e:
            logger.error(f"Trading error user {user['user_id']}: {e}")
            await report_error_to_admin(context, e, f"process_user uid={user['user_id']}")

    # 2. Price alerts
    try:
        await check_price_alerts(context)
    except Exception as e:
        logger.error(f"Alert error: {e}")

    # 3. Signal suggestions (every 5 min)
    if _suggestion_counter % SUGGESTION_INTERVAL == 0:
        try:
            await run_signal_suggestions(context)
        except Exception as e:
            logger.error(f"Suggestion error: {e}")

    # 4. Daily PnL reports
    try:
        await send_daily_reports(context)
    except Exception as e:
        logger.error(f"Daily report error: {e}")

    # 5. API key expiry checks (every 10 min)
    if _key_check_counter % KEY_CHECK_INTERVAL == 0:
        try:
            await check_api_key_expiry(context)
        except Exception as e:
            logger.error(f"Key expiry check error: {e}")

    # 6. Confirmation timeouts
    try:
        await expire_pending_confirmations(context)
    except Exception as e:
        logger.error(f"Confirmation timeout error: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# 1. Auto-trading loop
# ═════════════════════════════════════════════════════════════════════════════

async def process_user(context, user):
    user_id   = user["user_id"] if isinstance(user, dict) else user["user_id"]
    user      = dict(user)
    exchange  = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
    settings  = get_settings(user_id)
    trade_mode = settings.get("trade_mode", "auto")
    tp_val    = settings["take_profit"]
    sl_val    = settings["stop_loss"]
    tp_mode   = settings.get("tp_mode", "pct")
    sl_mode   = settings.get("sl_mode", "pct")
    tp_pct    = tp_val / 100 if tp_mode == "pct" else None
    sl_pct    = sl_val / 100 if sl_mode == "pct" else None
    tp_price  = tp_val if tp_mode == "price" else None
    sl_price  = sl_val if sl_mode == "price" else None
    trade_amt = settings["trade_amount"]
    trailing  = bool(settings.get("trailing_stop", 0))
    trail_pct = settings.get("trailing_stop_pct", 0.5) / 100
    confirm   = bool(settings.get("confirm_trades", 0))

    # Multi-symbol support
    symbols = get_multi_symbols(user_id)
    if not symbols:
        symbols = [settings.get("symbol", "BTC/USDT")]

    for symbol in symbols:
        try:
            await _process_symbol(
                context, user_id, exchange, user["exchange"],
                symbol, tp_pct, sl_pct, tp_price, sl_price,
                trade_amt, trailing, trail_pct, confirm, trade_mode
            )
        except Exception as e:
            logger.error(f"Symbol {symbol} error for user {user_id}: {e}")


async def _process_symbol(context, user_id, exchange, exchange_id, symbol,
                           tp_pct, sl_pct, tp_price, sl_price,
                           trade_amt, trailing, trail_pct, confirm,
                           trade_mode: str = "auto"):
    open_t  = get_open_trades(user_id)
    sym_trades = [t for t in open_t if dict(t)["symbol"] == symbol]

    ticker        = fetch_ticker(exchange, symbol)
    current_price = ticker["last"]

    # ── Check open trades for TP / SL / trailing stop ────────────────────────
    for trade in sym_trades:
        trade   = dict(trade)
        entry   = trade["entry_price"]
        pnl_pct = (current_price - entry) / entry if trade["side"] == "buy" else (entry - current_price) / entry

        # ── Take Profit check (pct or fixed price) ────────────────────────────
        if tp_price is not None:
            hit_tp = current_price >= tp_price if trade["side"] == "buy" else current_price <= tp_price
        else:
            hit_tp = pnl_pct >= (tp_pct or 0.02)

        # ── Stop Loss check (pct or fixed price, with optional trailing) ──────
        if sl_price is not None:
            hit_sl = current_price <= sl_price if trade["side"] == "buy" else current_price >= sl_price
        else:
            effective_sl = sl_pct or 0.01
            if trailing and pnl_pct > 0:
                effective_sl = max(effective_sl, pnl_pct - trail_pct)
            hit_sl = pnl_pct <= -effective_sl

        if hit_tp or hit_sl:
            pnl_usdt = trade["amount"] * pnl_pct
            reason   = "Take Profit" if hit_tp else ("Trailing Stop" if trailing else "Stop Loss")
            close_trade(trade["id"], current_price, round(pnl_usdt, 4), round(pnl_pct * 100, 2))
            log_trade_close(user_id, symbol, reason, entry, current_price, pnl_usdt, pnl_pct * 100)

            emoji = "✅" if hit_tp else "🛑"
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"{emoji} <b>{reason} Hit!</b>\n\n"
                    f"Symbol:  <code>{symbol}</code>\n"
                    f"Entry:   <code>${entry:,.6f}</code>\n"
                    f"Exit:    <code>${current_price:,.6f}</code>\n"
                    f"PnL:     <code>{'+'if pnl_usdt>=0 else ''}{pnl_usdt:.4f} USDT ({pnl_pct*100:+.2f}%)</code>"
                ),
                parse_mode="HTML"
            )
            return

    # ── No open trade for this symbol → scan for signal ───────────────────────
    if sym_trades:
        return

    ohlcv  = fetch_ohlcv(exchange, symbol, timeframe="1h", limit=100)
    signal = generate_signal(ohlcv, symbol)
    log_signal(symbol, signal["action"], signal["confidence"], signal["reason"], user_id)

    # In manual mode — only manage TP/SL on existing trades; skip new auto entries
    if trade_mode == "manual":
        return

    if signal["action"] == "BUY" and signal["confidence"] >= 50:
        # Check balance before ordering
        from exchange import fetch_usdt_balance
        try:
            balance = fetch_usdt_balance(exchange)
            if trade_amt > balance:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"⚠️ <b>Insufficient Balance</b>\n\n"
                        f"Wanted to buy <code>{symbol}</code> but your free USDT "
                        f"(<code>{balance:.4f}</code>) is below your trade amount "
                        f"(<code>{trade_amt} USDT</code>).\n\n"
                        f"Top up or lower your trade amount via /settings."
                    ),
                    parse_mode="HTML"
                )
                return
        except Exception as e:
            logger.warning(f"Balance check failed for {user_id}: {e}")

        if confirm:
            await _request_trade_confirmation(
                context, user_id, symbol, "buy", current_price, trade_amt, signal, exchange_id
            )
        else:
            await _execute_buy(context, user_id, exchange, exchange_id, symbol, current_price, trade_amt, signal)


async def _execute_buy(context, user_id, exchange, exchange_id, symbol, price, amount, signal):
    try:
        order    = place_market_order(exchange, symbol, "buy", amount)
        order_id = str(order.get("id", "N/A"))
        open_trade(user_id, symbol, "buy", price, amount, exchange_id, order_id, signal["reason"])
        log_trade_open(user_id, symbol, "buy", price, amount, exchange_id, order_id)
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"🚀 <b>BUY Executed</b>\n\n"
                f"Symbol:     <code>{symbol}</code>\n"
                f"Price:      <code>${price:,.6f}</code>\n"
                f"Amount:     <code>{amount} USDT</code>\n"
                f"Confidence: <code>{signal['confidence']}%</code>\n"
                f"📊 {signal['reason'][:180]}"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Buy failed user={user_id} symbol={symbol}: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"⚠️ <b>Order failed</b> for {symbol}:\n<code>{str(e)[:200]}</code>",
            parse_mode="HTML"
        )
        await report_error_to_admin(context, e, f"buy_order uid={user_id} {symbol}")


# ── Trade confirmation flow ───────────────────────────────────────────────────

async def _request_trade_confirmation(context, user_id, symbol, side, price, amount, signal, exchange_id):
    import json as _json
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    confirm_id = create_trade_confirmation(
        user_id, symbol, side, price, amount,
        _json.dumps({"reason": signal["reason"], "confidence": signal["confidence"]})
    )
    expiry_ts = datetime.utcnow().timestamp() + CONFIRM_TIMEOUT_SECS
    _pending_confirms[confirm_id] = (user_id, expiry_ts, exchange_id)

    keyboard = [[
        InlineKeyboardButton("✅ Approve", callback_data=f"confirm_trade_{confirm_id}_approve"),
        InlineKeyboardButton("⏭ Skip",    callback_data=f"confirm_trade_{confirm_id}_skip"),
    ]]
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"⚡ <b>Trade Signal — Approve?</b>\n\n"
            f"Symbol:     <code>{symbol}</code>\n"
            f"Side:       <code>{side.upper()}</code>\n"
            f"Price:      <code>${price:,.6f}</code>\n"
            f"Amount:     <code>{amount} USDT</code>\n"
            f"Confidence: <code>{signal['confidence']}%</code>\n\n"
            f"📊 {signal['reason'][:150]}\n\n"
            f"⏱ Auto-skips in {CONFIRM_TIMEOUT_SECS} seconds."
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def handle_trade_confirmation_callback(context, user_id: int, confirm_id: int, decision: str):
    """Called from handle_callback in handlers.py."""
    row = get_pending_confirmation(confirm_id)
    if not row:
        return  # already resolved or expired
    resolve_trade_confirmation(confirm_id, decision)
    _pending_confirms.pop(confirm_id, None)

    row = dict(row)
    if decision == "approve":
        from database import get_user
        user = get_user(user_id)
        if not user:
            return
        exchange = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
        signal   = {"reason": "Manual approval", "confidence": 100}
        try:
            sig_data = json.loads(row.get("signal_data") or "{}")
            signal   = {"reason": sig_data.get("reason", ""), "confidence": sig_data.get("confidence", 0)}
        except Exception:
            pass
        await _execute_buy(context, user_id, exchange, user["exchange"],
                           row["symbol"], row["price"], row["amount"], signal)
    else:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"⏭ Trade on <code>{row['symbol']}</code> skipped.",
            parse_mode="HTML"
        )


async def expire_pending_confirmations(context):
    """Auto-skip confirmations that have timed out."""
    now = datetime.utcnow().timestamp()
    expired = [(cid, data) for cid, data in list(_pending_confirms.items()) if data[1] < now]
    for confirm_id, (user_id, _, exchange_id) in expired:
        resolve_trade_confirmation(confirm_id, "expired")
        _pending_confirms.pop(confirm_id, None)
        try:
            row = get_pending_confirmation(confirm_id)
            sym = dict(row)["symbol"] if row else "?"
        except Exception:
            sym = "?"
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⏱ Trade confirmation for <code>{sym}</code> expired — skipped.",
                parse_mode="HTML"
            )
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
# 2. Price alert monitor
# ═════════════════════════════════════════════════════════════════════════════

async def check_price_alerts(context):
    alert_users = get_all_alert_users()
    if not alert_users:
        return

    for user in alert_users:
        user_id = user["user_id"]
        alerts  = get_active_alerts(user_id)
        if not alerts:
            continue
        try:
            exch = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
        except Exception as e:
            logger.warning(f"Alert exchange connect failed uid={user_id}: {e}")
            continue

        price_cache: dict = {}
        for alert in alerts:
            alert  = dict(alert)
            symbol = alert["symbol"]
            if symbol not in price_cache:
                try:
                    price_cache[symbol] = fetch_ticker(exch, symbol)["last"]
                except Exception:
                    continue

            current_price = price_cache[symbol]
            triggered = (
                (alert["condition"] == "above" and current_price >= alert["target_price"]) or
                (alert["condition"] == "below" and current_price <= alert["target_price"])
            )
            if triggered:
                mark_alert_triggered(alert["id"])
                await _send_alert_notification(context, user_id, alert, current_price)


async def _send_alert_notification(context, user_id, alert, current_price):
    direction = "risen above" if alert["condition"] == "above" else "fallen below"
    arrow     = "📈" if alert["condition"] == "above" else "📉"
    coin      = alert["symbol"].split("/")[0]
    pct_diff  = ((current_price - alert["target_price"]) / alert["target_price"]) * 100
    text = (
        f"🔔 <b>Price Alert Triggered!</b>\n\n"
        f"{arrow} <b>{coin}</b> has {direction} your target\n\n"
        f"Symbol:  <code>{alert['symbol']}</code>\n"
        f"Target:  <code>${alert['target_price']:,.6f}</code>\n"
        f"Current: <code>${current_price:,.6f}</code>  (<code>{pct_diff:+.2f}%</code>)\n"
    )
    if alert.get("note"):
        text += f"\n📝 <i>{alert['note']}</i>\n"
    text += "\n⚡ This alert has been removed. Use /setalert to set a new one."
    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Alert notification failed uid={user_id}: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# 3. Signal suggestions
# ═════════════════════════════════════════════════════════════════════════════

async def run_signal_suggestions(context):
    import time
    users   = get_all_subscribed_users()
    now_ts  = time.time()
    ohlcv_cache: dict = {}

    for user in users:
        user       = dict(user)
        user_id    = user["user_id"]
        exch_id    = user["exchange"]
        user_syms  = get_multi_symbols(user_id) or [user.get("symbol", "BTC/USDT")]
        if not user.get("api_key"):
            continue

        s = get_settings(user_id)
        if not s or not s.get("signal_suggestions", 1):
            continue

        try:
            exch = get_exchange(exch_id, user["api_key"], user["api_secret"], user["api_pass"])
        except Exception:
            continue

        sent = 0
        for symbol in SUGGESTION_SYMBOLS:
            if symbol in user_syms:
                continue
            user_cache = _recently_suggested.setdefault(user_id, {})
            if now_ts - user_cache.get(symbol, 0) < SUGGESTION_COOLDOWN_HOURS * 3600:
                continue

            ck = f"{exch_id}:{symbol}"
            if ck not in ohlcv_cache:
                try:
                    ohlcv_cache[ck] = fetch_ohlcv(exch, symbol, "1h", 100)
                except Exception:
                    ohlcv_cache[ck] = []

            if not ohlcv_cache[ck]:
                continue

            signal = generate_signal(ohlcv_cache[ck], symbol)
            log_signal(symbol, signal["action"], signal["confidence"], signal["reason"], user_id)

            if signal["action"] == "BUY" and signal["confidence"] >= SUGGESTION_MIN_CONFIDENCE:
                ind = signal.get("indicators", {})
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"💡 <b>Signal Suggestion</b>\n\n"
                            f"📈 <b>{symbol}</b> on <b>{exch_id.title()}</b>\n\n"
                            f"Signal:     <code>BUY</code>\n"
                            f"Confidence: <code>{signal['confidence']}%</code>\n"
                            f"Price:      <code>${ind.get('price', 0):,.6f}</code>\n"
                            f"RSI:        <code>{ind.get('rsi', 'N/A')}</code>\n"
                            f"EMA Trend:  <code>{'Bullish 📈' if ind.get('ema9',0)>ind.get('ema21',0) else 'Bearish 📉'}</code>\n\n"
                            f"📝 <i>{signal['reason'][:200]}</i>\n\n"
                            f"⚠️ Suggestion only — not financial advice.\n"
                            f"Add via /settings → 🪙 Symbol to trade it."
                        ),
                        parse_mode="HTML"
                    )
                    _recently_suggested[user_id][symbol] = now_ts
                    sent += 1
                except Exception as e:
                    logger.warning(f"Suggestion send failed uid={user_id}: {e}")
            if sent >= 2:
                break


# ═════════════════════════════════════════════════════════════════════════════
# 4. Daily PnL report
# ═════════════════════════════════════════════════════════════════════════════

async def send_daily_reports(context):
    users = get_users_due_for_report()
    for user in users:
        user    = dict(user)
        uid     = user["user_id"]
        daily   = get_daily_pnl(uid)
        weekly  = get_weekly_pnl(uid)
        if not daily or daily.get("total", 0) == 0:
            mark_report_sent(uid)
            continue

        win_rate = round((daily["wins"] / daily["total"]) * 100) if daily["total"] else 0
        text = (
            f"📊 <b>Daily Report — {datetime.utcnow().strftime('%d %b %Y')}</b>\n\n"
            f"<b>Today</b>\n"
            f"  Trades:    <code>{daily['total']}</code>\n"
            f"  Wins:      <code>{daily['wins']} ✅  Losses: {daily['losses']} 🔴</code>\n"
            f"  Win Rate:  <code>{win_rate}%</code>\n"
            f"  PnL:       <code>{'+'if daily['total_pnl']>=0 else ''}{daily['total_pnl']:.4f} USDT</code>\n"
            f"  Best:      <code>+{daily['best_trade']:.4f} USDT</code>\n"
            f"  Worst:     <code>{daily['worst_trade']:.4f} USDT</code>\n\n"
            f"<b>Last 7 Days</b>\n"
            f"  Trades:    <code>{weekly.get('total', 0)}</code>\n"
            f"  PnL:       <code>{'+'if weekly.get('total_pnl',0)>=0 else ''}{weekly.get('total_pnl',0):.4f} USDT</code>\n\n"
            f"Use /history for full trade log."
        )
        try:
            await context.bot.send_message(chat_id=uid, text=text, parse_mode="HTML")
            mark_report_sent(uid)
        except Exception as e:
            logger.warning(f"Daily report failed uid={uid}: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# 5. API key expiry check
# ═════════════════════════════════════════════════════════════════════════════

async def check_api_key_expiry(context):
    users = get_all_users_for_key_check()
    for user in users:
        user = dict(user)
        uid  = user["user_id"]
        if not user.get("mexc_key_saved_at"):
            continue
        try:
            saved    = datetime.fromisoformat(user["mexc_key_saved_at"])
            age_days = (datetime.utcnow() - saved).days
            days_left = MEXC_KEY_EXPIRY_DAYS - age_days
        except ValueError:
            continue

        if days_left in (14, 7, 3, 1):
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    f"⚠️ <b>MEXC API Key Expiry Warning</b>\n\n"
                    f"Your MEXC API key expires in <b>{days_left} day(s)</b>.\n\n"
                    f"Please:\n"
                    f"1. Log in to MEXC\n"
                    f"2. Go to API Management\n"
                    f"3. Create a new key\n"
                    f"4. Update via /settings → ⚙️ Settings → 🔑 Connect Exchange"
                ),
                parse_mode="HTML"
            )
