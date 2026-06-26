"""
handlers.py - All Telegram command and callback handlers
"""

import logging
import ccxt
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import (
    ADMIN_IDS, POPULAR_SYMBOLS, QUOTE_CURRENCY, SUPPORT_CHANNEL_ID,
    FREE_TRIAL_DAYS, MEXC_KEY_EXPIRY_DAYS, CRYPTO_NETWORKS,
)
from database import (
    get_user, upsert_user, grant_user, get_settings, update_setting,
    get_trade_history, get_open_trades, get_pnl_summary,
    save_exchange_creds, save_support_message, get_all_trading_users,
    get_stored_exchanges, get_exchange_creds, switch_exchange,
    close_trade, init_db,
    has_active_access, get_subscription_status, activate_subscription,
    record_pending_payment, get_subscription_history, grant_user_lifetime,
    get_all_subscribers, has_used_trial, activate_trial,
    record_crypto_payment, confirm_crypto_payment, get_crypto_payment_history,
    record_mexc_key_saved, get_mexc_key_age_days,
    get_multi_symbols, set_multi_symbols,
    get_daily_pnl, get_weekly_pnl,
    get_pending_confirmation, resolve_trade_confirmation,
)
from paystack import initialize_transaction, verify_transaction
from crypto_payment import verify_usdt_tx, get_payment_instructions, PLAN_PRICES_USDT
from config import TRONGRID_API_KEY, BSCSCAN_API_KEY, PLAN_PRICES
from referral import get_referral_link, get_referral_stats, resolve_start_referral, reward_referrer
from logger_setup import report_error_to_admin, init_error_reporter
from exchange import (
    get_exchange, fetch_balance, fetch_ticker, fetch_ohlcv,
    SUPPORTED_EXCHANGES, EXCHANGE_LABELS, close_all_positions,
    fetch_usdt_balance, PASSPHRASE_EXCHANGES, check_key_format,
    get_exchange_label, get_exchange_note, get_min_trade_amount,
)
from strategy import generate_signal
from persistence import get_arb_sel, set_arb_sel, K_ARB_SEL
from database import (
    get_paper_balance, update_paper_balance, open_paper_trade,
    get_open_paper_trades, get_paper_trade_history, get_paper_stats,
    generate_webhook_token, get_webhook_token, get_webhook_logs,
    create_dca_plan, get_dca_plans, set_dca_status, get_dca_stats, get_dca_plan,
    create_grid_plan, get_active_grids, get_grid_plan, get_grid_orders,
    set_grid_status, get_analytics_data, get_full_trade_history,
    publish_strategy, get_strategies, get_strategy, subscribe_strategy,
    unsubscribe_strategy, get_user_strategy_sub, get_strategy_leaderboard,
    write_audit, get_audit_log, create_webdash_token,
)
from analytics import compute_analytics

logger = logging.getLogger(__name__)

# Initialise DB on first import
init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────
# require_granted, require_creds, is_admin, is_granted live in utils.py
# to avoid circular imports with alerts_handlers.py
from utils import require_granted, require_creds, is_admin, is_granted


# ── Auto-delete helper ────────────────────────────────────────────────────────
_DELETE_DELAY = 120  # seconds before sensitive messages self-delete

async def _delete_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    """job_queue callback — silently deletes a message."""
    data = context.job.data
    try:
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["message_id"])
    except Exception:
        pass  # already deleted or bot lost permission — ignore

async def _send_expiring(context, chat_id: int, text: str, **kwargs) -> None:
    """
    Send a message that auto-deletes after _DELETE_DELAY seconds.
    Appends a notice so the user knows it will disappear.
    """
    notice = f"\n\n<i>🕐 This message deletes in {_DELETE_DELAY} seconds.</i>"
    parse_mode = kwargs.pop("parse_mode", ParseMode.HTML)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text + notice,
        parse_mode=parse_mode,
        **kwargs,
    )
    context.job_queue.run_once(
        _delete_message,
        when=_DELETE_DELAY,
        data={"chat_id": chat_id, "message_id": msg.message_id},
    )


# ── Persistent Reply Keyboards ────────────────────────────────────────────────

def get_main_menu(uid: int) -> ReplyKeyboardMarkup:
    """Bottom persistent keyboard — shown to all authorised users."""
    rows = [
        [KeyboardButton("📊 Dashboard"),  KeyboardButton("💰 Balance")],
        [KeyboardButton("▶️ Start Trade"), KeyboardButton("⏹ Stop Trade")],
        [KeyboardButton("📊 Chart"),      KeyboardButton("📈 PnL")],
        [KeyboardButton("📜 History"),    KeyboardButton("💊 Health")],
        [KeyboardButton("📂 Positions"),  KeyboardButton("📡 Signals")],
        [KeyboardButton("🔔 Set Alert"),  KeyboardButton("🔕 My Alerts")],
        [KeyboardButton("⚙️ Settings"),   KeyboardButton("🏦 Exchanges")],
        [KeyboardButton("💳 Subscribe"),  KeyboardButton("🪪 My Status")],
        [KeyboardButton("🔗 Referral"),   KeyboardButton("📩 Support")],
        [KeyboardButton("❓ Help"),        KeyboardButton("🚨 Panic")],
    ]
    if uid in ADMIN_IDS:
        rows.append([KeyboardButton("👥 Subscribers"), KeyboardButton("📴 Close All")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def get_unauth_menu() -> ReplyKeyboardMarkup:
    """Minimal keyboard for users without access."""
    rows = [
        [KeyboardButton("💳 Subscribe"), KeyboardButton("❓ Help")],
        [KeyboardButton("🏦 Exchanges")],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, persistent=True)


# Map button labels → handler functions (populated at bottom of file)
BUTTON_MAP: dict = {}

# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username or "", is_admin=1 if is_admin(user.id) else 0)

    if not is_admin(user.id) and not has_active_access(user.id):
        trial_used  = has_used_trial(user.id)
        active_nets = {k: v for k, v in CRYPTO_NETWORKS.items() if v.get("address")}

        # ── Build full payment keyboard ───────────────────────────────────────
        keyboard = []

        # Free trial first (if not used)
        if not trial_used:
            keyboard += [[InlineKeyboardButton(
                f"🆓 Start {FREE_TRIAL_DAYS}-Day Free Trial  (No payment needed)",
                callback_data="free_trial"
            )]]

        # Paystack plans
        keyboard += [
            [InlineKeyboardButton("── 💳 Pay via Paystack ──────────────", callback_data="noop")],
            [InlineKeyboardButton("1 Month  $12",          callback_data="pay_1"),
             InlineKeyboardButton("3 Months $34",          callback_data="pay_3")],
            [InlineKeyboardButton("6 Months $65 (best)",   callback_data="pay_6")],
        ]

        # Crypto networks (only configured ones)
        if active_nets:
            keyboard += [[InlineKeyboardButton(
                "── 🪙 Pay via Crypto (USDT) ─────────", callback_data="noop"
            )]]
            for net_key, net_info in active_nets.items():
                keyboard += [[InlineKeyboardButton(
                    f"🪙 {net_info['label']} — USDT",
                    callback_data=f"crypto_net_{net_key}"
                )]]

        # ── Build message ─────────────────────────────────────────────────────
        trial_section = ""
        if not trial_used:
            trial_section = (
                f"\n🆓 <b>Free Trial Available!</b>\n"
                f"  Enjoy <b>{FREE_TRIAL_DAYS} days</b> of full access — no payment needed.\n"
                f"  One per account, verified by your Telegram ID.\n"
            )

        crypto_line = ""
        if active_nets:
            nets_str = " • ".join(v["label"] for v in active_nets.values())
            crypto_line = f"\n<b>🪙 Crypto</b> — USDT via {nets_str}"

        _p1, _p3, _p6 = PLAN_PRICES[1], PLAN_PRICES[3], PLAN_PRICES[6]
        await update.effective_message.reply_text(
            f"👋 Welcome to <b>CryptoTradeBot</b>, {user.first_name}!\n"
            f"{trial_section}\n"
            f"<b>📦 Subscription Plans</b>\n"
            f"  • 1 Month  — ${_p1:.2f}\n"
            f"  • 3 Months — ${_p3:.2f}  <i>(save ${3*_p1-_p3:.0f})</i>\n"
            f"  • 6 Months — ${_p6:.2f}  <i>(save ${6*_p1-_p6:.0f})</i>\n\n"
            f"<b>💳 Paystack</b> — Card, Mobile Money, Bank Transfer"
            f"{crypto_line}\n\n"
            f"Your Telegram ID: <code>{user.id}</code>\n"
            f"<i>Share this with an admin for lifetime access.</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        await update.effective_message.reply_text(
            "Choose a payment method above to get started:",
            reply_markup=get_unauth_menu(),
            parse_mode=ParseMode.HTML
        )
        return

    s = get_settings(user.id)
    open_t = get_open_trades(user.id)
    trading_status = "🟢 Trading ON" if s and s["trading_on"] else "🔴 Trading OFF"

    user_obj     = get_user(user.id)
    has_exchange = bool(user_obj and user_obj.get("api_key"))
    exch_label   = get_exchange_label(user_obj["exchange"] if user_obj else "")
    exchange_line = f"Exchange: <code>{exch_label}</code>\n" if has_exchange else "Exchange: <code>Not Set ⚠️  — tap 🔑 Connect Exchange</code>\n"

    dashboard = (
        f"👋 Welcome back, <b>{user.first_name}</b>!\n\n"
        f"🤖 <b>CryptoTradeBot</b> is active.\n\n"
        f"{exchange_line}"
        f"Status:  {trading_status}\n"
        f"Symbol:  <code>{s['symbol'] if s else 'BTC/USDT'}</code>\n"
        f"Open trades: <code>{len(open_t)}</code>\n\n"
        f"Use the buttons below to navigate:"
    )

    # Quick-action inline buttons — context-aware based on trading status
    is_trading  = bool(s and s.get("trading_on"))
    trade_mode  = s.get("trade_mode", "auto") if s else "auto"

    inline_kb = [
        [InlineKeyboardButton("📊 Dashboard",      callback_data="cmd_dashboard"),
         InlineKeyboardButton("💰 Balance",        callback_data="cmd_balance")],
    ]
    if is_trading:
        if trade_mode == "manual":
            inline_kb += [
                [InlineKeyboardButton("🟢 Start Now — Buy Now", callback_data="manual_buy_now"),
                 InlineKeyboardButton("⏹ Stop Trading",         callback_data="cmd_stop_trade")],
                [InlineKeyboardButton("💊 Health",               callback_data="cmd_health"),
                 InlineKeyboardButton("📂 Positions",            callback_data="cmd_positions")],
            ]
        else:
            inline_kb += [
                [InlineKeyboardButton("💊 Health",               callback_data="cmd_health"),
                 InlineKeyboardButton("📂 Positions",            callback_data="cmd_positions")],
                [InlineKeyboardButton("⏹ Stop Trading",         callback_data="cmd_stop_trade"),
                 InlineKeyboardButton("🚨 Panic Close",          callback_data="cmd_panic")],
            ]
    else:
        inline_kb += [
            [InlineKeyboardButton("▶️ Start Trade",              callback_data="cmd_start_trade"),
             InlineKeyboardButton("⚙️ Settings",                 callback_data="cmd_settings")],
            [InlineKeyboardButton("🔑 Connect Exchange",          callback_data="set_exchange"),
             InlineKeyboardButton("🪙 Set Symbol",               callback_data="set_symbol")],
        ]
    inline_kb += [
        [InlineKeyboardButton("📊 Chart",          callback_data="cmd_chart"),
         InlineKeyboardButton("📈 PnL",            callback_data="cmd_pnl")],
        [InlineKeyboardButton("📜 History",        callback_data="cmd_history"),
         InlineKeyboardButton("📡 Signals",        callback_data="cmd_signals")],
        [InlineKeyboardButton("💳 Subscribe",      callback_data="subscribe"),
         InlineKeyboardButton("❓ Help",            callback_data="cmd_help")],
    ]

    await update.effective_message.reply_text(
        dashboard,
        reply_markup=InlineKeyboardMarkup(inline_kb),
        parse_mode=ParseMode.HTML
    )
    # Attach persistent reply keyboard silently
    await update.effective_message.reply_text(
        "📌 Menu ready — tap any button below or above to get started.",
        reply_markup=get_main_menu(user.id),
        parse_mode=ParseMode.HTML
    )


# ── /balance ──────────────────────────────────────────────────────────────────

@require_granted
@require_creds
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    user = get_user(uid)
    msg  = await update.effective_message.reply_text("⏳ Fetching balance...")

    try:
        exch  = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
        bal   = fetch_balance(exch)
        label = get_exchange_label(user["exchange"])

        def fmt(val: float) -> str:
            """Format with up to 8 significant decimal places, no trailing zeros."""
            if val == 0:
                return "0.00"
            if val >= 1_000:
                return f"{val:,.2f}"
            if val >= 1:
                return f"{val:,.4f}"
            # Small value — show up to 8 decimal places, strip trailing zeros
            s = f"{val:.8f}".rstrip("0").rstrip(".")
            return s

        keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="cmd_balance")]]
        lines    = [f"💰 <b>Balance — {label}</b>\n"]
        for coin, data in bal.items():
            free_fmt  = fmt(data["free"])
            total_fmt = fmt(data["total"])
            locked    = data["total"] - data["free"]
            locked_fmt = fmt(locked) if locked > 0 else None
            line = f"  <b>{coin}</b>\n    Free:   <code>{free_fmt}</code>\n    Total:  <code>{total_fmt}</code>"
            if locked_fmt:
                line += f"\n    Locked: <code>{locked_fmt}</code>"
            lines.append(line)
        if not bal:
            lines.append("  No assets found.")
        await msg.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    except ccxt.AuthenticationError:
        await msg.edit_text(
            f"❌ <b>Authentication Failed</b>\n\n"
            f"Your {EXCHANGE_LABELS.get(user['exchange'], 'exchange')} API keys are invalid or expired.\n\n"
            f"Please update them via /settings → ⚙️ Settings → 🔑 Connect Exchange.",
            parse_mode=ParseMode.HTML
        )
    except ccxt.PermissionDenied:
        await msg.edit_text(
            f"❌ <b>Permission Denied</b>\n\n"
            f"Your API keys lack the required permissions.\n"
            f"Enable <b>Read</b> and <b>Spot Trading</b> on your exchange API settings.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Could not fetch balance</b>\n\n<code>{str(e)[:200]}</code>",
            parse_mode=ParseMode.HTML
        )


# ── /start_trade ──────────────────────────────────────────────────────────────

@require_granted
@require_creds
async def start_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    s    = get_settings(uid)
    user = get_user(uid)

    # ── Validate: trade amount must not exceed available USDT balance ─────────
    try:
        exch         = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
        usdt_balance = fetch_usdt_balance(exch)
        trade_amount = s["trade_amount"]

        if trade_amount > usdt_balance:
            gap = trade_amount - usdt_balance
            await update.effective_message.reply_text(
                f"⚠️ <b>Insufficient Balance — Trading NOT started</b>\n\n"
                f"Trade amount:     <code>{trade_amount:.2f} USDT</code>\n"
                f"Available USDT:   <code>{usdt_balance:.4f} USDT</code>\n"
                f"Shortfall:        <code>{gap:.4f} USDT</code>\n\n"
                f"To fix this, either:\n"
                f"  • Lower your trade amount → /settings → 💵 Trade Amount\n"
                f"  • Deposit at least <code>{gap:.2f} USDT</code> more to your exchange",
                parse_mode=ParseMode.HTML
            )
            return
    except Exception as e:
        await update.effective_message.reply_text(
            f"⚠️ Could not verify balance: <code>{e}</code>\n"
            "Please check your API connection and try again.",
            parse_mode=ParseMode.HTML
        )
        return

    # ── Show mode picker: Auto or Manual ─────────────────────────────────────
    cur_mode = s.get("trade_mode", "auto")
    tp_label = f"{s['take_profit']}{'%' if s.get('tp_mode','pct')=='pct' else ' USDT'}"
    sl_label = f"{s['stop_loss']}{'%' if s.get('sl_mode','pct')=='pct' else ' USDT'}"

    keyboard = [
        [InlineKeyboardButton(
            f"🤖 Auto Trade{'  ✅' if cur_mode == 'auto' else ''}",
            callback_data="trade_mode_auto"
        )],
        [InlineKeyboardButton(
            f"👆 Manual Trade{'  ✅' if cur_mode == 'manual' else ''}",
            callback_data="trade_mode_manual"
        )],
    ]
    await update.effective_message.reply_text(
        f"⚡ <b>Start Trading — Choose Mode</b>\n\n"
        f"Symbol:        <code>{s['symbol']}</code>\n"
        f"Trade Amount:  <code>{trade_amount:.2f} USDT</code>\n"
        f"USDT Balance:  <code>{usdt_balance:.8f}</code>\n"
        f"Take Profit:   <code>{tp_label}</code>\n"
        f"Stop Loss:     <code>{sl_label}</code>\n\n"
        f"<b>🤖 Auto Trade</b> — Bot scans signals every 60s and buys automatically.\n\n"
        f"<b>👆 Manual Trade</b> — You tap <b>🟢 Start Now</b> to buy whenever you're ready. "
        f"TP/SL still close the trade automatically.\n\n"
        f"{'✅ Current mode: <b>Auto</b>' if cur_mode == 'auto' else '✅ Current mode: <b>Manual</b>'} — tap to confirm or switch:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


# ── /stop_trade ───────────────────────────────────────────────────────────────

@require_granted
async def stop_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    update_setting(uid, "trading_on", 0)
    open_t   = get_open_trades(uid)
    keyboard = []
    if open_t:
        keyboard += [[
            InlineKeyboardButton(f"💊 View {len(open_t)} Open Trade(s)", callback_data="cmd_health"),
            InlineKeyboardButton("🚨 Panic Close", callback_data="cmd_panic"),
        ]]
    keyboard += [[
        InlineKeyboardButton("▶️ Start Again",  callback_data="cmd_start_trade"),
        InlineKeyboardButton("📈 PnL",          callback_data="cmd_pnl"),
    ]]
    await update.effective_message.reply_text(
        f"⏹ <b>Trading DISABLED</b>\n\n"
        f"Open positions: <code>{len(open_t)}</code> — they will NOT be closed automatically.\n"
        f"Use /health to monitor them or /panic to close all.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


# ── /settings ─────────────────────────────────────────────────────────────────

AWAITING_SETTING = {}  # user_id -> which field we're waiting on

@require_granted
async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    s   = get_settings(uid)
    u   = get_user(uid)
    label = get_exchange_label(u["exchange"] if u else "")

    # Get extra settings
    confirm_on    = bool(s.get("confirm_trades", 0))
    trailing_on   = bool(s.get("trailing_stop", 0))
    suggestions_on = bool(s.get("signal_suggestions", 1))
    arb_alerts_on  = bool(s.get("arb_alerts", 1))
    multi_syms    = get_multi_symbols(uid)

    keyboard = [
        [InlineKeyboardButton("🔑 Connect Exchange",       callback_data="set_exchange")],
        [InlineKeyboardButton(
            f"🎯 Take Profit {'%' if s.get('tp_mode','pct')=='pct' else '$'}  ({s['take_profit']}{'%' if s.get('tp_mode','pct')=='pct' else ' USDT'})",
            callback_data="set_tp"
        )],
        [InlineKeyboardButton(
            f"🛑 Stop Loss {'%' if s.get('sl_mode','pct')=='pct' else '$'}  ({s['stop_loss']}{'%' if s.get('sl_mode','pct')=='pct' else ' USDT'})",
            callback_data="set_sl"
        )],
        [InlineKeyboardButton(
            f"🔁 TP Mode: {'Percentage %' if s.get('tp_mode','pct')=='pct' else 'Fixed Price $'}",
            callback_data="toggle_tp_mode"
        ),
         InlineKeyboardButton(
            f"🔁 SL Mode: {'Percentage %' if s.get('sl_mode','pct')=='pct' else 'Fixed Price $'}",
            callback_data="toggle_sl_mode"
        )],
        [InlineKeyboardButton("💵 Trade Amount",           callback_data="set_amount"),
         InlineKeyboardButton("🪙 Symbol(s)",              callback_data="set_symbol")],
        [InlineKeyboardButton(
            f"✅ Confirm Trades" if confirm_on else "⬜ Confirm Trades",
            callback_data="toggle_confirm"
        ),
         InlineKeyboardButton(
            f"✅ Trailing Stop" if trailing_on else "⬜ Trailing Stop",
            callback_data="toggle_trailing"
        )],
        [InlineKeyboardButton(
            f"✅ Signal Alerts" if suggestions_on else "⬜ Signal Alerts",
            callback_data="toggle_suggestions"
        ),
         InlineKeyboardButton(
            f"✅ Arb Alerts" if arb_alerts_on else "⬜ Arb Alerts",
            callback_data="toggle_arb_alerts"
        )],
        [InlineKeyboardButton(
            f"🤖 Auto Trade" if s.get("trade_mode","auto") == "auto" else "👆 Manual Trade",
            callback_data="toggle_trade_mode"
        )],
    ]
    # MEXC key expiry warning
    mexc_warning = ""
    if u and u["exchange"] == "mexc":
        age = get_mexc_key_age_days(uid)
        if age is not None:
            days_left = MEXC_KEY_EXPIRY_DAYS - age
            if days_left <= 14:
                mexc_warning = (
                    f"\n⚠️ <b>MEXC Key Expiry Warning</b>\n"
                    f"Your MEXC API key expires in <b>{days_left} day(s)</b>!\n"
                    f"Renew it on MEXC and update via 🔑 Connect Exchange.\n"
                )
            elif days_left <= 0:
                mexc_warning = (
                    f"\n🚨 <b>MEXC Key Likely Expired!</b>\n"
                    f"Your key is {abs(days_left)} days past the 90-day limit.\n"
                    f"Please renew immediately via 🔑 Connect Exchange.\n"
                )

    multi_sym_display = ", ".join(multi_syms) if multi_syms else s["symbol"]
    await update.effective_message.reply_text(
        f"⚙️ <b>Your Settings</b>\n\n"
        f"Exchange:          <code>{label}</code>\n"
        f"Symbol(s):         <code>{multi_sym_display}</code>\n"
        f"Trade Amount:      <code>{s['trade_amount']} USDT</code>\n"
        f"Take Profit:       <code>{s['take_profit']}{'%' if s.get('tp_mode','pct')=='pct' else ' USDT (fixed price)'}</code>\n"
        f"Stop Loss:         <code>{s['stop_loss']}{'%' if s.get('sl_mode','pct')=='pct' else ' USDT (fixed price)'}</code>\n"
        f"Confirm Trades:    <code>{'ON ✅' if confirm_on else 'OFF ⬜'}</code>\n"
        f"Trailing Stop:     <code>{'ON ✅' if trailing_on else 'OFF ⬜'}</code>\n"
        f"Signal Alerts:     <code>{'ON ✅' if suggestions_on else 'OFF ⬜'}</code>\n"
        f"Arb Alerts:        <code>{'ON ✅' if arb_alerts_on else 'OFF ⬜'}</code>\n"
        f"Trade Mode:        <code>{'🤖 Auto' if s.get('trade_mode','auto')=='auto' else '👆 Manual'}</code>\n"
        f"{mexc_warning}\n"
        f"Tap a button to update:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


# ── /history ──────────────────────────────────────────────────────────────────

@require_granted
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    trades = get_trade_history(uid, limit=10)
    if not trades:
        await update.effective_message.reply_text("📜 No trade history yet.")
        return

    lines = ["📜 <b>Last 10 Trades</b>\n"]
    for t in trades:
        icon = "✅" if (t["pnl"] or 0) >= 0 else "🔴"
        pnl  = t["pnl"] or 0
        pct  = t["pnl_pct"] or 0
        lines.append(
            f"{icon} <b>{t['symbol']}</b> [{t['side'].upper()}] @ <code>${t['entry_price']:,.4f}</code>\n"
            f"   PnL: <code>{'+'if pnl>=0 else ''}{pnl:.4f} USDT ({pct:+.2f}%)</code> | {t['status'].upper()}\n"
            f"   {t['opened_at'][:16]}"
        )
    hist_kb = [[
        InlineKeyboardButton("📈 Full PnL",    callback_data="cmd_pnl"),
        InlineKeyboardButton("📋 Summary",     callback_data="cmd_summary"),
    ]]
    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(hist_kb),
        parse_mode=ParseMode.HTML
    )


# ── /chart ────────────────────────────────────────────────────────────────────

@require_granted
@require_creds
async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    user = get_user(uid)
    s    = get_settings(uid)
    msg  = await update.effective_message.reply_text("⏳ Fetching chart data & signal...")

    try:
        exch   = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
        ohlcv  = fetch_ohlcv(exch, s["symbol"], "1h", 100)
        ticker = fetch_ticker(exch, s["symbol"])
        signal = generate_signal(ohlcv, s["symbol"])

        action_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal["action"], "⚪")
        ind = signal["indicators"]

        cmc = signal.get("cmc", {})
        cmc_block = ""
        if cmc:
            cmc_block = (
                f"\n<b>CoinMarketCap</b>\n"
                f"Rank:     <code>#{cmc.get('rank', 'N/A')}</code>\n"
                f"24h:      <code>{cmc.get('change_24h', 0):+.2f}%</code>\n"
                f"7d:       <code>{cmc.get('change_7d', 0):+.2f}%</code>\n"
                f"Mkt Cap:  <code>${cmc.get('market_cap', 0):,.0f}</code>\n"
            )

        text = (
            f"📊 <b>Chart — {s['symbol']} (1H)</b>\n\n"
            f"💲 Price:      <code>${ticker['last']:,.6f}</code>\n"
            f"📈 24h Change: <code>{ticker['change_pct']:+.2f}%</code>\n"
            f"📦 Volume:     <code>{ticker['volume']:,.0f} USDT</code>\n\n"
            f"<b>Technical Indicators</b>\n"
            f"RSI:    <code>{ind['rsi']}</code>\n"
            f"EMA9:   <code>{ind['ema9']}</code>\n"
            f"EMA21:  <code>{ind['ema21']}</code>\n"
            f"MACD:   <code>{ind['macd']}</code>\n"
            f"BB Up:  <code>{ind['bb_up']}</code>\n"
            f"BB Low: <code>{ind['bb_low']}</code>\n"
            f"News:   <code>{ind['news']}</code>\n"
            f"{cmc_block}"
            f"\n{action_emoji} <b>Signal: {signal['action']}</b>  "
            f"Confidence: <code>{signal['confidence']}%</code>\n"
            f"📝 {signal['reason'][:300]}"
        )
        # Context-aware buttons based on signal
        s2 = get_settings(uid)
        if signal["action"] == "BUY" and signal["confidence"] >= 50:
            action_btns = [[
                InlineKeyboardButton("🚀 Trade This Signal", callback_data="manual_buy_now"),
                InlineKeyboardButton("🔄 Refresh",           callback_data="cmd_chart"),
            ]]
        elif signal["action"] == "SELL":
            action_btns = [[
                InlineKeyboardButton("🔕 Set Price Alert",   callback_data="cmd_setalert"),
                InlineKeyboardButton("🔄 Refresh",           callback_data="cmd_chart"),
            ]]
        else:
            action_btns = [[
                InlineKeyboardButton("🔄 Refresh Chart",     callback_data="cmd_chart"),
                InlineKeyboardButton("⚙️ Settings",          callback_data="cmd_settings"),
            ]]
        await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(action_btns), parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(f"❌ Chart error:\n<code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /pnl ─────────────────────────────────────────────────────────────────────

@require_granted
async def pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    row = get_pnl_summary(uid)
    open_t = get_open_trades(uid)

    text = (
        f"📊 <b>PnL Summary</b>\n\n"
        f"Total Trades:  <code>{row['total_trades']}</code>\n"
        f"Wins:          <code>{row['wins']} ✅</code>\n"
        f"Losses:        <code>{row['losses']} 🔴</code>\n"
        f"Total PnL:     <code>{'+'if row['total_pnl']>=0 else ''}{row['total_pnl']:.4f} USDT</code>\n"
        f"Avg PnL/Trade: <code>{row['avg_pnl_pct']:+.2f}%</code>\n"
        f"Open Trades:   <code>{len(open_t)}</code>"
    )
    pnl_kb = [[
        InlineKeyboardButton("📜 History",     callback_data="cmd_history"),
        InlineKeyboardButton("📋 Summary",     callback_data="cmd_summary"),
    ]]
    await update.effective_message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(pnl_kb),
        parse_mode=ParseMode.HTML
    )


# ── /health ───────────────────────────────────────────────────────────────────

@require_granted
@require_creds
async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid        = update.effective_user.id
    user       = get_user(uid)
    open_t     = get_open_trades(uid)
    s          = get_settings(uid)
    status_txt = "🟢 ACTIVE" if s["trading_on"] else "🔴 STOPPED"

    if not open_t:
        await update.effective_message.reply_text(
            f"💊 <b>Trade Health</b>\n\nBot Status: {status_txt}\nNo open trades.",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        exch   = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
        lines  = [f"💊 <b>Trade Health</b>\nBot: {status_txt}\n"]

        for t in open_t:
            ticker  = fetch_ticker(exch, t["symbol"])
            price   = ticker["last"]
            pnl_pct = (price - t["entry_price"]) / t["entry_price"] * 100
            pnl_usd = t["amount"] * pnl_pct / 100
            icon    = "📈" if pnl_pct >= 0 else "📉"
            lines.append(
                f"{icon} <b>{t['symbol']}</b>\n"
                f"   Entry: <code>${t['entry_price']:,.4f}</code> → Now: <code>${price:,.4f}</code>\n"
                f"   PnL: <code>{'+'if pnl_usd>=0 else ''}{pnl_usd:.4f} USDT ({pnl_pct:+.2f}%)</code>\n"
                f"   TP: <code>{s['take_profit']}{'%' if s.get('tp_mode','pct')=='pct' else ' USDT'}</code> | "
                f"SL: <code>{s['stop_loss']}{'%' if s.get('sl_mode','pct')=='pct' else ' USDT'}</code>"
            )
        health_kb = [
            [InlineKeyboardButton("🔄 Refresh",      callback_data="cmd_health"),
             InlineKeyboardButton("🚨 Panic Close",  callback_data="cmd_panic")],
            [InlineKeyboardButton("📊 Chart",        callback_data="cmd_chart"),
             InlineKeyboardButton("📈 PnL",          callback_data="cmd_pnl")],
        ]
        await update.effective_message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(health_kb),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /summary ──────────────────────────────────────────────────────────────────

@require_granted
async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    trades = get_trade_history(uid, limit=50)
    row    = get_pnl_summary(uid)

    if not trades:
        await update.effective_message.reply_text("📋 No completed trades to summarise.")
        return

    best  = max(trades, key=lambda t: t["pnl"] or 0)
    worst = min(trades, key=lambda t: t["pnl"] or 0)
    win_rate = (row["wins"] / row["total_trades"] * 100) if row["total_trades"] else 0

    text = (
        f"📋 <b>Trade Cycle Summary</b>\n\n"
        f"Period:        <code>{trades[-1]['opened_at'][:10]} → {trades[0]['opened_at'][:10]}</code>\n"
        f"Total Trades:  <code>{row['total_trades']}</code>\n"
        f"Win Rate:      <code>{win_rate:.1f}%</code>\n"
        f"Total PnL:     <code>{'+'if row['total_pnl']>=0 else ''}{row['total_pnl']:.4f} USDT</code>\n\n"
        f"🏆 Best Trade:  <code>+{best['pnl']:.4f} USDT</code> on {best['symbol']}\n"
        f"💔 Worst Trade: <code>{worst['pnl']:.4f} USDT</code> on {worst['symbol']}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /exchanges ────────────────────────────────────────────────────────────────

async def exchanges(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["🏦 <b>Supported Exchanges</b>\n"]
    for key, label in EXCHANGE_LABELS.items():
        if not key:  # skip "Not Set" placeholder
            continue
        lines.append(f"  {label} — <code>{key}</code>")
    lines.append("\nTap a button below to connect your exchange:")
    ex_kb = [[InlineKeyboardButton("🔑 Connect Exchange", callback_data="set_exchange")]]
    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(ex_kb),
        parse_mode=ParseMode.HTML
    )


# ── /support ──────────────────────────────────────────────────────────────────

@require_granted
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    tg_user  = update.effective_user
    username = f"@{tg_user.username}" if tg_user.username else tg_user.full_name
    msg      = " ".join(context.args) if context.args else ""

    if not msg:
        PENDING_INPUT[uid] = {"field": "support_msg"}
        await update.effective_message.reply_text(
            "📩 <b>Contact Support</b>\n\n"
            "Please type your message and I will forward it to the support team:",
            parse_mode=ParseMode.HTML
        )
        return

    await _send_support_message(context, uid, username, msg)


async def _send_support_message(context, uid: int, username: str, msg: str):
    """Forward support message to the private support channel and notify user."""
    save_support_message(uid, msg)

    forward_text = (
        f"📩 <b>Support Request</b>\n\n"
        f"👤 User: {username} (<code>{uid}</code>)\n\n"
        f"💬 Message:\n{msg}"
    )

    sent = False

    # 1. Forward to private support channel if configured
    if SUPPORT_CHANNEL_ID:
        try:
            await context.bot.send_message(
                chat_id=int(SUPPORT_CHANNEL_ID),
                text=forward_text,
                parse_mode=ParseMode.HTML
            )
            sent = True
        except Exception as e:
            logger.error(f"Failed to send to support channel {SUPPORT_CHANNEL_ID}: {e}")

    # 2. Also DM each admin
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=forward_text,
                parse_mode=ParseMode.HTML
            )
            sent = True
        except Exception:
            pass

    if sent:
        await context.bot.send_message(
            chat_id=uid,
            text="✅ Your message has been forwarded to the support team. We'll get back to you soon!",
            parse_mode=ParseMode.HTML
        )
    else:
        await context.bot.send_message(
            chat_id=uid,
            text="⚠️ Could not deliver your message right now. Please try again later.",
            parse_mode=ParseMode.HTML
        )


# ── /grant (admin only) ───────────────────────────────────────────────────────

async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("🚫 Admin only.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: <code>/grant &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML)
        return
    try:
        target_id = int(context.args[0])
        upsert_user(target_id)   # ensure row exists before granting
        grant_user(target_id)
        await update.effective_message.reply_text(
            f"✅ User <code>{target_id}</code> has been granted <b>lifetime access</b>.",
            parse_mode=ParseMode.HTML
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="🎉 <b>Access Granted!</b>\nYou can now use CryptoTradeBot.\nType /start to begin.",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


# ── /panic (admin only) ───────────────────────────────────────────────────────

@require_granted
@require_creds
async def panic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Users — close all their own open trades + stop auto-trading."""
    uid    = update.effective_user.id
    open_t = get_open_trades(uid)

    if not open_t:
        update_setting(uid, "trading_on", 0)
        await update.effective_message.reply_text(
            "✅ <b>No open trades found.</b>\nAuto-trading has been stopped.",
            parse_mode=ParseMode.HTML
        )
        return

    keyboard = [[
        InlineKeyboardButton("🚨 YES — Close my trades", callback_data="panic_confirm_user"),
        InlineKeyboardButton("❌ Cancel",                  callback_data="panic_cancel"),
    ]]
    lines = ["🚨 <b>PANIC — Close Your Trades</b>\n\nTrades to be closed at market price:\n"]
    for t in open_t:
        lines.append(f"  • <b>{t['symbol']}</b> | Entry: <code>${t['entry_price']:,.4f}</code>")
    lines.append("\n⚠️ This cannot be undone. Confirm?")
    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


# ── /close (admin only) — close ALL trades platform-wide ─────────────────────

async def close_all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin — emergency close ALL open trades across every user on the platform."""
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("🚫 Admin only.")
        return

    all_users  = get_all_trading_users()
    open_count = sum(len(get_open_trades(u["user_id"])) for u in all_users)

    if open_count == 0:
        await update.effective_message.reply_text(
            "✅ No open trades found across any user.",
            parse_mode=ParseMode.HTML
        )
        return

    keyboard = [[
        InlineKeyboardButton("🚨 YES — Close ALL platform trades", callback_data="panic_confirm_admin"),
        InlineKeyboardButton("❌ Cancel", callback_data="panic_cancel"),
    ]]
    await update.effective_message.reply_text(
        f"🚨 <b>ADMIN — Close All Trades</b>\n\n"
        f"This will close <b>all open trades across all users</b> "
        f"and stop their auto-trading.\n\n"
        f"Open trades found: <code>{open_count}</code> across <code>{len(all_users)}</code> user(s)\n\n"
        f"⚠️ <b>This cannot be undone.</b> Are you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


async def _execute_user_panic(context, uid: int, notify_msg=None):
    """Close all open trades for a single user and stop their auto-trading."""
    user   = get_user(uid)
    open_t = get_open_trades(uid)
    closed = 0
    errors = []

    if open_t:
        try:
            exch    = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
            results = close_all_positions(exch, open_t)
            for r, t in zip(results, open_t):
                if r["status"] == "closed":
                    pnl_pct = (r["price"] - t["entry_price"]) / t["entry_price"] * 100
                    pnl_usd = t["amount"] * pnl_pct / 100
                    close_trade(t["id"], r["price"], round(pnl_usd, 4), round(pnl_pct, 2))
                    closed += 1
                else:
                    errors.append(f"{t['symbol']}: {r['status']}")
        except Exception as e:
            errors.append(str(e))

    update_setting(uid, "trading_on", 0)
    return closed, errors



# ── /subscribe ────────────────────────────────────────────────────────────────

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    user = update.effective_user
    upsert_user(uid, user.username or "")

    # Handle referral from /start ref_XXXXXXXX
    if context.args:
        start_param = context.args[0]
        referrer_id = resolve_start_referral(start_param)
        if referrer_id and referrer_id != uid:
            from referral import record_referral, generate_referral_code
            record_referral(referrer_id, uid, generate_referral_code(referrer_id))

    status = get_subscription_status(uid)
    if status["access"]:
        stype = status.get("type", "subscription")
        if stype == "lifetime":
            type_label = "♾ Lifetime (Admin Grant)"
        elif stype == "trial":
            type_label = f"🆓 Free Trial — {status.get('days_left', '?')} days left"
        else:
            type_label = f"📅 Paid — expires {status.get('expiry', '?')}"
        await update.effective_message.reply_text(
            f"✅ <b>You already have active access</b>\n\nPlan: <code>{type_label}</code>\n\nEnjoy trading! 🚀",
            parse_mode=ParseMode.HTML
        )
        return

    trial_used = has_used_trial(uid)

    # Build keyboard dynamically
    keyboard = []

    # Section 1: Free trial (only if not used)
    if not trial_used:
        keyboard += [[
            InlineKeyboardButton(
                f"🆓 {FREE_TRIAL_DAYS}-Day Free Trial  (No payment needed)",
                callback_data="free_trial"
            )
        ]]

    # Section 2: Paystack
    keyboard += [
        [InlineKeyboardButton("── 💳 Pay via Paystack ──────────────", callback_data="noop")],
        [InlineKeyboardButton("1 Month  $12",           callback_data="pay_1"),
         InlineKeyboardButton("3 Months $34",           callback_data="pay_3")],
        [InlineKeyboardButton("6 Months $65 (best value)", callback_data="pay_6")],
    ]

    # Section 3: Crypto — one button per configured network
    active_nets = {k: v for k, v in CRYPTO_NETWORKS.items() if v.get("address")}
    if active_nets:
        keyboard += [[InlineKeyboardButton("── 🪙 Pay via Crypto (USDT) ─────────", callback_data="noop")]]
        for net_key, net_info in active_nets.items():
            keyboard += [[InlineKeyboardButton(
                f"🪙 {net_info['label']} — USDT",
                callback_data=f"crypto_net_{net_key}"
            )]]

    trial_section = ""
    if not trial_used:
        trial_section = (
            f"\n🆓 <b>Free Trial Available!</b>\n"
            f"  Get <b>{FREE_TRIAL_DAYS} days free</b> — no payment needed.\n"
            f"  One per account, verified by your Telegram ID.\n"
        )

    crypto_section = ""
    if active_nets:
        nets_str = " • ".join(v["label"] for v in active_nets.values())
        crypto_section = f"<b>🪙 Crypto USDT</b> — {nets_str}\n"

    _p1, _p3, _p6 = PLAN_PRICES[1], PLAN_PRICES[3], PLAN_PRICES[6]
    await update.effective_message.reply_text(
        f"🤖 <b>CryptoTradeBot — Subscribe</b>\n"
        f"{trial_section}\n"
        f"<b>📦 Plans</b>\n"
        f"  • 1 Month  — ${_p1:.2f}\n"
        f"  • 3 Months — ${_p3:.2f}  <i>(save ${3*_p1-_p3:.0f})</i>\n"
        f"  • 6 Months — ${_p6:.2f}  <i>(save ${6*_p1-_p6:.0f})</i>\n\n"
        f"<b>💳 Paystack</b> — Card, Mobile Money, Bank Transfer\n"
        f"{crypto_section}\n"
        f"Tap a button to get started:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


# ── /mystatus ─────────────────────────────────────────────────────────────────

async def mystatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    status = get_subscription_status(uid)
    history = get_subscription_history(uid)

    if not status["access"]:
        keyboard = [[InlineKeyboardButton("💳 Subscribe Now", callback_data="subscribe")]]
        await update.effective_message.reply_text(
            "❌ <b>No Active Subscription</b>\n\n"
            f"Status: <code>{status['type'].title()}</code>\n\n"
            "Subscribe to access the bot:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        return

    type_label = "♾ Lifetime (Admin Grant)" if status["type"] == "lifetime" else f"📅 Until {status['expiry']} ({status.get('days_left', '?')} days left)"
    lines = [f"✅ <b>Subscription Status</b>\n\nAccess: {type_label}"]

    if history:
        lines.append("\n📋 <b>Payment History</b>")
        for row in history[:5]:
            icon = "✅" if row["status"] == "success" else "⏳"
            lines.append(f"{icon} {row['months']}mo — ${row['amount']:.2f} {row['currency']} | {(row['paid_at'] or row['created_at'])[:10]}")

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── /subscribers (admin only) ─────────────────────────────────────────────────

async def reply_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin only — reply to a user directly from the bot.
    Usage: /reply <user_id> <message>
    Also: if admin replies to a forwarded support message, the bot
          extracts the user_id from the message and forwards the reply.
    """
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("🚫 Admin only.")
        return

    # ── Case 1: /reply <user_id> <message> ───────────────────────────────────
    if context.args and len(context.args) >= 2:
        try:
            target_id = int(context.args[0])
            message   = " ".join(context.args[1:])
        except ValueError:
            await update.effective_message.reply_text(
                "Usage: <code>/reply &lt;user_id&gt; &lt;message&gt;</code>",
                parse_mode=ParseMode.HTML
            )
            return

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=(
                    f"📩 <b>Reply from Support</b>\n\n"
                    f"{message}"
                ),
                parse_mode=ParseMode.HTML
            )
            await update.effective_message.reply_text(
                f"✅ Reply sent to <code>{target_id}</code>.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            await update.effective_message.reply_text(
                f"❌ Failed to send reply: <code>{e}</code>",
                parse_mode=ParseMode.HTML
            )
        return

    # ── Case 2: Admin replies to a forwarded support message ─────────────────
    # The forwarded support message contains "user_id" in the text
    reply_to = update.message.reply_to_message if update.message else None
    if reply_to and reply_to.text:
        import re
        # Extract user_id from forwarded support message format:
        # "👤 User: @username (123456789)"
        match = re.search(r"\((\d{5,})\)", reply_to.text)
        if match:
            target_id = int(match.group(1))
            message   = " ".join(context.args) if context.args else update.message.text.replace("/reply", "").strip()
            if not message:
                await update.effective_message.reply_text(
                    "Please include a message after /reply, or use <code>/reply &lt;user_id&gt; &lt;message&gt;</code>",
                    parse_mode=ParseMode.HTML
                )
                return
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"📩 <b>Reply from Support</b>\n\n{message}",
                    parse_mode=ParseMode.HTML
                )
                await update.effective_message.reply_text(
                    f"✅ Reply sent to <code>{target_id}</code>.",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                await update.effective_message.reply_text(
                    f"❌ Could not send: <code>{e}</code>",
                    parse_mode=ParseMode.HTML
                )
            return

    await update.effective_message.reply_text(
        "Usage:\n"
        "  <code>/reply &lt;user_id&gt; &lt;message&gt;</code>\n\n"
        "Or reply to a forwarded support message with <code>/reply &lt;message&gt;</code>",
        parse_mode=ParseMode.HTML
    )


async def subscribers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("🚫 Admin only.")
        return

    rows = get_all_subscribers()
    if not rows:
        await update.effective_message.reply_text("No subscribers yet.")
        return

    lines = [f"👥 <b>All Subscribers ({len(rows)})</b>\n"]
    for r in rows:
        label = "♾ Lifetime" if r["granted"] == 1 else f"📅 {(r['sub_expiry'] or '')[:10]}"
        name  = f"@{r['username']}" if r["username"] else str(r["user_id"])
        lines.append(f"  {name} — {label}")

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)




# ── /dashboard ────────────────────────────────────────────────────────────────

@require_granted
@require_creds
async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    user   = get_user(uid)
    s      = get_settings(uid)
    open_t = get_open_trades(uid)
    daily  = get_daily_pnl(uid)
    status = get_subscription_status(uid)
    symbols = get_multi_symbols(uid) or [s["symbol"]]

    trading_icon = "🟢" if s["trading_on"] else "🔴"
    label        = get_exchange_label(user["exchange"])

    # Live balance
    balance_line = ""
    try:
        exch = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
        from exchange import fetch_usdt_balance
        bal  = fetch_usdt_balance(exch)
        balance_line = f"💵 Free USDT:   <code>{bal:.4f} USDT</code>\n"
    except Exception:
        balance_line = "💵 Balance:     <code>unavailable</code>\n"

    # Open trade summary
    trade_lines = ""
    for t in open_t:
        t = dict(t)
        trade_lines += f"  • {t['symbol']} | Entry: ${t['entry_price']:,.4f}\n"
    if not trade_lines:
        trade_lines = "  None\n"

    # Daily PnL
    if daily and daily.get("total", 0) > 0:
        pnl_line = (
            f"📈 Today PnL:   <code>{'+'if daily['total_pnl']>=0 else ''}"
            f"{daily['total_pnl']:.4f} USDT</code> "
            f"({daily['wins']}W / {daily['losses']}L)\n"
        )
    else:
        pnl_line = "📈 Today PnL:   <code>No trades today</code>\n"

    sub_type  = status.get("type", "none")
    sub_expiry = status.get("expiry", "N/A")
    sub_line  = f"{'♾' if sub_type=='lifetime' else '📅'} Subscription: <code>{sub_type.title()} — {sub_expiry}</code>\n"

    await update.effective_message.reply_text(
        f"📊 <b>Dashboard</b>\n\n"
        f"{trading_icon} Trading:       <code>{'ON' if s['trading_on'] else 'OFF'}</code>\n"
        f"🏦 Exchange:    <code>{label}</code>\n"
        f"🪙 Symbol(s):  <code>{', '.join(symbols)}</code>\n"
        f"{balance_line}"
        f"🎯 TP:         <code>{s['take_profit']}{'%' if s.get('tp_mode','pct')=='pct' else ' USDT (fixed)'}</code>\n"
        f"🛑 SL:         <code>{s['stop_loss']}{'%' if s.get('sl_mode','pct')=='pct' else ' USDT (fixed)'}</code>\n"
        f"💰 Trade Amt:  <code>{s['trade_amount']} USDT</code>\n"
        f"{sub_line}\n"
        f"<b>📂 Open Trades ({len(open_t)})</b>\n{trade_lines}\n"
        f"{pnl_line}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 Positions",    callback_data="cmd_positions"),
             InlineKeyboardButton("📊 Chart",        callback_data="cmd_chart")],
            [InlineKeyboardButton("💰 Balance",      callback_data="cmd_balance"),
             InlineKeyboardButton("⚙️ Settings",     callback_data="cmd_settings")],
            [InlineKeyboardButton("🔄 Refresh",      callback_data="cmd_dashboard"),
             InlineKeyboardButton("📡 Signals",      callback_data="cmd_signals")],
        ]),
        parse_mode=ParseMode.HTML
    )


# ── /broadcast (admin only) ───────────────────────────────────────────────────

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("🚫 Admin only.")
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: <code>/broadcast Your message here</code>",
            parse_mode=ParseMode.HTML
        )
        return

    message = " ".join(context.args)
    from database import get_all_subscribed_users
    users    = get_all_subscribed_users()
    sent     = 0
    failed   = 0
    msg      = await update.effective_message.reply_text(f"📡 Broadcasting to {len(users)} user(s)...")

    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=f"📢 <b>Announcement</b>\n\n{message}",
                parse_mode=ParseMode.HTML
            )
            sent += 1
        except Exception:
            failed += 1

    await msg.edit_text(
        f"✅ Broadcast complete.\n\nSent: <code>{sent}</code>  Failed: <code>{failed}</code>",
        parse_mode=ParseMode.HTML
    )


# ── /referral ─────────────────────────────────────────────────────────────────

@require_granted
async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    bot_info = await context.bot.get_me()
    link     = get_referral_link(uid, bot_info.username)
    stats    = get_referral_stats(uid)

    await _send_expiring(
        context, update.effective_user.id,
        f"🔗 <b>Your Referral Link</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"Share this link. When someone subscribes through it, "
        f"you earn <b>1 free month</b> automatically.\n\n"
        f"<b>Your Stats</b>\n"
        f"  Total referrals:    <code>{stats['total']}</code>\n"
        f"  Rewarded:           <code>{stats['rewarded']}</code>\n"
        f"  Pending (not paid): <code>{stats['pending']}</code>\n\n"
        f"Referral code: <code>{stats['code']}</code>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 My Status", callback_data="cmd_mystatus"),
        ]]),
    )



# ── /positions ────────────────────────────────────────────────────────────────

@require_granted
@require_creds
async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all open positions with live unrealised PnL."""
    uid    = update.effective_user.id
    user   = get_user(uid)
    open_t = get_open_trades(uid)

    if not open_t:
        keyboard = [[InlineKeyboardButton("▶️ Start Trading", callback_data="cmd_start_trade")]]
        await update.effective_message.reply_text(
            "📂 <b>No Open Positions</b>\n\nYou have no active trades right now.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        return

    msg = await update.effective_message.reply_text("⏳ Fetching live prices...")
    try:
        exch   = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
        label  = get_exchange_label(user["exchange"])
        lines  = [f"📂 <b>Open Positions — {label}</b>\n"]


        total_pnl = 0.0
        for t in open_t:
            t      = dict(t)
            ticker = fetch_ticker(exch, t["symbol"])
            price  = ticker["last"]
            entry  = t["entry_price"]
            side   = t["side"]
            pnl_pct = ((price - entry) / entry * 100) if side == "buy" else ((entry - price) / entry * 100)
            pnl_usd = t["amount"] * pnl_pct / 100
            total_pnl += pnl_usd
            icon = "📈" if pnl_pct >= 0 else "📉"
            duration = ""
            try:
                from datetime import datetime
                opened  = datetime.fromisoformat(t["opened_at"])
                elapsed = datetime.utcnow() - opened
                hrs     = int(elapsed.total_seconds() // 3600)
                mins    = int((elapsed.total_seconds() % 3600) // 60)
                duration = f"  ⏱ Open {hrs}h {mins}m\n"
            except Exception:
                pass
            lines.append(
                f"{icon} <b>{t['symbol']}</b> [{side.upper()}]\n"
                f"  Entry:   <code>${entry:,.6f}</code>\n"
                f"  Current: <code>${price:,.6f}</code>\n"
                f"  PnL:     <code>{'+'if pnl_usd>=0 else ''}{pnl_usd:.4f} USDT ({pnl_pct:+.2f}%)</code>\n"
                f"  Amount:  <code>{t['amount']:.2f} USDT</code>\n"
                f"{duration}"
            )

        total_icon = "📈" if total_pnl >= 0 else "📉"
        lines.append(f"\n{total_icon} <b>Total Unrealised PnL: <code>{'+'if total_pnl>=0 else ''}{total_pnl:.4f} USDT</code></b>")

        keyboard = [
            [InlineKeyboardButton("🔄 Refresh",      callback_data="cmd_positions"),
             InlineKeyboardButton("🚨 Panic Close",  callback_data="cmd_panic")],
        ]
        await msg.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Failed to fetch positions</b>\n\n<code>{str(e)[:200]}</code>",
            parse_mode=ParseMode.HTML
        )


# ── /export ───────────────────────────────────────────────────────────────────

@require_granted
async def export_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export full trade history as a CSV file."""
    uid    = update.effective_user.id
    trades = get_trade_history(uid, limit=1000)

    if not trades:
        await update.effective_message.reply_text("📭 No trade history to export yet.")
        return

    msg = await update.effective_message.reply_text("⏳ Generating CSV...")
    try:
        import csv, io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["#", "Symbol", "Side", "Entry Price", "Exit Price",
                         "Amount (USDT)", "PnL (USDT)", "PnL %",
                         "Status", "Exchange", "Signal", "Opened At", "Closed At"])
        for i, t in enumerate(trades, 1):
            t = dict(t)
            writer.writerow([
                i,
                t.get("symbol", ""),
                t.get("side", "").upper(),
                f"{t.get('entry_price', 0):.6f}",
                f"{t.get('exit_price', 0) or 0:.6f}",
                f"{t.get('amount', 0):.4f}",
                f"{t.get('pnl', 0) or 0:.4f}",
                f"{t.get('pnl_pct', 0) or 0:.2f}",
                t.get("status", ""),
                t.get("exchange", ""),
                (t.get("signal") or "")[:60],
                t.get("opened_at", "")[:16],
                t.get("closed_at", "")[:16] if t.get("closed_at") else "",
            ])

        csv_bytes = output.getvalue().encode("utf-8")
        from telegram import InputFile
        import io as _io
        await msg.delete()
        await update.effective_message.reply_document(
            document=InputFile(_io.BytesIO(csv_bytes), filename=f"trades_{uid}.csv"),
            caption=f"📊 <b>Trade History Export</b>\n{len(trades)} trades",
            parse_mode=ParseMode.HTML
        )
        export_kb = [[
            InlineKeyboardButton("📈 PnL Summary",  callback_data="cmd_pnl"),
            InlineKeyboardButton("📋 Summary",      callback_data="cmd_summary"),
        ]]
        await update.effective_message.reply_text(
            "Need anything else?",
            reply_markup=InlineKeyboardMarkup(export_kb),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Export failed</b>\n\n<code>{str(e)[:200]}</code>",
            parse_mode=ParseMode.HTML
        )


# ── /signals ─────────────────────────────────────────────────────────────────

@require_granted
async def signals_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show last 10 signals evaluated for this user with outcomes."""
    uid  = update.effective_user.id
    from database import get_signal_history
    rows = get_signal_history(uid, limit=10)

    if not rows:
        await update.effective_message.reply_text(
            "📡 No signal history yet. Signals are recorded once trading is active.",
            parse_mode=ParseMode.HTML
        )
        return

    lines = ["📡 <b>Last 10 Signals</b>\n"]
    for r in rows:
        icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(r["action"], "⚪")
        traded = "✅ Traded" if r["resulted_in_trade"] else "⏭ Not traded"
        lines.append(
            f"{icon} <b>{r['symbol']}</b> — {r['action']} ({r['confidence']}%) — {traded}\n"
            f"   <i>{r['reason'][:80]}</i>\n"
            f"   <code>{r['created_at'][:16]}</code>"
        )
    sig_kb = [[
        InlineKeyboardButton("📊 Chart",    callback_data="cmd_chart"),
        InlineKeyboardButton("📂 Positions", callback_data="cmd_positions"),
    ]]
    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(sig_kb),
        parse_mode=ParseMode.HTML
    )


# ── /status ───────────────────────────────────────────────────────────────────

async def bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show platform-wide stats. Public for admins, basic for users."""
    uid   = update.effective_user.id
    from database import get_platform_stats
    from backup import get_backup_list
    import time

    stats   = get_platform_stats()
    backups = get_backup_list()
    last_backup = backups[0]["created"] if backups else "Never"

    if is_admin(uid):
        text = (
            f"🖥 <b>Bot Status</b>\n\n"
            f"<b>Platform</b>\n"
            f"  Users (with exchange):  <code>{stats['total_users']}</code>\n"
            f"  Active subscribers:     <code>{stats['active_subs']}</code>\n"
            f"  Trading right now:      <code>{stats['active_traders']}</code>\n\n"
            f"<b>Today</b>\n"
            f"  Open trades:            <code>{stats['open_trades']}</code>\n"
            f"  Trades closed today:    <code>{stats['today_trades']}</code>\n"
            f"  Platform PnL today:     <code>{'+'if stats['today_pnl']>=0 else ''}{stats['today_pnl']:.4f} USDT</code>\n\n"
            f"<b>System</b>\n"
            f"  Last DB backup:         <code>{last_backup}</code>\n"
            f"  Backups stored:         <code>{len(backups)}</code>\n"
        )
    else:
        text = (
            f"🤖 <b>CryptoTradeBot — Status</b>\n\n"
            f"  Active traders:    <code>{stats['active_traders']}</code>\n"
            f"  Trades today:      <code>{stats['today_trades']}</code>\n\n"
            f"Bot is online and running. ✅"
        )
    stat_kb = [[
        InlineKeyboardButton("🔄 Refresh",       callback_data="cmd_status"),
        InlineKeyboardButton("👥 Subscribers",   callback_data="cmd_subscribers"),
    ]]
    await update.effective_message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(stat_kb),
        parse_mode=ParseMode.HTML
    )


# ── /user (admin) ─────────────────────────────────────────────────────────────

async def user_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: look up full profile of any user by ID."""
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("🚫 Admin only.")
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: <code>/user &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")
        return

    from database import get_user_full_profile
    profile = get_user_full_profile(target_id)
    if not profile:
        await update.effective_message.reply_text(f"❌ User <code>{target_id}</code> not found.", parse_mode=ParseMode.HTML)
        return

    u   = profile["user"]
    s   = profile["settings"]
    sub = profile["sub"]
    tr  = profile["trades"]
    uname  = f"@{u['username']}" if u.get("username") else str(target_id)
    label  = get_exchange_label(u.get("exchange", ""))
    sub_label = "♾ Lifetime" if sub["type"] == "lifetime" else f"📅 {sub.get('expiry','N/A')} ({sub.get('days_left','?')}d left)" if sub["access"] else "❌ No access"

    text = (
        f"👤 <b>User Profile</b>\n\n"
        f"ID:           <code>{target_id}</code>\n"
        f"Username:     {uname}\n"
        f"Access:       {sub_label}\n"
        f"Exchange:     <code>{label}</code>\n"
        f"Symbol:       <code>{s.get('symbol','N/A')}</code>\n"
        f"Trade Mode:   <code>{s.get('trade_mode','auto').title()}</code>\n"
        f"Trading ON:   <code>{'Yes' if s.get('trading_on') else 'No'}</code>\n\n"
        f"<b>Trade Stats</b>\n"
        f"  Total:    <code>{tr.get('total',0)}</code>\n"
        f"  Wins:     <code>{tr.get('wins',0)}</code>\n"
        f"  PnL:      <code>{'+'if tr.get('total_pnl',0)>=0 else ''}{tr.get('total_pnl',0):.4f} USDT</code>\n"
        f"  Referrals: <code>{profile['referrals']}</code>"
    )

    keyboard = [[
        InlineKeyboardButton("📩 Reply",          callback_data=f"admin_reply_{target_id}"),
        InlineKeyboardButton("🔑 Grant Lifetime", callback_data=f"admin_grant_{target_id}"),
    ]]
    await update.effective_message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


# ── /timezone ────────────────────────────────────────────────────────────────

@require_granted
async def timezone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Let users set their UTC offset for daily reports and alerts."""
    uid = update.effective_user.id
    if context.args:
        try:
            offset = int(context.args[0])
            if not -12 <= offset <= 14:
                raise ValueError()
            from database import set_user_timezone
            set_user_timezone(uid, offset)
            sign = "+" if offset >= 0 else ""
            await update.effective_message.reply_text(
                f"✅ Timezone set to <b>UTC{sign}{offset}</b>\n\n"
                f"Daily reports will be sent at <code>08:00 UTC{sign}{offset}</code>.",
                parse_mode=ParseMode.HTML
            )
            return
        except ValueError:
            pass

    # Show picker
    offsets = [
        ("UTC-12", -12), ("UTC-8", -8), ("UTC-5", -5), ("UTC-3", -3),
        ("UTC+0 (London)", 0), ("UTC+1 (Lagos/Accra)", 1), ("UTC+2", 2),
        ("UTC+3", 3), ("UTC+4", 4), ("UTC+5:30 → +5", 5),
        ("UTC+6", 6), ("UTC+7", 7), ("UTC+8", 8), ("UTC+9", 9),
        ("UTC+10", 10), ("UTC+12", 12),
    ]
    keyboard = []
    for i in range(0, len(offsets), 2):
        row = [InlineKeyboardButton(lbl, callback_data=f"tz_{off}") for lbl, off in offsets[i:i+2]]
        keyboard.append(row)

    await update.effective_message.reply_text(
        "🕐 <b>Set Your Timezone</b>\n\nChoose your UTC offset for daily reports:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

# ── /help ─────────────────────────────────────────────────────────────────────

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    user_commands = (
        "🤖 <b>CryptoTradeBot — Commands</b>\n\n"
        "<b>💼 Trading</b>\n"
        "  /dashboard     — Full overview in one screen\n"
        "  /balance       — View exchange balance\n"
        "  /start_trade   — Enable auto-trading\n"
        "  /stop_trade    — Disable auto-trading\n"
        "  /health        — Monitor open trades live\n"
        "  /history       — Last 10 closed trades\n"
        "  /pnl           — Profit & Loss summary\n"
        "  /summary       — Trade cycle summary\n"
        "  /chart         — Live price + signal + indicators\n\n"
        "<b>⚙️ Setup</b>\n"
        "  /settings      — Configure exchange, TP, SL, symbol, toggles\n"
        "  /exchanges     — List supported exchanges\n\n"
        "<b>🔔 Price Alerts</b>\n"
        "  /setalert SYMBOL above|below PRICE\n"
        "  /myalerts      — View active alerts\n"
        "  /delalert &lt;id&gt; — Delete an alert\n\n"
        "<b>💳 Subscription</b>\n"
        "  /subscribe     — Subscribe or start free trial\n"
        "  /mystatus      — View your subscription status\n"
        "  /referral      — Get your referral link (earn free months)\n\n"
        "<b>📩 Support</b>\n"
        "  /support &lt;message&gt; — Contact the support team\n\n"
        "<b>🚨 Emergency</b>\n"
        "  /panic         — Close all YOUR open trades immediately\n\n"
        "<b>📊 Analytics</b>\n"
        "  /positions     — Live open positions with unrealised PnL\n"
        "  /signals       — Last 10 signals with outcomes\n"
        "  /export        — Download trade history as CSV\n\n"
        "<b>⚡ Arbitrage</b>\n"
        "  /arbitrage     — Scan for cross-exchange &amp; triangular arbitrage\n"
        "                   Choose tokens, enable/disable, run on-demand scans\n\n"
        "<b>🧪 Paper Trading</b>\n"
        "  /paper         — Toggle paper trading on/off (simulated trades)\n"
        "  /paper_reset   — Reset paper balance and history\n"
        "  /paper_stats   — Paper trading performance summary\n\n"
        "<b>🔄 DCA Bot</b>\n"
        "  /dca           — Manage Dollar-Cost Averaging plans\n"
        "  /dca_stats &lt;id&gt; — Stats for a specific DCA plan\n\n"
        "<b>🔲 Grid Trading</b>\n"
        "  /grid          — Manage grid trading plans\n"
        "  /grid_status &lt;id&gt; — Grid order ladder and profit\n"
        "  /grid_stop &lt;id&gt;   — Stop a grid and cancel all orders\n\n"
        "<b>⏱ Smart Orders</b>\n"
        "  /twap          — Time-Weighted Average Price order\n"
        "  /iceberg       — Iceberg (hidden size) order\n"
        "  /oco           — One-Cancels-the-Other order\n"
        "  /smart_orders  — View all active smart orders\n\n"
        "<b>📊 Analytics</b>\n"
        "  /analytics [7d|30d|all] — Full performance report\n"
        "  /backtest &lt;SYMBOL&gt; &lt;DAYS&gt; — Backtest signal strategy\n"
        "  /webdash       — Get a 24h link to the web dashboard\n\n"
        "<b>📡 TradingView</b>\n"
        "  /webhook       — View your TradingView webhook URL\n"
        "  /webhook_new   — Regenerate webhook token\n"
        "  /webhook_log   — Last 10 webhook-triggered trades\n\n"
        "<b>🏪 Strategy Marketplace</b>\n"
        "  /market        — Browse published strategies\n"
        "  /market publish &lt;name&gt; — Publish your current strategy\n"
        "  /market subscribe &lt;id&gt; — Copy a strategy\n"
        "  /market leaderboard — Top strategies by 30d PnL\n\n"
        "<b>📋 Audit</b>\n"
        "  /audit         — View your recent activity log\n\n"
        "<b>🌐 Preferences</b>\n"
        "  /timezone      — Set your timezone for daily reports\n"
        "  /status        — Bot and platform status\n"
    )

    admin_commands = (
        "\n<b>🔐 Admin Commands</b>\n"
        "  /grant &lt;user_id&gt;              — Grant lifetime access\n"
        "  /user &lt;user_id&gt;               — Full user profile lookup\n"
        "  /reply &lt;user_id&gt; &lt;msg&gt;  — Reply to a user\n"
        "  /broadcast &lt;msg&gt;             — Message all subscribers\n"
        "  /subscribers                   — List all subscribers\n"
        "  /close                         — Emergency close ALL platform trades\n"
        "  /status                        — Platform stats and health\n"
    )

    text = user_commands + (admin_commands if is_admin(uid) else "")
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)



# ── Symbol Picker Helper ──────────────────────────────────────────────────────

PAGE_SIZE = 10  # symbols per page

async def _show_symbol_picker(message, uid: int, page: int = 1):
    """Show paginated popular symbols + search button."""
    start = (page - 1) * PAGE_SIZE
    page_syms = POPULAR_SYMBOLS[start:start + PAGE_SIZE]

    # Build 2-column grid
    rows = []
    for i in range(0, len(page_syms), 2):
        pair = page_syms[i:i+2]
        rows.append([InlineKeyboardButton(s, callback_data=f"sym_set_{s}") for s in pair])

    # Pagination row
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data="sym_page_1"))
    if start + PAGE_SIZE < len(POPULAR_SYMBOLS):
        nav.append(InlineKeyboardButton("Next ▶️", callback_data="sym_page_2"))
    if nav:
        rows.append(nav)

    # Search row always at bottom
    rows.append([InlineKeyboardButton("🔍 Search any coin...", callback_data="sym_search")])

    total_pages = (len(POPULAR_SYMBOLS) + PAGE_SIZE - 1) // PAGE_SIZE
    await message.reply_text(
        f"🪙 <b>Choose a Token to Trade</b>  (page {page}/{total_pages})\n\n"
        f"Select from popular pairs or search for any coin on your exchange:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )


# ── Shared trading activation helper ─────────────────────────────────────────

async def _activate_trading(context, uid: int, mode: str, usdt_balance: float):
    """Activate trading in the chosen mode and send a confirmation message."""
    s    = get_settings(uid)
    user = get_user(uid)
    # Guard: if no exchange connected, prompt setup
    if not user or not user.get("api_key"):
        await context.bot.send_message(
            chat_id=uid,
            text=(
                "⚙️ <b>Exchange Not Connected</b>\n\n"
                "You need to connect an exchange before trading.\n"
                "Go to /settings → 🔑 Connect Exchange."
            ),
            parse_mode="HTML"
        )
        return
    update_setting(uid, "trading_on", 1)
    update_setting(uid, "trade_mode", mode)

    tp_label = f"{s['take_profit']}{'%' if s.get('tp_mode','pct')=='pct' else ' USDT'}"
    sl_label = f"{s['stop_loss']}{'%' if s.get('sl_mode','pct')=='pct' else ' USDT'}"

    if mode == "auto":
        mode_desc = (
            "🤖 <b>Auto Trade Mode</b>\n"
            "The bot scans for signals every 60 seconds and trades automatically."
        )
        action_btns = []
    else:
        mode_desc = (
            "👆 <b>Manual Trade Mode</b>\n"
            "Tap <b>🟢 Start Now</b> below whenever you want to place a trade."
        )
        action_btns = [[InlineKeyboardButton("🟢 Start Now — Buy Now", callback_data="manual_buy_now")]]

    keyboard = action_btns + [
        [InlineKeyboardButton("⏹ Stop Trading", callback_data="cmd_stop_trade"),
         InlineKeyboardButton("📊 Chart",        callback_data="cmd_chart")],
        [InlineKeyboardButton("💊 Health",       callback_data="cmd_health"),
         InlineKeyboardButton("💰 Balance",      callback_data="cmd_balance")],
    ]

    await context.bot.send_message(
        chat_id=uid,
        text=(
            f"✅ <b>Trading ENABLED</b>\n\n"
            f"Symbol:        <code>{s['symbol']}</code>\n"
            f"Trade Amount:  <code>{s['trade_amount']:.2f} USDT</code>\n"
            f"USDT Balance:  <code>{usdt_balance:.8f}</code>\n"
            f"Take Profit:   <code>{tp_label}</code>\n"
            f"Stop Loss:     <code>{sl_label}</code>\n\n"
            f"{mode_desc}"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


# ── Callback & Message Handlers ───────────────────────────────────────────────

from utils import PENDING_INPUT  # shared across handlers and alerts_handlers


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data

    if data == "subscribe":
        # Show full payment modal: trial (if available) + Paystack + all crypto networks
        trial_used_cb  = has_used_trial(uid)
        active_nets_cb = {k: v for k, v in CRYPTO_NETWORKS.items() if v.get("address")}
        full_keyboard  = []

        if not trial_used_cb:
            full_keyboard += [[InlineKeyboardButton(
                f"🆓 {FREE_TRIAL_DAYS}-Day Free Trial  (No payment needed)",
                callback_data="free_trial"
            )]]

        full_keyboard += [
            [InlineKeyboardButton("── 💳 Pay via Paystack ──────────────", callback_data="noop")],
            [InlineKeyboardButton("1 Month  $12",         callback_data="pay_1"),
             InlineKeyboardButton("3 Months $34",         callback_data="pay_3")],
            [InlineKeyboardButton("6 Months $65 (best)",  callback_data="pay_6")],
        ]

        if active_nets_cb:
            full_keyboard += [[InlineKeyboardButton("── 🪙 Pay via Crypto (USDT) ─────────", callback_data="noop")]]
            for net_k, net_v in active_nets_cb.items():
                full_keyboard += [[InlineKeyboardButton(
                    f"🪙 {net_v['label']} — USDT",
                    callback_data=f"crypto_net_{net_k}"
                )]]

        trial_note_cb = f"\n🆓 <b>{FREE_TRIAL_DAYS}-day free trial available!</b>\n" if not trial_used_cb else ""
        crypto_note_cb = f"\n<b>🪙 Crypto:</b> {', '.join(v['label'] for v in active_nets_cb.values())}" if active_nets_cb else ""

        await query.message.reply_text(
            f"🤖 <b>CryptoTradeBot — Subscribe / Renew</b>\n"
            f"{trial_note_cb}\n"
            f"<b>Plans:</b> 1mo $12 · 3mo $34 · 6mo $65\n"
            f"<b>💳 Paystack</b> — Card, Mobile Money, Bank Transfer"
            f"{crypto_note_cb}\n\n"
            f"Choose a payment method:",
            reply_markup=InlineKeyboardMarkup(full_keyboard),
            parse_mode=ParseMode.HTML
        )

    elif data.startswith("pay_"):
        months = int(data.split("_")[1])
        amount = PLAN_PRICES.get(months, PLAN_PRICES[1])

        PENDING_INPUT[uid] = {"field": "pay_email", "months": months, "amount": amount}
        await query.message.reply_text(
            f"💳 <b>{months} Month{'s' if months > 1 else ''} Plan — ${amount:.2f}</b>\n\n"
            f"Please send your <b>email address</b> so we can generate your payment link.",
            parse_mode=ParseMode.HTML
        )

    # ── Paper trading callbacks ───────────────────────────────────────────────
    elif data == "paper_reset_confirm":
        update_setting(uid, "paper_balance",       1000.0)
        update_setting(uid, "paper_start_balance", 1000.0)
        with __import__("database").get_conn() as conn:
            conn.execute("DELETE FROM paper_trades WHERE user_id=?", (uid,))
        write_audit(uid, "paper_reset", {})
        await query.message.reply_text(
            "🧪 Paper account reset — balance restored to $1 000.00.",
        )
    elif data == "paper_reset_cancel":
        await query.message.reply_text("Cancelled.")

    # ── Webhook callbacks ─────────────────────────────────────────────────────
    elif data == "webhook_regen_confirm":
        token = generate_webhook_token(uid)
        write_audit(uid, "webhook_token_regenerated", {})
        await query.message.reply_text(
            f"✅ New webhook token generated.\nUse /webhook to see your updated URL.",
        )
    elif data == "webhook_regen_cancel":
        await query.message.reply_text("Cancelled — old token kept.")

    # ── DCA callbacks ─────────────────────────────────────────────────────────
    elif data == "dca_create":
        PENDING_INPUT[uid] = {"field": "dca_symbol"}
        await query.message.reply_text(
            "🔄 <b>New DCA Plan</b>\n\nSend the symbol (e.g. <code>BTC/USDT</code>):",
            parse_mode=ParseMode.HTML
        )
    elif data.startswith("dca_pause_"):
        plan_id = int(data.split("_")[-1])
        set_dca_status(plan_id, "paused")
        write_audit(uid, "dca_paused", {"plan_id": plan_id})
        await query.message.reply_text(f"⏸ DCA plan #{plan_id} paused.")
    elif data.startswith("dca_resume_"):
        plan_id = int(data.split("_")[-1])
        set_dca_status(plan_id, "active")
        write_audit(uid, "dca_resumed", {"plan_id": plan_id})
        await query.message.reply_text(f"▶️ DCA plan #{plan_id} resumed.")
    elif data.startswith("dca_delete_"):
        plan_id = int(data.split("_")[-1])
        set_dca_status(plan_id, "deleted")
        write_audit(uid, "dca_deleted", {"plan_id": plan_id})
        await query.message.reply_text(f"🗑 DCA plan #{plan_id} deleted.")
    elif data.startswith("dca_stats_"):
        plan_id = int(data.split("_")[-1])
        context.args = [str(plan_id)]
        await dca_stats_cmd(update, context)
    elif data.startswith("dca_int_"):
        interval_sec = int(data.split("_")[-1])
        pi = PENDING_INPUT.get(uid, {})
        if not pi or "symbol" not in pi or "amount" not in pi:
            await query.message.reply_text("Session expired. Start over with /dca.")
            return
        user = get_user(uid)
        if not user or not user.get("exchange"):
            await query.message.reply_text("Connect an exchange first with /exchanges.")
            del PENDING_INPUT[uid]
            return
        from config import MAX_DCA_PLANS
        existing = get_dca_plans(uid)
        active   = [p for p in existing if p["status"] == "active"]
        if len(active) >= MAX_DCA_PLANS:
            await query.message.reply_text(
                f"⚠️ You've reached the maximum of {MAX_DCA_PLANS} active DCA plans."
            )
            del PENDING_INPUT[uid]
            return
        plan_id = create_dca_plan(
            uid, user["exchange"], pi["symbol"], pi["amount"], interval_sec
        )
        del PENDING_INPUT[uid]
        write_audit(uid, "dca_created", {"plan_id": plan_id, "symbol": pi["symbol"]})
        hrs = interval_sec // 3600
        await query.message.reply_text(
            f"✅ <b>DCA Plan #{plan_id} Created</b>\n\n"
            f"  Symbol:   <code>{pi['symbol']}</code>\n"
            f"  Amount:   <code>${pi['amount']:.2f}</code> per buy\n"
            f"  Interval: <code>every {hrs}h</code>\n\n"
            f"First buy in {hrs}h. Use /dca to manage.",
            parse_mode=ParseMode.HTML
        )

    # ── Grid callbacks ────────────────────────────────────────────────────────
    elif data == "grid_create":
        PENDING_INPUT[uid] = {"field": "grid_symbol"}
        await query.message.reply_text(
            "🔲 <b>New Grid Plan</b>\n\nSend the symbol (e.g. <code>BTC/USDT</code>):",
            parse_mode=ParseMode.HTML
        )

    # ── Strategy marketplace callbacks ────────────────────────────────────────
    elif data.startswith("strat_sub_"):
        strat_id = int(data.split("_")[-1])
        strat    = get_strategy(strat_id)
        if not strat:
            await query.answer("Strategy not found.", show_alert=True)
            return
        keyboard = [[
            InlineKeyboardButton("✅ Confirm Subscribe", callback_data=f"strat_sub_confirm_{strat_id}"),
            InlineKeyboardButton("❌ Cancel",             callback_data="strat_sub_cancel"),
        ]]
        await query.message.reply_text(
            f"Subscribe to <b>{strat['name']}</b>?\n\n"
            f"This will update your: symbol, TP/SL, and trade mode.\n"
            f"You can revert with /market unsubscribe.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    elif data.startswith("strat_sub_confirm_"):
        import json as _j
        strat_id = int(data.split("_")[-1])
        strat    = get_strategy(strat_id)
        if not strat:
            await query.answer("Strategy not found.", show_alert=True)
            return
        curr = get_settings(uid)
        prev = _j.dumps({k: curr.get(k) for k in
                         ("symbol","take_profit","stop_loss","tp_mode","sl_mode","trade_mode")})
        for k, v in [("symbol", strat["symbol"]), ("take_profit", strat["take_profit"]),
                     ("stop_loss", strat["stop_loss"]), ("tp_mode", strat["tp_mode"]),
                     ("sl_mode", strat["sl_mode"]), ("trade_mode", strat["trade_mode"])]:
            if v is not None:
                update_setting(uid, k, v)
        subscribe_strategy(uid, strat_id, prev)
        write_audit(uid, "strategy_subscribed", {"strategy_id": strat_id})
        await query.message.reply_text(
            f"✅ Subscribed to <b>{strat['name']}</b>. Settings updated.",
            parse_mode=ParseMode.HTML
        )
    elif data == "strat_sub_cancel":
        await query.message.reply_text("Cancelled.")
    elif data == "strat_leaderboard":
        context.args = ["leaderboard"]
        await market_cmd(update, context)

    # ── Direct command callbacks (inline dashboard buttons) ──────────────────
    elif data in BUTTON_MAP:
        await BUTTON_MAP[data](update, context)

    # Settings flows
    elif data == "toggle_tp_mode":
        s      = get_settings(uid)
        cur    = s.get("tp_mode", "pct")
        new    = "price" if cur == "pct" else "pct"
        update_setting(uid, "tp_mode", new)
        label  = "Percentage %" if new == "pct" else "Fixed Price $"
        hint   = "Enter a % value (e.g. 2.5)" if new == "pct" else "Enter a price value in USDT (e.g. 68500)"
        await query.message.reply_text(
            f"🔁 <b>Take Profit mode → {label}</b>\n\n"
            f"ℹ️ {hint}\n\n"
            f"Update your Take Profit value via ⚙️ Settings → 🎯 Take Profit.",
            parse_mode=ParseMode.HTML
        )

    elif data == "toggle_sl_mode":
        s      = get_settings(uid)
        cur    = s.get("sl_mode", "pct")
        new    = "price" if cur == "pct" else "pct"
        update_setting(uid, "sl_mode", new)
        label  = "Percentage %" if new == "pct" else "Fixed Price $"
        hint   = "Enter a % value (e.g. 1.0)" if new == "pct" else "Enter a price value in USDT (e.g. 65000)"
        await query.message.reply_text(
            f"🔁 <b>Stop Loss mode → {label}</b>\n\n"
            f"ℹ️ {hint}\n\n"
            f"Update your Stop Loss value via ⚙️ Settings → 🛑 Stop Loss.",
            parse_mode=ParseMode.HTML
        )

    elif data == "toggle_confirm":
        s   = get_settings(uid)
        new = 0 if s.get("confirm_trades", 0) else 1
        update_setting(uid, "confirm_trades", new)
        status = "ON ✅ — I will ask you to approve each trade before it executes." if new else "OFF ⬜ — Trades execute automatically."
        await query.message.reply_text(
            f"{'✅' if new else '⬜'} <b>Trade Confirmation: {status}</b>",
            parse_mode=ParseMode.HTML
        )

    elif data == "toggle_trailing":
        s   = get_settings(uid)
        new = 0 if s.get("trailing_stop", 0) else 1
        update_setting(uid, "trailing_stop", new)
        status = "ON ✅ — Stop loss moves up as profit grows, locking in gains." if new else "OFF ⬜ — Fixed stop loss."
        await query.message.reply_text(
            f"{'✅' if new else '⬜'} <b>Trailing Stop: {status}</b>",
            parse_mode=ParseMode.HTML
        )

    elif data == "toggle_suggestions":
        s   = get_settings(uid)
        new = 0 if s.get("signal_suggestions", 1) else 1
        update_setting(uid, "signal_suggestions", new)
        status = "ON ✅ — You'll receive suggestions for high-confidence signals." if new else "OFF ⬜ — No signal suggestions."
        await query.message.reply_text(
            f"{'✅' if new else '⬜'} <b>Signal Alerts: {status}</b>",
            parse_mode=ParseMode.HTML
        )

    elif data == "toggle_arb_alerts":
        s   = get_settings(uid)
        new = 0 if s.get("arb_alerts", 1) else 1
        update_setting(uid, "arb_alerts", new)
        status = "ON ✅ — You'll be notified when profitable arbitrage opportunities arise." if new else "OFF ⬜ — No arbitrage alerts."
        await query.message.reply_text(
            f"{'✅' if new else '⬜'} <b>Arb Alerts: {status}</b>",
            parse_mode=ParseMode.HTML
        )

    elif data == "arb_toggle_enabled":
        s   = get_settings(uid)
        new = 0 if s.get("arb_enabled", 1) else 1
        update_setting(uid, "arb_enabled", new)
        icon   = "🟢" if new else "🔴"
        status = "ENABLED — background scanning and /arbitrage are now active." if new else "DISABLED — no arb scans will run until you re-enable."
        await query.message.reply_text(
            f"{icon} <b>Arbitrage {status}</b>",
            parse_mode=ParseMode.HTML
        )

    elif data == "arb_scan_now":
        if not is_admin(uid) and not has_active_access(uid):
            await query.answer("🔒 Subscription required.", show_alert=True)
            return
        wait_msg = await query.message.reply_text("🔍 Scanning for arbitrage opportunities…")
        await _arb_run_scan(update, context, edit_msg=wait_msg)

    elif data == "arb_sym_picker":
        if not is_admin(uid) and not has_active_access(uid):
            await query.answer("🔒 Subscription required.", show_alert=True)
            return
        chosen = _get_arb_symbols(uid)
        selected = set(chosen) if chosen else set(ARB_SYMBOL_OPTIONS)
        kb = _build_arb_symbol_keyboard(selected)
        await query.message.reply_text(
            "🪙 <b>Choose tokens to scan for arbitrage</b>\n\n"
            "Tap a token to toggle it on/off.\n"
            "Tap <b>Save &amp; Scan</b> when ready.\n\n"
            "<i>Selecting fewer tokens makes scans faster.</i>",
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )

    elif data.startswith("arb_sym_"):
        action = data[8:]   # e.g. "BTC/USDT", "all", "none", "save"

        # Retrieve current in-progress selection from persisted bot_data
        current_raw = get_arb_sel(context.bot_data, uid)
        if current_raw is None:
            existing   = _get_arb_symbols(uid)
            current_sel = set(existing) if existing else set(ARB_SYMBOL_OPTIONS)
        else:
            current_sel = set(current_raw)

        if action == "all":
            current_sel = set(ARB_SYMBOL_OPTIONS)
            set_arb_sel(context.bot_data, uid, list(current_sel))
            await query.edit_message_reply_markup(reply_markup=_build_arb_symbol_keyboard(current_sel))

        elif action == "none":
            current_sel = set()
            set_arb_sel(context.bot_data, uid, list(current_sel))
            await query.edit_message_reply_markup(reply_markup=_build_arb_symbol_keyboard(current_sel))

        elif action == "save":
            syms = sorted(current_sel) if current_sel else None
            update_setting(uid, "arb_symbols", __import__("json").dumps(syms) if syms else None)
            set_arb_sel(context.bot_data, uid, None)   # clear draft
            sym_display = ", ".join(syms) if syms else "all defaults"
            wait_msg = await query.message.reply_text(
                f"✅ <b>Tokens saved:</b> <code>{sym_display}</code>\n\n"
                "🔍 Running scan now…",
                parse_mode=ParseMode.HTML,
            )
            await _arb_run_scan(update, context, edit_msg=wait_msg)

        elif "/" in action:   # symbol toggle e.g. "BTC/USDT"
            if action in current_sel:
                current_sel.discard(action)
            else:
                current_sel.add(action)
            set_arb_sel(context.bot_data, uid, list(current_sel))
            try:
                await query.edit_message_reply_markup(
                    reply_markup=_build_arb_symbol_keyboard(current_sel)
                )
            except Exception:
                pass   # Telegram rejects edit when markup is identical

    elif data == "toggle_trade_mode":
        s   = get_settings(uid)
        cur = s.get("trade_mode", "auto")
        new = "manual" if cur == "auto" else "auto"
        update_setting(uid, "trade_mode", new)
        if new == "auto":
            desc = (
                "🤖 <b>Switched to Auto Trade Mode</b>\n\n"
                "The bot will scan for signals every 60 seconds and "
                "place trades automatically when confidence is high.\n\n"
                "Tap ▶️ Start Trade to activate."
            )
        else:
            desc = (
                "👆 <b>Switched to Manual Trade Mode</b>\n\n"
                "You decide when to buy. After starting, tap "
                "<b>🟢 Start Now</b> to place a trade instantly.\n"
                "TP/SL still trigger automatically to close the trade.\n\n"
                "Tap ▶️ Start Trade to activate."
            )
        keyboard = [[InlineKeyboardButton("▶️ Start Trade", callback_data="cmd_start_trade")]]
        await query.message.reply_text(
            desc,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    elif data == "set_tp":
        s    = get_settings(uid)
        mode = s.get("tp_mode", "pct")
        PENDING_INPUT[uid] = {"field": "take_profit"}
        if mode == "pct":
            await query.message.reply_text(
                "🎯 <b>Take Profit — Percentage Mode</b>\n\n"
                "Enter the % gain to close at.\n"
                "Example: <code>2.5</code> closes when up 2.5%\n\n"
                "To switch to fixed price mode, use ⚙️ Settings → 🔁 TP Mode.",
                parse_mode=ParseMode.HTML
            )
        else:
            await query.message.reply_text(
                "🎯 <b>Take Profit — Fixed Price Mode</b>\n\n"
                "Enter the exact price (in USDT) to close at.\n"
                "Example: <code>68500</code> closes when BTC hits $68,500\n\n"
                "To switch to percentage mode, use ⚙️ Settings → 🔁 TP Mode.",
                parse_mode=ParseMode.HTML
            )

    elif data == "set_sl":
        s    = get_settings(uid)
        mode = s.get("sl_mode", "pct")
        PENDING_INPUT[uid] = {"field": "stop_loss"}
        if mode == "pct":
            await query.message.reply_text(
                "🛑 <b>Stop Loss — Percentage Mode</b>\n\n"
                "Enter the % drop to exit at.\n"
                "Example: <code>1.0</code> exits when down 1%\n\n"
                "To switch to fixed price mode, use ⚙️ Settings → 🔁 SL Mode.",
                parse_mode=ParseMode.HTML
            )
        else:
            await query.message.reply_text(
                "🛑 <b>Stop Loss — Fixed Price Mode</b>\n\n"
                "Enter the exact price (in USDT) to exit at.\n"
                "Example: <code>62000</code> exits when BTC drops to $62,000\n\n"
                "To switch to percentage mode, use ⚙️ Settings → 🔁 SL Mode.",
                parse_mode=ParseMode.HTML
            )
    elif data == "set_amount":
        PENDING_INPUT[uid] = {"field": "trade_amount"}
        await query.message.reply_text("💵 Enter trade amount in USDT (e.g. <code>20</code>):", parse_mode=ParseMode.HTML)
    elif data == "set_symbol":
        await _show_symbol_picker(query.message, uid)
    elif data == "panic_cancel":
        await query.message.edit_text(
            "✅ <b>Panic cancelled.</b> Your trades remain open.",
            parse_mode=ParseMode.HTML
        )

    elif data == "panic_confirm_user":
        # User closing their own trades
        await query.message.edit_text(
            "⏳ Closing your open trades at market price...",
            parse_mode=ParseMode.HTML
        )
        closed, errors = await _execute_user_panic(context, uid)

        if errors:
            err_text = "\n".join(f"  ⚠️ {e}" for e in errors[:5])
            result_text = (
                f"🚨 <b>Panic Complete</b>\n\n"
                f"Closed: <code>{closed}</code> trade(s)\n"
                f"Auto-trading: <b>STOPPED</b>\n\n"
                f"Some errors occurred:\n{err_text}"
            )
        else:
            result_text = (
                f"✅ <b>Panic Complete</b>\n\n"
                f"Closed: <code>{closed}</code> trade(s)\n"
                f"Auto-trading: <b>STOPPED</b>\n\n"
                f"All your positions have been closed at market price."
            )
        await query.message.edit_text(result_text, parse_mode=ParseMode.HTML)

    elif data == "panic_confirm_admin":
        # Admin closing all trades platform-wide
        if not is_admin(uid):
            await query.answer("Not authorised.", show_alert=True)
            return

        await query.message.edit_text(
            "🚨 <b>ADMIN PANIC IN PROGRESS...</b>\n\nClosing all trades platform-wide...",
            parse_mode=ParseMode.HTML
        )

        all_users    = get_all_trading_users()
        total_closed = 0
        total_errors = 0

        for platform_user in all_users:
            user_id = platform_user["user_id"]
            closed, errors = await _execute_user_panic(context, user_id)
            total_closed += closed
            total_errors += len(errors)
            # Notify each affected user
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "🚨 <b>ADMIN PANIC ACTIVATED</b>\n\n"
                        f"All your trades have been closed by an admin.\n"
                        f"Auto-trading has been stopped.\n\n"
                        f"Trades closed: <code>{closed}</code>"
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

        summary_text = (
            f"✅ <b>Admin Panic Complete</b>\n\n"
            f"Users processed: <code>{len(all_users)}</code>\n"
            f"Trades closed:   <code>{total_closed}</code>\n"
            f"Errors:          <code>{total_errors}</code>\n\n"
            f"All auto-trading has been disabled."
        )
        await query.message.edit_text(summary_text, parse_mode=ParseMode.HTML)

    elif data.startswith("confirm_trade_"):
        # confirm_trade_<id>_approve  or  confirm_trade_<id>_skip
        parts = data.split("_")
        try:
            confirm_id = int(parts[2])
            decision   = parts[3]  # "approve" or "skip"
        except (IndexError, ValueError):
            return
        from scheduler import handle_trade_confirmation_callback
        await handle_trade_confirmation_callback(context, uid, confirm_id, decision)

    elif data == "trade_mode_auto":
        user = get_user(uid)
        try:
            exch = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
            bal  = fetch_usdt_balance(exch)
        except Exception:
            bal  = 0.0
        await _activate_trading(context, uid, "auto", bal)

    elif data == "trade_mode_manual":
        user = get_user(uid)
        try:
            exch = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
            bal  = fetch_usdt_balance(exch)
        except Exception:
            bal  = 0.0
        await _activate_trading(context, uid, "manual", bal)

    elif data == "manual_buy_now":
        # User tapped "Start Now" in manual mode — fire a buy immediately
        user = get_user(uid)
        s    = get_settings(uid)
        if not s or not s.get("trading_on"):
            await query.message.reply_text(
                "⚠️ Trading is not active. Use /start_trade first.",
                parse_mode=ParseMode.HTML
            )
            return
        await query.message.reply_text("⏳ Fetching chart and executing buy...")
        try:
            exch          = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
            ticker        = fetch_ticker(exch, s["symbol"])
            current_price = ticker["last"]
            usdt_bal      = fetch_usdt_balance(exch)
            trade_amount  = s["trade_amount"]

            if trade_amount > usdt_bal:
                await query.message.reply_text(
                    f"⚠️ <b>Insufficient funds</b>\n\n"
                    f"Need: <code>{trade_amount:.2f} USDT</code>\n"
                    f"Have: <code>{usdt_bal:.8f} USDT</code>",
                    parse_mode=ParseMode.HTML
                )
                return

            from exchange import place_market_order
            from database import open_trade as db_open_trade
            from logger_setup import log_trade_open
            order    = place_market_order(exch, s["symbol"], "buy", trade_amount)
            order_id = str(order.get("id", "manual"))
            db_open_trade(uid, s["symbol"], "buy", current_price, trade_amount,
                          user["exchange"], order_id, "Manual buy")
            log_trade_open(uid, s["symbol"], "buy", current_price, trade_amount,
                           user["exchange"], order_id)

            tp_label = f"{s['take_profit']}{'%' if s.get('tp_mode','pct')=='pct' else ' USDT'}"
            sl_label = f"{s['stop_loss']}{'%' if s.get('sl_mode','pct')=='pct' else ' USDT'}"

            keyboard = [
                [InlineKeyboardButton("🟢 Buy Again",    callback_data="manual_buy_now"),
                 InlineKeyboardButton("💊 Health",       callback_data="cmd_health")],
                [InlineKeyboardButton("⏹ Stop Trading", callback_data="cmd_stop_trade"),
                 InlineKeyboardButton("📊 Chart",        callback_data="cmd_chart")],
            ]
            await query.message.reply_text(
                f"🚀 <b>Manual Buy Executed!</b>\n\n"
                f"Symbol: <code>{s['symbol']}</code>\n"
                f"Price:  <code>${current_price:,.6f}</code>\n"
                f"Amount: <code>{trade_amount:.2f} USDT</code>\n"
                f"TP: <code>{tp_label}</code>  |  SL: <code>{sl_label}</code>\n\n"
                f"TP/SL will trigger automatically. Tap <b>Buy Again</b> for another entry.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            await query.message.reply_text(
                f"❌ <b>Buy failed</b>\n\n<code>{str(e)[:200]}</code>",
                parse_mode=ParseMode.HTML
            )

    elif data.startswith("tz_"):
        try:
            offset = int(data[3:])
            from database import set_user_timezone
            set_user_timezone(uid, offset)
            sign = "+" if offset >= 0 else ""
            await query.message.reply_text(
                f"✅ Timezone set to <b>UTC{sign}{offset}</b>\n"
                f"Daily reports at <code>08:00 UTC{sign}{offset}</code>.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            await query.message.reply_text(f"❌ Failed to set timezone: <code>{e}</code>", parse_mode=ParseMode.HTML)

    elif data.startswith("admin_reply_"):
        target_id = int(data[len("admin_reply_"):])
        PENDING_INPUT[uid] = {"field": "admin_reply_msg", "target_id": target_id}
        await query.message.reply_text(
            f"📩 Type your reply message for user <code>{target_id}</code>:",
            parse_mode=ParseMode.HTML
        )

    elif data.startswith("admin_grant_"):
        if not is_admin(uid):
            await query.answer("Not authorised.", show_alert=True)
            return
        target_id = int(data[len("admin_grant_"):])
        upsert_user(target_id)
        grant_user(target_id)
        await query.message.reply_text(
            f"✅ Lifetime access granted to <code>{target_id}</code>.",
            parse_mode=ParseMode.HTML
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="🎉 <b>Lifetime Access Granted!</b>\n\nAn admin has given you lifetime access to CryptoTradeBot.",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

    elif data == "ob_start":
        from onboarding import start_onboarding
        tg_user = query.from_user
        await start_onboarding(context, uid, tg_user.first_name or "")

    elif data.startswith("ob_exch_"):
        # Onboarding: exchange selected
        exch_id = data[8:]
        from onboarding import onboard_step_symbol
        PENDING_INPUT[uid] = {"field": "api_key", "exchange": exch_id, "onboarding": True}
        passphrase_note = " (also needs a passphrase)" if exch_id in PASSPHRASE_EXCHANGES else ""
        await _send_expiring(
            context, query.message.chat_id,
            f"🔑 <b>{EXCHANGE_LABELS.get(exch_id, exch_id)}</b>{passphrase_note}\n\n"
            f"Please send your <b>API Key</b>:",
        )

    elif data.startswith("ob_sym_"):
        # Onboarding: symbol selected
        sym = data[7:]
        if sym == "search":
            PENDING_INPUT[uid] = {"field": "symbol_search", "onboarding": True}
            await query.message.reply_text(
                "🔍 Type the coin ticker (e.g. <code>BTC/USDT</code>):",
                parse_mode=ParseMode.HTML
            )
        else:
            update_setting(uid, "symbol", sym)
            from onboarding import onboard_step_tpsl
            await onboard_step_tpsl(context, uid)

    elif data.startswith("ob_tpsl_"):
        # Onboarding: risk profile selected
        preset = data[8:]
        presets = {
            "conservative": (1.5, 0.8),
            "balanced":     (2.0, 1.0),
            "aggressive":   (3.0, 1.5),
        }
        if preset in presets:
            tp, sl = presets[preset]
            update_setting(uid, "take_profit", tp)
            update_setting(uid, "stop_loss",   sl)
            from onboarding import onboard_step_trade_mode
            await onboard_step_trade_mode(context, uid)
        else:
            PENDING_INPUT[uid] = {"field": "take_profit", "onboarding": True}
            await query.message.reply_text(
                "🎯 Enter your Take Profit % (e.g. <code>2.0</code>):",
                parse_mode=ParseMode.HTML
            )

    elif data.startswith("ob_mode_"):
        # Onboarding: trade mode selected
        mode = data[8:]
        update_setting(uid, "trade_mode", mode)
        from onboarding import onboard_done
        await onboard_done(context, uid)

    elif data.startswith("cmd_positions"):
        from handlers import positions as _pos
        await _pos(update, context)

    elif data == "noop":
        pass  # separator buttons — do nothing

    elif data == "free_trial":
        if has_used_trial(uid):
            await query.message.reply_text(
                "❌ <b>Trial Already Used</b>\n\n"
                "Your free trial has already been activated on this account.\n"
                "Please subscribe to continue using CryptoTradeBot.",
                parse_mode=ParseMode.HTML
            )
        else:
            expiry = activate_trial(uid)
            trial_kb = [[
                InlineKeyboardButton("▶️ Start Setup", callback_data="ob_start"),
                InlineKeyboardButton("⚙️ Settings",    callback_data="cmd_settings"),
            ]]
            await query.message.reply_text(
                f"🎉 <b>Free Trial Activated!</b>\n\n"
                f"You have <b>{FREE_TRIAL_DAYS} days</b> of full access.\n"
                f"Trial expires: <code>{expiry}</code>\n\n"
                f"Let\'s get you set up right away! 🚀",
                reply_markup=InlineKeyboardMarkup(trial_kb),
                parse_mode=ParseMode.HTML
            )
            # Start onboarding flow for new trial users
            from onboarding import start_onboarding
            tg_user = query.from_user
            await start_onboarding(context, uid, tg_user.first_name or "")

    elif data == "crypto_sub":
        # Legacy: show network picker
        active_nets = {k: v for k, v in CRYPTO_NETWORKS.items() if v.get("address")}
        if not active_nets:
            await query.message.reply_text(
                "⚠️ Crypto payment is not configured yet. Please use Paystack instead.",
                parse_mode=ParseMode.HTML
            )
            return
        keyboard = [[InlineKeyboardButton(
            f"🪙 {v['label']} — USDT", callback_data=f"crypto_net_{k}"
        )] for k, v in active_nets.items()]
        await query.message.reply_text(
            "🪙 <b>Choose your preferred network</b>\n\nAll networks accept USDT:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    elif data.startswith("crypto_net_"):
        # User picked a network — now show plan picker
        net_key  = data[len("crypto_net_"):]
        net_info = CRYPTO_NETWORKS.get(net_key)
        if not net_info or not net_info.get("address"):
            await query.message.reply_text(
                f"⚠️ {net_key.upper()} payment address is not configured. Choose another network.",
                parse_mode=ParseMode.HTML
            )
            return
        keyboard = [
            [InlineKeyboardButton(f"1 Month  — 12 USDT",        callback_data=f"crypto_plan_{net_key}_1")],
            [InlineKeyboardButton(f"3 Months — 34 USDT (save)", callback_data=f"crypto_plan_{net_key}_3")],
            [InlineKeyboardButton(f"6 Months — 65 USDT (best)", callback_data=f"crypto_plan_{net_key}_6")],
        ]
        await query.message.reply_text(
            f"🪙 <b>Pay via {net_info['label']}</b>\n\nChoose your plan:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    elif data.startswith("crypto_plan_"):
        # crypto_plan_<network>_<months>
        parts = data.split("_")
        # parts = ["crypto", "plan", net_key, months]
        try:
            months  = int(parts[-1])
            net_key = "_".join(parts[2:-1])
        except (IndexError, ValueError):
            return
        net_info = CRYPTO_NETWORKS.get(net_key)
        if not net_info or not net_info.get("address"):
            await query.message.reply_text("⚠️ Network not configured. Please choose another.", parse_mode=ParseMode.HTML)
            return
        amount  = PLAN_PRICES_USDT.get(months, 12.00)
        address = net_info["address"]
        token   = net_info["token"]
        note    = net_info["note"]
        PENDING_INPUT[uid] = {
            "field":   "tx_hash",
            "months":  months,
            "amount":  amount,
            "network": net_key,
        }
        await _send_expiring(
            context, query.message.chat_id,
            f"🪙 <b>Pay with {token} on {net_info['label']}</b>\n\n"
            f"Plan:    <b>{months} Month{'s' if months > 1 else ''}</b>\n"
            f"Amount:  <code>{amount:.2f} USDT</code>\n\n"
            f"Send exactly <b>{amount:.2f} USDT</b> to:\n\n"
            f"<code>{address}</code>\n\n"
            f"⚠️ <b>Important:</b> {note}\n\n"
            f"After sending, paste your <b>transaction hash (TX ID)</b> here:",
        )

    # Legacy single-plan crypto_N callbacks (kept for backward compat)
    elif data.startswith("crypto_") and len(data.split("_")) == 2:
        try:
            months = int(data.split("_")[1])
        except (IndexError, ValueError):
            return
        active_nets = {k: v for k, v in CRYPTO_NETWORKS.items() if v.get("address")}
        if not active_nets:
            await query.message.reply_text("⚠️ Crypto payment not configured.", parse_mode=ParseMode.HTML)
            return
        keyboard = [[InlineKeyboardButton(
            f"🪙 {v['label']}", callback_data=f"crypto_plan_{k}_{months}"
        )] for k, v in active_nets.items()]
        await query.message.reply_text(
            f"🪙 Choose network for your {months}-month plan:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    elif data == "sym_search":
        PENDING_INPUT[uid] = {"field": "symbol_search"}
        await query.message.reply_text(
            "🔍 <b>Search any token</b>\n\n"
            "Type the coin ticker (e.g. <code>PEPE</code>, <code>WLD</code>, <code>INJ</code>).\n"
            "I will validate it against your exchange in real time.",
            parse_mode=ParseMode.HTML
        )
    elif data == "sym_page_1":
        await _show_symbol_picker(query.message, uid, page=1)
    elif data == "sym_page_2":
        await _show_symbol_picker(query.message, uid, page=2)
    elif data.startswith("sym_set_"):
        sym = data[8:]
        update_setting(uid, "symbol", sym)
        await query.message.reply_text(
            f"✅ Symbol set to <b>{sym}</b>\n\nUse /start_trade to begin trading it.",
            parse_mode=ParseMode.HTML
        )

    elif data == "set_exchange":
        stored  = get_stored_exchanges(uid)
        buttons = []
        # Only show real exchanges — exclude the "" placeholder
        for key, label in EXCHANGE_LABELS.items():
            if not key:  # skip "Not Set" placeholder
                continue
            tag = " ✅" if key in stored else ""
            buttons.append([InlineKeyboardButton(f"{label}{tag}", callback_data=f"exch_{key}")])
        note = "✅ = credentials already saved" if stored else ""
        await query.message.reply_text(
            f"🏦 <b>Choose your exchange</b>\n"
            f"<i>{note}</i>",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.HTML
        )

    elif data.startswith("exch_switch_"):
        exch_id = data[12:]
        from database import get_exchange_creds as _get_creds
        creds = _get_creds(uid, exch_id)
        if not creds:
            await query.message.reply_text(
                "❌ No saved keys found for this exchange. Please enter new API keys.",
                parse_mode=ParseMode.HTML
            )
            return
        switch_exchange(uid, exch_id)
        await query.message.reply_text(
            f"✅ Switched to <b>{EXCHANGE_LABELS.get(exch_id, exch_id)}</b> using saved keys.\n\n"
            f"💡 Use /balance to confirm the connection is working.",
            parse_mode=ParseMode.HTML
        )

    elif data.startswith("exch_new_"):
        exch_id = data[9:]
        PENDING_INPUT[uid] = {"field": "api_key", "exchange": exch_id}
        passphrase_note = " (also requires a passphrase)" if exch_id in PASSPHRASE_EXCHANGES else ""
        extra_note = get_exchange_note(exch_id)
        note_block = f"\n\nℹ️ {extra_note}" if extra_note else ""
        await _send_expiring(
            context, query.message.chat_id,
            f"🔑 <b>{EXCHANGE_LABELS.get(exch_id, exch_id)}</b>{passphrase_note}{note_block}\n\n"
            f"Please send your <b>API Key</b>:",
        )

    elif data.startswith("exch_"):
        exch_id = data[5:]
        stored  = get_stored_exchanges(uid)

        if exch_id in stored:
            buttons = [
                [InlineKeyboardButton("🔄 Use saved keys", callback_data=f"exch_switch_{exch_id}")],
                [InlineKeyboardButton("🔑 Enter new API keys", callback_data=f"exch_new_{exch_id}")],
            ]
            await query.message.reply_text(
                f"🏦 <b>{EXCHANGE_LABELS.get(exch_id, exch_id)}</b>\n\n"
                f"You already have saved keys for this exchange.\n"
                f"Would you like to use them or enter new ones?",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode=ParseMode.HTML
            )
        else:
            PENDING_INPUT[uid] = {"field": "api_key", "exchange": exch_id}
            passphrase_note = " (also requires a passphrase)" if exch_id in PASSPHRASE_EXCHANGES else ""
            extra_note = get_exchange_note(exch_id)
            note_block = f"\n\nℹ️ {extra_note}" if extra_note else ""
            await _send_expiring(
                context, query.message.chat_id,
                f"🔑 <b>{EXCHANGE_LABELS.get(exch_id, exch_id)}</b>{passphrase_note}{note_block}\n\n"
                f"Please send your <b>API Key</b>:",
            )


# Reply keyboard button label → callback_data mapping
REPLY_BUTTON_COMMANDS = {
    "📊 Dashboard":    "cmd_dashboard",
    "💰 Balance":      "cmd_balance",
    "🔗 Referral":     "cmd_referral",
    "📊 Chart":        "cmd_chart",
    "▶️ Start Trade":  "cmd_start_trade",
    "⏹ Stop Trade":   "cmd_stop_trade",
    "📜 History":      "cmd_history",
    "📈 PnL":          "cmd_pnl",
    "💊 Health":       "cmd_health",
    "📋 Summary":      "cmd_summary",
    "⚙️ Settings":     "cmd_settings",
    "🏦 Exchanges":    "cmd_exchanges",
    "💳 Subscribe":    "cmd_subscribe",
    "🪪 My Status":    "cmd_mystatus",
    "📩 Support":      "cmd_support",
    "❓ Help":          "cmd_help",
    "🔔 Set Alert":    "cmd_setalert",
    "🔕 My Alerts":    "cmd_myalerts",
    "👥 Subscribers":  "cmd_subscribers",
    "🚨 Panic":        "cmd_panic",
    "📴 Close All":   "cmd_close",
    "📂 Positions":   "cmd_positions",
    "📡 Signals":     "cmd_signals",
    "👥 Subscribers":  "cmd_subscribers",
}


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = (update.message.text if update.message else "").strip()

    # ── Reply keyboard button pressed → route directly to handler ────────────
    if text in REPLY_BUTTON_COMMANDS:
        cb_key = REPLY_BUTTON_COMMANDS[text]
        if cb_key in BUTTON_MAP:
            await BUTTON_MAP[cb_key](update, context)
        return

    pi = PENDING_INPUT.get(uid)
    if not pi:
        return

    field = pi["field"]

    if field == "take_profit":
        try:
            val  = float(text.replace(",", "").strip())
            s    = get_settings(uid)
            mode = s.get("tp_mode", "pct")
            if mode == "pct":
                if val <= 0 or val > 100:
                    await update.effective_message.reply_text(
                        "❌ Percentage must be between 0 and 100. Try again:"
                    )
                    return
                label = f"{val}%"
            else:
                if val <= 0:
                    await update.effective_message.reply_text("❌ Price must be greater than 0. Try again:")
                    return
                label = f"${val:,.4f} (fixed price)"
            update_setting(uid, "take_profit", val)
            del PENDING_INPUT[uid]
            await update.effective_message.reply_text(
                f"✅ <b>Take Profit set to <code>{label}</code></b>\n\n"
                f"The bot will close the trade when the position reaches this target.",
                parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.effective_message.reply_text("❌ Please enter a valid number.")

    elif field == "stop_loss":
        try:
            val  = float(text.replace(",", "").strip())
            s    = get_settings(uid)
            mode = s.get("sl_mode", "pct")
            if mode == "pct":
                if val <= 0 or val > 100:
                    await update.effective_message.reply_text(
                        "❌ Percentage must be between 0 and 100. Try again:"
                    )
                    return
                label = f"{val}%"
            else:
                if val <= 0:
                    await update.effective_message.reply_text("❌ Price must be greater than 0. Try again:")
                    return
                label = f"${val:,.4f} (fixed price)"
            update_setting(uid, "stop_loss", val)
            del PENDING_INPUT[uid]
            await update.effective_message.reply_text(
                f"✅ <b>Stop Loss set to <code>{label}</code></b>\n\n"
                f"The bot will exit the trade to limit losses at this level.",
                parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.effective_message.reply_text("❌ Please enter a valid number.")

    elif field == "trade_amount":
        try:
            val = float(text)
            update_setting(uid, "trade_amount", val)
            await update.effective_message.reply_text(f"✅ Trade Amount set to <code>{val} USDT</code>", parse_mode=ParseMode.HTML)
            del PENDING_INPUT[uid]
        except ValueError:
            await update.effective_message.reply_text("❌ Please enter a valid number.")

    elif field == "tx_hash":
        tx_hash  = text.strip()
        months   = pi.get("months", 1)
        amount   = pi.get("amount", 12.00)
        network  = pi.get("network", "aptos")
        del PENDING_INPUT[uid]

        net_info = CRYPTO_NETWORKS.get(network, {})
        net_label = net_info.get("label", network.upper())
        address   = net_info.get("address", "")
        api_key   = TRONGRID_API_KEY if network == "tron" else (BSCSCAN_API_KEY if network == "bsc" else "")

        msg = await update.effective_message.reply_text(
            f"⏳ Verifying your transaction on the {net_label} network...",
            parse_mode=ParseMode.HTML
        )
        result = verify_usdt_tx(network, tx_hash, amount, months, address, api_key)

        if result["valid"]:
            record_crypto_payment(uid, tx_hash, result["amount"], months, result.get("from_address", ""))
            expiry = confirm_crypto_payment(tx_hash)
            await msg.edit_text(
                f"✅ <b>Payment Confirmed!</b>\n\n"
                f"Amount:   <code>{result['amount']:.2f} USDT</code>\n"
                f"Plan:     <code>{months} month{'s' if months > 1 else ''}</code>\n"
                f"Expires:  <code>{expiry}</code>\n"
                f"TX Hash:  <code>{tx_hash[:20]}...{tx_hash[-10:]}</code>\n\n"
                f"🎉 Your subscription is now active! Use /start to get started.",
                parse_mode=ParseMode.HTML
            )
            # Notify admins
            for admin_id in ADMIN_IDS:
                try:
                    user = get_user(uid)
                    uname = f"@{user['username']}" if user and user["username"] else str(uid)
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=(
                            f"💰 <b>Crypto Payment Confirmed</b>\n\n"
                            f"User:    {uname} (<code>{uid}</code>)\n"
                            f"Network: <code>{net_label}</code>\n"
                            f"Amount:  <code>{result['amount']:.2f} USDT</code>\n"
                            f"Plan:    <code>{months} month(s)</code>\n"
                            f"TX:      <code>{tx_hash}</code>"
                        ),
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
        else:
            # Let user retry with a new hash
            PENDING_INPUT[uid] = {"field": "tx_hash", "months": months, "amount": amount, "network": network}
            await msg.edit_text(
                f"❌ <b>Payment Not Verified</b>\n\n"
                f"{result['error']}\n\n"
                f"Send your transaction hash again once resolved:",
                parse_mode=ParseMode.HTML
            )

    elif field == "pay_email":
        email  = text.strip()
        months = pi.get("months", 1)
        amount = pi.get("amount", 12.00)
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            await update.effective_message.reply_text("❌ That doesn't look like a valid email. Please try again.")
            return

        await update.effective_message.reply_text("⏳ Generating your payment link...")
        result = initialize_transaction(uid, email, months)
        del PENDING_INPUT[uid]

        if result["ok"]:
            record_pending_payment(uid, result["reference"], months, amount, "USD")
            keyboard = [[InlineKeyboardButton("💳 Pay Now", url=result["authorization_url"])]]
            await update.effective_message.reply_text(
                f"✅ <b>Payment Link Ready!</b>\n\n"
                f"Plan:   <code>{months} month{'s' if months > 1 else ''}</code>\n"
                f"Amount: <code>${amount:.2f} USD</code>\n\n"
                f"Tap the button below to pay securely via Paystack.\n"
                f"Your subscription activates automatically once payment is confirmed.\n\n"
                f"Ref: <code>{result['reference']}</code>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        else:
            await update.effective_message.reply_text(
                f"❌ Could not generate payment link:\n<code>{result['message']}</code>\n\nPlease try /subscribe again.",
                parse_mode=ParseMode.HTML
            )

    elif field == "symbol_search":
        raw    = text.upper().strip().replace("/", "").replace("USDT", "")
        symbol = f"{raw}/USDT"
        user   = get_user(uid)
        if not user or not user["api_key"]:
            update_setting(uid, "symbol", symbol)
            await update.effective_message.reply_text(
                f"✅ Symbol set to <b>{symbol}</b> (exchange not connected yet — pair not validated).",
                parse_mode=ParseMode.HTML
            )
            del PENDING_INPUT[uid]
            return
        # Validate against live exchange markets
        await update.effective_message.reply_text(f"⏳ Validating <code>{symbol}</code> on your exchange...", parse_mode=ParseMode.HTML)
        try:
            exch = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
            markets = exch.load_markets()
            if symbol in markets:
                update_setting(uid, "symbol", symbol)
                ticker = exch.fetch_ticker(symbol)
                price  = ticker["last"]
                chg    = ticker.get("percentage", 0) or 0
                await update.effective_message.reply_text(
                    f"✅ <b>{symbol}</b> found and saved!\n"
                    f"Current price: <code>${price:,.6f}</code>\n"
                    f"24h change: <code>{chg:+.2f}%</code>\n\n"
                    f"Use /start_trade to begin trading it.",
                    parse_mode=ParseMode.HTML
                )
            else:
                # Suggest similar markets
                suggestions = [m for m in markets if raw in m and "USDT" in m][:5]
                if suggestions:
                    sug_text = "\n".join([f"  • <code>{s}</code>" for s in suggestions])
                    hint = f"Did you mean:\n{sug_text}"
                else:
                    hint = "No similar pairs found on this exchange."
                await update.effective_message.reply_text(
                    f"❌ <b>{symbol}</b> not available on your exchange.\n\n"
                    f"{hint}\n\n"
                    f"Tap /settings → 🪙 Symbol to try again.",
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            await update.effective_message.reply_text(f"⚠️ Validation failed: <code>{e}</code>", parse_mode=ParseMode.HTML)
        del PENDING_INPUT[uid]

    elif field == "admin_reply_msg":
        if not is_admin(uid):
            del PENDING_INPUT[uid]
            return
        target_id = pi.get("target_id")
        del PENDING_INPUT[uid]
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"📩 <b>Reply from Support</b>\n\n{text}",
                parse_mode="HTML"
            )
            await update.effective_message.reply_text(
                f"✅ Reply sent to <code>{target_id}</code>.",
                parse_mode="HTML"
            )
        except Exception as e:
            await update.effective_message.reply_text(
                f"❌ Failed to send: <code>{e}</code>",
                parse_mode="HTML"
            )
        return

    elif field == "support_msg":
        tg_user  = update.effective_user
        username = f"@{tg_user.username}" if tg_user.username else tg_user.full_name
        del PENDING_INPUT[uid]
        await _send_support_message(context, uid, username, text)
        return

    elif field == "alert_symbol":
        # User typed alert inline e.g. "BTC/USDT above 70000 my note"
        import alerts_handlers as _ah_mod  # lazy: safe inside function
        parts = text.strip().split()
        if len(parts) < 3:
            await update.effective_message.reply_text(
                "❌ Format: <code>SYMBOL above|below PRICE note</code>\n"
                "Example: <code>BTC/USDT above 70000</code>",
                parse_mode=ParseMode.HTML
            )
            return
        context.args = parts
        del PENDING_INPUT[uid]
        await _ah_mod.setalert(update, context)
        return

    elif field == "api_key":
        # Delete the message containing the API key for security
        try:
            await update.message.delete()
        except Exception:
            pass
        PENDING_INPUT[uid]["api_key"] = text
        PENDING_INPUT[uid]["field"]   = "api_secret"
        await context.bot.send_message(
            chat_id=uid,
            text="🔐 API Key saved securely. Now send your <b>API Secret</b>:\n"
                 "<i>(This message will also be deleted after saving)</i>",
            parse_mode=ParseMode.HTML
        )

    elif field == "api_secret":
        try:
            await update.message.delete()
        except Exception:
            pass
        PENDING_INPUT[uid]["api_secret"] = text
        exch_id = PENDING_INPUT[uid].get("exchange", "binance")

        if exch_id in PASSPHRASE_EXCHANGES:
            # Need passphrase before we can validate — collect it first
            PENDING_INPUT[uid]["field"] = "api_pass"
            await context.bot.send_message(
                chat_id=uid,
                text=f"🔑 <b>{EXCHANGE_LABELS.get(exch_id, exch_id)}</b> requires a <b>Passphrase</b>. Please send it:\n"
                     "<i>(Message will be deleted for security)</i>",
                parse_mode=ParseMode.HTML
            )
        else:
            api_key_stored = PENDING_INPUT[uid]["api_key"]
            # Format check only — instant, no network call
            fmt = check_key_format(exch_id, api_key_stored, text)
            if not fmt["valid"]:
                PENDING_INPUT[uid]["field"] = "api_key"
                await _send_expiring(
                    context, uid,
                    f"❌ <b>Invalid keys</b>\n\n{fmt['error']}\n\nPlease send your <b>API Key</b> again:",
                )
            else:
                save_exchange_creds(uid, exch_id, api_key_stored, text)
                if exch_id == "mexc":
                    record_mexc_key_saved(uid)
                del PENDING_INPUT[uid]
                mexc_note = (
                    "\n\n⚠️ <b>MEXC Note:</b> MEXC API keys expire after 90 days. "
                    "Set a reminder to renew them!"
                ) if exch_id == "mexc" else ""
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"✅ <b>{EXCHANGE_LABELS.get(exch_id, exch_id)}</b> keys saved!\n\n"
                         f"🔒 Stored securely in the database.\n"
                         f"💡 Use /balance to verify the connection is working.{mexc_note}",
                    parse_mode=ParseMode.HTML
                )

    elif field == "api_pass":
        try:
            await update.message.delete()
        except Exception:
            pass
        exch_id = PENDING_INPUT[uid].get("exchange", "okx")
        api_key    = PENDING_INPUT[uid].get("api_key", "")
        api_secret = PENDING_INPUT[uid].get("api_secret", "")
        api_pass   = text

        # Format check only — instant, no network call
        fmt = check_key_format(exch_id, api_key, api_secret, api_pass)
        if not fmt["valid"]:
            PENDING_INPUT[uid]["field"] = "api_pass"
            await context.bot.send_message(
                chat_id=uid,
                text=f"❌ <b>Invalid credentials</b>\n\n{fmt['error']}\n\nPlease send your <b>Passphrase</b> again:",
                parse_mode=ParseMode.HTML
            )
        else:
            save_exchange_creds(uid, exch_id, api_key, api_secret, api_pass)
            del PENDING_INPUT[uid]
            await context.bot.send_message(
                chat_id=uid,
                text=f"✅ <b>{EXCHANGE_LABELS.get(exch_id, exch_id)}</b> keys saved!\n\n"
                     f"🔒 Stored securely in the database.\n"
                     f"💡 Use /balance to verify the connection is working.",
                parse_mode=ParseMode.HTML
            )

    # ── DCA multi-step wizard ─────────────────────────────────────────────────
    elif field == "dca_symbol":
        symbol = text.upper()
        if "/" not in symbol:
            symbol += "/USDT"
        PENDING_INPUT[uid]["symbol"] = symbol
        PENDING_INPUT[uid]["field"]  = "dca_amount"
        await update.effective_message.reply_text(
            f"Symbol: <code>{symbol}</code>\n\nHow much USDT per buy? (e.g. <code>50</code>)",
            parse_mode=ParseMode.HTML
        )

    elif field == "dca_amount":
        try:
            amount = float(text)
            assert amount >= 5
        except Exception:
            await update.effective_message.reply_text("Enter a valid amount (min $5).")
            return
        PENDING_INPUT[uid]["amount"] = amount
        PENDING_INPUT[uid]["field"]  = "dca_interval"
        keyboard = [[
            InlineKeyboardButton("1h",   callback_data="dca_int_3600"),
            InlineKeyboardButton("4h",   callback_data="dca_int_14400"),
            InlineKeyboardButton("8h",   callback_data="dca_int_28800"),
            InlineKeyboardButton("24h",  callback_data="dca_int_86400"),
            InlineKeyboardButton("7d",   callback_data="dca_int_604800"),
        ]]
        await update.effective_message.reply_text(
            "Choose buy interval:", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # ── Grid multi-step wizard ────────────────────────────────────────────────
    elif field == "grid_symbol":
        symbol = text.upper()
        if "/" not in symbol:
            symbol += "/USDT"
        PENDING_INPUT[uid]["symbol"] = symbol
        PENDING_INPUT[uid]["field"]  = "grid_lower"
        await update.effective_message.reply_text(
            f"Symbol: <code>{symbol}</code>\n\nEnter the <b>lower price</b> of the grid range:",
            parse_mode=ParseMode.HTML
        )

    elif field == "grid_lower":
        try:
            lower = float(text)
        except ValueError:
            await update.effective_message.reply_text("Enter a valid price.")
            return
        PENDING_INPUT[uid]["lower"] = lower
        PENDING_INPUT[uid]["field"] = "grid_upper"
        await update.effective_message.reply_text("Enter the <b>upper price</b>:",
                                                   parse_mode=ParseMode.HTML)

    elif field == "grid_upper":
        try:
            upper = float(text)
            lower = PENDING_INPUT[uid].get("lower", 0)
            assert upper > lower
        except Exception:
            await update.effective_message.reply_text("Upper must be greater than lower price.")
            return
        PENDING_INPUT[uid]["upper"] = upper
        PENDING_INPUT[uid]["field"] = "grid_levels"
        await update.effective_message.reply_text(
            f"Range: <code>${lower:.4f} – ${upper:.4f}</code>\n\n"
            "How many grid levels? (5–20)",
            parse_mode=ParseMode.HTML
        )

    elif field == "grid_levels":
        try:
            levels = int(text)
            assert 5 <= levels <= 20
        except Exception:
            await update.effective_message.reply_text("Enter a number between 5 and 20.")
            return
        PENDING_INPUT[uid]["levels"] = levels
        PENDING_INPUT[uid]["field"]  = "grid_usdt"
        await update.effective_message.reply_text(
            f"Levels: <code>{levels}</code>\n\nTotal USDT to allocate for this grid:",
            parse_mode=ParseMode.HTML
        )

    elif field == "grid_usdt":
        import asyncio as _asyncio
        try:
            total_usdt = float(text)
            assert total_usdt >= 10
        except Exception:
            await update.effective_message.reply_text("Enter a valid USDT amount (min $10).")
            return

        pi     = PENDING_INPUT[uid]
        symbol = pi["symbol"]
        lower  = pi["lower"]
        upper  = pi["upper"]
        levels = pi["levels"]
        user   = get_user(uid)
        del PENDING_INPUT[uid]

        if not user or not user.get("exchange"):
            await update.effective_message.reply_text("Connect an exchange first with /exchanges.")
            return

        msg = await update.effective_message.reply_text("⏳ Placing grid orders…")
        try:
            creds   = get_exchange_creds(uid, user["exchange"])
            exch    = get_exchange(user["exchange"], creds["api_key"],
                                   creds["api_secret"], creds.get("api_pass", ""))
            plan_id = create_grid_plan(uid, user["exchange"], symbol, lower, upper, levels, total_usdt)
            usdt_per = total_usdt / levels
            prices   = [lower + (upper - lower) / (levels - 1) * i for i in range(levels)]
            placed   = 0

            for price in prices:
                try:
                    qty     = usdt_per / price
                    ticker  = await _asyncio.to_thread(fetch_ticker, exch, symbol)
                    side    = "buy" if price <= ticker["last"] else "sell"
                    from exchange import place_limit_order
                    order   = await _asyncio.to_thread(place_limit_order, exch, symbol, side, qty, price)
                    from database import add_grid_order
                    add_grid_order(plan_id, order["id"], side, price, qty)
                    placed += 1
                except Exception as e:
                    logger.warning(f"[GRID] Place order at {price}: {e}")

            if placed == 0:
                set_grid_status(plan_id, "stopped")
                await msg.edit_text("❌ Could not place any grid orders. Check exchange balance.")
                return

            write_audit(uid, "grid_created", {
                "plan_id": plan_id, "symbol": symbol,
                "levels": levels, "placed": placed
            })
            await msg.edit_text(
                f"✅ <b>Grid Created #{plan_id}</b>\n\n"
                f"  Symbol:  <code>{symbol}</code>\n"
                f"  Range:   <code>${lower:.4f} – ${upper:.4f}</code>\n"
                f"  Levels:  <code>{levels}</code>  ({placed} orders placed)\n"
                f"  Capital: <code>${total_usdt:.2f}</code>\n\n"
                f"Use /grid_status {plan_id} to monitor.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"grid_create uid={uid}: {e}", exc_info=True)
            await msg.edit_text(f"❌ Grid creation failed: {e}")
            await report_error_to_admin(context, e, f"grid_create uid={uid}")


# ── BUTTON_MAP: wire callback_data keys to real handler functions ──────────────
# These are imported lazily after all functions are defined.
# Both inline dashboard buttons (cmd_*) and reply keyboard buttons use this map.

async def _run_cmd(handler_fn, update: Update, context):
    """
    Adapter: works for both CallbackQuery updates and Message updates.
    Builds a fake update.message if called from a callback so handlers
    that call update.effective_message.reply_text() work transparently.
    """
    if update.callback_query:
        # Patch message reference so handlers can call update.effective_message.reply_text
        update._effective_message = update.callback_query.message
    await handler_fn(update, context)


def _make_cmd(fn):
    async def _inner(update, context):
        await _run_cmd(fn, update, context)
    return _inner


# Populated after all handlers are defined
def _build_button_map():
    # Import here (not at top) to avoid any residual import ordering issues
    import alerts_handlers as _ah  # lazy: safe inside function
    # close_all_cmd and reply_user are defined in handlers.py (same module)
    return {
        "cmd_balance":     _make_cmd(balance),
        "cmd_chart":       _make_cmd(chart),
        "cmd_start_trade": _make_cmd(start_trade),
        "cmd_stop_trade":  _make_cmd(stop_trade),
        "cmd_history":     _make_cmd(history),
        "cmd_pnl":         _make_cmd(pnl),
        "cmd_health":      _make_cmd(health),
        "cmd_summary":     _make_cmd(summary),
        "cmd_settings":    _make_cmd(settings),
        "cmd_exchanges":   _make_cmd(exchanges),
        "cmd_subscribe":   _make_cmd(subscribe),
        "cmd_mystatus":    _make_cmd(mystatus),
        "cmd_support":     _make_cmd(support),
        "cmd_help":        _make_cmd(help_cmd),
        "cmd_dashboard":   _make_cmd(dashboard),
        "cmd_referral":    _make_cmd(referral),
        "cmd_broadcast":   _make_cmd(broadcast),
        "cmd_setalert":    _make_cmd(_ah.setalert),
        "cmd_myalerts":    _make_cmd(_ah.myalerts),
        "cmd_subscribers": _make_cmd(subscribers),
        "cmd_panic":       _make_cmd(panic),
        "cmd_close":       _make_cmd(close_all_cmd),
        "cmd_reply":       _make_cmd(reply_user),
        "cmd_positions":   _make_cmd(positions),
        "cmd_export":      _make_cmd(export_trades),
        "cmd_signals":     _make_cmd(signals_history),
        "cmd_status":      _make_cmd(bot_status),
        "cmd_timezone":    _make_cmd(timezone_cmd),
        "cmd_arbitrage":   _make_cmd(arbitrage_cmd),
        "cmd_paper":       _make_cmd(paper_cmd),
        "cmd_paper_stats": _make_cmd(paper_stats_cmd),
        "cmd_dca":         _make_cmd(dca_cmd),
        "cmd_grid":        _make_cmd(grid_cmd),
        "cmd_analytics":   _make_cmd(analytics_cmd),
        "cmd_audit":       _make_cmd(audit_cmd),
        "cmd_market":      _make_cmd(market_cmd),
        "cmd_webhook":     _make_cmd(webhook_cmd),
        "cmd_smart_orders":_make_cmd(smart_orders_cmd),
    }


# Build the map at module load time
# BUTTON_MAP.update(_build_button_map())


# ═════════════════════════════════════════════════════════════════════════════
# /arbitrage — on-demand arbitrage scan  +  symbol picker  +  enable/disable
# ═════════════════════════════════════════════════════════════════════════════

import json as _json

# All tokens the user can pick from for arbitrage scanning
ARB_SYMBOL_OPTIONS: list[str] = [
    "BTC/USDT",  "ETH/USDT",  "SOL/USDT",  "BNB/USDT",
    "XRP/USDT",  "ADA/USDT",  "DOGE/USDT", "AVAX/USDT",
    "LINK/USDT", "DOT/USDT",  "MATIC/USDT","LTC/USDT",
    "UNI/USDT",  "ATOM/USDT", "TRX/USDT",  "NEAR/USDT",
    "APT/USDT",  "OP/USDT",   "ARB/USDT",  "TON/USDT",
    "FIL/USDT",  "INJ/USDT",  "SUI/USDT",  "SEI/USDT",
]


def _get_arb_symbols(uid: int) -> list[str] | None:
    """Return the user\'s chosen arb symbols, or None (= scan defaults)."""
    s = get_settings(uid)
    raw = s.get("arb_symbols")
    if not raw:
        return None
    try:
        parsed = _json.loads(raw)
        return parsed if isinstance(parsed, list) and parsed else None
    except Exception:
        return None


def _build_arb_symbol_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    """Build a toggle keyboard for the arb symbol picker."""
    rows = []
    items = list(ARB_SYMBOL_OPTIONS)
    for i in range(0, len(items), 3):
        chunk = items[i:i+3]
        row = []
        for sym in chunk:
            tick = "✅ " if sym in selected else ""
            base = sym.split("/")[0]
            row.append(InlineKeyboardButton(
                f"{tick}{base}",
                callback_data=f"arb_sym_{sym}"
            ))
        rows.append(row)
    rows.append([
        InlineKeyboardButton("✅ Select All",  callback_data="arb_sym_all"),
        InlineKeyboardButton("🗑 Clear All",  callback_data="arb_sym_none"),
    ])
    rows.append([
        InlineKeyboardButton("💾 Save & Scan", callback_data="arb_sym_save"),
    ])
    return InlineKeyboardMarkup(rows)


async def arbitrage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /arbitrage — show the arbitrage control panel.

    From here the user can:
    - Enable / disable background arb scanning
    - Choose which tokens to scan
    - Run an on-demand scan
    """
    import asyncio as _asyncio
    from arbitrage import run_arbitrage_scan, MIN_PROFIT_PCT, ALL_SCANNABLE_SYMBOLS

    uid = update.effective_user.id

    if not is_admin(uid) and not has_active_access(uid):
        keyboard = [[InlineKeyboardButton("💳 Subscribe", callback_data="subscribe")]]
        await update.effective_message.reply_text(
            "🔒 *Arbitrage scanning requires an active subscription.*\n\n"
            "Use /subscribe to get started.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    s           = get_settings(uid)
    arb_enabled = bool(s.get("arb_enabled", 1))
    arb_alerts  = bool(s.get("arb_alerts",  1))
    chosen      = _get_arb_symbols(uid)
    sym_display = ", ".join(chosen) if chosen else "All defaults"

    keyboard = [
        [InlineKeyboardButton(
            f"{'🟢 Arbitrage: ON' if arb_enabled else '🔴 Arbitrage: OFF'} — tap to toggle",
            callback_data="arb_toggle_enabled"
        )],
        [InlineKeyboardButton(
            f"{'🔔 Auto-Alerts: ON' if arb_alerts else '🔕 Auto-Alerts: OFF'}",
            callback_data="toggle_arb_alerts"
        )],
        [InlineKeyboardButton("🪙 Choose Tokens to Scan", callback_data="arb_sym_picker")],
        [InlineKeyboardButton("🔍 Scan Now",              callback_data="arb_scan_now")],
    ]

    status_icon = "🟢 ACTIVE" if arb_enabled else "🔴 DISABLED"
    await update.effective_message.reply_text(
        f"⚡ *Arbitrage Control Panel*\n\n"
        f"Status:       `{status_icon}`\n"
        f"Auto-alerts:  `{'ON' if arb_alerts else 'OFF'}`\n"
        f"Tokens:       `{sym_display}`\n"
        f"Min profit:   `{MIN_PROFIT_PCT}%` (after all fees)\n\n"
        f"📊 *Cross-Exchange* — buy low on one exchange, sell high on another\n"
        f"🔺 *Triangular* — exploit mispricing across 3 pairs on one exchange\n\n"
        f"Tap a button to configure or scan:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _arb_run_scan(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_msg=None):
    """
    Internal helper: build exchange pool, run scan, reply with results.
    Called from both the 'Scan Now' button and the /arbitrage command.
    """
    import asyncio as _asyncio
    from arbitrage import run_arbitrage_scan, MIN_PROFIT_PCT

    uid = update.effective_user.id
    msg_target = edit_msg or await (update.callback_query.message if update.callback_query
                                    else update.effective_message).reply_text(
        "🔍 Scanning… this may take 15–30 seconds.",
    )

    try:
        s = get_settings(uid)
        if not s.get("arb_enabled", 1):
            await msg_target.edit_text(
                "⚠️ Arbitrage is currently *disabled* for your account.\n"
                "Use /arbitrage → toggle ON to re-enable.",
                parse_mode="Markdown",
            )
            return

        stored = get_stored_exchanges(uid)
        if not stored:
            await msg_target.edit_text(
                "⚠️ *No exchange API keys found.*\n"
                "Add at least one exchange via /exchanges, then try again.\n"
                "_Tip: Connect 2+ exchanges to enable cross-exchange scanning._",
                parse_mode="Markdown",
            )
            return

        exchanges_pool: dict = {}
        failed: list[str]   = []
        for ex_id in stored:
            try:
                creds = get_exchange_creds(uid, ex_id)
                if not creds:
                    continue
                ex = get_exchange(
                    ex_id,
                    creds.get("api_key",    ""),
                    creds.get("api_secret", ""),
                    creds.get("api_pass",   ""),
                )
                exchanges_pool[ex_id] = ex
            except Exception as exc:
                failed.append(ex_id)
                logger.warning(f"[ARB-CMD] Could not build {ex_id} uid={uid}: {exc}")

        if not exchanges_pool:
            await msg_target.edit_text(
                "❌ Could not connect to any of your exchanges.\n"
                "Please check your API keys via /exchanges."
            )
            return

        chosen  = _get_arb_symbols(uid)
        result  = await _asyncio.to_thread(
            run_arbitrage_scan, exchanges_pool, 1_000.0, chosen
        )

        cross_viable = [o for o in result["cross_exchange"] if o.viable]
        tri_viable   = [o for o in result["triangular"]     if o.viable]
        scan_errors  = result.get("scan_errors", [])

        exchanges_str = ", ".join(f"`{e.upper()}`" for e in exchanges_pool)
        sym_str = ", ".join(chosen) if chosen else "defaults"
        header = (
            f"⚡ *Arbitrage Scan Results*\n"
            f"Exchanges: {exchanges_str}\n"
            f"Tokens: `{sym_str}`\n"
            f"Min net profit: `{MIN_PROFIT_PCT}%`\n\n"
        )

        # ── Non-fatal scan errors → surface to user ────────────────────────────
        error_note = ""
        if scan_errors:
            error_note = (
                f"\n⚠️ _{len(scan_errors)} symbol(s) could not be priced "
                f"(network/listing issue) and were skipped._"
            )
            logger.warning(
                f"[ARB-CMD] {len(scan_errors)} scan errors for uid={uid}: "
                + "; ".join(f"{e.exchange}/{e.symbol}: {e.error}" for e in scan_errors[:5])
            )
            # Also report to admins if errors are exchange-wide (not just symbol-level)
            exchange_wide = [e for e in scan_errors if e.symbol in ("cross_exchange","triangular")]
            if exchange_wide:
                try:
                    await report_error_to_admin(
                        context,
                        Exception(exchange_wide[0].error),
                        f"arb_scan uid={uid} {exchange_wide[0].exchange}"
                    )
                except Exception:
                    pass

        if result["viable_count"] == 0:
            all_cross = sorted(result["cross_exchange"], key=lambda o: -o.net_profit_pct)
            all_tri   = sorted(result["triangular"],     key=lambda o: -o.net_profit_pct)

            near: list[str] = []
            if all_cross:
                b = all_cross[0]
                near.append(
                    f"  📊 {b.symbol} ({b.buy_exchange.upper()} → {b.sell_exchange.upper()})"
                    f"  net `{b.net_profit_pct:.3f}%`"
                )
            if all_tri:
                b = all_tri[0]
                near.append(
                    f"  🔺 {b.exchange.upper()} `{'→'.join(b.path)}`"
                    f"  net `{b.net_profit_pct:.3f}%`"
                )

            body = "😴 *No viable opportunities right now.*\nSpreads are currently smaller than fees.\n"
            if near:
                body += "\n*Closest misses:*\n" + "\n".join(near)
            body += "\n\n💡 _Auto-alerts fire every 3 min when opportunities appear._"

            keyboard = [
                [InlineKeyboardButton("🔄 Re-scan", callback_data="arb_scan_now")],
                [InlineKeyboardButton("🪙 Change Tokens", callback_data="arb_sym_picker")],
            ]
            await msg_target.edit_text(
                header + body + error_note,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        lines: list[str] = [header]

        if cross_viable:
            lines.append(f"📊 *Cross-Exchange ({len(cross_viable)} found):*\n")
            for opp in cross_viable[:5]:
                lines.append(
                    f"✅ *{opp.symbol}*\n"
                    f"  Buy  `{opp.buy_exchange.upper()}` @ `${opp.buy_price:,.4f}`\n"
                    f"  Sell `{opp.sell_exchange.upper()}` @ `${opp.sell_price:,.4f}`\n"
                    f"  Spread `{opp.spread_pct:.3f}%` · Fees `{opp.fee_pct:.3f}%` · "
                    f"*Net `{opp.net_profit_pct:.3f}%`* (~`${opp.net_profit_usdt:.2f}` / $1k)\n"
                )

        if tri_viable:
            lines.append(f"🔺 *Triangular ({len(tri_viable)} found):*\n")
            for opp in tri_viable[:5]:
                path_str = " \u2192 ".join(
                    ("BUY " if d == "buy" else "SELL ") + sym
                    for sym, d in zip(opp.path, opp.directions)
                )
                lines.append(
                    f"\u2705 *{opp.exchange.upper()}*\n"
                    f"  `{path_str}`\n"
                    f"  *Net `{opp.net_profit_pct:.3f}%`* (~`${opp.net_profit_usdt:.2f}` / $1k)\n"
                )
        lines.append(
            "⚠️ _Cross-exchange arb requires pre-funded balances on both sides._\n"
            "⚠️ _Prices change in ms — verify live before executing manually._"
        )
        if failed:
            _failed_str = ", ".join(failed)
            lines.append(f"\n_Could not connect to: {_failed_str}_")
        if error_note:
            lines.append(error_note)

        full_msg = "\n".join(lines)
        if len(full_msg) > 4000:
            full_msg = full_msg[:3970] + "\n\n_…truncated_"

        keyboard = [
            [InlineKeyboardButton("🔄 Re-scan",          callback_data="arb_scan_now")],
            [InlineKeyboardButton("🪙 Change Tokens",    callback_data="arb_sym_picker")],
        ]
        await msg_target.edit_text(
            full_msg,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"_arb_run_scan error uid={uid}: {e}", exc_info=True)
        try:
            await msg_target.edit_text(f"❌ Scan failed: {e}")
        except Exception:
            pass
        try:
            await report_error_to_admin(context, e, f"arb_scan uid={uid}")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE HANDLERS — Paper Trading, Backtest, Analytics, Webhook, DCA,
#                    Grid, Smart Orders, Strategy Marketplace, Audit
# ═══════════════════════════════════════════════════════════════════════════════

# ── Paper Trading ─────────────────────────────────────────────────────────────

async def paper_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid) and not has_active_access(uid):
        await update.effective_message.reply_text("🔒 Subscription required. Use /subscribe.")
        return
    s = get_settings(uid)
    current = bool(s.get("paper_mode", 0))
    new_mode = 0 if current else 1
    update_setting(uid, "paper_mode", new_mode)
    write_audit(uid, "setting_change", {"key": "paper_mode", "old": current, "new": bool(new_mode)})
    if new_mode:
        bal = get_paper_balance(uid)
        start_bal = s.get("paper_start_balance", 1000.0)
        await update.effective_message.reply_text(
            f"🧪 <b>Paper Trading ON</b>\n\n"
            f"Paper balance: <code>${bal:.2f} USDT</code>\n"
            f"Starting balance: <code>${start_bal:.2f} USDT</code>\n\n"
            f"All trades will now be <b>simulated</b> — no real orders placed.\n"
            f"Use /paper_stats to track performance.",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.effective_message.reply_text(
            "✅ <b>Paper Trading OFF</b>\n\nLive trading is now active.",
            parse_mode=ParseMode.HTML
        )


async def paper_reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    keyboard = [[
        InlineKeyboardButton("✅ Confirm Reset", callback_data="paper_reset_confirm"),
        InlineKeyboardButton("❌ Cancel",         callback_data="paper_reset_cancel"),
    ]]
    await update.effective_message.reply_text(
        "⚠️ This will reset your paper balance to $1 000 and clear all paper trade history.\nAre you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def paper_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    stats = get_paper_stats(uid)
    bal   = get_paper_balance(uid)
    s     = get_settings(uid)
    start = s.get("paper_start_balance", 1000.0)
    total = stats.get("total", 0)
    if total == 0:
        await update.effective_message.reply_text("🧪 No paper trades yet. Enable paper mode with /paper.")
        return
    wins  = stats.get("wins", 0)
    losses= stats.get("losses", 0)
    wr    = wins / total * 100 if total else 0
    pnl   = stats.get("total_pnl", 0)
    pnl_pct = (bal - start) / start * 100 if start else 0
    await update.effective_message.reply_text(
        f"🧪 <b>Paper Trading Stats</b>\n\n"
        f"  Balance:    <code>${bal:.2f}</code> (started ${start:.2f})\n"
        f"  Total PnL:  <code>${pnl:+.4f}  ({pnl_pct:+.2f}%)</code>\n"
        f"  Trades:     <code>{total}</code>  ({wins}W / {losses}L)\n"
        f"  Win rate:   <code>{wr:.1f}%</code>\n"
        f"  Best trade: <code>{stats.get('best_pct', 0):+.2f}%</code>\n"
        f"  Worst:      <code>{stats.get('worst_pct', 0):+.2f}%</code>",
        parse_mode=ParseMode.HTML
    )


# ── Backtesting ───────────────────────────────────────────────────────────────

async def backtest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import asyncio as _asyncio
    from backtest import run_backtest

    uid  = update.effective_user.id
    if not is_admin(uid) and not has_active_access(uid):
        await update.effective_message.reply_text("🔒 Subscription required.")
        return

    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Usage: /backtest SYMBOL DAYS\nExample: /backtest BTC/USDT 90"
        )
        return

    symbol = args[0].upper()
    try:
        days = int(args[1])
        if days < 7 or days > 365:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("Days must be between 7 and 365.")
        return

    user = get_user(uid)
    if not user or not user.get("exchange"):
        await update.effective_message.reply_text("Connect an exchange first with /exchanges.")
        return

    msg = await update.effective_message.reply_text(
        f"⏳ Running backtest for <b>{symbol}</b> over <b>{days} days</b>…",
        parse_mode=ParseMode.HTML
    )
    try:
        creds = get_exchange_creds(uid, user["exchange"])
        if not creds:
            await msg.edit_text("❌ No credentials found. Reconnect your exchange.")
            return
        exch = get_exchange(user["exchange"], creds["api_key"],
                            creds["api_secret"], creds.get("api_pass", ""))
        limit = min(days * 24, 1000)
        ohlcv = await _asyncio.to_thread(fetch_ohlcv, exch, symbol, "1h", limit)

        s = get_settings(uid)
        result = await _asyncio.to_thread(
            run_backtest,
            ohlcv,
            s.get("take_profit", 2.0),
            s.get("stop_loss",   1.0),
            s.get("tp_mode",     "pct"),
            s.get("sl_mode",     "pct"),
            symbol,
        )
        await msg.edit_text(result.format_telegram(), parse_mode=ParseMode.HTML)
    except ValueError as e:
        await msg.edit_text(f"⚠️ {e}")
    except Exception as e:
        logger.error(f"backtest_cmd uid={uid}: {e}", exc_info=True)
        await msg.edit_text(f"❌ Backtest failed: {e}")
        await report_error_to_admin(context, e, f"backtest uid={uid}")


# ── Analytics ─────────────────────────────────────────────────────────────────

async def analytics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import asyncio as _asyncio

    uid  = update.effective_user.id
    if not is_admin(uid) and not has_active_access(uid):
        await update.effective_message.reply_text("🔒 Subscription required.")
        return

    args = context.args or []
    period_map = {"7d": 7, "30d": 30, "90d": 90, "all": 0}
    period_str = args[0].lower() if args else "30d"
    days = period_map.get(period_str, 30)

    msg = await update.effective_message.reply_text("📊 Computing analytics…")
    try:
        trades = await _asyncio.to_thread(get_full_trade_history, uid, days)
        result = await _asyncio.to_thread(compute_analytics, trades, days or 9999)
        if result.total_trades < 5:
            await msg.edit_text(
                f"📊 Not enough data yet (<5 trades in the selected period).\n"
                f"Keep trading and check back soon!",
            )
            return
        await msg.edit_text(result.format_telegram(), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"analytics_cmd uid={uid}: {e}", exc_info=True)
        await msg.edit_text(f"❌ Error: {e}")
        await report_error_to_admin(context, e, f"analytics uid={uid}")


async def webdash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid) and not has_active_access(uid):
        await update.effective_message.reply_text("🔒 Subscription required.")
        return
    token = create_webdash_token(uid)
    from config import BOT_WEBHOOK_URL
    base  = BOT_WEBHOOK_URL.rstrip("/") if BOT_WEBHOOK_URL else "https://yourdomain.com"
    url   = f"{base}/dashboard/{token}"
    await update.effective_message.reply_text(
        f"📊 <b>Web Dashboard</b>\n\n"
        f"<a href='{url}'>Open Dashboard →</a>\n\n"
        f"⏳ Link valid for <b>24 hours</b>.\n"
        f"Use /webdash to generate a new one.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ── TradingView Webhook ────────────────────────────────────────────────────────

async def webhook_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid) and not has_active_access(uid):
        await update.effective_message.reply_text("🔒 Subscription required.")
        return
    token = get_webhook_token(uid)
    if not token:
        token = generate_webhook_token(uid)
    from config import BOT_WEBHOOK_URL, WEBHOOK_PORT
    base = BOT_WEBHOOK_URL.rstrip("/") if BOT_WEBHOOK_URL else "https://yourdomain.com"
    url  = f"{base.replace(str(WEBHOOK_PORT), str(WEBHOOK_PORT + 1))}/tv/{token}"
    await update.effective_message.reply_text(
        f"📡 <b>TradingView Webhook</b>\n\n"
        f"POST to:\n<code>{url}</code>\n\n"
        f"<b>Payload format:</b>\n"
        f"<code>{{\n"
        f'  "token": "{token[:8]}…",\n'
        f'  "action": "buy" | "sell" | "close",\n'
        f'  "symbol": "BTCUSDT",\n'
        f'  "exchange": "binance",\n'
        f'  "amount": 100.0\n'
        f"}}</code>\n\n"
        f"Use /webhook_new to regenerate your token.",
        parse_mode=ParseMode.HTML
    )


async def webhook_new_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    keyboard = [[
        InlineKeyboardButton("✅ Regenerate Token", callback_data="webhook_regen_confirm"),
        InlineKeyboardButton("❌ Cancel",            callback_data="webhook_regen_cancel"),
    ]]
    await update.effective_message.reply_text(
        "⚠️ Regenerating your webhook token will <b>immediately invalidate</b> "
        "your current token. TradingView alerts using the old URL will fail.\n\nProceed?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


async def webhook_log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    logs = get_webhook_logs(uid, 10)
    if not logs:
        await update.effective_message.reply_text("No webhook activity yet.")
        return
    lines = ["📡 <b>Webhook Log (last 10)</b>\n"]
    for log in logs:
        icon = "✅" if log["status"] == "executed" else ("⏳" if log["status"] == "queued" else "❌")
        lines.append(
            f"{icon} <code>{log['action'].upper()} {log['symbol']}</code>  "
            f"{log['status']}  <i>{(log['created_at'] or '')[:16]}</i>"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── DCA Bot ───────────────────────────────────────────────────────────────────

async def dca_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    if not is_admin(uid) and not has_active_access(uid):
        await update.effective_message.reply_text("🔒 Subscription required.")
        return
    plans = get_dca_plans(uid)
    from config import MAX_DCA_PLANS

    if not plans:
        keyboard = [[InlineKeyboardButton("➕ Create DCA Plan", callback_data="dca_create")]]
        await update.effective_message.reply_text(
            "🔄 <b>DCA Bot</b>\n\nNo active plans. Create one to start dollar-cost averaging.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        return

    lines = ["🔄 <b>Your DCA Plans</b>\n"]
    for p in plans:
        status_icon = "▶️" if p["status"] == "active" else "⏸"
        interval_h  = p["interval_sec"] // 3600
        lines.append(
            f"{status_icon} <b>#{p['id']} {p['symbol']}</b>\n"
            f"  Amount: <code>${p['amount_usdt']:.2f}</code> every {interval_h}h\n"
            f"  Invested: <code>${p['total_invested']:.2f}</code>  "
            f"Runs: <code>{p['runs_completed']}</code>\n"
            f"  Next: <code>{(p['next_run_at'] or '')[:16]}</code>"
        )

    buttons = [[InlineKeyboardButton("➕ New Plan", callback_data="dca_create")]] \
              if len(plans) < MAX_DCA_PLANS else []
    buttons.append([InlineKeyboardButton("📊 Stats", callback_data=f"dca_stats_{plans[0]['id']}")])

    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        parse_mode=ParseMode.HTML
    )


async def dca_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /dca_stats <plan_id>")
        return
    try:
        plan_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("Invalid plan ID.")
        return

    stats = get_dca_stats(plan_id)
    if not stats:
        await update.effective_message.reply_text("Plan not found.")
        return
    plan = stats["plan"]
    if plan["user_id"] != uid and not is_admin(uid):
        await update.effective_message.reply_text("Not your plan.")
        return

    # Try to get live price
    live_val = None
    try:
        creds = get_exchange_creds(uid, plan["exchange_id"])
        if creds:
            exch = get_exchange(plan["exchange_id"], creds["api_key"],
                                creds["api_secret"], creds.get("api_pass", ""))
            import asyncio as _asyncio
            ticker = await _asyncio.to_thread(fetch_ticker, exch, plan["symbol"])
            live_price = ticker["last"]
            live_val   = stats["total_bought"] * live_price
    except Exception:
        pass

    pnl_usdt = (live_val - stats["total_invested"]) if live_val else None
    pnl_str  = f"${pnl_usdt:+.4f}" if pnl_usdt is not None else "N/A (fetch price failed)"

    await update.effective_message.reply_text(
        f"📊 <b>DCA Plan #{plan_id} — {plan['symbol']}</b>\n\n"
        f"  Total invested:  <code>${stats['total_invested']:.2f}</code>\n"
        f"  Total bought:    <code>{stats['total_bought']:.6f}</code>\n"
        f"  Avg entry price: <code>${stats['avg_price']:.4f}</code>\n"
        f"  Runs completed:  <code>{stats['runs']}</code>\n"
        f"  Current value:   <code>${live_val:.2f}</code>\n" if live_val else
        f"  Current value:   <code>N/A</code>\n"
        f"  Unrealised PnL:  <code>{pnl_str}</code>",
        parse_mode=ParseMode.HTML
    )


# ── Grid Trading ──────────────────────────────────────────────────────────────

async def grid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid) and not has_active_access(uid):
        await update.effective_message.reply_text("🔒 Subscription required.")
        return
    grids = get_active_grids(uid)
    keyboard = [[InlineKeyboardButton("➕ New Grid", callback_data="grid_create")]]
    if not grids:
        await update.effective_message.reply_text(
            "🔲 <b>Grid Trading</b>\n\nNo active grids.\n"
            "A grid places buy/sell orders at regular price intervals, profiting from oscillation.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        return
    lines = ["🔲 <b>Active Grids</b>\n"]
    for g in grids:
        lines.append(
            f"  <b>#{g['id']} {g['symbol']}</b>\n"
            f"  Range: <code>${g['lower_price']:.2f} – ${g['upper_price']:.2f}</code>  "
            f"Levels: <code>{g['grid_levels']}</code>\n"
            f"  Profit: <code>${g['total_profit']:.4f}</code>"
        )
    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


async def grid_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /grid_status <plan_id>")
        return
    try:
        plan_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("Invalid plan ID.")
        return
    plan = get_grid_plan(plan_id)
    if not plan or plan["user_id"] != uid:
        await update.effective_message.reply_text("Plan not found.")
        return
    orders = get_grid_orders(plan_id)

    # Build visual price ladder
    ladder_lines = []
    try:
        creds = get_exchange_creds(uid, plan["exchange_id"])
        exch  = get_exchange(plan["exchange_id"], creds["api_key"],
                             creds["api_secret"], creds.get("api_pass", ""))
        import asyncio as _asyncio
        ticker = await _asyncio.to_thread(fetch_ticker, exch, plan["symbol"])
        cur_price = ticker["last"]
    except Exception:
        cur_price = None

    prices = sorted(set(o["price"] for o in orders), reverse=True)
    for price in prices[:15]:
        order = next((o for o in orders if abs(o["price"] - price) < 0.001), None)
        if order:
            status_icon = "✅" if order["status"] == "filled" else ("⬜" if order["status"] == "open" else "❌")
            side_label  = "SELL" if order["side"] == "sell" else "BUY "
            cur_marker  = " 📍" if cur_price and abs(cur_price - price) / price < 0.005 else ""
            ladder_lines.append(
                f"  {status_icon} {side_label} @ <code>${price:.4f}</code>{cur_marker}"
            )

    await update.effective_message.reply_text(
        f"🔲 <b>Grid #{plan_id} — {plan['symbol']}</b>\n\n"
        f"  Range: <code>${plan['lower_price']:.2f} – ${plan['upper_price']:.2f}</code>\n"
        f"  Profit captured: <code>${plan['total_profit']:.4f}</code>\n\n"
        f"<b>Price Ladder:</b>\n" + "\n".join(ladder_lines),
        parse_mode=ParseMode.HTML
    )


async def grid_stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import asyncio as _asyncio
    uid  = update.effective_user.id
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /grid_stop <plan_id>")
        return
    try:
        plan_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("Invalid plan ID.")
        return
    plan = get_grid_plan(plan_id)
    if not plan or plan["user_id"] != uid:
        await update.effective_message.reply_text("Plan not found.")
        return

    msg = await update.effective_message.reply_text("⏳ Cancelling all grid orders…")
    try:
        creds = get_exchange_creds(uid, plan["exchange_id"])
        exch  = get_exchange(plan["exchange_id"], creds["api_key"],
                             creds["api_secret"], creds.get("api_pass", ""))
        orders = get_grid_orders(plan_id)
        cancelled = 0
        for o in orders:
            if o["status"] == "open":
                try:
                    await _asyncio.to_thread(exch.cancel_order, o["order_id"], plan["symbol"])
                    from database import update_grid_order_status
                    update_grid_order_status(o["id"], "cancelled")
                    cancelled += 1
                except Exception:
                    pass
        set_grid_status(plan_id, "stopped")
        write_audit(uid, "grid_stopped", {"plan_id": plan_id, "cancelled_orders": cancelled})
        await msg.edit_text(
            f"✅ <b>Grid #{plan_id} Stopped</b>\n\n"
            f"Cancelled {cancelled} open orders.\n"
            f"Total profit captured: <code>${plan['total_profit']:.4f}</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await msg.edit_text(f"❌ Error stopping grid: {e}")
        await report_error_to_admin(context, e, f"grid_stop uid={uid}")


# ── Smart Orders ──────────────────────────────────────────────────────────────

async def twap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import asyncio as _asyncio
    from smart_orders import create_smart_order

    uid  = update.effective_user.id
    if not is_admin(uid) and not has_active_access(uid):
        await update.effective_message.reply_text("🔒 Subscription required.")
        return

    args = context.args or []
    if len(args) < 5:
        await update.effective_message.reply_text(
            "Usage: /twap SYMBOL SIDE TOTAL_USDT SLICES INTERVAL_MINUTES\n"
            "Example: /twap BTC/USDT buy 1000 10 5"
        )
        return
    try:
        symbol  = args[0].upper()
        side    = args[1].lower()
        total   = float(args[2])
        slices  = int(args[3])
        mins    = int(args[4])
        assert side in ("buy", "sell") and slices >= 2 and mins >= 1 and total >= 10
    except Exception:
        await update.effective_message.reply_text("Invalid parameters. Check format.")
        return

    user = get_user(uid)
    if not user or not user.get("exchange"):
        await update.effective_message.reply_text("Connect an exchange first.")
        return

    order_id = create_smart_order(
        uid, user["exchange"], "twap", symbol, side, total,
        {"slices": slices, "interval_sec": mins * 60}
    )
    write_audit(uid, "twap_created", {
        "order_id": order_id, "symbol": symbol, "total": total, "slices": slices
    })
    await update.effective_message.reply_text(
        f"⏱ <b>TWAP Order Created #{order_id}</b>\n\n"
        f"  Symbol:   <code>{symbol}</code>\n"
        f"  Side:     <code>{side.upper()}</code>\n"
        f"  Total:    <code>${total:.2f}</code>\n"
        f"  Slices:   <code>{slices}</code> × <code>${total/slices:.2f}</code>\n"
        f"  Interval: <code>every {mins} min</code>\n\n"
        f"First slice executes in {mins} min.",
        parse_mode=ParseMode.HTML
    )


async def iceberg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from smart_orders import create_smart_order, place_iceberg_chunk, add_smart_order_leg
    import asyncio as _asyncio

    uid  = update.effective_user.id
    if not is_admin(uid) and not has_active_access(uid):
        await update.effective_message.reply_text("🔒 Subscription required.")
        return

    args = context.args or []
    if len(args) < 4:
        await update.effective_message.reply_text(
            "Usage: /iceberg SYMBOL SIDE TOTAL_USDT VISIBLE_PCT\n"
            "Example: /iceberg ETH/USDT buy 500 20  (shows 20% at a time)"
        )
        return
    try:
        symbol  = args[0].upper()
        side    = args[1].lower()
        total   = float(args[2])
        vis_pct = float(args[3])
        assert side in ("buy", "sell") and 5 <= vis_pct <= 50 and total >= 10
    except Exception:
        await update.effective_message.reply_text("Invalid parameters.")
        return

    user = get_user(uid)
    if not user or not user.get("exchange"):
        await update.effective_message.reply_text("Connect an exchange first.")
        return

    msg = await update.effective_message.reply_text("⏳ Placing first iceberg chunk…")
    try:
        creds = get_exchange_creds(uid, user["exchange"])
        exch  = get_exchange(user["exchange"], creds["api_key"],
                             creds["api_secret"], creds.get("api_pass", ""))
        ticker = await _asyncio.to_thread(fetch_ticker, exch, symbol)
        price  = ticker["last"]
        chunk  = total * vis_pct / 100

        order_id = create_smart_order(
            uid, user["exchange"], "iceberg", symbol, side, total,
            {"visible_pct": vis_pct}
        )
        first_order = await _asyncio.to_thread(
            place_iceberg_chunk, exch, symbol, side, chunk, price
        )
        add_smart_order_leg(order_id, first_order["id"], side, price, chunk / price, "open")
        write_audit(uid, "iceberg_created", {"order_id": order_id, "symbol": symbol})
        await msg.edit_text(
            f"🧊 <b>Iceberg Order Created #{order_id}</b>\n\n"
            f"  Symbol:  <code>{symbol}</code>  Side: <code>{side.upper()}</code>\n"
            f"  Total:   <code>${total:.2f}</code>\n"
            f"  Visible: <code>{vis_pct:.0f}%</code> (${chunk:.2f}) at a time\n\n"
            f"First chunk placed @ ~<code>${price:.4f}</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
        await report_error_to_admin(context, e, f"iceberg uid={uid}")


async def oco_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from smart_orders import create_smart_order, place_oco_legs
    import asyncio as _asyncio

    uid  = update.effective_user.id
    if not is_admin(uid) and not has_active_access(uid):
        await update.effective_message.reply_text("🔒 Subscription required.")
        return

    args = context.args or []
    if len(args) < 5:
        await update.effective_message.reply_text(
            "Usage: /oco SYMBOL SIDE AMOUNT TP_PRICE SL_PRICE\n"
            "Example: /oco BTC/USDT sell 100 70000 60000"
        )
        return
    try:
        symbol   = args[0].upper()
        side     = args[1].lower()
        amount   = float(args[2])
        tp_price = float(args[3])
        sl_price = float(args[4])
        assert side in ("buy", "sell") and tp_price != sl_price and amount >= 5
    except Exception:
        await update.effective_message.reply_text("Invalid parameters.")
        return

    user = get_user(uid)
    if not user or not user.get("exchange"):
        await update.effective_message.reply_text("Connect an exchange first.")
        return

    msg = await update.effective_message.reply_text("⏳ Placing OCO orders…")
    try:
        creds = get_exchange_creds(uid, user["exchange"])
        exch  = get_exchange(user["exchange"], creds["api_key"],
                             creds["api_secret"], creds.get("api_pass", ""))
        order_id = create_smart_order(
            uid, user["exchange"], "oco", symbol, side, amount,
            {"tp_price": tp_price, "sl_price": sl_price}
        )
        await _asyncio.to_thread(
            place_oco_legs, exch, symbol, side, amount, tp_price, sl_price, order_id
        )
        write_audit(uid, "oco_created", {
            "order_id": order_id, "symbol": symbol,
            "tp": tp_price, "sl": sl_price
        })
        await msg.edit_text(
            f"🎯 <b>OCO Order Created #{order_id}</b>\n\n"
            f"  Symbol:    <code>{symbol}</code>\n"
            f"  Side:      <code>{side.upper()}</code>\n"
            f"  TP target: <code>${tp_price:,.4f}</code>\n"
            f"  SL target: <code>${sl_price:,.4f}</code>\n\n"
            f"Both orders placed. When one fills, the other cancels automatically.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
        await report_error_to_admin(context, e, f"oco uid={uid}")


async def smart_orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from smart_orders import get_active_smart_orders
    uid   = update.effective_user.id
    orders = get_active_smart_orders(uid)
    if not orders:
        await update.effective_message.reply_text("No active smart orders.")
        return
    lines = ["⚙️ <b>Active Smart Orders</b>\n"]
    for o in orders:
        lines.append(
            f"  <b>#{o['id']} {o['type'].upper()}</b> — {o['symbol']}\n"
            f"  {o['side'].upper()}  ${o['total_usdt']:.2f}  "
            f"Slices: {o['slices_done']}  Status: <code>{o['status']}</code>"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── Strategy Marketplace ──────────────────────────────────────────────────────

async def market_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import json as _json
    uid  = update.effective_user.id
    if not is_admin(uid) and not has_active_access(uid):
        await update.effective_message.reply_text("🔒 Subscription required.")
        return

    args = context.args or []
    sub_cmd = args[0].lower() if args else "browse"

    if sub_cmd == "browse" or sub_cmd not in ("publish", "subscribe", "unsubscribe", "leaderboard"):
        strategies = get_strategies(limit=5, offset=0)
        if not strategies:
            await update.effective_message.reply_text(
                "🏪 <b>Strategy Marketplace</b>\n\nNo published strategies yet.\n"
                "Use /market publish <name> to share yours!",
                parse_mode=ParseMode.HTML
            )
            return
        lines = ["🏪 <b>Strategy Marketplace</b>\n"]
        buttons = []
        for s in strategies:
            lines.append(
                f"  <b>#{s['id']} {s['name']}</b>\n"
                f"  Symbol: <code>{s['symbol']}</code>  "
                f"TP: <code>{s['take_profit']}%</code>  SL: <code>{s['stop_loss']}%</code>\n"
                f"  Subscribers: <code>{s['subscriber_count']}</code>"
            )
            buttons.append([InlineKeyboardButton(
                f"Subscribe to #{s['id']} {s['name']}",
                callback_data=f"strat_sub_{s['id']}"
            )])
        buttons.append([InlineKeyboardButton("🏆 Leaderboard", callback_data="strat_leaderboard")])
        await update.effective_message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.HTML
        )

    elif sub_cmd == "publish":
        if len(args) < 2:
            await update.effective_message.reply_text(
                "Usage: /market publish <name> [description]\n"
                "Example: /market publish \"EMA Scalper\" Fast EMA crossover strategy"
            )
            return
        name = args[1]
        desc = " ".join(args[2:]) if len(args) > 2 else ""
        s    = get_settings(uid)
        strat_id = publish_strategy(uid, name, desc, s)
        write_audit(uid, "strategy_published", {"id": strat_id, "name": name})
        await update.effective_message.reply_text(
            f"✅ <b>Strategy Published #{strat_id}</b>\n\n"
            f"Name: <b>{name}</b>\n"
            f"Symbol: <code>{s.get('symbol')}</code>  "
            f"TP: <code>{s.get('take_profit')}%</code>  SL: <code>{s.get('stop_loss')}%</code>\n\n"
            f"Other users can now subscribe to your strategy!",
            parse_mode=ParseMode.HTML
        )

    elif sub_cmd == "subscribe":
        if len(args) < 2:
            await update.effective_message.reply_text("Usage: /market subscribe <strategy_id>")
            return
        try:
            strat_id = int(args[1])
        except ValueError:
            await update.effective_message.reply_text("Invalid strategy ID.")
            return
        strategy = get_strategy(strat_id)
        if not strategy:
            await update.effective_message.reply_text("Strategy not found.")
            return
        curr = get_settings(uid)
        prev = _json.dumps({
            "symbol": curr.get("symbol"), "take_profit": curr.get("take_profit"),
            "stop_loss": curr.get("stop_loss"), "tp_mode": curr.get("tp_mode"),
            "sl_mode": curr.get("sl_mode"), "trade_mode": curr.get("trade_mode"),
        })
        # Apply strategy settings
        for k, v in [
            ("symbol", strategy["symbol"]), ("take_profit", strategy["take_profit"]),
            ("stop_loss", strategy["stop_loss"]), ("tp_mode", strategy["tp_mode"]),
            ("sl_mode", strategy["sl_mode"]), ("trade_mode", strategy["trade_mode"]),
        ]:
            if v is not None:
                update_setting(uid, k, v)
        subscribe_strategy(uid, strat_id, prev)
        write_audit(uid, "strategy_subscribed", {"strategy_id": strat_id})
        await update.effective_message.reply_text(
            f"✅ <b>Subscribed to #{strat_id} {strategy['name']}</b>\n\n"
            f"Your settings have been updated to match this strategy.\n"
            f"Use /market unsubscribe to revert to your previous settings.",
            parse_mode=ParseMode.HTML
        )

    elif sub_cmd == "unsubscribe":
        import json as _json
        prev_json = unsubscribe_strategy(uid)
        if not prev_json:
            await update.effective_message.reply_text("You're not subscribed to any strategy.")
            return
        try:
            prev = _json.loads(prev_json)
            for k, v in prev.items():
                if v is not None:
                    update_setting(uid, k, v)
        except Exception:
            pass
        write_audit(uid, "strategy_unsubscribed", {})
        await update.effective_message.reply_text(
            "✅ <b>Unsubscribed</b>\n\nYour previous settings have been restored.",
            parse_mode=ParseMode.HTML
        )

    elif sub_cmd == "leaderboard":
        rows = get_strategy_leaderboard(10)
        if not rows:
            await update.effective_message.reply_text("No strategies with 30-day data yet.")
            return
        lines = ["🏆 <b>Strategy Leaderboard (30d PnL)</b>\n"]
        for i, row in enumerate(rows, 1):
            sign = "+" if row["pnl_30d"] >= 0 else ""
            wr   = (row["wins_30d"] / row["trades_30d"] * 100) if row["trades_30d"] else 0
            lines.append(
                f"  <b>{i}. #{row['id']} {row['name']}</b>\n"
                f"  PnL: <code>{sign}{row['pnl_30d']:.2f}</code>  "
                f"Trades: <code>{row['trades_30d']}</code>  "
                f"WR: <code>{wr:.0f}%</code>  "
                f"Subs: <code>{row['subscriber_count']}</code>"
            )
        await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── Audit Log ─────────────────────────────────────────────────────────────────

async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import json as _json
    uid  = update.effective_user.id
    if not is_admin(uid) and not has_active_access(uid):
        await update.effective_message.reply_text("🔒 Subscription required.")
        return
    logs = get_audit_log(uid if not is_admin(uid) else None, limit=20)
    if not logs:
        await update.effective_message.reply_text("📋 No audit entries yet.")
        return
    lines = ["📋 <b>Audit Log</b>\n"]
    for log in logs:
        ts = (log.get("created_at") or "")[:16]
        try:
            det = _json.loads(log.get("details") or "{}")
            detail_str = ", ".join(f"{k}={v}" for k, v in list(det.items())[:3])
        except Exception:
            detail_str = str(log.get("details", ""))[:60]
        uid_part = f"uid={log['user_id']}  " if is_admin(uid) else ""
        lines.append(
            f"  <code>{ts}</code>  {uid_part}<b>{log['event_type']}</b>\n"
            f"  <i>{detail_str}</i>"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


BUTTON_MAP.update(_build_button_map())