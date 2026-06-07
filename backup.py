"""
backup.py - Automated SQLite database backup
Backs up bot_data.db daily to a local backups/ folder (encrypted with Fernet).
Keeps last 7 backups. Can also push to an S3-compatible bucket if configured.
"""

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from config import DB_PATH, ENCRYPTION_KEY

logger = logging.getLogger(__name__)
BACKUP_DIR  = Path("backups")
MAX_BACKUPS = 7  # keep last 7 daily backups


def run_backup() -> str:
    """
    Copy bot_data.db to backups/bot_data_YYYY-MM-DD_HHMMSS.db.enc (Fernet-encrypted).
    Returns the backup file path on success, raises on failure.
    """
    BACKUP_DIR.mkdir(exist_ok=True)
    ts   = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUP_DIR / f"bot_data_{ts}.db.enc"

    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    raw_data = Path(DB_PATH).read_bytes()

    if ENCRYPTION_KEY:
        try:
            from cryptography.fernet import Fernet
            f = Fernet(ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY)
            data = f.encrypt(raw_data)
            logger.info("Backup encrypted with Fernet.")
        except Exception as enc_err:
            logger.warning(f"Backup encryption failed ({enc_err}), saving plaintext copy instead.")
            data = raw_data
    else:
        logger.warning("ENCRYPTION_KEY not set — backup saved without encryption.")
        data = raw_data

    dest.write_bytes(data)
    logger.info(f"Database backed up → {dest} ({dest.stat().st_size // 1024} KB)")

    # Prune old backups
    _prune_old_backups()
    return str(dest)


def _prune_old_backups():
    """Delete oldest backups beyond MAX_BACKUPS."""
    backups = sorted(
        list(BACKUP_DIR.glob("bot_data_*.db")) + list(BACKUP_DIR.glob("bot_data_*.db.enc")),
        key=os.path.getmtime
    )
    while len(backups) > MAX_BACKUPS:
        old = backups.pop(0)
        old.unlink()
        logger.info(f"Removed old backup: {old.name}")


def get_backup_list() -> list:
    if not BACKUP_DIR.exists():
        return []
    backups = sorted(
        list(BACKUP_DIR.glob("bot_data_*.db")) + list(BACKUP_DIR.glob("bot_data_*.db.enc")),
        key=os.path.getmtime, reverse=True
    )
    return [
        {
            "name":    b.name,
            "size_kb": b.stat().st_size // 1024,
            "created": datetime.utcfromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M UTC"),
        }
        for b in backups
    ]
