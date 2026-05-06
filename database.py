"""
database.py - SQLite persistence layer for users, trades, settings
"""

import sqlite3
import json
from datetime import datetime, timedelta
from config import DB_PATH

# Lazy import encryption to avoid circular import at startup
def _enc(val: str) -> str:
    if not val:
        return val
    try:
        from encryption import encrypt, is_configured
        if is_configured():
            return encrypt(val)
    except Exception:
        pass
    return val

def _dec(val: str) -> str:
    if not val:
        return val
    try:
        from encryption import decrypt, is_configured
        if is_configured():
            return decrypt(val)
    except Exception:
        pass
    return val


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist. Also migrates existing DBs."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id             INTEGER PRIMARY KEY,
            username            TEXT,
            granted             INTEGER DEFAULT 0,
            is_admin            INTEGER DEFAULT 0,
            exchange            TEXT DEFAULT 'binance',
            api_key             TEXT DEFAULT '',
            api_secret          TEXT DEFAULT '',
            api_pass            TEXT DEFAULT '',
            sub_expiry          TEXT DEFAULT NULL,
            trial_used          INTEGER DEFAULT 0,
            trial_started_at    TEXT DEFAULT NULL,
            mexc_key_saved_at   TEXT DEFAULT NULL,
            created_at          TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS crypto_payments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            tx_hash       TEXT UNIQUE,
            amount_usdt   REAL,
            months        INTEGER DEFAULT 1,
            wallet_from   TEXT,
            status        TEXT DEFAULT 'pending',
            confirmed_at  TEXT,
            expiry        TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
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

        CREATE TABLE IF NOT EXISTS trade_confirmations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            symbol      TEXT,
            side        TEXT,
            price       REAL,
            amount      REAL,
            signal_data TEXT,
            status      TEXT DEFAULT 'pending',
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS referrals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id  INTEGER NOT NULL,
            referred_id  INTEGER NOT NULL UNIQUE,
            code         TEXT NOT NULL,
            rewarded     INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_settings (
            user_id              INTEGER PRIMARY KEY,
            take_profit          REAL    DEFAULT 2.0,
            stop_loss            REAL    DEFAULT 1.0,
            tp_mode              TEXT    DEFAULT 'pct',
            sl_mode              TEXT    DEFAULT 'pct',
            trade_amount         REAL    DEFAULT 10.0,
            symbol               TEXT    DEFAULT 'BTC/USDT',
            trading_on           INTEGER DEFAULT 0,
            confirm_trades       INTEGER DEFAULT 0,
            trailing_stop        INTEGER DEFAULT 0,
            trailing_stop_pct    REAL    DEFAULT 0.5,
            report_hour          INTEGER DEFAULT 8,
            last_report_date     TEXT    DEFAULT NULL,
            signal_suggestions   INTEGER DEFAULT 1,
            multi_symbols        TEXT    DEFAULT NULL,
            trade_mode           TEXT    DEFAULT 'auto'
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

        # ── Migrate existing databases — add columns added in later versions ──
        migrations = [
            "ALTER TABLE users ADD COLUMN trial_used INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN trial_started_at TEXT DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN mexc_key_saved_at TEXT DEFAULT NULL",
            "ALTER TABLE user_settings ADD COLUMN tp_mode TEXT DEFAULT 'pct'",
            "ALTER TABLE user_settings ADD COLUMN sl_mode TEXT DEFAULT 'pct'",
            "ALTER TABLE user_settings ADD COLUMN confirm_trades INTEGER DEFAULT 0",
            "ALTER TABLE user_settings ADD COLUMN trailing_stop INTEGER DEFAULT 0",
            "ALTER TABLE user_settings ADD COLUMN trailing_stop_pct REAL DEFAULT 0.5",
            "ALTER TABLE user_settings ADD COLUMN signal_suggestions INTEGER DEFAULT 1",
            "ALTER TABLE user_settings ADD COLUMN multi_symbols TEXT DEFAULT NULL",
            "ALTER TABLE user_settings ADD COLUMN report_hour INTEGER DEFAULT 8",
            "ALTER TABLE user_settings ADD COLUMN last_report_date TEXT DEFAULT NULL",
            "ALTER TABLE user_settings ADD COLUMN trade_mode TEXT DEFAULT 'auto'",
            "ALTER TABLE users ADD COLUMN tz_offset INTEGER DEFAULT 0",
            """CREATE TABLE IF NOT EXISTS signal_history (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL,
                symbol            TEXT,
                action            TEXT,
                confidence        INTEGER,
                reason            TEXT,
                resulted_in_trade INTEGER DEFAULT 0,
                outcome_pnl       REAL    DEFAULT NULL,
                created_at        TEXT    DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS onboarding_state (
                user_id   INTEGER PRIMARY KEY,
                step      TEXT DEFAULT 'exchange',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS crypto_payments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                tx_hash       TEXT UNIQUE,
                amount_usdt   REAL,
                months        INTEGER DEFAULT 1,
                wallet_from   TEXT,
                status        TEXT DEFAULT 'pending',
                confirmed_at  TEXT,
                expiry        TEXT,
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP
            )""",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
            except Exception:
                pass  # already exists — safe to ignore


# ── User helpers ──────────────────────────────────────────────────────────────

def get_user(user_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    # Decrypt API keys transparently
    row = dict(row)
    row["api_key"]    = _dec(row.get("api_key", ""))
    row["api_secret"] = _dec(row.get("api_secret", ""))
    row["api_pass"]   = _dec(row.get("api_pass", ""))
    return row


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
    """Save credentials (encrypted) to the active user record and the exchange_creds vault."""
    e_key  = _enc(api_key)
    e_sec  = _enc(api_secret)
    e_pass = _enc(api_pass)
    with get_conn() as conn:
        conn.execute("""
            UPDATE users SET exchange=?, api_key=?, api_secret=?, api_pass=?
            WHERE user_id=?
        """, (exchange, e_key, e_sec, e_pass, user_id))
        conn.execute("""
            INSERT INTO exchange_creds (user_id, exchange, api_key, api_secret, api_pass)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, exchange) DO UPDATE SET
                api_key=excluded.api_key,
                api_secret=excluded.api_secret,
                api_pass=excluded.api_pass,
                saved_at=CURRENT_TIMESTAMP
        """, (user_id, exchange, e_key, e_sec, e_pass))


def get_stored_exchanges(user_id: int) -> list:
    """Return list of exchanges for which this user has stored credentials."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT exchange FROM exchange_creds WHERE user_id=? ORDER BY saved_at DESC",
            (user_id,)
        ).fetchall()
        return [r["exchange"] for r in rows]


def get_exchange_creds(user_id: int, exchange: str) -> dict | None:
    """Retrieve and decrypt stored credentials for a specific exchange."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT api_key, api_secret, api_pass FROM exchange_creds WHERE user_id=? AND exchange=?",
            (user_id, exchange)
        ).fetchone()
        if row:
            return {
                "api_key":    _dec(row["api_key"]),
                "api_secret": _dec(row["api_secret"]),
                "api_pass":   _dec(row["api_pass"]),
            }
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

# Default values for every settings column — ensures .get() always works
_SETTINGS_DEFAULTS = {
    "take_profit":       2.0,
    "stop_loss":         1.0,
    "tp_mode":           "pct",
    "sl_mode":           "pct",
    "trade_amount":      10.0,
    "symbol":            "BTC/USDT",
    "trading_on":        0,
    "confirm_trades":    0,
    "trailing_stop":     0,
    "trailing_stop_pct": 0.5,
    "report_hour":       8,
    "last_report_date":  None,
    "signal_suggestions":1,
    "multi_symbols":     None,
    "trade_mode":        "auto",
}


def get_settings(user_id: int) -> dict:
    """Return user settings as a plain dict with safe defaults for all columns."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
            conn.commit()
            row = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
    # Merge row into defaults so missing columns always have a safe value
    result = dict(_SETTINGS_DEFAULTS)
    if row:
        for key in row.keys():
            val = row[key]
            if val is not None:
                result[key] = val
    result["user_id"] = user_id
    return result


def update_setting(user_id: int, key: str, value):
    allowed = {
        "take_profit", "stop_loss", "tp_mode", "sl_mode",
        "trade_amount", "symbol", "trading_on",
        "confirm_trades", "trailing_stop", "trailing_stop_pct",
        "report_hour", "last_report_date",
        "signal_suggestions", "multi_symbols", "trade_mode",
    }
    if key not in allowed:
        raise ValueError(f"Unknown setting: {key}")
    # Ensure row exists before updating
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
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
        rows = conn.execute("""
            SELECT u.*, s.take_profit, s.stop_loss, s.trade_amount, s.symbol,
                   s.tp_mode, s.sl_mode, s.trade_mode, s.confirm_trades,
                   s.trailing_stop, s.trailing_stop_pct, s.signal_suggestions,
                   s.multi_symbols
            FROM users u
            JOIN user_settings s ON u.user_id = s.user_id
            WHERE s.trading_on=1
              AND u.api_key != ''
              AND (u.granted=1 OR (u.sub_expiry IS NOT NULL AND u.sub_expiry > ?))
        """, (now,)).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["api_key"]    = _dec(d.get("api_key", ""))
        d["api_secret"] = _dec(d.get("api_secret", ""))
        d["api_pass"]   = _dec(d.get("api_pass", ""))
        result.append(d)
    return result


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
    """True if user has lifetime grant, active paid subscription, or active trial."""
    user = get_user(user_id)
    if not user:
        return False
    if user["granted"] == 1:
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
        return {"access": False, "type": "none", "expiry": None, "trial_used": False}
    if user["granted"] == 1:
        return {"access": True, "type": "lifetime", "expiry": "Never", "trial_used": True}
    expiry    = user["sub_expiry"]
    trial_used = bool(user["trial_used"] if "trial_used" in user.keys() else 0)
    if expiry and expiry != "lifetime":
        try:
            exp_dt    = datetime.fromisoformat(expiry)
            now       = datetime.utcnow()
            if exp_dt > now:
                days_left  = (exp_dt - now).days
                hours_left = int(((exp_dt - now).seconds) / 3600)
                # Distinguish trial from paid
                sub_type = "trial" if trial_used and days_left <= 7 and user["granted"] == 0 else "subscription"
                # More precise: check trial_started_at
                if user["trial_started_at"]:
                    try:
                        trial_start = datetime.fromisoformat(user["trial_started_at"])
                        if (now - trial_start).days < 7:
                            sub_type = "trial"
                    except ValueError:
                        pass
                return {
                    "access":     True,
                    "type":       sub_type,
                    "expiry":     expiry[:10],
                    "days_left":  days_left,
                    "hours_left": hours_left,
                    "trial_used": trial_used,
                }
        except ValueError:
            pass
    return {"access": False, "type": "expired", "expiry": expiry, "trial_used": trial_used}


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


# ── Free Trial helpers ────────────────────────────────────────────────────────

def has_used_trial(user_id: int) -> bool:
    """Check if user has already consumed their free trial."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT trial_used FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        return bool(row and row["trial_used"])


def activate_trial(user_id: int) -> str:
    """
    Activate a 7-day free trial for the user.
    Returns the expiry date string. Raises if already used.
    """
    if has_used_trial(user_id):
        raise ValueError("Trial already used for this account.")
    now    = datetime.utcnow()
    expiry = (now + timedelta(days=7)).isoformat()
    with get_conn() as conn:
        conn.execute("""
            UPDATE users
            SET trial_used=1, trial_started_at=?, sub_expiry=?
            WHERE user_id=?
        """, (now.isoformat(), expiry, user_id))
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    return expiry[:10]


# ── Crypto Payment helpers ────────────────────────────────────────────────────

def record_crypto_payment(user_id: int, tx_hash: str, amount_usdt: float, months: int, wallet_from: str = ""):
    """Record a pending on-chain payment."""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO crypto_payments
                (user_id, tx_hash, amount_usdt, months, wallet_from, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        """, (user_id, tx_hash, amount_usdt, months, wallet_from))


def confirm_crypto_payment(tx_hash: str) -> str | None:
    """
    Mark a crypto payment as confirmed, activate the subscription.
    Returns new expiry string or None if tx not found.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, months FROM crypto_payments WHERE tx_hash=? AND status='pending'",
            (tx_hash,)
        ).fetchone()
        if not row:
            return None
        user_id = row["user_id"]
        months  = row["months"]

    expiry = activate_subscription(user_id, months)
    with get_conn() as conn:
        conn.execute("""
            UPDATE crypto_payments
            SET status='confirmed', confirmed_at=CURRENT_TIMESTAMP, expiry=?
            WHERE tx_hash=?
        """, (expiry, tx_hash))
    return expiry


def get_pending_crypto_payments():
    """Return all pending crypto payments (for the verification scheduler)."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM crypto_payments
            WHERE status='pending'
            ORDER BY created_at ASC
        """).fetchall()


def get_crypto_payment_history(user_id: int):
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM crypto_payments
            WHERE user_id=? ORDER BY created_at DESC
        """, (user_id,)).fetchall()


# ── MEXC Key expiry helpers ───────────────────────────────────────────────────

def record_mexc_key_saved(user_id: int):
    """Record the timestamp when the user saved MEXC API keys."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE users SET mexc_key_saved_at=CURRENT_TIMESTAMP WHERE user_id=?
        """, (user_id,))


def get_mexc_key_age_days(user_id: int) -> int | None:
    """Return how many days ago the MEXC key was saved, or None if never."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT mexc_key_saved_at FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row or not row["mexc_key_saved_at"]:
            return None
        try:
            saved = datetime.fromisoformat(row["mexc_key_saved_at"])
            return (datetime.utcnow() - saved).days
        except ValueError:
            return None


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


def get_all_subscribed_users():
    """
    Return all users with active access and exchange credentials
    as plain dicts with decrypted keys.
    """
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT u.user_id, u.exchange, u.api_key, u.api_secret, u.api_pass,
                   s.symbol, s.take_profit, s.stop_loss, s.trade_amount,
                   s.signal_suggestions, s.trade_mode
            FROM users u
            LEFT JOIN user_settings s ON u.user_id = s.user_id
            WHERE u.api_key != ''
              AND (u.granted = 1 OR (u.sub_expiry IS NOT NULL AND u.sub_expiry > ?))
        """, (now,)).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["api_key"]    = _dec(d.get("api_key", ""))
        d["api_secret"] = _dec(d.get("api_secret", ""))
        d["api_pass"]   = _dec(d.get("api_pass", ""))
        result.append(d)
    return result



# ── Trade Confirmation helpers ────────────────────────────────────────────────

def create_trade_confirmation(user_id: int, symbol: str, side: str, price: float, amount: float, signal_data: str) -> int:
    import json as _json
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO trade_confirmations (user_id, symbol, side, price, amount, signal_data, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """, (user_id, symbol, side, price, amount, signal_data))
        return cur.lastrowid


def resolve_trade_confirmation(confirm_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE trade_confirmations SET status=? WHERE id=?", (status, confirm_id))


def get_pending_confirmation(confirm_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM trade_confirmations WHERE id=? AND status='pending'", (confirm_id,)
        ).fetchone()


# ── Daily PnL Report helpers ──────────────────────────────────────────────────

def get_users_due_for_report() -> list:
    """Return users who have not received a report today and whose report_hour has arrived."""
    now   = datetime.utcnow()
    today = now.date().isoformat()
    with get_conn() as conn:
        return conn.execute("""
            SELECT u.user_id, u.username, s.report_hour, s.last_report_date
            FROM users u
            JOIN user_settings s ON u.user_id = s.user_id
            WHERE u.api_key != ''
              AND (u.granted=1 OR (u.sub_expiry IS NOT NULL AND u.sub_expiry > ?))
              AND (s.last_report_date IS NULL OR s.last_report_date < ?)
              AND ? >= s.report_hour
        """, (now.isoformat(), today, now.hour)).fetchall()


def mark_report_sent(user_id: int):
    today = datetime.utcnow().date().isoformat()
    with get_conn() as conn:
        conn.execute("UPDATE user_settings SET last_report_date=? WHERE user_id=?", (today, user_id))


def get_daily_pnl(user_id: int) -> dict:
    """Return PnL stats for trades closed today (UTC)."""
    today = datetime.utcnow().date().isoformat()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(MAX(pnl), 0) as best_trade,
                COALESCE(MIN(pnl), 0) as worst_trade
            FROM trades
            WHERE user_id=? AND status='closed' AND closed_at LIKE ?
        """, (user_id, f"{today}%")).fetchone()
    return dict(row) if row else {}


def get_weekly_pnl(user_id: int) -> dict:
    """Return PnL stats for the last 7 days."""
    since = (datetime.utcnow().date() - timedelta(days=7)).isoformat()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(pnl), 0) as total_pnl
            FROM trades
            WHERE user_id=? AND status='closed' AND closed_at >= ?
        """, (user_id, since)).fetchone()
    return dict(row) if row else {}


# ── Multi-symbol helpers ──────────────────────────────────────────────────────

def get_multi_symbols(user_id: int) -> list:
    """Return list of symbols user is trading (up to 3)."""
    with get_conn() as conn:
        row = conn.execute("SELECT symbol, multi_symbols FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return []
    extras = []
    if row["multi_symbols"]:
        try:
            extras = json.loads(row["multi_symbols"])
        except Exception:
            extras = []
    primary = row["symbol"] or "BTC/USDT"
    symbols = [primary] + [s for s in extras if s != primary]
    return symbols[:3]


def set_multi_symbols(user_id: int, symbols: list):
    """Set up to 3 symbols for multi-symbol trading."""
    symbols = list(dict.fromkeys(symbols))[:3]  # deduplicate, max 3
    primary = symbols[0] if symbols else "BTC/USDT"
    extras  = json.dumps(symbols[1:]) if len(symbols) > 1 else None
    with get_conn() as conn:
        conn.execute("""
            UPDATE user_settings SET symbol=?, multi_symbols=? WHERE user_id=?
        """, (primary, extras, user_id))


# ── API Key expiry check ──────────────────────────────────────────────────────

def get_all_users_for_key_check() -> list:
    """Return users with MEXC keys for expiry checking."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT user_id, exchange, mexc_key_saved_at
            FROM users
            WHERE exchange='mexc' AND api_key != '' AND mexc_key_saved_at IS NOT NULL
        """).fetchall()


# ── Subscription renewal reminder helpers ─────────────────────────────────────

def get_users_expiring_soon(days: int) -> list:
    """Return users whose subscription expires in exactly `days` days."""
    from datetime import date, timedelta
    target      = (datetime.utcnow().date() + timedelta(days=days)).isoformat()
    target_next = (datetime.utcnow().date() + timedelta(days=days + 1)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT user_id, username, sub_expiry
            FROM users
            WHERE granted = 0
              AND sub_expiry IS NOT NULL
              AND sub_expiry >= ?
              AND sub_expiry < ?
        """, (target, target_next)).fetchall()
    return [dict(r) for r in rows]


# ── Trade deduplication guard ─────────────────────────────────────────────────

def has_open_trade_for_symbol(user_id: int, symbol: str) -> bool:
    """Return True if there is already an open trade for this user+symbol."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT id FROM trades
            WHERE user_id=? AND symbol=? AND status='open'
            LIMIT 1
        """, (user_id, symbol)).fetchone()
    return row is not None


# ── Rate limiting state stored in DB for cross-restart persistence ────────────

def get_user_timezone(user_id: int) -> str:
    """Return user timezone string (default UTC)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT timezone FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
    return (row["timezone"] if row and row["timezone"] else "UTC") if row else "UTC"


def set_user_timezone(user_id: int, tz: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET timezone=? WHERE user_id=?", (tz, user_id))


# ── Admin user lookup ─────────────────────────────────────────────────────────

def get_user_full_profile(user_id: int) -> dict | None:
    """Return complete user profile for admin lookup."""
    user = get_user(user_id)
    if not user:
        return None
    s       = get_settings(user_id)
    status  = get_subscription_status(user_id)
    pnl_row = get_pnl_summary(user_id)
    open_t  = get_open_trades(user_id)

    return {
        "user":        user,
        "settings":    s,
        "status":      status,
        "pnl":         dict(pnl_row) if pnl_row else {},
        "open_trades": len(open_t),
        "referral_stats": None,  # filled by referral.py if needed
    }


# ── Bot status / platform stats ───────────────────────────────────────────────

def get_platform_stats() -> dict:
    """Return platform-wide statistics for /status command."""
    now   = datetime.utcnow().isoformat()
    today = datetime.utcnow().date().isoformat()
    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        active_subs = conn.execute("""
            SELECT COUNT(*) as c FROM users
            WHERE granted=1 OR (sub_expiry IS NOT NULL AND sub_expiry > ?)
        """, (now,)).fetchone()["c"]
        active_traders = conn.execute("""
            SELECT COUNT(*) as c FROM user_settings WHERE trading_on=1
        """).fetchone()["c"]
        open_trades = conn.execute("""
            SELECT COUNT(*) as c FROM trades WHERE status='open'
        """).fetchone()["c"]
        today_trades = conn.execute("""
            SELECT COUNT(*) as c FROM trades
            WHERE status='closed' AND closed_at LIKE ?
        """, (f"{today}%",)).fetchone()["c"]
        today_pnl = conn.execute("""
            SELECT COALESCE(SUM(pnl),0) as p FROM trades
            WHERE status='closed' AND closed_at LIKE ?
        """, (f"{today}%",)).fetchone()["p"]
        total_pnl = conn.execute("""
            SELECT COALESCE(SUM(pnl),0) as p FROM trades WHERE status='closed'
        """).fetchone()["p"]
    return {
        "total_users":    total_users,
        "active_subs":    active_subs,
        "active_traders": active_traders,
        "open_trades":    open_trades,
        "today_trades":   today_trades,
        "today_pnl":      round(today_pnl, 4),
        "total_pnl":      round(total_pnl, 4),
    }


# ── Signal history ────────────────────────────────────────────────────────────

def log_signal_history(user_id: int, symbol: str, action: str, confidence: int, resulted_in_trade: bool = False):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO signal_history
                (user_id, symbol, action, confidence, resulted_in_trade)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, symbol, action, confidence, int(resulted_in_trade)))


def get_signal_history(user_id: int, limit: int = 10) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM signal_history
            WHERE user_id=?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ── SL proximity check ────────────────────────────────────────────────────────

def get_open_trades_near_sl(threshold_pct: float = 0.80) -> list:
    """
    Return open trades that are within threshold_pct of their stop loss.
    e.g. threshold=0.80 means the price has moved 80% of the way to SL.
    Returns a list of dicts with trade info + user settings.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.*, s.stop_loss, s.sl_mode, u.exchange, u.api_key, u.api_secret, u.api_pass
            FROM trades t
            JOIN user_settings s ON t.user_id = s.user_id
            JOIN users u ON t.user_id = u.user_id
            WHERE t.status = 'open'
              AND u.api_key != ''
        """).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["api_key"]    = _dec(d.get("api_key", ""))
        d["api_secret"] = _dec(d.get("api_secret", ""))
        d["api_pass"]   = _dec(d.get("api_pass", ""))
        result.append(d)
    return result



# ── Subscription Renewal helpers ──────────────────────────────────────────────

def get_users_expiring_soon(days: int) -> list:
    """Return users whose subscription expires in exactly N days (for renewal reminders)."""
    from_dt = (datetime.utcnow() + timedelta(days=days)).date().isoformat()
    to_dt   = (datetime.utcnow() + timedelta(days=days+1)).date().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT user_id, username, sub_expiry
            FROM users
            WHERE granted=0
              AND sub_expiry IS NOT NULL
              AND sub_expiry >= ?
              AND sub_expiry < ?
        """, (from_dt, to_dt)).fetchall()
    return [dict(r) for r in rows]


# ── Trade deduplication ───────────────────────────────────────────────────────

def has_open_trade_for_symbol(user_id: int, symbol: str) -> bool:
    """Return True if user already has an open trade for this symbol."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT id FROM trades
            WHERE user_id=? AND symbol=? AND status='open'
            LIMIT 1
        """, (user_id, symbol)).fetchone()
    return row is not None


# ── Signal history ────────────────────────────────────────────────────────────

def log_signal_to_db(user_id: int, symbol: str, action: str, confidence: int,
                     reason: str, resulted_in_trade: bool = False):
    """Store signal in signal_history table."""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO signal_history
                (user_id, symbol, action, confidence, reason, resulted_in_trade)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, symbol, action, confidence, reason[:300], int(resulted_in_trade)))


def get_signal_history(user_id: int, limit: int = 10) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM signal_history
            WHERE user_id=?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ── Platform stats (for /status command) ─────────────────────────────────────

def get_platform_stats() -> dict:
    now   = datetime.utcnow()
    today = now.date().isoformat()
    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) as c FROM users WHERE api_key != ''").fetchone()["c"]
        active_subs = conn.execute("""
            SELECT COUNT(*) as c FROM users
            WHERE granted=1 OR (sub_expiry IS NOT NULL AND sub_expiry > ?)
        """, (now.isoformat(),)).fetchone()["c"]
        active_traders = conn.execute("""
            SELECT COUNT(*) as c FROM user_settings WHERE trading_on=1
        """).fetchone()["c"]
        open_trades = conn.execute("SELECT COUNT(*) as c FROM trades WHERE status='open'").fetchone()["c"]
        today_trades = conn.execute("""
            SELECT COUNT(*) as c FROM trades
            WHERE status='closed' AND closed_at LIKE ?
        """, (f"{today}%",)).fetchone()["c"]
        today_pnl = conn.execute("""
            SELECT COALESCE(SUM(pnl), 0) as p FROM trades
            WHERE status='closed' AND closed_at LIKE ?
        """, (f"{today}%",)).fetchone()["p"]
    return {
        "total_users":    total_users,
        "active_subs":    active_subs,
        "active_traders": active_traders,
        "open_trades":    open_trades,
        "today_trades":   today_trades,
        "today_pnl":      round(today_pnl, 4),
    }


# ── User lookup (for admin /user command) ────────────────────────────────────

def get_user_full_profile(user_id: int) -> dict | None:
    user = get_user(user_id)
    if not user:
        return None
    s     = get_settings(user_id)
    stats = None
    with get_conn() as conn:
        stats = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                   COALESCE(SUM(pnl), 0) as total_pnl
            FROM trades WHERE user_id=? AND status='closed'
        """, (user_id,)).fetchone()
        ref_count = conn.execute(
            "SELECT COUNT(*) as c FROM referrals WHERE referrer_id=?", (user_id,)
        ).fetchone()["c"]
    sub = get_subscription_status(user_id)
    return {
        "user":      user,
        "settings":  s,
        "sub":       sub,
        "trades":    dict(stats) if stats else {},
        "referrals": ref_count,
    }


# ── Timezone helpers ──────────────────────────────────────────────────────────

def set_user_timezone(user_id: int, tz_offset: int):
    """Store UTC offset in hours (-12 to +14)."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET tz_offset=? WHERE user_id=?", (tz_offset, user_id))


def get_user_tz_offset(user_id: int) -> int:
    """Return stored UTC offset or 0."""
    with get_conn() as conn:
        row = conn.execute("SELECT tz_offset FROM users WHERE user_id=?", (user_id,)).fetchone()
    if row:
        try:
            return int(row["tz_offset"] or 0)
        except (TypeError, ValueError):
            return 0
    return 0
