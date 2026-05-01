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
    grant, panic, handle_message, handle_callback,
    subscribe, mystatus, subscribers
)
from alerts_handlers import setalert, myalerts, delalert
from scheduler import start_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


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

    logger.info("CryptoTradeBot is starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
