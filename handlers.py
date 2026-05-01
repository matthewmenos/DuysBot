"""
handlers.py - All Telegram command and callback handlers
"""

import logging
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import ADMIN_IDS, POPULAR_SYMBOLS, QUOTE_CURRENCY
from database import (
    get_user, upsert_user, grant_user, get_settings, update_setting,
    get_trade_history, get_open_trades, get_pnl_summary,
    save_exchange_creds, save_support_message, get_all_trading_users,
    close_trade, init_db,
    has_active_access, get_subscription_status, activate_subscription,
    record_pending_payment, get_subscription_history, grant_user_lifetime,
    get_all_subscribers,
)
from paystack import initialize_transaction, verify_transaction
from exchange import (
    get_exchange, fetch_balance, fetch_ticker, fetch_ohlcv,
    SUPPORTED_EXCHANGES, EXCHANGE_LABELS, close_all_positions
)
from strategy import generate_signal

logger = logging.getLogger(__name__)

# Initialise DB on first import
init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────
# require_granted, require_creds, is_admin, is_granted live in utils.py
# to avoid circular imports with alerts_handlers.py
from utils import require_granted, require_creds, is_admin, is_granted



# ── Persistent Reply Keyboards ────────────────────────────────────────────────

def get_main_menu(uid: int) -> ReplyKeyboardMarkup:
    """Bottom persistent keyboard — shown to all authorised users."""
    rows = [
        [KeyboardButton("💰 Balance"),    KeyboardButton("📊 Chart")],
        [KeyboardButton("▶️ Start Trade"), KeyboardButton("⏹ Stop Trade")],
        [KeyboardButton("📜 History"),    KeyboardButton("📈 PnL")],
        [KeyboardButton("💊 Health"),     KeyboardButton("📋 Summary")],
        [KeyboardButton("🔔 Set Alert"),  KeyboardButton("🔕 My Alerts")],
        [KeyboardButton("⚙️ Settings"),   KeyboardButton("🏦 Exchanges")],
        [KeyboardButton("💳 Subscribe"),  KeyboardButton("🪪 My Status")],
        [KeyboardButton("📩 Support"),    KeyboardButton("❓ Help")],
    ]
    if uid in ADMIN_IDS:
        rows.append([KeyboardButton("👥 Subscribers"), KeyboardButton("🚨 Panic")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, persistent=True)


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
        keyboard = [
            [InlineKeyboardButton("💳 Subscribe — $12/month", callback_data="subscribe")],
        ]
        await update.message.reply_text(
            f"👋 Welcome to <b>CryptoTradeBot</b>!\n\n"
            f"🔒 <b>Subscription Required</b>\n\n"
            f"Get started with a <b>$12/month</b> subscription via Paystack.\n"
            f"Accepted: card, mobile money, bank transfer.\n\n"
            f"Or ask an admin to grant you lifetime access.\n"
            f"Your Telegram ID: <code>{user.id}</code>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        return

    s = get_settings(user.id)
    open_t = get_open_trades(user.id)
    trading_status = "🟢 Trading ON" if s and s["trading_on"] else "🔴 Trading OFF"

    dashboard = (
        f"👋 Welcome back, <b>{user.first_name}</b>!\n\n"
        f"🤖 <b>CryptoTradeBot</b> is active.\n\n"
        f"Status:  {trading_status}\n"
        f"Symbol:  <code>{s['symbol'] if s else 'BTC/USDT'}</code>\n"
        f"Open trades: <code>{len(open_t)}</code>\n\n"
        f"Use the buttons below to navigate:"
    )

    # Quick-action inline buttons
    inline_kb = [
        [InlineKeyboardButton("💰 Balance",     callback_data="cmd_balance"),
         InlineKeyboardButton("📊 Chart",       callback_data="cmd_chart")],
        [InlineKeyboardButton("▶️ Start Trade",  callback_data="cmd_start_trade"),
         InlineKeyboardButton("⏹ Stop Trade",   callback_data="cmd_stop_trade")],
        [InlineKeyboardButton("📜 History",     callback_data="cmd_history"),
         InlineKeyboardButton("📈 PnL",         callback_data="cmd_pnl")],
        [InlineKeyboardButton("💊 Health",      callback_data="cmd_health"),
         InlineKeyboardButton("📋 Summary",     callback_data="cmd_summary")],
        [InlineKeyboardButton("⚙️ Settings",    callback_data="cmd_settings"),
         InlineKeyboardButton("❓ Help",         callback_data="cmd_help")],
    ]

    await update.message.reply_text(
        dashboard,
        reply_markup=InlineKeyboardMarkup(inline_kb),
        parse_mode=ParseMode.HTML
    )
    # Also attach the persistent reply keyboard
    await update.message.reply_text(
        "⬇️ <b>Quick menu always available below:</b>",
        reply_markup=get_main_menu(user.id),
        parse_mode=ParseMode.HTML
    )


# ── /balance ──────────────────────────────────────────────────────────────────

@require_granted
@require_creds
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    user = get_user(uid)
    msg  = await update.message.reply_text("⏳ Fetching balance...")

    try:
        exch = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
        bal  = fetch_balance(exch)
        label = EXCHANGE_LABELS.get(user["exchange"], user["exchange"].title())

        lines = [f"💰 <b>Balance — {label}</b>\n"]
        for coin, data in bal.items():
            lines.append(f"  <b>{coin}</b>:  Free: <code>{data['free']}</code>  |  Total: <code>{data['total']}</code>")
        if not bal:
            lines.append("  No assets found.")

        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(f"❌ Failed to fetch balance:\n<code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /start_trade ──────────────────────────────────────────────────────────────

@require_granted
@require_creds
async def start_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    update_setting(uid, "trading_on", 1)
    s = get_settings(uid)
    await update.message.reply_text(
        f"✅ <b>Auto-trading ENABLED</b>\n\n"
        f"📈 Symbol: <code>{s['symbol']}</code>\n"
        f"💵 Amount per trade: <code>{s['trade_amount']} USDT</code>\n"
        f"🎯 Take Profit: <code>{s['take_profit']}%</code>\n"
        f"🛑 Stop Loss: <code>{s['stop_loss']}%</code>\n\n"
        "The bot will scan for signals every minute.",
        parse_mode=ParseMode.HTML
    )


# ── /stop_trade ───────────────────────────────────────────────────────────────

@require_granted
async def stop_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    update_setting(uid, "trading_on", 0)
    await update.message.reply_text(
        "⏹ <b>Auto-trading DISABLED</b>\n\nOpen positions are NOT closed automatically.\nUse /health to review them.",
        parse_mode=ParseMode.HTML
    )


# ── /settings ─────────────────────────────────────────────────────────────────

AWAITING_SETTING = {}  # user_id -> which field we're waiting on

@require_granted
async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    s   = get_settings(uid)
    u   = get_user(uid)
    label = EXCHANGE_LABELS.get(u["exchange"] if u else "binance", "Not set")

    keyboard = [
        [InlineKeyboardButton("🔑 Connect Exchange", callback_data="set_exchange")],
        [InlineKeyboardButton("🎯 Take Profit %",    callback_data="set_tp"),
         InlineKeyboardButton("🛑 Stop Loss %",      callback_data="set_sl")],
        [InlineKeyboardButton("💵 Trade Amount",     callback_data="set_amount"),
         InlineKeyboardButton("🪙 Symbol",           callback_data="set_symbol")],
    ]
    await update.message.reply_text(
        f"⚙️ <b>Your Settings</b>\n\n"
        f"Exchange:     <code>{label}</code>\n"
        f"Symbol:       <code>{s['symbol']}</code>\n"
        f"Trade Amount: <code>{s['trade_amount']} USDT</code>\n"
        f"Take Profit:  <code>{s['take_profit']}%</code>\n"
        f"Stop Loss:    <code>{s['stop_loss']}%</code>\n\n"
        "Tap a button to update:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


# ── /history ──────────────────────────────────────────────────────────────────

@require_granted
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    trades = get_trade_history(uid, limit=10)
    if not trades:
        await update.message.reply_text("📜 No trade history yet.")
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
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── /chart ────────────────────────────────────────────────────────────────────

@require_granted
@require_creds
async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    user = get_user(uid)
    s    = get_settings(uid)
    msg  = await update.message.reply_text("⏳ Fetching chart data & signal...")

    try:
        exch   = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
        ohlcv  = fetch_ohlcv(exch, s["symbol"], "1h", 100)
        ticker = fetch_ticker(exch, s["symbol"])
        signal = generate_signal(ohlcv, s["symbol"])

        action_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal["action"], "⚪")
        ind = signal["indicators"]

        text = (
            f"📊 <b>Chart — {s['symbol']} (1H)</b>\n\n"
            f"💲 Price:       <code>${ticker['last']:,.4f}</code>\n"
            f"📈 24h Change:  <code>{ticker['change_pct']:+.2f}%</code>\n"
            f"📦 Volume:      <code>{ticker['volume']:,.0f} USDT</code>\n\n"
            f"<b>Indicators</b>\n"
            f"RSI:    <code>{ind['rsi']}</code>\n"
            f"EMA9:   <code>{ind['ema9']}</code>\n"
            f"EMA21:  <code>{ind['ema21']}</code>\n"
            f"MACD:   <code>{ind['macd']}</code>\n"
            f"BB Up:  <code>{ind['bb_up']}</code>\n"
            f"BB Low: <code>{ind['bb_low']}</code>\n"
            f"News:   <code>{ind['news']}</code>\n\n"
            f"{action_emoji} <b>Signal: {signal['action']}</b> (Confidence: {signal['confidence']}%)\n"
            f"📝 {signal['reason'][:300]}"
        )
        await msg.edit_text(text, parse_mode=ParseMode.HTML)
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
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


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
        await update.message.reply_text(
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
                f"   TP: <code>{s['take_profit']}%</code> | SL: <code>{s['stop_loss']}%</code>"
            )
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /summary ──────────────────────────────────────────────────────────────────

@require_granted
async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    trades = get_trade_history(uid, limit=50)
    row    = get_pnl_summary(uid)

    if not trades:
        await update.message.reply_text("📋 No completed trades to summarise.")
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
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /exchanges ────────────────────────────────────────────────────────────────

async def exchanges(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["🏦 <b>Supported Exchanges</b>\n"]
    for key, label in EXCHANGE_LABELS.items():
        lines.append(f"  {label} — <code>{key}</code>")
    lines.append("\nUse /settings → Connect Exchange to link your account.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── /support ──────────────────────────────────────────────────────────────────

@require_granted
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = " ".join(context.args) if context.args else ""
    if not msg:
        await update.message.reply_text(
            "📩 <b>Contact Support</b>\nUsage: <code>/support Your message here</code>",
            parse_mode=ParseMode.HTML
        )
        return
    save_support_message(uid, msg)
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"📩 <b>Support Request</b>\nFrom: <code>{uid}</code>\n\n{msg}",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
    await update.message.reply_text("✅ Message sent to admin. We'll get back to you soon!")


# ── /grant (admin only) ───────────────────────────────────────────────────────

async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("🚫 Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: <code>/grant &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML)
        return
    try:
        target_id = int(context.args[0])
        upsert_user(target_id)   # ensure row exists before granting
        grant_user(target_id)
        await update.message.reply_text(
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
        await update.message.reply_text("❌ Invalid user ID.")


# ── /panic (admin only) ───────────────────────────────────────────────────────

async def panic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("🚫 Admin only.")
        return

    await update.message.reply_text("🚨 <b>PANIC MODE</b> — Closing ALL open trades...", parse_mode=ParseMode.HTML)
    users = get_all_trading_users()
    total_closed = 0

    for user in users:
        open_t = get_open_trades(user["user_id"])
        if not open_t:
            continue
        try:
            exch    = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
            results = close_all_positions(exch, open_t)
            for r, t in zip(results, open_t):
                if r["status"] == "closed":
                    pnl_pct = (r["price"] - t["entry_price"]) / t["entry_price"] * 100
                    pnl_usd = t["amount"] * pnl_pct / 100
                    close_trade(t["id"], r["price"], round(pnl_usd, 4), round(pnl_pct, 2))
                    total_closed += 1
            # Disable trading for user
            update_setting(user["user_id"], "trading_on", 0)
            try:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text="🚨 <b>PANIC:</b> Admin has closed all your trades and stopped auto-trading.",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Panic close failed for user {user['user_id']}: {e}")

    await update.message.reply_text(
        f"✅ Panic complete. <code>{total_closed}</code> trades closed across all users.",
        parse_mode=ParseMode.HTML
    )



# ── /subscribe ────────────────────────────────────────────────────────────────

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    user = update.effective_user
    upsert_user(uid, user.username or "")

    status = get_subscription_status(uid)
    if status["access"]:
        type_label = "Lifetime (Admin Grant)" if status["type"] == "lifetime" else f"Active until {status['expiry']}"
        await update.message.reply_text(
            f"✅ <b>You already have active access</b>\n\nPlan: <code>{type_label}</code>\n\nEnjoy trading! 🚀",
            parse_mode=ParseMode.HTML
        )
        return

    keyboard = [
        [InlineKeyboardButton("1 Month — $12",            callback_data="pay_1")],
        [InlineKeyboardButton("3 Months — $34 (save $2)", callback_data="pay_3")],
        [InlineKeyboardButton("6 Months — $65 (save $7)", callback_data="pay_6")],
    ]
    await update.message.reply_text(
        "💳 <b>Subscribe to CryptoTradeBot</b>\n\n"
        "Choose a plan to get started:\n\n"
        "  🔹 <b>1 Month</b>  — $12.00\n"
        "  🔹 <b>3 Months</b> — $34.00 <i>(save $2)</i>\n"
        "  🔹 <b>6 Months</b> — $65.00 <i>(save $7)</i>\n\n"
        "Accepted: 💳 Card • 📱 Mobile Money • 🏦 Bank Transfer\n"
        "Powered by <b>Paystack</b>",
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
        await update.message.reply_text(
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

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── /subscribers (admin only) ─────────────────────────────────────────────────

async def subscribers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("🚫 Admin only.")
        return

    rows = get_all_subscribers()
    if not rows:
        await update.message.reply_text("No subscribers yet.")
        return

    lines = [f"👥 <b>All Subscribers ({len(rows)})</b>\n"]
    for r in rows:
        label = "♾ Lifetime" if r["granted"] == 1 else f"📅 {(r['sub_expiry'] or '')[:10]}"
        name  = f"@{r['username']}" if r["username"] else str(r["user_id"])
        lines.append(f"  {name} — {label}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)



# ── /help ─────────────────────────────────────────────────────────────────────

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 <b>CryptoTradeBot Commands</b>\n\n"
        "💰 /balance       — View exchange balance\n"
        "▶️  /start_trade   — Enable auto-trading\n"
        "⏹  /stop_trade    — Disable auto-trading\n"
        "⚙️  /settings      — Configure exchange & parameters\n"
        "📜 /history       — Last 10 closed trades\n"
        "📊 /chart         — Current signal & indicators\n"
        "📈 /pnl           — Profit & Loss summary\n"
        "💊 /health        — Monitor open trades live\n"
        "📋 /summary       — Trade cycle summary\n"
        "🏦 /exchanges     — List supported exchanges\n"
        "📩 /support       — Message admin\n"
        "💳 /subscribe     — Subscribe for $12/month\n"
        "🪪  /mystatus      — View your subscription status\n\n"
        "<b>🔔 Price Alerts</b>\n"
        "/setalert SYMBOL above|below PRICE — Set a price alert\n"
        "/myalerts — View all your active alerts\n"
        "/delalert &lt;id&gt; — Remove an alert\n\n"
        "<i>Admin only:</i>\n"
        "🔑 /grant &lt;id&gt;    — Grant lifetime access\n"
        "👥 /subscribers   — List all subscribers\n"
        "🚨 /panic         — Emergency close all trades\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)



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


# ── Callback & Message Handlers ───────────────────────────────────────────────

from utils import PENDING_INPUT  # shared across handlers and alerts_handlers


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data

    if data == "subscribe":
        keyboard = [
            [InlineKeyboardButton("1 Month — $12",            callback_data="pay_1")],
            [InlineKeyboardButton("3 Months — $34 (save $2)", callback_data="pay_3")],
            [InlineKeyboardButton("6 Months — $65 (save $7)", callback_data="pay_6")],
        ]
        await query.message.reply_text(
            "💳 <b>Subscribe to CryptoTradeBot</b>\n\n"
            "Choose a plan:\n\n"
            "  🔹 <b>1 Month</b>  — $12.00\n"
            "  🔹 <b>3 Months</b> — $34.00 <i>(save $2)</i>\n"
            "  🔹 <b>6 Months</b> — $65.00 <i>(save $7)</i>\n\n"
            "Accepted: 💳 Card • 📱 Mobile Money • 🏦 Bank Transfer",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    elif data.startswith("pay_"):
        months = int(data.split("_")[1])
        prices = {1: 12.00, 3: 34.00, 6: 65.00}
        amount = prices.get(months, 12.00)

        PENDING_INPUT[uid] = {"field": "pay_email", "months": months, "amount": amount}
        await query.message.reply_text(
            f"💳 <b>{months} Month{'s' if months > 1 else ''} Plan — ${amount:.2f}</b>\n\n"
            f"Please send your <b>email address</b> so we can generate your payment link.",
            parse_mode=ParseMode.HTML
        )

    # ── Direct command callbacks (inline dashboard buttons) ──────────────────
    elif data in BUTTON_MAP:
        await BUTTON_MAP[data](update, context)

    # Settings flows
    elif data == "set_tp":
        PENDING_INPUT[uid] = {"field": "take_profit"}
        await query.message.reply_text("🎯 Enter new Take Profit % (e.g. <code>2.5</code>):", parse_mode=ParseMode.HTML)
    elif data == "set_sl":
        PENDING_INPUT[uid] = {"field": "stop_loss"}
        await query.message.reply_text("🛑 Enter new Stop Loss % (e.g. <code>1.0</code>):", parse_mode=ParseMode.HTML)
    elif data == "set_amount":
        PENDING_INPUT[uid] = {"field": "trade_amount"}
        await query.message.reply_text("💵 Enter trade amount in USDT (e.g. <code>20</code>):", parse_mode=ParseMode.HTML)
    elif data == "set_symbol":
        await _show_symbol_picker(query.message, uid)
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
        buttons = [[InlineKeyboardButton(label, callback_data=f"exch_{key}")] for key, label in EXCHANGE_LABELS.items()]
        await query.message.reply_text("🏦 Choose your exchange:", reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("exch_"):
        exch_id = data[5:]
        PENDING_INPUT[uid] = {"field": "api_key", "exchange": exch_id}
        passphrase_note = " (OKX requires a passphrase too)" if exch_id == "okx" else ""
        await query.message.reply_text(
            f"🔑 Selected: <b>{EXCHANGE_LABELS[exch_id]}</b>{passphrase_note}\n\n"
            f"Please send your <b>API Key</b>:",
            parse_mode=ParseMode.HTML
        )


# Reply keyboard button label → callback_data mapping
REPLY_BUTTON_COMMANDS = {
    "💰 Balance":      "cmd_balance",
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
}


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text.strip()

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
            val = float(text)
            update_setting(uid, "take_profit", val)
            await update.message.reply_text(f"✅ Take Profit set to <code>{val}%</code>", parse_mode=ParseMode.HTML)
            del PENDING_INPUT[uid]
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number.")

    elif field == "stop_loss":
        try:
            val = float(text)
            update_setting(uid, "stop_loss", val)
            await update.message.reply_text(f"✅ Stop Loss set to <code>{val}%</code>", parse_mode=ParseMode.HTML)
            del PENDING_INPUT[uid]
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number.")

    elif field == "trade_amount":
        try:
            val = float(text)
            update_setting(uid, "trade_amount", val)
            await update.message.reply_text(f"✅ Trade Amount set to <code>{val} USDT</code>", parse_mode=ParseMode.HTML)
            del PENDING_INPUT[uid]
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number.")

    elif field == "pay_email":
        email  = text.strip()
        months = pi.get("months", 1)
        amount = pi.get("amount", 12.00)
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            await update.message.reply_text("❌ That doesn't look like a valid email. Please try again.")
            return

        await update.message.reply_text("⏳ Generating your payment link...")
        result = initialize_transaction(uid, email, months)
        del PENDING_INPUT[uid]

        if result["ok"]:
            record_pending_payment(uid, result["reference"], months, amount, "USD")
            keyboard = [[InlineKeyboardButton("💳 Pay Now", url=result["authorization_url"])]]
            await update.message.reply_text(
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
            await update.message.reply_text(
                f"❌ Could not generate payment link:\n<code>{result['message']}</code>\n\nPlease try /subscribe again.",
                parse_mode=ParseMode.HTML
            )

    elif field == "symbol_search":
        raw    = text.upper().strip().replace("/", "").replace("USDT", "")
        symbol = f"{raw}/USDT"
        user   = get_user(uid)
        if not user or not user["api_key"]:
            update_setting(uid, "symbol", symbol)
            await update.message.reply_text(
                f"✅ Symbol set to <b>{symbol}</b> (exchange not connected yet — pair not validated).",
                parse_mode=ParseMode.HTML
            )
            del PENDING_INPUT[uid]
            return
        # Validate against live exchange markets
        await update.message.reply_text(f"⏳ Validating <code>{symbol}</code> on your exchange...", parse_mode=ParseMode.HTML)
        try:
            exch = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
            markets = exch.load_markets()
            if symbol in markets:
                update_setting(uid, "symbol", symbol)
                ticker = exch.fetch_ticker(symbol)
                price  = ticker["last"]
                chg    = ticker.get("percentage", 0) or 0
                await update.message.reply_text(
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
                await update.message.reply_text(
                    f"❌ <b>{symbol}</b> not available on your exchange.\n\n"
                    f"{hint}\n\n"
                    f"Tap /settings → 🪙 Symbol to try again.",
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Validation failed: <code>{e}</code>", parse_mode=ParseMode.HTML)
        del PENDING_INPUT[uid]

    elif field == "alert_symbol":
        # User typed alert inline e.g. "BTC/USDT above 70000 my note"
        import alerts_handlers as _ah_mod  # lazy: safe inside function
        parts = text.strip().split()
        if len(parts) < 3:
            await update.message.reply_text(
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
        PENDING_INPUT[uid]["api_key"] = text
        PENDING_INPUT[uid]["field"]   = "api_secret"
        await update.message.reply_text("🔐 Now send your <b>API Secret</b>:", parse_mode=ParseMode.HTML)

    elif field == "api_secret":
        PENDING_INPUT[uid]["api_secret"] = text
        exch_id = PENDING_INPUT[uid].get("exchange", "binance")
        if exch_id == "okx":
            PENDING_INPUT[uid]["field"] = "api_pass"
            await update.message.reply_text("🔑 OKX requires a <b>Passphrase</b>. Please send it:", parse_mode=ParseMode.HTML)
        else:
            save_exchange_creds(uid, exch_id, PENDING_INPUT[uid]["api_key"], text)
            await update.message.reply_text(
                f"✅ <b>{EXCHANGE_LABELS[exch_id]}</b> connected successfully!",
                parse_mode=ParseMode.HTML
            )
            del PENDING_INPUT[uid]

    elif field == "api_pass":
        exch_id = PENDING_INPUT[uid].get("exchange", "okx")
        save_exchange_creds(uid, exch_id, PENDING_INPUT[uid]["api_key"], PENDING_INPUT[uid]["api_secret"], text)
        await update.message.reply_text(
            f"✅ <b>{EXCHANGE_LABELS[exch_id]}</b> connected with passphrase!",
            parse_mode=ParseMode.HTML
        )
        del PENDING_INPUT[uid]


# ── BUTTON_MAP: wire callback_data keys to real handler functions ──────────────
# These are imported lazily after all functions are defined.
# Both inline dashboard buttons (cmd_*) and reply keyboard buttons use this map.

async def _run_cmd(handler_fn, update: Update, context):
    """
    Adapter: works for both CallbackQuery updates and Message updates.
    Builds a fake update.message if called from a callback so handlers
    that call update.message.reply_text() work transparently.
    """
    if update.callback_query:
        # Patch message reference so handlers can call update.message.reply_text
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
        "cmd_setalert":    _make_cmd(_ah.setalert),
        "cmd_myalerts":    _make_cmd(_ah.myalerts),
        "cmd_subscribers": _make_cmd(subscribers),
        "cmd_panic":       _make_cmd(panic),
    }


# Build the map at module load time
BUTTON_MAP.update(_build_button_map())
