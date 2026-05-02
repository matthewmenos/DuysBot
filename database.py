"""
database.py - SQLite persistence layer for users, trades, settings
"""

import sqlite3
import json
from datetime import datetime, timedelta
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id          INTEGER PRIMARY KEY,
            username         TEXT,
            granted          INTEGER DEFAULT 0,
            is_admin         INTEGER DEFAULT 0,
            exchange         TEXT DEFAULT 'binance',
            api_key          TEXT DEFAULT '',
            api_secret       TEXT DEFAULT '',
            api_pass         TEXT DEFAULT '',
            sub_expiry       TEXT DEFAULT NULL,
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            reference   TEXT UNIQUE,
            months      INTEGER DEFAULT 1,
            amount      REAL,
            currency    TEXT DEFAULT 'USD',
            status      TEXT DEFAULT 'pending',
            paid_at     TEXT,
            expiry      TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_settings (
            user_id       INTEGER PRIMARY KEY,
            take_profit   REAL    DEFAULT 2.0,
            stop_loss     REAL    DEFAULT 1.0,
            trade_amount  REAL    DEFAULT 10.0,
            symbol        TEXT    DEFAULT 'BTC/USDT',
            trading_on    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER,
            symbol        TEXT,
            side          TEXT,
            entry_price   REAL,
            exit_price    REAL,
            amount        REAL,
            pnl           REAL,
            pnl_pct       REAL,
            status        TEXT DEFAULT 'open',
            exchange      TEXT,
            order_id      TEXT,
            signal        TEXT,
            opened_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            closed_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS support_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            message    TEXT,
            sent_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS exchange_creds (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            exchange   TEXT NOT NULL,
            api_key    TEXT NOT NULL,
            api_secret TEXT NOT NULL,
            api_pass   TEXT DEFAULT '',
            saved_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, exchange)
        );

        CREATE TABLE IF NOT EXISTS price_alerts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            symbol       TEXT,
            target_price REAL,
            condition    TEXT DEFAULT 'above',  -- 'above' or 'below'
            note         TEXT DEFAULT '',
            triggered    INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            triggered_at TEXT DEFAULT NULL
        );
        """)


# ── User helpers ──────────────────────────────────────────────────────────────

def get_user(user_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def upsert_user(user_id: int, username: str = "", granted: int = 0, is_admin: int = 0):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, granted, is_admin)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username
        """, (user_id, username, granted, is_admin))


def grant_user(user_id: int):
    """Backward-compat alias for lifetime grant."""
    grant_user_lifetime(user_id)


def save_exchange_creds(user_id: int, exchange: str, api_key: str, api_secret: str, api_pass: str = ""):
    """Save credentials to both the active user record and the exchange_creds vault."""
    with get_conn() as conn:
        # Update active exchange on user row
        conn.execute("""
            UPDATE users SET exchange=?, api_key=?, api_secret=?, api_pass=?
            WHERE user_id=?
        """, (exchange, api_key, api_secret, api_pass, user_id))
        # Upsert into per-exchange vault (so user can switch back without re-entering)
        conn.execute("""
            INSERT INTO exchange_creds (user_id, exchange, api_key, api_secret, api_pass)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, exchange) DO UPDATE SET
                api_key=excluded.api_key,
                api_secret=excluded.api_secret,
                api_pass=excluded.api_pass,
                saved_at=CURRENT_TIMESTAMP
        """, (user_id, exchange, api_key, api_secret, api_pass))


def get_stored_exchanges(user_id: int) -> list:
    """Return list of exchanges for which this user has stored credentials."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT exchange FROM exchange_creds WHERE user_id=? ORDER BY saved_at DESC",
            (user_id,)
        ).fetchall()
        return [r["exchange"] for r in rows]


def get_exchange_creds(user_id: int, exchange: str) -> dict | None:
    """Retrieve stored credentials for a specific exchange."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT api_key, api_secret, api_pass FROM exchange_creds WHERE user_id=? AND exchange=?",
            (user_id, exchange)
        ).fetchone()
        if row:
            return {"api_key": row["api_key"], "api_secret": row["api_secret"], "api_pass": row["api_pass"]}
        return None


def switch_exchange(user_id: int, exchange: str) -> bool:
    """Switch active exchange using already-stored credentials. Returns True if successful."""
    creds = get_exchange_creds(user_id, exchange)
    if not creds:
        return False
    with get_conn() as conn:
        conn.execute("""
            UPDATE users SET exchange=?, api_key=?, api_secret=?, api_pass=?
            WHERE user_id=?
        """, (exchange, creds["api_key"], creds["api_secret"], creds["api_pass"], user_id))
    return True


# ── Settings helpers ──────────────────────────────────────────────────────────

def get_settings(user_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
            conn.commit()
            row = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
        return row


def update_setting(user_id: int, key: str, value):
    allowed = {"take_profit", "stop_loss", "trade_amount", "symbol", "trading_on"}
    if key not in allowed:
        raise ValueError(f"Unknown setting: {key}")
    with get_conn() as conn:
        conn.execute(f"UPDATE user_settings SET {key}=? WHERE user_id=?", (value, user_id))


# ── Trade helpers ─────────────────────────────────────────────────────────────

def open_trade(user_id, symbol, side, entry_price, amount, exchange, order_id, signal):
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO trades (user_id, symbol, side, entry_price, amount, exchange, order_id, signal)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, symbol, side, entry_price, amount, exchange, order_id, signal))
        return cur.lastrowid


def close_trade(trade_id, exit_price, pnl, pnl_pct):
    with get_conn() as conn:
        conn.execute("""
            UPDATE trades
            SET exit_price=?, pnl=?, pnl_pct=?, status='closed', closed_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (exit_price, pnl, pnl_pct, trade_id))


def get_open_trades(user_id=None):
    with get_conn() as conn:
        if user_id:
            return conn.execute(
                "SELECT * FROM trades WHERE status='open' AND user_id=?", (user_id,)
            ).fetchall()
        return conn.execute("SELECT * FROM trades WHERE status='open'").fetchall()


def get_trade_history(user_id: int, limit: int = 20):
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM trades WHERE user_id=? ORDER BY opened_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()


def get_all_trading_users():
    """Return users who have trading enabled, valid credentials, and active access."""
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        return conn.execute("""
            SELECT u.*, s.take_profit, s.stop_loss, s.trade_amount, s.symbol
            FROM users u
            JOIN user_settings s ON u.user_id = s.user_id
            WHERE s.trading_on=1
              AND u.api_key != ''
              AND (u.granted=1 OR (u.sub_expiry IS NOT NULL AND u.sub_expiry > ?))
        """, (now,)).fetchall()


def save_support_message(user_id: int, message: str):
    with get_conn() as conn:
        conn.execute("INSERT INTO support_messages (user_id, message) VALUES (?, ?)", (user_id, message))


def get_pnl_summary(user_id: int):
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(AVG(pnl_pct), 0) as avg_pnl_pct
            FROM trades WHERE user_id=? AND status='closed'
        """, (user_id,)).fetchone()
        return row

# ── Subscription helpers ───────────────────────────────────────────────────────


def has_active_access(user_id: int) -> bool:
    """True if user has lifetime grant OR an active paid subscription."""
    user = get_user(user_id)
    if not user:
        return False
    if user["granted"] == 1:          # lifetime admin grant
        return True
    expiry = user["sub_expiry"]
    if expiry and expiry != "lifetime":
        try:
            return datetime.fromisoformat(expiry) > datetime.utcnow()
        except ValueError:
            return False
    return False


def get_subscription_status(user_id: int) -> dict:
    """Return a dict describing the user's current access status."""
    user = get_user(user_id)
    if not user:
        return {"access": False, "type": "none", "expiry": None}
    if user["granted"] == 1:
        return {"access": True, "type": "lifetime", "expiry": "Never"}
    expiry = user["sub_expiry"]
    if expiry:
        try:
            exp_dt = datetime.fromisoformat(expiry)
            if exp_dt > datetime.utcnow():
                days_left = (exp_dt - datetime.utcnow()).days
                return {"access": True, "type": "subscription", "expiry": expiry[:10], "days_left": days_left}
        except ValueError:
            pass
    return {"access": False, "type": "expired", "expiry": expiry}


def activate_subscription(user_id: int, months: int) -> str:
    """
    Extend (or start) a paid subscription by N months.
    Returns the new expiry date string.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT sub_expiry FROM users WHERE user_id=?", (user_id,)).fetchone()
        now = datetime.utcnow()
        if row and row["sub_expiry"] and row["sub_expiry"] != "lifetime":
            try:
                current_expiry = datetime.fromisoformat(row["sub_expiry"])
                base = max(current_expiry, now)  # extend from current expiry if still valid
            except ValueError:
                base = now
        else:
            base = now
        new_expiry = base + timedelta(days=30 * months)
        expiry_str = new_expiry.isoformat()

        conn.execute("""
            UPDATE users SET sub_expiry=? WHERE user_id=?
        """, (expiry_str, user_id))
        conn.execute("""
            INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)
        """, (user_id,))
    return expiry_str[:10]


def record_pending_payment(user_id: int, reference: str, months: int, amount: float, currency: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO subscriptions (user_id, reference, months, amount, currency, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        """, (user_id, reference, months, amount, currency))


def confirm_payment(reference: str, expiry: str):
    with get_conn() as conn:
        conn.execute("""
            UPDATE subscriptions
            SET status='success', paid_at=CURRENT_TIMESTAMP, expiry=?
            WHERE reference=?
        """, (expiry, reference))


def get_subscription_history(user_id: int):
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM subscriptions WHERE user_id=? ORDER BY created_at DESC
        """, (user_id,)).fetchall()


def grant_user_lifetime(user_id: int):
    """Admin grant — sets lifetime access, clears any paid sub expiry."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET granted=1, sub_expiry='lifetime' WHERE user_id=?", (user_id,))
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))


def get_all_subscribers():
    """Admin: list all users with active access."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT user_id, username, granted, sub_expiry, created_at
            FROM users
            WHERE granted=1 OR (sub_expiry IS NOT NULL AND sub_expiry != '')
            ORDER BY created_at DESC
        """).fetchall()

# ── Price Alert helpers ────────────────────────────────────────────────────────

def add_price_alert(user_id: int, symbol: str, target_price: float, condition: str, note: str = "") -> int:
    """Add a new price alert. condition = 'above' or 'below'. Returns alert id."""
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO price_alerts (user_id, symbol, target_price, condition, note)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, symbol.upper(), target_price, condition, note))
        return cur.lastrowid


def get_active_alerts(user_id: int = None):
    """Return all untriggered alerts, optionally filtered by user."""
    with get_conn() as conn:
        if user_id:
            return conn.execute("""
                SELECT * FROM price_alerts
                WHERE triggered=0 AND user_id=?
                ORDER BY created_at DESC
            """, (user_id,)).fetchall()
        return conn.execute("""
            SELECT * FROM price_alerts WHERE triggered=0
            ORDER BY created_at DESC
        """).fetchall()


def mark_alert_triggered(alert_id: int):
    with get_conn() as conn:
        conn.execute("""
            UPDATE price_alerts
            SET triggered=1, triggered_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (alert_id,))


def delete_alert(alert_id: int, user_id: int) -> bool:
    """Delete an alert — only if it belongs to user_id. Returns True on success."""
    with get_conn() as conn:
        cur = conn.execute("""
            DELETE FROM price_alerts WHERE id=? AND user_id=?
        """, (alert_id, user_id))
        return cur.rowcount > 0


def get_all_alert_users():
    """Return distinct user_ids that have active alerts, joined with exchange credentials."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT DISTINCT u.user_id, u.exchange, u.api_key, u.api_secret, u.api_pass
            FROM price_alerts pa
            JOIN users u ON pa.user_id = u.user_id
            WHERE pa.triggered=0 AND u.api_key != ''
        """).fetchall()
