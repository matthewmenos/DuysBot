"""
paystack.py - Paystack payment integration
Handles initializing transactions and verifying payments.
Docs: https://paystack.com/docs/api/
"""

import hashlib
import hmac
import json
import logging
import requests
from config import PAYSTACK_SECRET_KEY, PLAN_PRICES, BOT_WEBHOOK_URL

logger = logging.getLogger(__name__)

PAYSTACK_BASE = "https://api.paystack.co"
HEADERS = {
    "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
    "Content-Type": "application/json",
}



def initialize_transaction(user_id: int, email: str, months: int = 1) -> dict:
    """
    Create a Paystack payment link for the user.
    Returns {"status": True, "authorization_url": "...", "reference": "..."}
    """
    reference = f"ctb_{user_id}_{months}mo_{_now_ts()}"
    payload = {
        "email":        email,
        "amount":       int(PLAN_PRICES.get(months, PLAN_PRICES[1]) * 100),
        "currency":     "USD",
        "reference":    reference,
        "callback_url": f"{BOT_WEBHOOK_URL}/paystack/callback",
        "metadata": {
            "user_id":  user_id,
            "months":   months,
            "cancel_action": "abandon",
        },
        "channels": ["card", "mobile_money", "bank_transfer"],
    }
    try:
        resp = requests.post(f"{PAYSTACK_BASE}/transaction/initialize", json=payload, headers=HEADERS, timeout=10)
        data = resp.json()
        if data.get("status"):
            return {
                "ok":               True,
                "authorization_url": data["data"]["authorization_url"],
                "reference":         data["data"]["reference"],
            }
        return {"ok": False, "message": data.get("message", "Unknown error")}
    except Exception as e:
        logger.error(f"Paystack init error: {e}")
        return {"ok": False, "message": str(e)}


def verify_transaction(reference: str) -> dict:
    """
    Verify a transaction by reference.
    Returns {"ok": True, "paid": True/False, "user_id": ..., "months": ..., "amount": ...}
    """
    try:
        resp = requests.get(f"{PAYSTACK_BASE}/transaction/verify/{reference}", headers=HEADERS, timeout=10)
        data = resp.json()
        if not data.get("status"):
            return {"ok": False, "message": data.get("message", "Verify failed")}

        tx   = data["data"]
        paid = tx["status"] == "success"
        meta = tx.get("metadata", {})
        return {
            "ok":      True,
            "paid":    paid,
            "user_id": int(meta.get("user_id", 0)),
            "months":  int(meta.get("months", 1)),
            "amount":  tx["amount"] / 100,
            "currency": tx["currency"],
            "reference": reference,
        }
    except Exception as e:
        logger.error(f"Paystack verify error: {e}")
        return {"ok": False, "message": str(e)}


def validate_webhook_signature(payload_bytes: bytes, signature: str, secret: str) -> bool:
    """Verify Paystack webhook HMAC-SHA512 signature."""
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def _now_ts() -> int:
    import time
    return int(time.time())
