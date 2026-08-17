"""
db.py - Unified data-access layer (SQLite locally, PostgreSQL on Render).

Behaviour
---------
* If ``DATABASE_URL`` is set (Render auto-injects it for attached PostgreSQL
  databases), every ``get_conn()`` call opens a PostgreSQL connection.
* Otherwise SQLite is used exactly as before (``bot_data.db``), so local
  development keeps working unchanged.

SQL translation
---------------
The rest of the codebase writes SQLite-flavoured SQL:
  * ``?`` placeholders            -> ``%s`` for PostgreSQL
  * ``INSERT OR IGNORE``          -> ``INSERT ... ON CONFLICT DO NOTHING``
  * ``INTEGER PRIMARY KEY AUTOINCREMENT`` -> ``BIGSERIAL PRIMARY KEY``

These are converted on the fly inside ``PgConnection``, so callers (database.py,
referral.py, smart_orders.py, handlers.py, scheduler.py) do not need to change.
Rows are dict-like and also support integer index access (sqlite3.Row style).

``INSERT ... RETURNING id`` is used directly by callers that need the new row
id — this is supported natively by both SQLite (>= 3.35) and PostgreSQL.
"""

import os
import re
import logging

from config import DB_PATH

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)


class Row(dict):
    """dict-like row that also supports integer index access (sqlite3.Row style)."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return dict.__getitem__(self, list(self.keys())[key])
        return dict.__getitem__(self, key)


# ── PostgreSQL SQL translation ────────────────────────────────────────────────

_RE_PLACEHOLDER = re.compile(r"(?<![A-Za-z0-9_])[?](?![A-Za-z0-9_])")
_RE_PG_AUTOINCREMENT = re.compile(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.IGNORECASE)
_RE_INSERT_OR_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", re.IGNORECASE)


def _translate_pg(sql: str) -> str:
    """Convert SQLite-flavoured SQL to PostgreSQL-flavoured SQL."""
    # DDL: INTEGER PRIMARY KEY AUTOINCREMENT -> BIGSERIAL PRIMARY KEY
    sql = _RE_PG_AUTOINCREMENT.sub("BIGSERIAL PRIMARY KEY", sql)
    # INSERT OR IGNORE INTO t (...) VALUES (...) -> INSERT INTO t (...) VALUES (...) ON CONFLICT DO NOTHING
    if _RE_INSERT_OR_IGNORE.match(sql):
        sql = _RE_INSERT_OR_IGNORE.sub("INSERT INTO", sql, count=1)
        sql = re.sub(r";\s*$", "", sql)
        sql = sql.rstrip() + " ON CONFLICT DO NOTHING"
    # ? -> %s (postgres placeholders)
    sql = _RE_PLACEHOLDER.sub("%s", sql)
    return sql


def _strip_sql_comments(stmt: str) -> str:
    """Remove whole-line ``--`` comments (used when splitting executescript)."""
    return "\n".join(
        ln for ln in stmt.splitlines() if not ln.strip().startswith("--")
    )


# ── PostgreSQL connection / cursor wrappers ───────────────────────────────────

class PgCursor:
    """Thin cursor wrapper exposing the fetch/rowcount surface callers use."""

    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = None  # callers use RETURNING id instead; kept for safety

    @property
    def rowcount(self):
        return self._cur.rowcount

    def _wrap(self, row):
        return Row(dict(row)) if row is not None else None

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cur.fetchall()]


class PgConnection:
    """PostgreSQL connection mimicking the sqlite3.Connection surface the bot uses."""

    def __init__(self):
        import psycopg
        from psycopg.rows import dict_row
        self._conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        self._conn.autocommit = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def execute(self, sql, params=None):
        pg_sql = _translate_pg(sql)
        cur = self._conn.execute(pg_sql, params if params is not None else ())
        return PgCursor(cur)

    def executescript(self, script):
        """Run a multi-statement script (used by database.init_db schema creation)."""
        for stmt in script.split(";"):
            stmt = _strip_sql_comments(stmt).strip()
            if not stmt:
                continue
            try:
                self.execute(stmt)
            except Exception as e:
                # Tables/columns may already exist — mirror SQLite's migration
                # flow which silently continues on "already exists".
                logger.warning("pg executescript statement skipped (%s): %s",
                               stmt.splitlines()[0][:60] if stmt.splitlines() else "", e)


# ── Public entry point ────────────────────────────────────────────────────────

def get_conn():
    """Return a database connection (PostgreSQL if DATABASE_URL set, else SQLite)."""
    if USE_POSTGRES:
        return PgConnection()

    import sqlite3
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL mode: allows concurrent reads, survives crashes without data loss
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
