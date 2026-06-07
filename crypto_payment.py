"""
crypto_payment.py - Multi-network USDT payment verification
Supports: Aptos, TRON (TRC-20), BSC (BEP-20)

Free APIs used:
  Aptos  → https://fullnode.mainnet.aptoslabs.com/v1  (no key)
  TRON   → https://api.trongrid.io  (free key at trongrid.io)
  BSC    → https://api.bscscan.com  (free key at bscscan.com/apis)
"""

import hashlib
import logging
import time
import requests

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF = [1, 3, 7]  # seconds between attempts


def _get_with_retry(url: str, headers: dict = None, params: dict = None, timeout: int = 12) -> requests.Response:
    """GET with up to _MAX_RETRIES attempts and exponential-ish backoff."""
    last_exc = None
    for attempt, wait in enumerate(_RETRY_BACKOFF[:_MAX_RETRIES]):
        try:
            resp = requests.get(url, headers=headers or {}, params=params, timeout=timeout)
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < _MAX_RETRIES - 1:
                logger.debug(f"[PAYMENT] Request failed (attempt {attempt+1}), retrying in {wait}s: {e}")
                time.sleep(wait)
    raise last_exc


from config import PLAN_PRICES as PLAN_PRICES_USDT  # {1: price, 3: price, 6: price}

# ── Network constants ─────────────────────────────────────────────────────────
APTOS_NODE_URL      = "https://fullnode.mainnet.aptoslabs.com/v1"
USDT_APTOS_TYPE     = "0xf22bede237a07cfa3b5e7e1be539b213e41d3b35::asset::USDT"

TRONGRID_BASE       = "https://api.trongrid.io"
USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

BSCSCAN_BASE        = "https://api.bscscan.com/api"
USDT_BEP20_CONTRACT = "0x55d398326f99059ff775485246999027b3197955"


# ═════════════════════════════════════════════════════════════════════════════
# Public entry point
# ═════════════════════════════════════════════════════════════════════════════

def verify_usdt_tx(
    network: str,
    tx_hash: str,
    expected_amount_usdt: float,
    months: int,
    recipient_address: str,
    api_key: str = "",
) -> dict:
    """
    Verify a USDT payment on the given network.

    Args:
        network:              "aptos" | "tron" | "bsc"
        tx_hash:              Transaction hash string
        expected_amount_usdt: Amount the user should have sent
        months:               Subscription months (for info only)
        recipient_address:    Your wallet on that network
        api_key:              TronGrid or BscScan API key (optional for Aptos)

    Returns:
        {"valid": True,  "amount": float, "from_address": str, "confirmed": True}
        {"valid": False, "error": str,    "confirmed": bool}
    """
    network = network.lower()
    if network == "aptos":
        return _verify_aptos(tx_hash, expected_amount_usdt, months, recipient_address)
    elif network == "tron":
        return _verify_tron(tx_hash, expected_amount_usdt, months, recipient_address, api_key)
    elif network == "bsc":
        return _verify_bsc(tx_hash, expected_amount_usdt, months, recipient_address, api_key)
    else:
        return {"valid": False, "confirmed": False, "error": f"Unknown network: {network}"}


def get_payment_instructions(network: str, months: int, wallet_address: str) -> dict:
    """Return display info for the payment screen."""
    amount = PLAN_PRICES_USDT.get(months, 12.00)
    meta = {
        "aptos": {"label": "Aptos",        "token": "USDT (LayerZero)", "explorer": "https://explorer.aptoslabs.com"},
        "tron":  {"label": "TRON (TRC-20)","token": "USDT (TRC-20)",    "explorer": "https://tronscan.org"},
        "bsc":   {"label": "BSC (BEP-20)", "token": "USDT (BEP-20)",    "explorer": "https://bscscan.com"},
    }.get(network, {"label": network, "token": "USDT", "explorer": ""})

    return {
        "network":  meta["label"],
        "token":    meta["token"],
        "address":  wallet_address,
        "amount":   amount,
        "months":   months,
        "explorer": meta["explorer"],
    }


# ═════════════════════════════════════════════════════════════════════════════
# Aptos verifier
# ═════════════════════════════════════════════════════════════════════════════

def _verify_aptos(tx_hash: str, expected: float, months: int, recipient: str) -> dict:
    tx_hash = tx_hash.strip()
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    clean = tx_hash[2:]
    if len(clean) != 64 or not all(c in "0123456789abcdefABCDEF" for c in clean):
        return {"valid": False, "confirmed": False,
                "error": "Invalid Aptos TX hash. Should be 0x followed by 64 hex characters."}

    try:
        resp = _get_with_retry(f"{APTOS_NODE_URL}/transactions/by_hash/{tx_hash}", timeout=12)
    except Exception as e:
        return {"valid": False, "confirmed": False, "error": f"Network error: {e}"}

    if resp.status_code == 404:
        return {"valid": False, "confirmed": False,
                "error": "Transaction not found. It may still be processing — wait a moment and try again."}
    if resp.status_code != 200:
        return {"valid": False, "confirmed": False,
                "error": f"Aptos node returned status {resp.status_code}. Try again shortly."}

    tx = resp.json()
    if not tx.get("success"):
        return {"valid": False, "confirmed": True,
                "error": f"Transaction failed on-chain (status: {tx.get('vm_status', 'unknown')})."}

    from_address = tx.get("sender", "unknown")
    amount_usdt  = _parse_aptos_usdt_amount(tx)

    if amount_usdt == 0:
        return {"valid": False, "confirmed": True,
                "error": "No USDT transfer detected. Ensure you sent USDT (LayerZero) on Aptos."}

    if recipient and recipient.lower() not in str(tx).lower():
        return {"valid": False, "confirmed": True,
                "error": f"Payment sent to wrong address.\nExpected: <code>{recipient}</code>"}

    if amount_usdt < expected * 0.98:
        return {"valid": False, "confirmed": True,
                "error": f"Insufficient amount.\nExpected: {expected:.2f} USDT\nReceived: {amount_usdt:.4f} USDT"}

    return {"valid": True, "confirmed": True, "amount": round(amount_usdt, 4), "from_address": from_address}


def _parse_aptos_usdt_amount(tx: dict) -> float:
    """Extract USDT amount from Aptos transaction events/changes."""
    for event in tx.get("events", []):
        etype = event.get("type", "")
        if USDT_APTOS_TYPE in etype or ("USDT" in etype and "coin_store" in etype.lower()):
            data = event.get("data", {})
            raw  = data.get("amount", {})
            val  = raw.get("value", raw) if isinstance(raw, dict) else raw
            try:
                return int(val) / 1_000_000
            except (TypeError, ValueError):
                pass
    for change in tx.get("changes", []):
        if USDT_APTOS_TYPE in str(change):
            data = change.get("data", {}).get("data", {})
            raw  = (data.get("coin") or {}).get("value", 0)
            try:
                return int(raw) / 1_000_000
            except (TypeError, ValueError):
                pass
    return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# TRON verifier
# ═════════════════════════════════════════════════════════════════════════════

def _verify_tron(tx_hash: str, expected: float, months: int, recipient: str, api_key: str) -> dict:
    tx_hash = tx_hash.strip()
    if len(tx_hash) != 64 or not all(c in "0123456789abcdefABCDEF" for c in tx_hash):
        return {"valid": False, "confirmed": False,
                "error": "Invalid TRON TX hash. Should be 64 hex characters (no 0x prefix)."}

    headers = {"Accept": "application/json"}
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key

    try:
        resp = _get_with_retry(f"{TRONGRID_BASE}/v1/transactions/{tx_hash}", headers=headers, timeout=12)
    except Exception as e:
        return {"valid": False, "confirmed": False, "error": f"Network error reaching TRON: {e}"}

    if resp.status_code == 404:
        return {"valid": False, "confirmed": False,
                "error": "Transaction not found on TRON. Wait a moment and try again."}
    if resp.status_code != 200:
        return {"valid": False, "confirmed": False,
                "error": f"TronGrid returned status {resp.status_code}. Try again shortly."}

    data  = resp.json()
    tx    = (data.get("data") or [{}])[0]
    if not tx:
        return {"valid": False, "confirmed": False, "error": "Empty transaction data — may still be pending."}

    confirmed = (tx.get("ret") or [{}])[0].get("contractRet") == "SUCCESS"
    if not confirmed:
        status = (tx.get("ret") or [{}])[0].get("contractRet", "PENDING")
        return {"valid": False, "confirmed": False,
                "error": f"Transaction not confirmed yet (status: {status}). Please wait and retry."}

    raw_data     = tx.get("raw_data", {})
    contract     = (raw_data.get("contract") or [{}])[0]
    param        = contract.get("parameter", {}).get("value", {})
    to_addr      = _tron_hex_to_base58(param.get("to_address", ""))
    from_addr    = _tron_hex_to_base58(param.get("owner_address", ""))
    contract_hex = _tron_hex_to_base58(param.get("contract_address", ""))

    if contract_hex and contract_hex != USDT_TRC20_CONTRACT:
        return {"valid": False, "confirmed": True,
                "error": "Not a USDT TRC-20 transaction. Ensure you sent USDT on the TRON network."}

    if recipient and to_addr.lower() != recipient.lower():
        return {"valid": False, "confirmed": True,
                "error": f"Wrong recipient.\nExpected: <code>{recipient}</code>\nReceived: <code>{to_addr}</code>"}

    amount_usdt = _parse_tron_usdt_amount(tx)
    if amount_usdt < expected * 0.98:
        return {"valid": False, "confirmed": True,
                "error": f"Insufficient amount.\nExpected: {expected:.2f} USDT\nReceived: {amount_usdt:.4f} USDT"}

    return {"valid": True, "confirmed": True, "amount": round(amount_usdt, 4), "from_address": from_addr}


def _parse_tron_usdt_amount(tx: dict) -> float:
    for log in tx.get("trc20", []):
        for entry in (log if isinstance(log, list) else [log]):
            if isinstance(entry, dict) and "amount" in entry:
                try:
                    return int(entry["amount"]) / 1_000_000
                except (TypeError, ValueError):
                    pass
    return 0.0


def _tron_hex_to_base58(hex_addr: str) -> str:
    if not hex_addr:
        return ""
    try:
        import base58
        if not hex_addr.startswith("41"):
            hex_addr = "41" + hex_addr
        raw   = bytes.fromhex(hex_addr)
        check = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
        return base58.b58encode(raw + check).decode()
    except ImportError:
        return hex_addr
    except Exception:
        return hex_addr


# ═════════════════════════════════════════════════════════════════════════════
# BSC verifier
# ═════════════════════════════════════════════════════════════════════════════

def _verify_bsc(tx_hash: str, expected: float, months: int, recipient: str, api_key: str) -> dict:
    tx_hash = tx_hash.strip().lower()
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    if len(tx_hash) != 66:
        return {"valid": False, "confirmed": False,
                "error": "Invalid BSC TX hash. Should be 0x followed by 64 hex characters."}

    params = {
        "module":  "proxy",
        "action":  "eth_getTransactionReceipt",
        "txhash":  tx_hash,
        "apikey":  api_key or "YourApiKeyToken",
    }

    try:
        resp = _get_with_retry(BSCSCAN_BASE, params=params, timeout=12)
    except Exception as e:
        return {"valid": False, "confirmed": False, "error": f"Network error reaching BSCScan: {e}"}

    if resp.status_code != 200:
        return {"valid": False, "confirmed": False,
                "error": f"BSCScan returned status {resp.status_code}. Try again."}

    data   = resp.json()
    result = data.get("result")
    if not result:
        return {"valid": False, "confirmed": False,
                "error": "Transaction not found or still pending. Wait for BSC confirmation and retry."}

    # Check transaction status (0x1 = success)
    status = result.get("status", "0x0")
    if status != "0x1":
        return {"valid": False, "confirmed": True,
                "error": "BSC transaction failed on-chain. No payment was made."}

    from_address = result.get("from", "unknown")
    to_address   = (result.get("to") or "").lower()

    # USDT transfers appear in logs as ERC-20 Transfer events
    amount_usdt  = _parse_bsc_usdt_amount(result.get("logs", []), recipient)

    if amount_usdt == 0:
        return {"valid": False, "confirmed": True,
                "error": "No USDT BEP-20 transfer detected in this transaction. "
                         "Ensure you sent USDT on the BSC (BNB Smart Chain) network."}

    if amount_usdt < expected * 0.98:
        return {"valid": False, "confirmed": True,
                "error": f"Insufficient amount.\nExpected: {expected:.2f} USDT\nReceived: {amount_usdt:.4f} USDT"}

    return {"valid": True, "confirmed": True, "amount": round(amount_usdt, 4), "from_address": from_address}


def _parse_bsc_usdt_amount(logs: list, recipient: str) -> float:
    """
    Parse ERC-20 Transfer event from BSC transaction logs.
    Transfer(address,address,uint256) topic:
    0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef
    """
    TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    recipient_norm = (recipient or "").lower().replace("0x", "").zfill(64)

    for log in logs:
        # Check it's from the USDT contract
        contract = (log.get("address") or "").lower()
        if USDT_BEP20_CONTRACT.lower() not in contract:
            continue
        topics = log.get("topics", [])
        if not topics or topics[0].lower() != TRANSFER_TOPIC:
            continue
        # topics[2] = recipient address (padded to 32 bytes)
        if len(topics) >= 3:
            to_in_log = topics[2].lower().replace("0x", "").lstrip("0")
            recip_short = recipient_norm.lstrip("0")
            if recipient and to_in_log != recip_short:
                continue
        # data = hex-encoded uint256 transfer amount (18 decimals for USDT BEP-20)
        raw_hex = log.get("data", "0x0")
        try:
            raw_int = int(raw_hex, 16)
            return raw_int / (10 ** 18)
        except (ValueError, TypeError):
            continue
    return 0.0
