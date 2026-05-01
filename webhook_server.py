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
        user = get_user(user_id)
        username = f"@{user['username']}" if user and user["username"] else str(user_id)
        await bot.send_message(
            chat_id=admin_id,
            text=(
                f"💰 <b>New Subscription Payment</b>\n\n"
                f"User:   {username} (<code>{user_id}</code>)\n"
                f"Plan:   <code>{months} month{'s' if months > 1 else ''}</code>\n"
                f"Amount: <code>{currency} {amount:.2f}</code>\n"
                f"Ref:    <code>{ref}</code>"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to notify admin {admin_id}: {e}")


def run_webhook_server():
    server = HTTPServer(("0.0.0.0", WEBHOOK_PORT), PaystackWebhookHandler)
    logger.info(f"Paystack webhook server running on port {WEBHOOK_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_webhook_server()
