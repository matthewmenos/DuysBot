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
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL mode: allows concurrent reads, survives crashes without data loss
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
            exchange            TEXT DEFAULT '',  -- empty = not yet connected
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
            trade_mode           TEXT    DEFAULT 'auto',
            arb_alerts           INTEGER DEFAULT 1,
            arb_enabled          INTEGER DEFAULT 1,
            arb_symbols          TEXT    DEFAULT NULL
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


        CREATE TABLE IF NOT EXISTS paper_trades (
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
            opened_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            closed_at     TEXT,
            close_reason  TEXT
        );

        CREATE TABLE IF NOT EXISTS webhook_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            token      TEXT,
            payload    TEXT,
            action     TEXT,
            symbol     TEXT,
            status     TEXT,
            message    TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS dca_plans (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER,
            exchange_id     TEXT,
            symbol          TEXT,
            amount_usdt     REAL,
            interval_sec    INTEGER,
            price_ceiling   REAL DEFAULT NULL,
            status          TEXT DEFAULT 'active',
            next_run_at     TEXT,
            total_invested  REAL DEFAULT 0.0,
            total_bought    REAL DEFAULT 0.0,
            runs_completed  INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS grid_plans (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            exchange_id  TEXT,
            symbol       TEXT,
            lower_price  REAL,
            upper_price  REAL,
            grid_levels  INTEGER,
            total_usdt   REAL,
            grid_spacing REAL,
            status       TEXT DEFAULT 'active',
            total_profit REAL DEFAULT 0.0,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS grid_orders (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id   INTEGER,
            order_id  TEXT,
            side      TEXT,
            price     REAL,
            amount    REAL,
            status    TEXT DEFAULT 'open',
            filled_at TEXT
        );

        CREATE TABLE IF NOT EXISTS smart_orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            exchange_id TEXT,
            type        TEXT,
            symbol      TEXT,
            side        TEXT,
            total_usdt  REAL,
            params      TEXT,
            status      TEXT DEFAULT 'active',
            slices_done INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS smart_order_legs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            smart_order_id  INTEGER,
            order_id        TEXT,
            side            TEXT,
            price           REAL,
            amount          REAL,
            status          TEXT DEFAULT 'pending',
            executed_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS strategies (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER,
            name             TEXT,
            description      TEXT,
            symbol           TEXT,
            multi_symbols    TEXT,
            take_profit      REAL,
            stop_loss        REAL,
            tp_mode          TEXT,
            sl_mode          TEXT,
            trailing_stop    INTEGER,
            trade_mode       TEXT,
            is_public        INTEGER DEFAULT 1,
            subscriber_count INTEGER DEFAULT 0,
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS strategy_subscriptions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            subscriber_id   INTEGER,
            strategy_id     INTEGER,
            prev_settings   TEXT,
            subscribed_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(subscriber_id, strategy_id)
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            event_type TEXT,
            details    TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS webdash_tokens (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            token      TEXT UNIQUE,
            expires_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
            # Reset exchange to '' ONLY for users who have no API key stored
            # and whose exchange is still the old 'binance' default
            # This preserves real exchange selections
            "UPDATE users SET exchange='' WHERE (api_key='' OR api_key IS NULL) AND exchange='binance'",
            "ALTER TABLE user_settings ADD COLUMN arb_alerts INTEGER DEFAULT 1",
            "ALTER TABLE user_settings ADD COLUMN arb_enabled INTEGER DEFAULT 1",
            "ALTER TABLE user_settings ADD COLUMN arb_symbols TEXT DEFAULT NULL",
            "ALTER TABLE user_settings ADD COLUMN paper_mode INTEGER DEFAULT 0",
            "ALTER TABLE user_settings ADD COLUMN paper_balance REAL DEFAULT 1000.0",
            "ALTER TABLE user_settings ADD COLUMN paper_start_balance REAL DEFAULT 1000.0",
            "ALTER TABLE users ADD COLUMN webhook_token TEXT",
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
    "arb_alerts":        1,    # 1 = background arb scan + auto-alerts ON
    "arb_enabled":       1,    # 1 = arbitrage feature enabled for this user
    "arb_symbols":       None, # JSON list of symbols user wants scanned, None = defaults,
    "paper_mode":        0,     # 1 = paper trading ON
    "paper_balance":     1000.0,# current paper USDT balance
    "paper_start_balance":1000.0,# starting paper balance
}


def get_settings(user_id: int) -> dict:
    """Return user settings as a plain dict with safe defaults for all columns."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            # Insert with all defaults — never overwrites existing rows
            conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
            conn.commit()
            row = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
    # Return a full dict — existing values take priority, defaults fill in missing columns
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
        "arb_alerts", "arb_enabled", "arb_symbols",
        "paper_mode", "paper_balance", "paper_start_balance",
    }
    # Keys where auditing every write would be noisy (balances, timestamps)
    _no_audit = {"paper_balance", "last_report_date", "paper_start_balance"}
    if key not in allowed:
        raise ValueError(f"Unknown setting: {key}")

    # ── Financial value guards ─────────────────────────────────────────────────
    if key == "take_profit":
        v = float(value)
        if not (0.01 <= v <= 1000):
            raise ValueError(f"take_profit must be between 0.01 and 1000, got {v}")
        value = v
    elif key == "stop_loss":
        v = float(value)
        if not (0.01 <= v <= 100):
            raise ValueError(f"stop_loss must be between 0.01 and 100, got {v}")
        value = v
    elif key == "trade_amount":
        v = float(value)
        if not (1.0 <= v <= 1_000_000):
            raise ValueError(f"trade_amount must be between 1 and 1,000,000, got {v}")
        value = v
    elif key == "trailing_stop_pct":
        v = float(value)
        if not (0.01 <= v <= 50):
            raise ValueError(f"trailing_stop_pct must be between 0.01 and 50, got {v}")
        value = v
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
        # Capture old value before updating
        old_row = conn.execute(
            f"SELECT {key} FROM user_settings WHERE user_id=?", (user_id,)
        ).fetchone()
        old_val = old_row[0] if old_row else None
        conn.execute(f"UPDATE user_settings SET {key}=? WHERE user_id=?", (value, user_id))
    if key not in _no_audit:
        write_audit(user_id, "setting_change", {"key": key, "old": old_val, "new": value})


# ── Trade helpers ─────────────────────────────────────────────────────────────

def open_trade(user_id, symbol, side, entry_price, amount, exchange, order_id, signal):
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO trades (user_id, symbol, side, entry_price, amount, exchange, order_id, signal)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, symbol, side, entry_price, amount, exchange, order_id, signal))
        trade_id = cur.lastrowid
    write_audit(user_id, "trade_open", {
        "trade_id": trade_id, "symbol": symbol, "side": side,
        "entry_price": entry_price, "amount": amount, "signal": signal
    })
    return trade_id


def close_trade(trade_id, exit_price, pnl, pnl_pct, close_reason="tp"):
    with get_conn() as conn:
        row = conn.execute("SELECT user_id, symbol, entry_price, amount FROM trades WHERE id=?",
                           (trade_id,)).fetchone()
        conn.execute("""
            UPDATE trades
            SET exit_price=?, pnl=?, pnl_pct=?, status='closed',
                closed_at=CURRENT_TIMESTAMP, close_reason=?
            WHERE id=?
        """, (exit_price, pnl, pnl_pct, close_reason, trade_id))
    if row:
        write_audit(row["user_id"], "trade_close", {
            "trade_id": trade_id, "symbol": row["symbol"],
            "entry_price": row["entry_price"], "exit_price": exit_price,
            "pnl": pnl, "pnl_pct": pnl_pct, "reason": close_reason
        })


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
                   s.signal_suggestions, s.trade_mode, s.arb_alerts
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


# ── API key submission rate limiting ─────────────────────────────────────────
# In-memory only; resets on restart (intentional — legitimate users just retry).
import time as _time
_api_key_failures: dict = {}   # user_id → [timestamp, ...]
_MAX_KEY_FAILURES  = 5
_KEY_FAILURE_WINDOW = 300      # 5 minutes
_KEY_COOLDOWN_SECS  = 600      # 10 minute lockout after too many failures


def record_api_key_failure(user_id: int) -> None:
    """Record a failed API key validation attempt."""
    now = _time.time()
    hits = _api_key_failures.get(user_id, [])
    hits = [t for t in hits if now - t < _KEY_FAILURE_WINDOW]
    hits.append(now)
    _api_key_failures[user_id] = hits


def is_api_key_rate_limited(user_id: int) -> tuple[bool, int]:
    """
    Return (is_limited, seconds_remaining).
    Limited if the user has >= _MAX_KEY_FAILURES attempts in _KEY_FAILURE_WINDOW.
    """
    now = _time.time()
    hits = _api_key_failures.get(user_id, [])
    hits = [t for t in hits if now - t < _KEY_FAILURE_WINDOW]
    _api_key_failures[user_id] = hits
    if len(hits) >= _MAX_KEY_FAILURES:
        oldest = min(hits)
        remaining = int(_KEY_COOLDOWN_SECS - (now - oldest))
        return True, max(0, remaining)
    return False, 0


def clear_api_key_failures(user_id: int) -> None:
    """Clear failure history after a successful key submission."""
    _api_key_failures.pop(user_id, None)


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
    try:
        tz_offset = int(tz_offset)
    except (TypeError, ValueError):
        raise ValueError("Timezone offset must be an integer")
    if not (-12 <= tz_offset <= 14):
        raise ValueError(f"Timezone offset must be between -12 and +14, got {tz_offset}")
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


# ── Paper Trading helpers ─────────────────────────────────────────────────────

def get_paper_balance(user_id: int) -> float:
    s = get_settings(user_id)
    return float(s.get("paper_balance", 1000.0))

def update_paper_balance(user_id: int, new_balance: float):
    update_setting(user_id, "paper_balance", round(new_balance, 6))

def open_paper_trade(user_id: int, symbol: str, side: str, entry_price: float, amount: float) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO paper_trades (user_id, symbol, side, entry_price, amount)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, symbol, side, entry_price, amount))
        return cur.lastrowid


def open_paper_trade_atomic(user_id: int, symbol: str, side: str,
                             entry_price: float, amount: float) -> int:
    """
    Open a paper trade and deduct the amount from paper balance in a single
    transaction so a crash between the two operations cannot leave the
    balance inconsistent.
    Returns the new trade_id, or raises if balance is insufficient.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT paper_balance FROM user_settings WHERE user_id=?", (user_id,)
        ).fetchone()
        balance = float(row["paper_balance"]) if row and row["paper_balance"] is not None else 1000.0
        if balance < amount:
            raise ValueError(f"Insufficient paper balance: {balance:.2f} < {amount:.2f}")
        cur = conn.execute("""
            INSERT INTO paper_trades (user_id, symbol, side, entry_price, amount)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, symbol, side, entry_price, amount))
        trade_id = cur.lastrowid
        new_balance = round(balance - amount, 6)
        conn.execute(
            "UPDATE user_settings SET paper_balance=? WHERE user_id=?",
            (new_balance, user_id)
        )
        return trade_id

def close_paper_trade(trade_id: int, exit_price: float, pnl: float, pnl_pct: float, reason: str = "tp"):
    with get_conn() as conn:
        conn.execute("""
            UPDATE paper_trades
            SET exit_price=?, pnl=?, pnl_pct=?, status='closed',
                closed_at=CURRENT_TIMESTAMP, close_reason=?
            WHERE id=?
        """, (exit_price, pnl, pnl_pct, reason, trade_id))

def get_open_paper_trades(user_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE user_id=? AND status='open'", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]

def get_paper_trade_history(user_id: int, limit: int = 20) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM paper_trades WHERE user_id=? ORDER BY opened_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
    return [dict(r) for r in rows]

def get_paper_stats(user_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                   COALESCE(SUM(pnl), 0) as total_pnl,
                   COALESCE(MAX(pnl_pct), 0) as best_pct,
                   COALESCE(MIN(pnl_pct), 0) as worst_pct
            FROM paper_trades WHERE user_id=? AND status='closed'
        """, (user_id,)).fetchone()
    return dict(row) if row else {}


# ── Webhook helpers ───────────────────────────────────────────────────────────

def generate_webhook_token(user_id: int) -> str:
    import secrets
    token = secrets.token_urlsafe(32)
    with get_conn() as conn:
        conn.execute("UPDATE users SET webhook_token=? WHERE user_id=?", (token, user_id))
    return token

def get_user_by_webhook_token(token: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE webhook_token=?", (token,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["api_key"]    = _dec(d.get("api_key", ""))
    d["api_secret"] = _dec(d.get("api_secret", ""))
    d["api_pass"]   = _dec(d.get("api_pass", ""))
    return d

def get_webhook_token(user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT webhook_token FROM users WHERE user_id=?", (user_id,)).fetchone()
    return row["webhook_token"] if row else None

def log_webhook(user_id: int, token: str, payload: str, action: str,
                symbol: str, status: str, message: str = ""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO webhook_logs (user_id, token, payload, action, symbol, status, message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, token, payload, action, symbol, status, message))

def get_webhook_logs(user_id: int, limit: int = 10) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM webhook_logs WHERE user_id=? ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ── DCA helpers ───────────────────────────────────────────────────────────────

def create_dca_plan(user_id: int, exchange_id: str, symbol: str,
                    amount_usdt: float, interval_sec: int,
                    price_ceiling: float = None) -> int:
    from datetime import datetime, timedelta
    next_run = (datetime.utcnow() + timedelta(seconds=interval_sec)).isoformat()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO dca_plans
                (user_id, exchange_id, symbol, amount_usdt, interval_sec, price_ceiling, next_run_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, exchange_id, symbol, amount_usdt, interval_sec, price_ceiling, next_run))
        return cur.lastrowid

def get_dca_plans(user_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM dca_plans WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]

def get_due_dca_plans() -> list:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT d.*, u.api_key, u.api_secret, u.api_pass
            FROM dca_plans d JOIN users u ON d.user_id = u.user_id
            WHERE d.status='active' AND d.next_run_at <= ?
        """, (now,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["api_key"]    = _dec(d.get("api_key", ""))
        d["api_secret"] = _dec(d.get("api_secret", ""))
        d["api_pass"]   = _dec(d.get("api_pass", ""))
        result.append(d)
    return result

def update_dca_after_run(plan_id: int, amount_usdt: float, qty_bought: float):
    from datetime import datetime
    with get_conn() as conn:
        row = conn.execute("SELECT interval_sec, runs_completed FROM dca_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return
        next_run = (datetime.utcnow() + timedelta(seconds=row["interval_sec"])).isoformat()
        conn.execute("""
            UPDATE dca_plans
            SET next_run_at=?, total_invested=total_invested+?,
                total_bought=total_bought+?, runs_completed=runs_completed+1
            WHERE id=?
        """, (next_run, amount_usdt, qty_bought, plan_id))

def set_dca_status(plan_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE dca_plans SET status=? WHERE id=?", (status, plan_id))

def get_dca_plan(plan_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM dca_plans WHERE id=?", (plan_id,)).fetchone()
    return dict(row) if row else None

def get_dca_stats(plan_id: int) -> dict:
    plan = get_dca_plan(plan_id)
    if not plan:
        return {}
    total_invested = plan["total_invested"]
    total_bought   = plan["total_bought"]
    avg_price = total_invested / total_bought if total_bought > 0 else 0.0
    return {
        "plan":          plan,
        "avg_price":     round(avg_price, 6),
        "total_invested":round(total_invested, 4),
        "total_bought":  round(total_bought, 8),
        "runs":          plan["runs_completed"],
    }


# ── Grid Trading helpers ───────────────────────────────────────────────────────

def create_grid_plan(user_id: int, exchange_id: str, symbol: str,
                     lower: float, upper: float, levels: int,
                     total_usdt: float) -> int:
    spacing = (upper - lower) / (levels - 1) if levels > 1 else 0
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO grid_plans
                (user_id, exchange_id, symbol, lower_price, upper_price,
                 grid_levels, total_usdt, grid_spacing)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, exchange_id, symbol, lower, upper, levels, total_usdt, spacing))
        return cur.lastrowid

def get_active_grids(user_id: int = None) -> list:
    with get_conn() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT * FROM grid_plans WHERE status='active' AND user_id=?", (user_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM grid_plans WHERE status='active'").fetchall()
    return [dict(r) for r in rows]

def get_grid_plan(plan_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM grid_plans WHERE id=?", (plan_id,)).fetchone()
    return dict(row) if row else None

def set_grid_status(plan_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE grid_plans SET status=? WHERE id=?", (status, plan_id))

def add_grid_order(plan_id: int, order_id: str, side: str, price: float, amount: float):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO grid_orders (plan_id, order_id, side, price, amount)
            VALUES (?, ?, ?, ?, ?)
        """, (plan_id, order_id, side, price, amount))

def get_grid_orders(plan_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM grid_orders WHERE plan_id=?", (plan_id,)
        ).fetchall()
    return [dict(r) for r in rows]

def update_grid_order_status(grid_order_id: int, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE grid_orders SET status=?, filled_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, grid_order_id)
        )

def update_grid_profit(plan_id: int, profit_delta: float):
    with get_conn() as conn:
        conn.execute(
            "UPDATE grid_plans SET total_profit=total_profit+? WHERE id=?",
            (profit_delta, plan_id)
        )


# ── Strategy Marketplace helpers ──────────────────────────────────────────────

def publish_strategy(user_id: int, name: str, description: str, settings: dict) -> int:
    with get_conn() as conn:
        # Replace any existing strategy for this user
        conn.execute("DELETE FROM strategies WHERE user_id=?", (user_id,))
        cur = conn.execute("""
            INSERT INTO strategies
                (user_id, name, description, symbol, multi_symbols,
                 take_profit, stop_loss, tp_mode, sl_mode, trailing_stop, trade_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, name, description,
            settings.get("symbol", "BTC/USDT"),
            settings.get("multi_symbols"),
            settings.get("take_profit", 2.0),
            settings.get("stop_loss", 1.0),
            settings.get("tp_mode", "pct"),
            settings.get("sl_mode", "pct"),
            settings.get("trailing_stop", 0),
            settings.get("trade_mode", "auto"),
        ))
        return cur.lastrowid

def get_strategies(limit: int = 20, offset: int = 0) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT s.*, u.username FROM strategies s
            JOIN users u ON s.user_id = u.user_id
            WHERE s.is_public=1
            ORDER BY s.subscriber_count DESC, s.created_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
    return [dict(r) for r in rows]

def get_strategy(strategy_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM strategies WHERE id=?", (strategy_id,)).fetchone()
    return dict(row) if row else None

def subscribe_strategy(subscriber_id: int, strategy_id: int, prev_settings: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO strategy_subscriptions
                (subscriber_id, strategy_id, prev_settings)
            VALUES (?, ?, ?)
        """, (subscriber_id, strategy_id, prev_settings))
        conn.execute(
            "UPDATE strategies SET subscriber_count=subscriber_count+1 WHERE id=?",
            (strategy_id,)
        )

def unsubscribe_strategy(subscriber_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT prev_settings, strategy_id FROM strategy_subscriptions WHERE subscriber_id=?
        """, (subscriber_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE strategies SET subscriber_count=MAX(0,subscriber_count-1) WHERE id=?",
                (row["strategy_id"],)
            )
            conn.execute(
                "DELETE FROM strategy_subscriptions WHERE subscriber_id=?", (subscriber_id,)
            )
            return row["prev_settings"]
    return None

def get_user_strategy_sub(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT ss.*, s.name FROM strategy_subscriptions ss
            JOIN strategies s ON ss.strategy_id = s.id
            WHERE ss.subscriber_id=?
        """, (user_id,)).fetchone()
    return dict(row) if row else None

def get_strategy_leaderboard(limit: int = 10) -> list:
    """Return top strategies by 30-day PnL of the strategy author."""
    since = (datetime.utcnow() - timedelta(days=30)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT s.id, s.name, s.subscriber_count, s.user_id,
                   COALESCE(SUM(t.pnl), 0) as pnl_30d,
                   COUNT(t.id) as trades_30d,
                   SUM(CASE WHEN t.pnl > 0 THEN 1 ELSE 0 END) as wins_30d
            FROM strategies s
            LEFT JOIN trades t ON t.user_id = s.user_id
                AND t.status='closed' AND t.closed_at >= ?
            WHERE s.is_public=1
            GROUP BY s.id
            ORDER BY pnl_30d DESC
            LIMIT ?
        """, (since, limit)).fetchall()
    return [dict(r) for r in rows]


# ── Audit Log helpers ─────────────────────────────────────────────────────────

def write_audit(user_id: int, event_type: str, details: dict):
    """Append-only audit entry. Never raises to caller."""
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO audit_log (user_id, event_type, details)
                VALUES (?, ?, ?)
            """, (user_id, event_type, json.dumps(details, default=str)))
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).warning(f"[AUDIT] write failed: {e}")

def get_audit_log(user_id: int = None, limit: int = 20) -> list:
    with get_conn() as conn:
        if user_id:
            rows = conn.execute("""
                SELECT * FROM audit_log WHERE user_id=?
                ORDER BY created_at DESC LIMIT ?
            """, (user_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
    return [dict(r) for r in rows]


# ── Web Dashboard token helpers ───────────────────────────────────────────────

def create_webdash_token(user_id: int) -> str:
    import secrets
    from datetime import datetime, timedelta
    token   = secrets.token_urlsafe(24)
    expires = (datetime.utcnow() + timedelta(hours=24)).isoformat()
    with get_conn() as conn:
        conn.execute("DELETE FROM webdash_tokens WHERE user_id=?", (user_id,))
        conn.execute("""
            INSERT INTO webdash_tokens (user_id, token, expires_at)
            VALUES (?, ?, ?)
        """, (user_id, token, expires))
    return token

def get_webdash_token_user(token: str) -> dict | None:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT wt.user_id, wt.expires_at FROM webdash_tokens wt
            WHERE wt.token=? AND wt.expires_at > ?
        """, (token, now)).fetchone()
    return dict(row) if row else None


# ── Analytics helpers ─────────────────────────────────────────────────────────

def get_analytics_data(user_id: int, days: int = 30) -> list:
    """Return closed trades for analytics period."""
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM trades
            WHERE user_id=? AND status='closed' AND closed_at >= ?
            ORDER BY closed_at ASC
        """, (user_id, since)).fetchall()
    return [dict(r) for r in rows]

def get_full_trade_history(user_id: int, days: int = 0) -> list:
    with get_conn() as conn:
        if days > 0:
            since = (datetime.utcnow() - timedelta(days=days)).isoformat()
            rows = conn.execute("""
                SELECT * FROM trades WHERE user_id=? AND status='closed' AND closed_at >= ?
                ORDER BY closed_at ASC
            """, (user_id, since)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM trades WHERE user_id=? AND status='closed'
                ORDER BY closed_at ASC
            """, (user_id,)).fetchall()
    return [dict(r) for r in rows]
