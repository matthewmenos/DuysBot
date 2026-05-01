"""
alerts_handlers.py - Price alert command handlers
/setalert  - set a price target alert
/myalerts  - list all active alerts
/delalert  - delete an alert by ID
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database import (
    add_price_alert, get_active_alerts, delete_alert,
    get_user
)
from exchange import get_exchange, fetch_ticker
from utils import require_granted

logger = logging.getLogger(__name__)


# ── /setalert ─────────────────────────────────────────────────────────────────

@require_granted
async def setalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      /setalert BTC/USDT above 70000
      /setalert ETH/USDT below 3000 buy the dip
    """
    uid  = update.effective_user.id
    args = context.args

    if len(args) < 3:
        from utils import PENDING_INPUT
        uid2 = update.effective_user.id
        PENDING_INPUT[uid2] = {"field": "alert_symbol"}
        await update.message.reply_text(
            "🔔 <b>Set a Price Alert</b>\n\n"
            "Reply with your alert in this format:\n"
            "  <code>SYMBOL above PRICE</code>\n"
            "  <code>SYMBOL below PRICE note</code>\n\n"
            "<b>Examples:</b>\n"
            "  <code>BTC/USDT above 70000</code>\n"
            "  <code>ETH/USDT below 3000 buy the dip</code>\n"
            "  <code>SOL/USDT above 200</code>",
            parse_mode=ParseMode.HTML
        )
        return

    symbol    = args[0].upper()
    condition = args[1].lower()

    if condition not in ("above", "below"):
        await update.message.reply_text(
            "❌ Condition must be <code>above</code> or <code>below</code>.\n\n"
            "Example: <code>/setalert BTC/USDT above 70000</code>",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        target = float(args[2].replace(",", ""))
    except ValueError:
        await update.message.reply_text(
            "❌ Price must be a number.\n"
            "Example: <code>/setalert BTC/USDT above 70000</code>",
            parse_mode=ParseMode.HTML
        )
        return

    note = " ".join(args[3:]) if len(args) > 3 else ""

    # Validate symbol & check current price against exchange
    user       = get_user(uid)
    price_line = ""
    warn_line  = ""

    if user and user["api_key"]:
        try:
            exch    = get_exchange(user["exchange"], user["api_key"], user["api_secret"], user["api_pass"])
            ticker  = fetch_ticker(exch, symbol)
            current = ticker["last"]
            price_line = f"Current price: <code>${current:,.6f}</code>\n"
            already_met = (
                (condition == "above" and current >= target) or
                (condition == "below" and current <= target)
            )
            if already_met:
                warn_line = "⚠️ <i>Note: target is already met at current price!</i>\n"
        except Exception as e:
            logger.warning(f"setalert price check failed for {uid}: {e}")

    alert_id = add_price_alert(uid, symbol, target, condition, note)
    arrow    = "📈" if condition == "above" else "📉"

    await update.message.reply_text(
        f"🔔 <b>Alert Set!</b>\n\n"
        f"Symbol:    <code>{symbol}</code>\n"
        f"Condition: {arrow} price goes <b>{condition}</b> <code>${target:,.6f}</code>\n"
        f"{price_line}"
        f"{warn_line}"
        f"{'📝 Note: <i>' + note + '</i>' + chr(10) if note else ''}"
        f"Alert ID:  <code>#{alert_id}</code>\n\n"
        f"I'll notify you the moment the target is hit. 🔔\n"
        f"Use /myalerts to manage your alerts.",
        parse_mode=ParseMode.HTML
    )


# ── /myalerts ─────────────────────────────────────────────────────────────────

@require_granted
async def myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    alerts = get_active_alerts(uid)

    if not alerts:
        await update.message.reply_text(
            "🔕 <b>No Active Alerts</b>\n\n"
            "Use /setalert to create a price alert.\n\n"
            "<b>Example:</b>\n"
            "  <code>/setalert BTC/USDT above 70000</code>\n"
            "  <code>/setalert ETH/USDT below 3000</code>",
            parse_mode=ParseMode.HTML
        )
        return

    lines = [f"🔔 <b>Your Active Alerts ({len(alerts)})</b>\n"]
    for a in alerts:
        arrow = "📈" if a["condition"] == "above" else "📉"
        note  = f"\n   📝 <i>{a['note']}</i>" if a["note"] else ""
        lines.append(
            f"{arrow} <b>#{a['id']}</b>  {a['symbol']}  {a['condition']}  "
            f"<code>${a['target_price']:,.6f}</code>{note}\n"
            f"   <i>Set: {a['created_at'][:16]}</i>"
        )

    lines.append("\n🗑 Remove an alert: <code>/delalert &lt;id&gt;</code>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── /delalert ─────────────────────────────────────────────────────────────────

@require_granted
async def delalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "🗑 <b>Delete an Alert</b>\n\n"
            "Usage: <code>/delalert &lt;alert_id&gt;</code>\n"
            "Find your alert IDs with /myalerts.",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        alert_id = int(context.args[0].lstrip("#"))
    except ValueError:
        await update.message.reply_text("❌ Invalid alert ID. Use /myalerts to see your IDs.")
        return

    if delete_alert(alert_id, uid):
        await update.message.reply_text(
            f"✅ Alert <code>#{alert_id}</code> deleted successfully.",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            f"❌ Alert <code>#{alert_id}</code> not found or doesn't belong to you.",
            parse_mode=ParseMode.HTML
        )
