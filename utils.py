"""
utils.py - Shared utilities used across handlers and alerts_handlers.
Kept in a separate module to avoid circular imports.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from config import ADMIN_IDS
from database import has_active_access, get_user


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_granted(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    user = get_user(user_id)
    return bool(user and user["granted"])


def require_granted(func):
    """Decorator: gate commands behind active access (lifetime grant OR paid sub)."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if is_admin(uid) or has_active_access(uid):
            return await func(update, context)
        keyboard = [[InlineKeyboardButton("💳 Subscribe — $12/month", callback_data="subscribe")]]
        await update.effective_message.reply_text(
            "🔒 <b>Access Required</b>\n\n"
            "You need an active subscription to use CryptoTradeBot.\n\n"
            "  • <b>Pay $12/month</b> via Paystack (card, mobile money, bank transfer)\n"
            "  • Ask an admin to grant you lifetime access\n\n"
            f"Your Telegram ID: <code>{uid}</code>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        return
    return wrapper


def require_creds(func):
    """Decorator: ensure user has exchange credentials set up."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid  = update.effective_user.id
        user = get_user(uid)
        if not user or not user["api_key"]:
            await update.effective_message.reply_text(
                "⚙️ <b>No exchange connected</b>\n"
                "Use /settings to connect your exchange API keys first.",
                parse_mode=ParseMode.HTML
            )
            return
        return await func(update, context)
    return wrapper


# Shared mutable state for multi-step user input flows
PENDING_INPUT: dict = {}
