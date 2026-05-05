"""
referral.py - Referral system for CryptoTradeBot
Users get a unique referral link. When a referred user subscribes,
the referrer earns a free month of access.
"""

import hashlib
import logging
from database import get_conn, activate_subscription, get_user, upsert_user

logger = logging.getLogger(__name__)

REFERRAL_REWARD_MONTHS = 1  # months given to referrer on successful referral


def generate_referral_code(user_id: int) -> str:
    """Generate a deterministic referral code from user_id."""
    raw = f"duys_{user_id}_trading"
    return hashlib.md5(raw.encode()).hexdigest()[:8].upper()


def get_referral_link(user_id: int, bot_username: str) -> str:
    code = generate_referral_code(user_id)
    return f"https://t.me/{bot_username}?start=ref_{code}"


def _ensure_referral_tables():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS referrals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id  INTEGER NOT NULL,
            referred_id  INTEGER NOT NULL UNIQUE,
            code         TEXT NOT NULL,
            rewarded     INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)


def record_referral(referrer_id: int, referred_id: int, code: str):
    """Record that referred_id joined via referrer's code."""
    _ensure_referral_tables()
    with get_conn() as conn:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO referrals (referrer_id, referred_id, code)
                VALUES (?, ?, ?)
            """, (referrer_id, referred_id, code))
        except Exception as e:
            logger.warning(f"record_referral error: {e}")


def reward_referrer(referred_id: int) -> int | None:
    """
    Called when referred_id subscribes for the first time.
    Returns referrer_id if a reward was granted, else None.
    """
    _ensure_referral_tables()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT referrer_id FROM referrals
            WHERE referred_id=? AND rewarded=0
        """, (referred_id,)).fetchone()
        if not row:
            return None
        referrer_id = row["referrer_id"]
        # Grant 1 free month to referrer
        activate_subscription(referrer_id, REFERRAL_REWARD_MONTHS)
        conn.execute("UPDATE referrals SET rewarded=1 WHERE referred_id=?", (referred_id,))
        logger.info(f"Referral reward: {referrer_id} earned {REFERRAL_REWARD_MONTHS} month for referring {referred_id}")
        return referrer_id


def get_referral_stats(user_id: int) -> dict:
    """Return referral stats for a user."""
    _ensure_referral_tables()
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) as c FROM referrals WHERE referrer_id=?", (user_id,)
        ).fetchone()["c"]
        rewarded = conn.execute(
            "SELECT COUNT(*) as c FROM referrals WHERE referrer_id=? AND rewarded=1", (user_id,)
        ).fetchone()["c"]
        return {
            "code":     generate_referral_code(user_id),
            "total":    total,
            "rewarded": rewarded,
            "pending":  total - rewarded,
        }


def resolve_start_referral(start_param: str) -> int | None:
    """
    Parse /start ref_XXXXXXXX and return the referrer's user_id.
    Returns None if not a valid referral code.
    """
    if not start_param or not start_param.startswith("ref_"):
        return None
    code = start_param[4:].upper()
    _ensure_referral_tables()
    # Find the user whose MD5-based code matches
    with get_conn() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
    for row in rows:
        uid = row["user_id"]
        if generate_referral_code(uid) == code:
            return uid
    return None
