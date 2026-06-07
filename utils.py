"""
utils.py - Shared utilities used across handlers and alerts_handlers.
Kept in a separate module to avoid circular imports.

PENDING_INPUT is now a PersistedDict — every write is mirrored into
bot_data[K_PENDING_INPUT] so PicklePersistence saves it on its 30-second
cycle.  This means in-progress multi-step flows (API key entry, settings
changes, etc.) survive bot restarts.
"""

import html as _html_mod

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from config import ADMIN_IDS
from database import has_active_access, get_user


def esc(text: str) -> str:
    """HTML-escape a user-supplied string before embedding in Telegram HTML messages."""
    return _html_mod.escape(str(text) if text is not None else "")


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
            "You need an active subscription to use this bot.\n\n"
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
        if not user or not user.get("api_key") or not user.get("exchange"):
            keyboard = [[InlineKeyboardButton("🔑 Connect Exchange", callback_data="set_exchange")]]
            await update.effective_message.reply_text(
                "⚙️ <b>No Exchange Connected</b>\n\n"
                "You haven't connected an exchange yet.\n"
                "Tap the button below to get started:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            return
        return await func(update, context)
    return wrapper


# ── PersistedDict — writes through to bot_data automatically ──────────────────

class PersistedDict(dict):
    """
    A dict subclass that mirrors every write/delete into a backing store
    (bot_data[key]) so PicklePersistence captures it automatically.

    Usage is identical to a plain dict. The backing store is optional;
    if not attached (e.g. during import) the object behaves as a normal dict.
    """
    _backing: dict | None = None
    _backing_key: str = ""

    def attach(self, backing: dict, key: str) -> None:
        """Wire this dict to bot_data[key]. Called once in post_init."""
        self._backing = backing
        self._backing_key = key
        # Initialise backing store with whatever is already in this dict
        if key not in backing:
            backing[key] = dict(self)

    def _push(self) -> None:
        if self._backing is not None:
            self._backing[self._backing_key] = dict(self)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._push()

    def __delitem__(self, key):
        super().__delitem__(key)
        self._push()

    def pop(self, key, *args):
        result = super().pop(key, *args)
        self._push()
        return result

    def clear(self):
        super().clear()
        self._push()

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self._push()


# Shared mutable state for multi-step user input flows.
# Populated from bot_data on restart via persistence.restore_in_memory_state().
PENDING_INPUT: PersistedDict = PersistedDict()
