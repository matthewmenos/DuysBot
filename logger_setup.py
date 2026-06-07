"""
logger_setup.py - Centralised logging with trade-specific log file and
                  admin Telegram error reporting.
"""

import logging
import sys
import traceback
from functools import wraps

# ── Loggers ───────────────────────────────────────────────────────────────────
def setup_logging():
    """Call once at startup to configure all loggers."""
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    # Ensure the console stream supports UTF-8 and safely replaces unsupported chars.
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # Root logger — INFO to stdout + bot.log
    file_handler = logging.FileHandler("bot.log", encoding="utf-8", errors="replace")
    file_handler.setFormatter(logging.Formatter(fmt))

    console_stream = open(sys.stderr.fileno(), mode="w", encoding="utf-8", errors="replace", closefd=False)
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setFormatter(logging.Formatter(fmt))

    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler],
    )

    # Trades logger — dedicated file, every signal + order
    trade_handler = logging.FileHandler("trades.log")
    trade_handler.setFormatter(logging.Formatter(fmt))
    trade_logger = logging.getLogger("trades")
    trade_logger.addHandler(trade_handler)
    trade_logger.setLevel(logging.INFO)
    trade_logger.propagate = False

    return logging.getLogger(__name__)


def get_trade_logger():
    return logging.getLogger("trades")


# ── Admin error reporter ───────────────────────────────────────────────────────
_bot_ref = None
_admin_ids = []


def init_error_reporter(bot, admin_ids: list):
    """Call after bot is built so the reporter has a send reference."""
    global _bot_ref, _admin_ids
    _bot_ref   = bot
    _admin_ids = admin_ids


async def report_error_to_admin(context, error: Exception, source: str = ""):
    """Send a formatted error traceback to all admin Telegram IDs."""
    global _bot_ref, _admin_ids
    bot = _bot_ref or (context.bot if context else None)
    if not bot or not _admin_ids:
        return

    tb   = traceback.format_exc()
    text = (
        f"🚨 <b>Bot Error</b>\n\n"
        f"<b>Source:</b> <code>{source or 'unknown'}</code>\n"
        f"<b>Error:</b>  <code>{str(error)[:200]}</code>\n\n"
        f"<pre>{tb[:600]}</pre>"
    )
    for admin_id in _admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception:
            pass


def log_signal(symbol: str, action: str, confidence: int, reason: str, user_id: int = 0):
    """Write a signal evaluation to trades.log."""
    get_trade_logger().info(
        f"SIGNAL | user={user_id} | {symbol} | {action} | conf={confidence}% | {reason[:120]}"
    )


def log_trade_open(user_id: int, symbol: str, side: str, price: float, amount: float, exchange: str, order_id: str):
    # Omit order_id and exact amount to limit information in log files.
    # Full trade details are in the encrypted database with audit trail.
    get_trade_logger().info(
        f"OPEN   | user={user_id} | {symbol} | {side.upper()} | exchange={exchange}"
    )


def log_trade_close(user_id: int, symbol: str, reason: str, entry: float, exit_p: float, pnl: float, pnl_pct: float):
    # Log direction of PnL only, not the exact amount.
    outcome = "PROFIT" if pnl >= 0 else "LOSS"
    get_trade_logger().info(
        f"CLOSE  | user={user_id} | {symbol} | {reason} | outcome={outcome} ({pnl_pct:+.2f}%)"
    )
