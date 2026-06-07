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
    bot_status, user_lookup, timezone_cmd,
    arbitrage_cmd,
    # New feature handlers
    paper_cmd, paper_reset_cmd, paper_stats_cmd,
    backtest_cmd, analytics_cmd, webdash_cmd,
    webhook_cmd, webhook_new_cmd, webhook_log_cmd,
    dca_cmd, dca_stats_cmd,
    grid_cmd, grid_status_cmd, grid_stop_cmd,
    twap_cmd, iceberg_cmd, oco_cmd, smart_orders_cmd,
    market_cmd, audit_cmd,
)
from alerts_handlers import setalert, myalerts, delalert
from scheduler import start_scheduler
from persistence import build_persistence, restore_in_memory_state, sync_to_bot_data

from logger_setup import setup_logging, init_error_reporter
logger = setup_logging()


def main():
    persistence = build_persistence()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .build()
    )

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
    app.add_handler(CommandHandler("arbitrage",    arbitrage_cmd))
    app.add_handler(CommandHandler("paper",        paper_cmd))
    app.add_handler(CommandHandler("paper_reset",  paper_reset_cmd))
    app.add_handler(CommandHandler("paper_stats",  paper_stats_cmd))
    app.add_handler(CommandHandler("analytics",    analytics_cmd))
    app.add_handler(CommandHandler("backtest",     backtest_cmd))
    app.add_handler(CommandHandler("webdash",      webdash_cmd))
    app.add_handler(CommandHandler("webhook",      webhook_cmd))
    app.add_handler(CommandHandler("webhook_new",  webhook_new_cmd))
    app.add_handler(CommandHandler("webhook_log",  webhook_log_cmd))
    app.add_handler(CommandHandler("dca",          dca_cmd))
    app.add_handler(CommandHandler("dca_stats",    dca_stats_cmd))
    app.add_handler(CommandHandler("grid",         grid_cmd))
    app.add_handler(CommandHandler("grid_status",  grid_status_cmd))
    app.add_handler(CommandHandler("grid_stop",    grid_stop_cmd))
    app.add_handler(CommandHandler("twap",         twap_cmd))
    app.add_handler(CommandHandler("iceberg",      iceberg_cmd))
    app.add_handler(CommandHandler("oco",          oco_cmd))
    app.add_handler(CommandHandler("smart_orders", smart_orders_cmd))
    app.add_handler(CommandHandler("market",       market_cmd))
    app.add_handler(CommandHandler("audit",        audit_cmd))

    from telegram import BotCommand

    BOT_COMMANDS = [
        BotCommand("start",       "🚀 Start / onboarding"),
        BotCommand("balance",     "💰 View your exchange balance"),
        BotCommand("start_trade", "▶️ Enable auto-trading"),
        BotCommand("stop_trade",  "⏹ Disable auto-trading"),
        BotCommand("positions",   "📂 View open positions"),
        BotCommand("arbitrage",   "⚡ Scan for arbitrage opportunities"),
        BotCommand("signals",     "📡 Recent trading signals"),
        BotCommand("pnl",         "📊 Profit & loss summary"),
        BotCommand("history",     "📜 Trade history"),
        BotCommand("settings",    "⚙️ Bot settings"),
        BotCommand("exchanges",   "🔗 Manage exchange API keys"),
        BotCommand("setalert",    "🔔 Set a price alert"),
        BotCommand("myalerts",    "📋 View your price alerts"),
        BotCommand("delalert",    "🗑 Delete a price alert"),
        BotCommand("summary",     "📈 Market summary"),
        BotCommand("health",      "🩺 Bot health check"),
        BotCommand("subscribe",   "💳 Subscribe / manage plan"),
        BotCommand("mystatus",    "👤 Your subscription status"),
        BotCommand("referral",    "🎁 Referral programme"),
        BotCommand("paper",        "🧪 Paper trading (simulated)"),
        BotCommand("paper_stats",  "🧪 Paper trading performance"),
        BotCommand("analytics",    "📊 Full performance analytics"),
        BotCommand("backtest",     "📈 Backtest signal strategy"),
        BotCommand("webdash",      "🌐 Web dashboard link"),
        BotCommand("webhook",      "📡 TradingView webhook URL"),
        BotCommand("webhook_new",  "📡 Regenerate webhook token"),
        BotCommand("webhook_log",  "📡 Webhook activity log"),
        BotCommand("dca",          "🔄 Dollar-cost averaging plans"),
        BotCommand("grid",         "🔲 Grid trading plans"),
        BotCommand("twap",         "⏱ TWAP order"),
        BotCommand("iceberg",      "🧊 Iceberg order"),
        BotCommand("oco",          "🎯 OCO order"),
        BotCommand("smart_orders", "⚙️ View smart orders"),
        BotCommand("market",       "🏪 Strategy marketplace"),
        BotCommand("audit",        "📋 Audit log"),
        BotCommand("support",      "🆘 Contact support"),
        BotCommand("help",         "❓ Help & command list"),
    ]

    async def _post_init(application):
        """
        Runs once after the bot is fully initialised and persistence has been loaded.
        1. Re-populate in-memory dicts from persisted bot_data (restart safety).
        2. Register the Telegram command menu.
        """
        restore_in_memory_state(application.bot_data)
        await application.bot.set_my_commands(BOT_COMMANDS)
        logger.info("Telegram command menu registered.")

    app.post_init = _post_init

    async def _sync_state(context):
        """Flush live scheduler dicts back into bot_data every 2 min so
        PicklePersistence can write them to disk on its 30-second cycle."""
        sync_to_bot_data(context.application.bot_data)

    app.job_queue.run_repeating(_sync_state, interval=120, first=30)

    # Callback and message handlers
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start scheduler for auto-trading
    app.job_queue.run_repeating(start_scheduler, interval=60, first=10)

    # Start Paystack webhook server in background
    from webhook_server import run_webhook_server, run_tv_webhook_server, TVWebhookHandler
    from web_app import run_web_app
    from threading import Thread

    Thread(target=run_webhook_server, daemon=True).start()

    # TradingView webhook server (Paystack port + 1)
    tv_thread = Thread(target=run_tv_webhook_server, daemon=True)
    tv_thread.start()

    # Web dashboard (port 5000)
    Thread(target=run_web_app, daemon=True).start()

    # Wire bot app reference so TV handler can enqueue trades
    TVWebhookHandler.bot_app = app

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
