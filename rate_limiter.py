"""
rate_limiter.py - Per-user command rate limiting.
Prevents users from spamming commands or buttons, and guards
against duplicate trade orders from rapid double-taps.
"""

import time
import logging
from collections import defaultdict
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# {user_id: {command: last_call_timestamp}}
_last_calls: dict = defaultdict(dict)

# {user_id: set of active operation keys} — for deduplication locks
_active_ops: dict = defaultdict(set)

# Default cooldowns in seconds per command
COOLDOWNS = {
    "balance":     5,
    "chart":       10,
    "start_trade": 3,
    "stop_trade":  3,
    "health":      5,
    "pnl":         5,
    "history":     5,
    "summary":     5,
    "dashboard":   5,
    "setalert":    2,
    "manual_buy":  5,    # prevent double-tap buys
    "arbitrage":   30,   # scan is network-heavy; 30s cooldown
    "default":     2,
}


def rate_limited(command: str = "default", silent: bool = False):
    """
    Decorator for command handlers.
    Blocks rapid repeated calls and optionally notifies the user.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            uid      = update.effective_user.id if update.effective_user else 0
            cooldown = COOLDOWNS.get(command, COOLDOWNS["default"])
            now      = time.time()
            last     = _last_calls[uid].get(command, 0)

            if now - last < cooldown:
                remaining = round(cooldown - (now - last), 1)
                if not silent and update.effective_message:
                    try:
                        await update.effective_message.reply_text(
                            f"⏳ Please wait <b>{remaining}s</b> before using this again.",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                logger.debug(f"Rate limited uid={uid} cmd={command} ({remaining}s remaining)")
                return

            _last_calls[uid][command] = now
            return await func(update, context)
        return wrapper
    return decorator


def acquire_lock(user_id: int, op_key: str) -> bool:
    """
    Try to acquire a deduplication lock for an operation.
    Returns True if lock acquired, False if already running.
    Use for preventing duplicate buy orders.
    """
    if op_key in _active_ops[user_id]:
        return False
    _active_ops[user_id].add(op_key)
    return True


def release_lock(user_id: int, op_key: str):
    """Release a deduplication lock."""
    _active_ops[user_id].discard(op_key)


def clear_user_locks(user_id: int):
    """Clear all locks for a user (e.g. after stop_trade)."""
    _active_ops[user_id].clear()
