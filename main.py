"""
CryptoTradeBot - Telegram Crypto Trading Bot
Entry point: starts the bot and scheduler
"""

import asyncio
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from config import BOT_TOKEN
from handlers import (
    start, balance, start_trade, stop_trade, settings, history,
    chart, pnl, help_cmd, health, summary, exchanges, support,
    grant, panic, close_all_cmd, reply_user,
    handle_message, handle_callback,
    subscribe, mystatus, subscribers,
    dashboard, broadcast, referral,
    positions, export_trades, signals_history,
    bot_status, user_lookup, timezone_cmd
)
from alerts_handlers import setalert, myalerts, delalert
from scheduler import start_scheduler

from logger_setup import setup_logging, init_error_reporter
logger = setup_logging()


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("start_trade", start_trade))
    app.add_handler(CommandHandler("stop_trade", stop_trade))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("chart", chart))
    app.add_handler(CommandHandler("pnl", pnl))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("exchanges", exchanges))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("panic", panic))
    app.add_handler(CommandHandler("close", close_all_cmd))
    app.add_handler(CommandHandler("reply", reply_user))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("referral", referral))
    app.add_handler(CommandHandler("positions", positions))
    app.add_handler(CommandHandler("export", export_trades))
    app.add_handler(CommandHandler("signals", signals_history))
    app.add_handler(CommandHandler("status", bot_status))
    app.add_handler(CommandHandler("user", user_lookup))
    app.add_handler(CommandHandler("timezone", timezone_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("mystatus", mystatus))
    app.add_handler(CommandHandler("subscribers", subscribers))

    app.add_handler(CommandHandler("setalert", setalert))
    app.add_handler(CommandHandler("myalerts", myalerts))
    app.add_handler(CommandHandler("delalert", delalert))

    # Callback and message handlers
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start scheduler for auto-trading
    app.job_queue.run_repeating(start_scheduler, interval=60, first=10)

    # Start Paystack webhook server in background
    from webhook_server import run_webhook_server
    from threading import Thread
    Thread(target=run_webhook_server, daemon=True).start()

    # Initialise error reporter so scheduler can DM admins on errors
    from config import ADMIN_IDS as _ADMIN_IDS
    init_error_reporter(app.bot, _ADMIN_IDS)

    # Add encryption key check
    from encryption import is_configured as _enc_ok
    if not _enc_ok():
        logger.warning("⚠️  ENCRYPTION_KEY not set — API keys stored unencrypted. See encryption.py for setup.")

    logger.info("CryptoTradeBot is starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
