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
import time
import asyncio
from datetime import datetime, timedelta
from telegram.ext import ContextTypes

from database import (
    get_all_trading_users, get_open_trades, open_trade, close_trade,
    get_all_alert_users, get_active_alerts, mark_alert_triggered,
    get_users_due_for_report, mark_report_sent, get_daily_pnl, get_weekly_pnl,
    get_all_users_for_key_check, get_settings, get_multi_symbols,
    get_pending_confirmation, resolve_trade_confirmation,
    create_trade_confirmation, get_all_subscribed_users,
    get_users_expiring_soon, has_open_trade_for_symbol,
    log_signal_to_db, get_platform_stats,
)
from exchange import get_exchange, fetch_ohlcv, fetch_ticker, place_market_order
from strategy import generate_signal
from arbitrage import run_arbitrage_scan
from logger_setup import (
    log_signal, log_trade_open, log_trade_close, report_error_to_admin
)
from config import ADMIN_IDS, MEXC_KEY_EXPIRY_DAYS

logger = logging.getLogger(__name__)

# ── Scheduler tick counters ───────────────────────────────────────────────────
_suggestion_counter  = 0
_key_check_counter   = 0
_arb_counter         = 0
SUGGESTION_INTERVAL  = 5    # every 5 ticks (5 min)
KEY_CHECK_INTERVAL   = 10   # every 10 ticks (10 min)
ARB_SCAN_INTERVAL    = 3    # every 3 ticks (3 min) — arb windows are short-lived
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
    global _suggestion_counter, _key_check_counter, _arb_counter
    _suggestion_counter += 1
    _key_check_counter  += 1
    _arb_counter        += 1

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
            await report_error_to_admin(context, e, "check_api_key_expiry")

    # 6. Confirmation timeouts
    try:
        await expire_pending_confirmations(context)
    except Exception as e:
        logger.error(f"Confirmation timeout error: {e}")

    # 7. Subscription renewal reminders (every 10 min)
    if _key_check_counter % KEY_CHECK_INTERVAL == 0:
        try:
            await send_renewal_reminders(context)
        except Exception as e:
            logger.error(f"Renewal reminder error: {e}")

    # 8. Daily database backup (every 24 hours = 1440 ticks)
    if _suggestion_counter % 1440 == 0:
        try:
            await run_db_backup(context)
        except Exception as e:
            logger.error(f"DB backup error: {e}")
            await report_error_to_admin(context, e, "run_db_backup")

    # 9. SL warning check (every 5 min)
    if _suggestion_counter % SUGGESTION_INTERVAL == 0:
        try:
            await check_sl_warnings(context)
        except Exception as e:
            logger.error(f"SL warning check error: {e}")

    # 10. Arbitrage scan (every 3 min)
    if _arb_counter % ARB_SCAN_INTERVAL == 0:
        try:
            await run_arbitrage_notifications(context)
        except Exception as e:
            logger.error(f"Arbitrage scan error: {e}")


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
    log_signal_to_db(user_id, symbol, signal["action"], signal["confidence"], signal["reason"])

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

        # Deduplication: block if we already have an open trade on this symbol
        if has_open_trade_for_symbol(user_id, symbol):
            logger.info(f"Skipping buy — already have open trade on {symbol} for user {user_id}")
            return

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
    """
    Scan SUGGESTION_SYMBOLS for high-confidence signals and push Telegram
    notifications to every subscribed user who has signal_suggestions ON.

    Runs every SUGGESTION_INTERVAL ticks (~5 min by default).

    Per-user, per-symbol cooldown (SUGGESTION_COOLDOWN_HOURS) prevents
    repeated alerts for the same opportunity.  Signals are also written to
    the database so /signals history is always up-to-date.
    """
    now_ts            = time.time()
    ohlcv_cache: dict = {}   # keyed "exch_id:symbol" — shared across users
    users             = get_all_subscribed_users()

    for user in users:
        user    = dict(user)
        user_id = user["user_id"]
        exch_id = user.get("exchange", "")

        if not user.get("api_key"):
            continue
        s = get_settings(user_id)
        if not s or not s.get("signal_suggestions", 1):
            continue

        try:
            exch = get_exchange(exch_id, user["api_key"], user["api_secret"], user.get("api_pass", ""))
        except Exception as e:
            logger.warning(f"[SIG] Could not connect {exch_id} uid={user_id}: {e}")
            continue

        user_syms  = get_multi_symbols(user_id) or [user.get("symbol", "BTC/USDT")]
        user_cache = _recently_suggested.setdefault(user_id, {})

        for symbol in SUGGESTION_SYMBOLS:
            if symbol in user_syms:
                continue   # already trading it — skip suggestion

            if now_ts - user_cache.get(symbol, 0) < SUGGESTION_COOLDOWN_HOURS * 3600:
                continue   # too soon to repeat

            ck = f"{exch_id}:{symbol}"
            if ck not in ohlcv_cache:
                try:
                    ohlcv_cache[ck] = fetch_ohlcv(exch, symbol, "1h", 100)
                except Exception as e:
                    logger.debug(f"[SIG] OHLCV fetch failed {ck}: {e}")
                    ohlcv_cache[ck] = []

            if not ohlcv_cache[ck]:
                continue

            try:
                signal = generate_signal(ohlcv_cache[ck], symbol)
            except Exception as e:
                logger.warning(f"[SIG] generate_signal error {symbol} uid={user_id}: {e}")
                continue

            # Always persist to DB (populates /signals history)
            try:
                log_signal(symbol, signal["action"], signal["confidence"], signal["reason"], user_id)
                log_signal_to_db(user_id, symbol, signal["action"], signal["confidence"], signal["reason"])
            except Exception as e:
                logger.warning(f"[SIG] DB log failed {symbol} uid={user_id}: {e}")

            action     = signal.get("action", "HOLD")
            confidence = signal.get("confidence", 0)

            # Only notify on strong actionable signals
            if action == "HOLD" or confidence < SUGGESTION_MIN_CONFIDENCE:
                continue

            # Stamp cooldown before the send so parallel ticks cannot double-fire
            user_cache[symbol] = now_ts

            action_icon = "\U0001f4c8" if action == "BUY" else "\U0001f4c9"
            filled      = confidence // 20
            conf_bar    = "\U0001f7e9" * filled + "\u2b1c" * (5 - filled)

            text = (
                f"{action_icon} <b>Signal Alert \u2014 {action} {symbol}</b>\n\n"
                f"Confidence: <code>{confidence}%</code>  {conf_bar}\n"
                f"Reason:     <i>{signal.get('reason', 'N/A')}</i>\n\n"
                f"\U0001f4a1 This is a <b>suggestion</b> \u2014 you are not currently trading {symbol}.\n"
                f"Add it via /settings \u2192 \U0001f501 Multi-Symbol, or update your symbol and /start_trade.\n\n"
                f"\U0001f515 Disable these alerts: /settings \u2192 Signal Alerts"
            )
            try:
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
                logger.info(f"[SIG] Sent signal alert uid={user_id} {symbol} {action} {confidence}%")
            except Exception as e:
                logger.warning(f"[SIG] Notification failed uid={user_id} {symbol}: {e}")
                user_cache.pop(symbol, None)   # revert so it retries next tick
                await report_error_to_admin(context, e, f"signal_notification uid={user_id} {symbol}")

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


# ═════════════════════════════════════════════════════════════════════════════
# 7. Subscription renewal reminders
# ═════════════════════════════════════════════════════════════════════════════

async def send_renewal_reminders(context):
    """Notify users 3 days and 1 day before subscription expiry."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    for days in [3, 1]:
        users = get_users_expiring_soon(days)
        for user in users:
            uid = user["user_id"]
            day_word = f"{days} day" + ("s" if days > 1 else "")
            # Build full renewal keyboard with all payment options
            from config import CRYPTO_NETWORKS, FREE_TRIAL_DAYS
            active_nets = {k: v for k, v in CRYPTO_NETWORKS.items() if v.get("address")}
            renew_keyboard = [
                [InlineKeyboardButton("── 💳 Renew via Paystack ─────────────", callback_data="noop")],
                [InlineKeyboardButton("1 Month  $12",        callback_data="pay_1"),
                 InlineKeyboardButton("3 Months $34",        callback_data="pay_3")],
                [InlineKeyboardButton("6 Months $65 (best)", callback_data="pay_6")],
            ]
            if active_nets:
                renew_keyboard += [[InlineKeyboardButton("── 🪙 Renew via Crypto (USDT) ───────", callback_data="noop")]]
                for net_key, net_info in active_nets.items():
                    renew_keyboard += [[InlineKeyboardButton(
                        f"🪙 {net_info['label']} — USDT",
                        callback_data=f"crypto_net_{net_key}"
                    )]]
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=(
                        f"⏰ <b>Subscription Expiring Soon!</b>\n\n"
                        f"Your CryptoTradeBot subscription expires in "
                        f"<b>{day_word}</b> on <code>{user['sub_expiry'][:10]}</code>.\n\n"
                        f"Renew now to avoid losing access and having open trades "
                        f"go unmonitored.\n\n"
                        f"Choose a payment method below:"
                    ),
                    reply_markup=InlineKeyboardMarkup(renew_keyboard),
                    parse_mode="HTML"
                )
                logger.info(f"Renewal reminder sent to uid={uid} ({day_word} left)")
            except Exception as e:
                logger.warning(f"Renewal reminder failed uid={uid}: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# 8. Database backup
# ═════════════════════════════════════════════════════════════════════════════

async def run_db_backup(context):
    """Run a database backup and notify admins."""
    from backup import run_backup
    try:
        path = run_backup()
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"💾 <b>Database Backup Complete</b>\n\n<code>{path}</code>",
                    parse_mode="HTML"
                )
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        raise


# ═════════════════════════════════════════════════════════════════════════════
# 9. Stop Loss warning before close
# ═════════════════════════════════════════════════════════════════════════════

# Track which trades have already been warned to avoid repeat messages
_sl_warned: set = set()


async def check_sl_warnings(context):
    """
    Warn users when a trade reaches 80% of its stop loss distance
    so they can intervene before it auto-closes.
    """
    from database import get_all_trading_users, get_open_trades, get_settings
    users = get_all_trading_users()
    for user in users:
        uid  = user["user_id"]
        s    = get_settings(uid)
        open_t = get_open_trades(uid)
        if not open_t:
            continue
        try:
            exch = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
        except Exception:
            continue

        sl_val  = s["stop_loss"]
        sl_mode = s.get("sl_mode", "pct")

        for trade in open_t:
            trade = dict(trade)
            tid   = trade["id"]
            if tid in _sl_warned:
                continue
            try:
                ticker = fetch_ticker(exch, trade["symbol"])
                price  = ticker["last"]
                entry  = trade["entry_price"]

                if sl_mode == "pct":
                    sl_distance_pct = sl_val / 100
                    current_loss    = (entry - price) / entry if trade["side"] == "buy" else (price - entry) / entry
                    pct_to_sl       = current_loss / sl_distance_pct if sl_distance_pct > 0 else 0
                else:
                    sl_price       = sl_val
                    total_dist     = abs(entry - sl_price)
                    current_dist   = abs(price - sl_price)
                    pct_to_sl      = 1 - (current_dist / total_dist) if total_dist > 0 else 0

                if pct_to_sl >= 0.80:
                    _sl_warned.add(tid)
                    pnl_pct = ((price - entry) / entry * 100) if trade["side"] == "buy" else ((entry - price) / entry * 100)
                    await context.bot.send_message(
                        chat_id=uid,
                        text=(
                            f"⚠️ <b>Stop Loss Warning!</b>\n\n"
                            f"<b>{trade['symbol']}</b> is approaching your stop loss.\n\n"
                            f"Entry:     <code>${entry:,.6f}</code>\n"
                            f"Current:   <code>${price:,.6f}</code>\n"
                            f"Current P/L: <code>{pnl_pct:+.2f}%</code>\n"
                            f"SL proximity: <code>{pct_to_sl*100:.0f}%</code> of the way there\n\n"
                            f"The trade will close automatically when SL is hit.\n"
                            f"Use /panic to close now if you want to exit manually."
                        ),
                        parse_mode="HTML"
                    )
                    logger.info(f"SL warning sent: uid={uid} trade={tid} {trade['symbol']}")
            except Exception as e:
                logger.warning(f"SL warning check failed for trade {tid}: {e}")



# ═════════════════════════════════════════════════════════════════════════════
# 10. Arbitrage scan + Telegram notifications
# ═════════════════════════════════════════════════════════════════════════════

# Per-opportunity fingerprint tracking for arb alerts.
# Maps user_id → {fingerprint: last_sent_ts}.
# A fingerprint encodes the specific exchange pair + symbol (cross) or
# exchange + path (triangular).  This means:
#   • The SAME opportunity won't spam across back-to-back scan ticks
#   • A DIFFERENT opportunity fires immediately regardless
#   • Fingerprints expire after ARB_OPP_COOLDOWN_SECS so recurring opps re-alert
_arb_seen:             dict[int, dict[str, float]] = {}
ARB_OPP_COOLDOWN_SECS: int = 600   # 10 min per specific opportunity


def _arb_fp_cross(opp) -> str:
    return f"X:{opp.buy_exchange}>{opp.sell_exchange}:{opp.symbol}"


def _arb_fp_tri(opp) -> str:
    return f"T:{opp.exchange}:{'|'.join(opp.path)}"


def _filter_new_arb_opps(
    user_id: int,
    opps_cross: list,
    opps_tri:   list,
    now:        float,
) -> tuple[list, list]:
    """
    Return only opps whose fingerprint hasn't been notified within the cooldown
    window.  Prunes expired fingerprints to keep the dict bounded.
    """
    seen = _arb_seen.setdefault(user_id, {})
    for fp in [k for k, ts in list(seen.items()) if now - ts > ARB_OPP_COOLDOWN_SECS]:
        del seen[fp]
    new_cross = [o for o in opps_cross if _arb_fp_cross(o) not in seen]
    new_tri   = [o for o in opps_tri   if _arb_fp_tri(o)   not in seen]
    return new_cross, new_tri


async def run_arbitrage_notifications(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Iterate every subscribed user, build their exchange pool, scan for
    arbitrage opportunities, and fire a Telegram alert when viable ones exist.

    The network-bound scan runs in a thread pool (asyncio.to_thread) so it
    does not block the async event loop.
    """
    from database import get_all_subscribed_users
    subscribed = get_all_subscribed_users()
    if not subscribed:
        return

    for user in subscribed:
        user_id = user["user_id"] if isinstance(user, dict) else int(user)
        try:
            await _run_arb_for_user(context, user_id)
        except Exception as e:
            logger.warning(f"[ARB] User {user_id} arb scan error: {e}")


async def _run_arb_for_user(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """
    Background arb scan for one user.

    New-opportunity model: each distinct opportunity (exchange+pair fingerprint)
    has its own 10-min cooldown so:
    • A brand-new opportunity notifies immediately even if another just fired
    • The same stale opportunity doesn't spam every 3-min scan tick
    • A previously seen opportunity re-alerts after ARB_OPP_COOLDOWN_SECS (10 min)
    """
    from database import get_stored_exchanges, get_exchange_creds, get_settings
    from exchange import get_exchange as _get_exchange

    now = time.time()

    # ── User preferences ──────────────────────────────────────────────────────
    settings = get_settings(user_id)
    if not settings.get("arb_enabled", 1):
        return
    if not settings.get("arb_alerts", 1):
        return

    # ── Exchange credentials ──────────────────────────────────────────────────
    stored = get_stored_exchanges(user_id)
    if not stored:
        return

    exchanges: dict = {}
    for ex_id in stored:
        try:
            creds = get_exchange_creds(user_id, ex_id)
            if not creds:
                continue
            exchanges[ex_id] = _get_exchange(
                ex_id,
                creds.get("api_key",    ""),
                creds.get("api_secret", ""),
                creds.get("api_pass",   ""),
            )
        except Exception as e:
            logger.debug(f"[ARB] Could not build {ex_id} uid={user_id}: {e}")

    if not exchanges:
        return

    # ── Resolve user-selected symbols ─────────────────────────────────────────
    import json as _json
    user_symbols = None
    raw_syms = settings.get("arb_symbols")
    if raw_syms:
        try:
            parsed = _json.loads(raw_syms)
            user_symbols = parsed if isinstance(parsed, list) and parsed else None
        except Exception:
            pass

    # ── Blocking scan off the event loop ─────────────────────────────────────
    try:
        result = await asyncio.to_thread(run_arbitrage_scan, exchanges, 1_000.0, user_symbols)
    except Exception as e:
        logger.error(f"[ARB] Scan thread crashed uid={user_id}: {e}", exc_info=True)
        await report_error_to_admin(context, e, f"arb_background_scan uid={user_id}")
        return

    # ── Surface exchange-wide scan errors to admin ────────────────────────────
    scan_errors   = result.get("scan_errors", [])
    exchange_wide = [e for e in scan_errors if e.symbol in ("cross_exchange", "triangular")]
    if exchange_wide:
        summary = "; ".join(f"{e.exchange}: {e.error}" for e in exchange_wide[:3])
        logger.warning(f"[ARB] Exchange-wide errors uid={user_id}: {summary}")
        await report_error_to_admin(context, Exception(summary), f"arb_scan uid={user_id}")
    elif scan_errors:
        logger.warning(
            f"[ARB] {len(scan_errors)} symbol errors uid={user_id}: "
            + "; ".join(f"{e.exchange}/{e.symbol}" for e in scan_errors[:5])
        )

    if result["viable_count"] == 0:
        return

    # ── Filter to only NEW (unseen/expired) opportunities ────────────────────
    all_cross_viable = [o for o in result["cross_exchange"] if o.viable]
    all_tri_viable   = [o for o in result["triangular"]     if o.viable]
    new_cross, new_tri = _filter_new_arb_opps(user_id, all_cross_viable, all_tri_viable, now)

    if not new_cross and not new_tri:
        return   # all current opps were already notified recently

    # ── Stamp fingerprints BEFORE sending ─────────────────────────────────────
    seen = _arb_seen.setdefault(user_id, {})
    for o in new_cross:
        seen[_arb_fp_cross(o)] = now
    for o in new_tri:
        seen[_arb_fp_tri(o)] = now

    # ── Build notification ────────────────────────────────────────────────────
    total_new = len(new_cross) + len(new_tri)
    lines: list[str] = [f"⚡ *{total_new} New Arbitrage Opportunity(ies) Found!*\n"]

    if new_cross:
        lines.append(f"📊 *Cross-Exchange ({len(new_cross)}):*")
        for opp in new_cross[:4]:
            lines.append(opp.summary())

    if new_tri:
        lines.append(f"🔺 *Triangular ({len(new_tri)}):*")
        for opp in new_tri[:4]:
            lines.append(opp.summary())

    lines.append(
        "\n💡 _Estimates based on $1 000 notional, all fees included._\n"
        "⚠️ _Cross-exchange arb requires pre-funded balances on both sides._\n"
        "👉 /arbitrage to scan on-demand, change tokens, or adjust settings."
    )

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="\n".join(lines),
            parse_mode="Markdown",
        )
        logger.info(f"[ARB] Notified uid={user_id}: {total_new} new opportunities "
                    f"({len(new_cross)} cross, {len(new_tri)} tri)")
    except Exception as e:
        logger.warning(f"[ARB] Failed to send alert to uid={user_id}: {e}")
        # Revert fingerprints so they re-attempt next tick
        for o in new_cross:
            seen.pop(_arb_fp_cross(o), None)
        for o in new_tri:
            seen.pop(_arb_fp_tri(o), None)