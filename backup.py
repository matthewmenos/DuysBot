"""
backup.py - Automated PostgreSQL database backup
Runs pg_dump daily, saving a compressed, Fernet-encrypted dump to backups/.
Keeps last 7 backups.
"""

import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

from config import DATABASE_URL, ENCRYPTION_KEY

logger = logging.getLogger(__name__)
BACKUP_DIR  = Path("backups")
MAX_BACKUPS = 7  # keep last 7 daily backups


def run_backup() -> str:
    """
    Run ``pg_dump`` on the PostgreSQL database, encrypt the dump with Fernet,
    and save to ``backups/db_backup_YYYY-MM-DD_HHMMSS.dump.enc``.
    Returns the backup file path on success, raises on failure.
    """
    BACKUP_DIR.mkdir(exist_ok=True)
    ts   = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUP_DIR / f"db_backup_{ts}.dump.enc"

    # pg_dump with --format=custom produces a compact, compressed binary dump
    cmd = [
        "pg_dump",
        "--dbname", DATABASE_URL,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
    ]
    logger.info("Running pg_dump for backup ...")
    result = subprocess.run(cmd, capture_output=True)

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"pg_dump failed (exit {result.returncode}): {stderr}")

    raw_data = result.stdout
    if not raw_data:
        raise RuntimeError("pg_dump produced no output — check DATABASE_URL and server availability.")

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
        list(BACKUP_DIR.glob("db_backup_*.dump.enc"))
        + list(BACKUP_DIR.glob("db_backup_*.dump"))
        + list(BACKUP_DIR.glob("db_backup_*.sql.enc"))
        + list(BACKUP_DIR.glob("db_backup_*.sql")),
        key=os.path.getmtime,
    )
    while len(backups) > MAX_BACKUPS:
        old = backups.pop(0)
        old.unlink()
        logger.info(f"Removed old backup: {old.name}")


def get_backup_list() -> list:
    """Return a list of backed-up database dumps, newest first."""
    if not BACKUP_DIR.exists():
        return []
    backups = sorted(
        list(BACKUP_DIR.glob("db_backup_*.dump.enc"))
        + list(BACKUP_DIR.glob("db_backup_*.dump"))
        + list(BACKUP_DIR.glob("db_backup_*.sql.enc"))
        + list(BACKUP_DIR.glob("db_backup_*.sql")),
        key=os.path.getmtime,
        reverse=True,
    )
    return [
        {
            "name":    b.name,
            "size_kb": b.stat().st_size // 1024,
            "created": datetime.utcfromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M UTC"),
        }
        for b in backups
    ]
