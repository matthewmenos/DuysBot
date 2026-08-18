"""
webhook_server.py - Lightweight HTTP server for TradingView webhook alerts.

Run this alongside main.py (as a thread, started in main.py).

TradingView Pine Script sends a POST to /tv/<token> when an alert fires.
The server validates the token, checks the user subscription, builds the
order, and enqueues it into the scheduler async queue for execution.
"""

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from config import WEBHOOK_PORT

logger = logging.getLogger(__name__)


def _require_tls_proxy():
    """
    Warn loudly if the server is exposed without a TLS reverse proxy.
    Set BEHIND_TLS_PROXY=1 in .env to suppress this warning once nginx/caddy is in place.
    """
    if not os.getenv("BEHIND_TLS_PROXY"):
        logger.warning(
            "SECURITY WARNING: Webhook server is running over plain HTTP. "
            "TradingView trade triggers are vulnerable to interception. "
            "Put an nginx/caddy TLS reverse proxy in front and set BEHIND_TLS_PROXY=1 in .env."
        )


# TradingView Webhook handler
# Runs on its own port (WEBHOOK_PORT + 1).


import json as _json
from http.server import HTTPServer as _HTTPServer

class TVWebhookHandler(BaseHTTPRequestHandler):


    """POST /tv/<token>  — TradingView Pine Script alert receiver."""

    bot_app = None   # set by main.py after Application is built

    def log_message(self, format, *args):
        pass   # suppress default access log

    def do_POST(self):
        from database import get_user_by_webhook_token, log_webhook, has_active_access
        from exchange import get_exchange
        from config import MAX_SMART_ORDERS

        if not self.path.startswith("/tv/"):
            self.send_response(404); self.end_headers()
            return

        token = self.path[4:].strip("/")
        length = int(self.headers.get("Content-Length", 0))
        raw    = self.rfile.read(length)

        try:
            payload = _json.loads(raw)
        except Exception:
            self.send_response(400); self.end_headers()
            self.wfile.write(b'{"error":"invalid JSON"}')
            return

        user = get_user_by_webhook_token(token)
        if not user:
            logger.warning(f"[TV] Unknown token: {token[:8]}…")
            log_webhook(0, token, raw.decode(), "", "", "rejected", "unknown token")
            self.send_response(401); self.end_headers()
            self.wfile.write(b'{"error":"unauthorized"}')
            return

        uid = user["user_id"]
        if not has_active_access(uid):
            log_webhook(uid, token, raw.decode(), "", "", "rejected", "no active access")
            self.send_response(403); self.end_headers()
            self.wfile.write(b'{"error":"subscription required"}')
            return

        action  = payload.get("action", "").lower()
        symbol  = (payload.get("symbol") or "").upper().strip()
        exch_id = (payload.get("exchange") or user.get("exchange", "")).strip().lower()
        try:
            amount = float(payload.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0

        # ── Validate action ───────────────────────────────────────────────────
        if action not in ("buy", "sell", "close"):
            log_webhook(uid, token, raw.decode(), action, symbol, "rejected", "unknown action")
            self.send_response(400); self.end_headers()
            self.wfile.write(b'{"error":"action must be buy/sell/close"}')
            return

        # ── Normalise symbol (BTCUSDT → BTC/USDT) ────────────────────────────
        if symbol and "/" not in symbol and symbol.endswith("USDT"):
            symbol = symbol[:-4] + "/USDT"

        # ── Validate symbol format ────────────────────────────────────────────
        import re as _re
        if symbol and not _re.match(r'^[A-Z0-9]{2,10}/[A-Z]{2,8}$', symbol):
            log_webhook(uid, token, raw.decode(), action, symbol, "rejected", "invalid symbol format")
            self.send_response(400); self.end_headers()
            self.wfile.write(b'{"error":"invalid symbol format"}')
            return

        # ── Clamp amount to sane bounds ───────────────────────────────────────
        # Minimum $1, maximum $100,000 per single webhook trade
        if amount < 0:
            amount = 0.0
        MAX_WEBHOOK_AMOUNT = 100_000.0
        if amount > MAX_WEBHOOK_AMOUNT:
            log_webhook(uid, token, raw.decode(), action, symbol, "rejected", f"amount {amount} exceeds max")
            self.send_response(400); self.end_headers()
            self.wfile.write(b'{"error":"amount exceeds maximum allowed"}')
            return

        # ── Validate exchange ─────────────────────────────────────────────────
        from exchange import SUPPORTED_EXCHANGES
        if exch_id and exch_id not in SUPPORTED_EXCHANGES:
            exch_id = user.get("exchange", "")

        # Rate limit: 1 webhook trade per 10 seconds per user
        import time
        _tv_rate: dict = getattr(TVWebhookHandler, "_rate", {})
        TVWebhookHandler._rate = _tv_rate
        now = time.time()
        if now - _tv_rate.get(uid, 0) < 10:
            log_webhook(uid, token, raw.decode(), action, symbol, "rejected", "rate limited")
            self.send_response(429); self.end_headers()
            self.wfile.write(b'{"error":"rate limited"}')
            return
        _tv_rate[uid] = now

        try:
            exchange_obj = get_exchange(exch_id, user["api_key"],
                                        user["api_secret"], user.get("api_pass", ""))
        except Exception as e:
            log_webhook(uid, token, raw.decode(), action, symbol, "error", str(e))
            self.send_response(500); self.end_headers()
            self.wfile.write(b'{"error":"exchange connection failed"}')
            return

        # Enqueue into scheduler's async queue
        if TVWebhookHandler.bot_app:
            import asyncio
            from scheduler import _tv_trade_queue
            job = {
                "user_id":      uid,
                "action":       action,
                "symbol":       symbol,
                "amount":       amount,
                "exchange_obj": exchange_obj,
                "token":        token,
            }
            try:
                loop = TVWebhookHandler.bot_app.bot._application.bot_data.get("_loop")
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(_tv_trade_queue.put(job), loop)
                else:
                    _tv_trade_queue.put_nowait(job)
            except Exception:
                _tv_trade_queue.put_nowait(job)

        log_webhook(uid, token, raw.decode(), action, symbol, "queued", "")
        self.send_response(200); self.end_headers()
        self.wfile.write(b'{"status":"queued"}')


TV_WEBHOOK_PORT = WEBHOOK_PORT + 1

def run_tv_webhook_server():
    _require_tls_proxy()
    server = _HTTPServer(("0.0.0.0", TV_WEBHOOK_PORT), TVWebhookHandler)
    logger.info(f"TradingView webhook server running on port {TV_WEBHOOK_PORT}")
    server.serve_forever()
