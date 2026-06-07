"""
webhook_server.py - Lightweight HTTP server for Paystack payment webhooks.
Run this alongside main.py (as a separate process or thread).

Paystack sends a POST to /paystack/webhook when a payment is confirmed.
We verify the signature, activate the user's subscription, and notify them via Telegram.

Run:
    python webhook_server.py
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from config import PAYSTACK_WEBHOOK_SECRET, WEBHOOK_PORT, BOT_TOKEN
from database import activate_subscription, get_user
from telegram import Bot

logger = logging.getLogger(__name__)
bot = Bot(token=BOT_TOKEN)


class PaystackWebhookHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        logger.info(f"Webhook: {format % args}")

    def do_POST(self):
        if self.path not in ("/paystack/webhook", "/paystack/callback"):
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        # ── Verify signature (webhook only, not callback redirect) ────────────
        if self.path == "/paystack/webhook":
            sig = self.headers.get("x-paystack-signature", "")
            if PAYSTACK_WEBHOOK_SECRET and not _verify_sig(body, sig):
                logger.warning("Paystack webhook: invalid signature")
                self.send_response(401)
                self.end_headers()
                return

        try:
            event = json.loads(body)
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

        # Handle event asynchronously
        Thread(target=_handle_event, args=(event,), daemon=True).start()

    def do_GET(self):
        """Paystack redirects user here after payment — show a simple thank-you page."""
        if self.path.startswith("/paystack/callback"):
            html = b"""<!DOCTYPE html>
<html>
<head><title>CryptoTradeBot</title>
<style>body{font-family:sans-serif;text-align:center;padding:60px;background:#0d1117;color:#e6edf3;}
h1{color:#3fb950;}a{color:#58a6ff;}</style></head>
<body>
<h1>&#10003; Payment Received!</h1>
<p>Your subscription is being activated. Head back to Telegram and send /start.</p>
<p><small>If your access isn't active within 2 minutes, contact <a href="https://t.me/">support</a>.</small></p>
</body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html)
        else:
            self.send_response(404)
            self.end_headers()


def _verify_sig(body: bytes, signature: str) -> bool:
    expected = hmac.new(PAYSTACK_WEBHOOK_SECRET.encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def _handle_event(event: dict):
    """Process charge.success webhook event."""
    try:
        if event.get("event") != "charge.success":
            return

        data     = event["data"]
        meta     = data.get("metadata", {})
        user_id  = int(meta.get("user_id", 0))
        months   = int(meta.get("months", 1))
        amount   = data["amount"] / 100
        currency = data["currency"]
        ref      = data["reference"]

        if not user_id:
            logger.warning(f"Webhook: no user_id in metadata for ref {ref}")
            return

        # Activate subscription in DB
        expiry = activate_subscription(user_id, months)
        logger.info(f"Subscription activated: user={user_id} months={months} expiry={expiry} ref={ref}")

        # Notify user via Telegram
        asyncio.run(_notify_user(
            user_id, months, amount, currency, expiry, ref
        ))

        # Notify admins
        from config import ADMIN_IDS
        for admin_id in ADMIN_IDS:
            asyncio.run(_notify_admin(admin_id, user_id, months, amount, currency, ref))

    except Exception as e:
        logger.error(f"Webhook handler error: {e}")


async def _notify_user(user_id, months, amount, currency, expiry, ref):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"🎉 <b>Subscription Activated!</b>\n\n"
                f"Plan:     <code>{months} month{'s' if months > 1 else ''}</code>\n"
                f"Amount:   <code>{currency} {amount:.2f}</code>\n"
                f"Expires:  <code>{expiry}</code>\n"
                f"Ref:      <code>{ref}</code>\n\n"
                f"You now have full access to CryptoTradeBot.\n"
                f"Use /start to get started! 🚀"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to notify user {user_id}: {e}")


async def _notify_admin(admin_id, user_id, months, amount, currency, ref):
    try:
        import html as _html
        user = get_user(user_id)
        raw_username = f"@{user['username']}" if user and user["username"] else str(user_id)
        username = _html.escape(raw_username)
        await bot.send_message(
            chat_id=admin_id,
            text=(
                f"💰 <b>New Subscription Payment</b>\n\n"
                f"User:   {username} (<code>{user_id}</code>)\n"
                f"Plan:   <code>{months} month{'s' if months > 1 else ''}</code>\n"
                f"Amount: <code>{_html.escape(currency)} {amount:.2f}</code>\n"
                f"Ref:    <code>{_html.escape(str(ref))}</code>"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to notify admin {admin_id}: {e}")


def run_webhook_server():
    _require_tls_proxy()
    server = HTTPServer(("0.0.0.0", WEBHOOK_PORT), PaystackWebhookHandler)
    logger.info(f"Paystack webhook server running on port {WEBHOOK_PORT}")
    server.serve_forever()


def _require_tls_proxy():
    """
    Warn loudly if the server is exposed without a TLS reverse proxy.
    Set BEHIND_TLS_PROXY=1 in .env to suppress this warning once nginx/caddy is in place.
    """
    if not os.getenv("BEHIND_TLS_PROXY"):
        logger.warning(
            "SECURITY WARNING: Webhook server is running over plain HTTP. "
            "Payment webhooks (including Paystack signatures and trade triggers) "
            "are vulnerable to interception. "
            "Put an nginx/caddy TLS reverse proxy in front and set BEHIND_TLS_PROXY=1 in .env."
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_webhook_server()


# ── TradingView Webhook handler ───────────────────────────────────────────────
# Runs on a separate port (WEBHOOK_PORT + 1) to keep Paystack isolated.

import json as _json
from http.server import BaseHTTPRequestHandler, HTTPServer as _HTTPServer

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
