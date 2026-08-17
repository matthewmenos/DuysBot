"""db.py - PostgreSQL data-access layer for DuysBot.

Every ``get_conn()`` call opens a PostgreSQL connection using the
``DATABASE_URL`` connection string (set in config.py / .env).

SQL translation
---------------
The rest of the codebase writes portable SQL with a few convenience idioms:
  * ``?`` placeholders                        -> ``%s`` for PostgreSQL
  * ``INSERT OR IGNORE``                      -> ``INSERT ... ON CONFLICT DO NOTHING``
  * ``INTEGER PRIMARY KEY AUTOINCREMENT``     -> ``BIGSERIAL PRIMARY KEY``

These are converted on the fly inside ``PgConnection``, so callers
(database.py, referral.py, smart_orders.py, handlers.py, scheduler.py)
do not need to change.

Rows are dict-like and also support integer index access (positional as well as by key).

``INSERT ... RETURNING id`` is used directly by callers that need the new row
id — this is supported natively by PostgreSQL.
"""

import re
import logging

from config import DATABASE_URL

logger = logging.getLogger(__name__)


# ── PostgreSQL SQL translation ────────────────────────────────────────────────

_RE_PLACEHOLDER = re.compile(r"(?<![A-Za-z0-9_])[?](?![A-Za-z0-9_])")
_RE_PG_AUTOINCREMENT = re.compile(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.IGNORECASE)
_RE_INSERT_OR_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", re.IGNORECASE)
_RE_ALTER_ADD_COLUMN = re.compile(r"(ALTER\s+TABLE\s+\S+\s+ADD\s+COLUMN\s+)(?=\S)", re.IGNORECASE)


def _translate_pg(sql: str) -> str:
    """Convert portable SQL to PostgreSQL SQL."""
    # DDL: INTEGER PRIMARY KEY AUTOINCREMENT -> BIGSERIAL PRIMARY KEY
    sql = _RE_PG_AUTOINCREMENT.sub("BIGSERIAL PRIMARY KEY", sql)
    # INSERT OR IGNORE INTO t (...) VALUES (...) -> INSERT INTO t (...) VALUES (...) ON CONFLICT DO NOTHING
    if _RE_INSERT_OR_IGNORE.match(sql):
        sql = _RE_INSERT_OR_IGNORE.sub("INSERT INTO", sql, count=1)
        sql = re.sub(r";\s*$", "", sql)
        sql = sql.rstrip() + " ON CONFLICT DO NOTHING"
    # ALTER TABLE t ADD COLUMN c ... -> ALTER TABLE t ADD COLUMN IF NOT EXISTS c ...
    # PostgreSQL aborts the whole transaction when "column already exists", which
    # would silently discard the rest of the schema during bootstrap. Making the
    # ALTER idempotent keeps migrations safe to re-run.
    sql = _RE_ALTER_ADD_COLUMN.sub(r"\1IF NOT EXISTS ", sql, count=1)
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


class Row(dict):
    """dict-like row that also supports integer index access (positional as well as by key)."""

    def __getitem__(self, key):
        if isinstance(key, int):
            keys = list(self.keys())
            if -len(keys) <= key < len(keys):
                return dict.__getitem__(self, keys[key])
            raise IndexError(key)
        return dict.__getitem__(self, key)


class PgConnection:
    """PostgreSQL connection mimicking the connection surface the bot uses."""

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
        """Run a multi-statement script (used by database.init_db schema creation).

        Each statement is committed on its own so that one bad statement cannot
        abort the entire PostgreSQL transaction (which would otherwise silently
        discard every later CREATE TABLE and leave an empty schema).
        """
        for stmt in script.split(";"):
            stmt = _strip_sql_comments(stmt).strip()
            if not stmt:
                continue
            try:
                self.execute(stmt)
                self._conn.commit()
            except Exception as e:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                head = (stmt.splitlines()[0][:80] if stmt.splitlines() else stmt)
                logger.warning("pg executescript statement skipped (%s): %s", head, e)


# ── Public entry point ───────────────────────────────────────────────────────

def get_conn():
    """Return a PostgreSQL connection."""
    return PgConnection()