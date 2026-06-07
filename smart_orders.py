"""
smart_orders.py - TWAP, Iceberg, and OCO order execution.
All heavy work is synchronous; callers wrap in asyncio.to_thread.
"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


# ── Database helpers (inline to avoid circular imports) ───────────────────────

def _db():
    from database import get_conn
    return get_conn()


def create_smart_order(
    user_id:     int,
    exchange_id: str,
    order_type:  str,
    symbol:      str,
    side:        str,
    total_usdt:  float,
    params:      dict,
) -> int:
    """Insert a smart order record. Returns new id."""
    with _db() as conn:
        cur = conn.execute("""
            INSERT INTO smart_orders
                (user_id, exchange_id, type, symbol, side, total_usdt, params, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
        """, (user_id, exchange_id, order_type, symbol, side, total_usdt, json.dumps(params)))
        return cur.lastrowid


def get_active_smart_orders(user_id: int = None) -> list:
    with _db() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT * FROM smart_orders WHERE status='active' AND user_id=?", (user_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM smart_orders WHERE status='active'").fetchall()
    return [dict(r) for r in rows]


def update_smart_order_status(order_id: int, status: str, slices_done: int = None):
    with _db() as conn:
        if slices_done is not None:
            conn.execute(
                "UPDATE smart_orders SET status=?, slices_done=? WHERE id=?",
                (status, slices_done, order_id)
            )
        else:
            conn.execute("UPDATE smart_orders SET status=? WHERE id=?", (status, order_id))


def add_smart_order_leg(smart_order_id: int, order_id: str, side: str,
                        price: float, amount: float, status: str = "pending"):
    with _db() as conn:
        conn.execute("""
            INSERT INTO smart_order_legs
                (smart_order_id, order_id, side, price, amount, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (smart_order_id, order_id, side, price, amount, status))


def get_smart_order_legs(smart_order_id: int) -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM smart_order_legs WHERE smart_order_id=?", (smart_order_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_leg_status(leg_id: int, status: str):
    with _db() as conn:
        conn.execute(
            "UPDATE smart_order_legs SET status=?, executed_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, leg_id)
        )


def get_smart_order(order_id: int) -> Optional[dict]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM smart_orders WHERE id=?", (order_id,)).fetchone()
    return dict(row) if row else None


# ── TWAP ─────────────────────────────────────────────────────────────────────

def execute_twap_slice(exchange, symbol: str, side: str, slice_usdt: float) -> dict:
    """Execute one TWAP slice as a market order."""
    from exchange import place_market_order
    return place_market_order(exchange, symbol, side, slice_usdt)


def get_twap_next_slice_time(order: dict) -> float:
    """Return unix timestamp when the next slice should fire."""
    params  = json.loads(order["params"])
    slices  = params["slices"]
    done    = order["slices_done"]
    created = time.mktime(time.strptime(order.get("created_at", ""), "%Y-%m-%d %H:%M:%S")) \
              if order.get("created_at") else time.time()
    interval = params["interval_sec"]
    return created + (done * interval)


def twap_slice_due(order: dict) -> bool:
    """True if enough time has passed for the next slice."""
    return time.time() >= get_twap_next_slice_time(order)


def twap_completed(order: dict) -> bool:
    params = json.loads(order["params"])
    return order["slices_done"] >= params["slices"]


# ── Iceberg ───────────────────────────────────────────────────────────────────

def get_iceberg_visible_amount(order: dict) -> float:
    params      = json.loads(order["params"])
    visible_pct = params["visible_pct"] / 100.0
    return order["total_usdt"] * visible_pct


def iceberg_remaining(order: dict) -> float:
    params    = json.loads(order["params"])
    slice_u   = get_iceberg_visible_amount(order)
    placed    = order["slices_done"] * slice_u
    return max(0.0, order["total_usdt"] - placed)


def check_iceberg_fill(exchange, symbol: str, order_id: str) -> bool:
    """Return True if the given limit order has been filled."""
    try:
        o = exchange.fetch_order(order_id, symbol)
        return o["status"] in ("closed", "filled")
    except Exception as e:
        logger.warning(f"[ICEBERG] fetch_order {order_id}: {e}")
        return False


def place_iceberg_chunk(exchange, symbol: str, side: str, amount_usdt: float,
                        current_price: float) -> dict:
    """Place a limit order at or near current price for the iceberg chunk."""
    try:
        qty = exchange.amount_to_precision(symbol, amount_usdt / current_price)
        if side == "buy":
            # Limit slightly above to improve fill odds
            price = exchange.price_to_precision(symbol, current_price * 1.0005)
            return exchange.create_limit_buy_order(symbol, float(qty), float(price))
        else:
            price = exchange.price_to_precision(symbol, current_price * 0.9995)
            return exchange.create_limit_sell_order(symbol, float(qty), float(price))
    except Exception as e:
        logger.error(f"[ICEBERG] place_chunk error: {e}", exc_info=True)
        raise


# ── OCO ──────────────────────────────────────────────────────────────────────

def place_oco_legs(exchange, symbol: str, side: str,
                   amount_usdt: float, tp_price: float, sl_price: float,
                   smart_order_id: int) -> tuple[dict, dict]:
    """
    Place two limit orders:
    - TP limit at tp_price
    - SL stop-limit at sl_price
    Returns (tp_order, sl_order).
    Adds both to smart_order_legs.
    """
    ticker   = exchange.fetch_ticker(symbol)
    price    = ticker["last"]
    qty      = float(exchange.amount_to_precision(symbol, amount_usdt / price))

    if side == "buy":
        # We own the base; want to sell at TP and also sell (stop) at SL
        tp_order = exchange.create_limit_sell_order(
            symbol, qty,
            float(exchange.price_to_precision(symbol, tp_price))
        )
        sl_order = exchange.create_limit_sell_order(
            symbol, qty,
            float(exchange.price_to_precision(symbol, sl_price))
        )
    else:
        tp_order = exchange.create_limit_buy_order(
            symbol, qty,
            float(exchange.price_to_precision(symbol, tp_price))
        )
        sl_order = exchange.create_limit_buy_order(
            symbol, qty,
            float(exchange.price_to_precision(symbol, sl_price))
        )

    add_smart_order_leg(smart_order_id, tp_order["id"], "tp", tp_price, qty, "open")
    add_smart_order_leg(smart_order_id, sl_order["id"], "sl", sl_price, qty, "open")
    return tp_order, sl_order


def check_oco_fills(exchange, symbol: str, smart_order_id: int) -> Optional[str]:
    """
    Check if either OCO leg has filled. If so cancel the other.
    Returns 'tp', 'sl', or None.
    """
    legs = get_smart_order_legs(smart_order_id)
    if not legs or len(legs) < 2:
        return None

    for leg in legs:
        if leg["status"] == "open":
            try:
                o = exchange.fetch_order(leg["order_id"], symbol)
                if o["status"] in ("closed", "filled"):
                    # This leg filled — cancel the other
                    other_leg = next(
                        (l for l in legs if l["id"] != leg["id"] and l["status"] == "open"), None
                    )
                    if other_leg:
                        try:
                            exchange.cancel_order(other_leg["order_id"], symbol)
                        except Exception:
                            pass
                        update_leg_status(other_leg["id"], "cancelled")
                    update_leg_status(leg["id"], "filled")
                    return leg["side"]   # 'tp' or 'sl'
            except Exception as e:
                logger.debug(f"[OCO] check leg {leg['id']}: {e}")

    return None


def cancel_all_smart_order_legs(exchange, symbol: str, smart_order_id: int):
    """Cancel all open ccxt child orders for a smart order."""
    legs = get_smart_order_legs(smart_order_id)
    for leg in legs:
        if leg["status"] == "open":
            try:
                exchange.cancel_order(leg["order_id"], symbol)
                update_leg_status(leg["id"], "cancelled")
            except Exception as e:
                logger.warning(f"[SMART] cancel leg {leg['order_id']}: {e}")
