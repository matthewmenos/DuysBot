"""
onboarding.py - Guided setup flow for new users after subscribing/trialing.
Walks user through: connect exchange → set symbol → set TP/SL → start trading.
"""

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from exchange import EXCHANGE_LABELS, PASSPHRASE_EXCHANGES

logger = logging.getLogger(__name__)

ONBOARD_STEPS = ["exchange", "symbol", "tp_sl", "trade_mode", "done"]


async def start_onboarding(context, user_id: int, first_name: str = ""):
    """Send the first onboarding step to a new user."""
    keyboard = [
        [InlineKeyboardButton(f"{v}", callback_data=f"ob_exch_{k}")]
        for k, v in EXCHANGE_LABELS.items()
    ]
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"🎉 <b>Welcome{', ' + first_name if first_name else ''}!</b>\n\n"
            f"Let's get you set up in <b>4 quick steps</b>.\n\n"
            f"<b>Step 1 of 4 — Connect Your Exchange</b>\n\n"
            f"Which exchange do you trade on?\n"
            f"(You'll enter your API keys after selecting)"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


async def onboard_step_symbol(context, user_id: int):
    """Step 2 — choose a symbol."""
    from config import POPULAR_SYMBOLS
    rows = []
    for i in range(0, len(POPULAR_SYMBOLS[:8]), 2):
        pair = POPULAR_SYMBOLS[i:i+2]
        rows.append([InlineKeyboardButton(s, callback_data=f"ob_sym_{s}") for s in pair])
    rows.append([InlineKeyboardButton("🔍 Search any coin", callback_data="ob_sym_search")])

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "✅ Exchange connected!\n\n"
            "<b>Step 2 of 4 — Choose Your Trading Symbol</b>\n\n"
            "Which coin would you like to trade?\n"
            "You can always change this later in /settings."
        ),
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )


async def onboard_step_tpsl(context, user_id: int):
    """Step 3 — set TP/SL."""
    keyboard = [
        [InlineKeyboardButton("Conservative  (TP 1.5% / SL 0.8%)", callback_data="ob_tpsl_conservative")],
        [InlineKeyboardButton("Balanced      (TP 2.0% / SL 1.0%)", callback_data="ob_tpsl_balanced")],
        [InlineKeyboardButton("Aggressive    (TP 3.0% / SL 1.5%)", callback_data="ob_tpsl_aggressive")],
        [InlineKeyboardButton("⚙️ Set manually",                    callback_data="ob_tpsl_manual")],
    ]
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "✅ Symbol selected!\n\n"
            "<b>Step 3 of 4 — Risk Profile</b>\n\n"
            "Choose a Take Profit / Stop Loss preset:\n\n"
            "  🟢 <b>Conservative</b> — Smaller gains, tighter stops. Safer.\n"
            "  🟡 <b>Balanced</b>     — Good balance of risk and reward.\n"
            "  🔴 <b>Aggressive</b>   — Higher targets, wider stops. More risk.\n\n"
            "You can always fine-tune these in /settings."
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


async def onboard_step_trade_mode(context, user_id: int):
    """Step 4 — choose trading mode."""
    keyboard = [
        [InlineKeyboardButton("🤖 Auto Trade  (recommended for beginners)", callback_data="ob_mode_auto")],
        [InlineKeyboardButton("👆 Manual Trade  (I decide when to buy)",     callback_data="ob_mode_manual")],
    ]
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "✅ Risk profile set!\n\n"
            "<b>Step 4 of 4 — Trading Mode</b>\n\n"
            "<b>🤖 Auto Trade</b>\n"
            "Bot scans signals every 60s and trades automatically.\n"
            "Best for hands-off trading.\n\n"
            "<b>👆 Manual Trade</b>\n"
            "Bot arms itself and waits. You tap <b>🟢 Start Now</b> to buy.\n"
            "TP/SL still close trades automatically.\n"
            "Best if you want full control."
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


async def onboard_done(context, user_id: int):
    """Final step — congratulate and activate."""
    keyboard = [
        [InlineKeyboardButton("▶️ Start Trading Now", callback_data="cmd_start_trade")],
        [InlineKeyboardButton("⚙️ Review Settings",   callback_data="cmd_settings")],
    ]
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "🎉 <b>You're all set!</b>\n\n"
            "Your bot is ready to trade.\n\n"
            "📌 Quick tips:\n"
            "  • Use /dashboard for a full overview\n"
            "  • Use /chart to see the current signal\n"
            "  • Use /health to monitor open trades\n"
            "  • Use /panic to close everything instantly\n\n"
            "Tap <b>Start Trading Now</b> to go live! 🚀"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
