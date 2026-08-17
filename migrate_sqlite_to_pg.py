"""
migrate_sqlite_to_pg.py - one-time migration of bot_data.db (SQLite) to PostgreSQL.

Usage
-----
  1. Make sure psycopg is installed:  pip install -r requirements.txt
  2. Put your PostgreSQL connection string in .env as DATABASE_URL (Render's
     Postgres "Internal Database URL").
  3. Run:  python migrate_sqlite_to_pg.py

What it does
------------
* Creates the full schema in PostgreSQL (via database.init_db()).
* Copies every table's rows from bot_data.db into PostgreSQL, preserving
  primary-key ids (so relationships like grid_orders.plan_id stay valid).
* Resets PostgreSQL sequences so new inserts never collide with copied ids.
* Is idempotent — safe to re-run (uses ON CONFLICT DO NOTHING).

IMPORTANT
---------
* Your Render service's ENCRYPTION_KEY env var MUST equal the one used to
  encrypt the keys in this SQLite file, or stored API keys won't decrypt.
* Stop the bot before running this to get a consistent copy of bot_data.db.
"""

import os
import sqlite3
import sys

# ── 0. Load .env first so DATABASE_URL + ENCRYPTION_KEY are available ────────
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    sys.exit("DATABASE_URL is not set. Add it to .env first.")

os.environ["DATABASE_URL"] = DATABASE_URL  # force the app to use Postgres

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database  # noqa: E402  (schema + helpers, uses Postgres now)

DB_PATH = os.getenv("DB_PATH", "bot_data.db")
if not os.path.exists(DB_PATH):
    sys.exit(f"SQLite database not found: {DB_PATH}")

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

# ── 1. Create schema in PostgreSQL ────────────────────────────────────────────
print("Creating schema in PostgreSQL ...")
database.init_db()

# ── 2. Copy data ──────────────────────────────────────────────────────────────
src = sqlite3.connect(DB_PATH)
src.row_factory = sqlite3.Row
tables = [
    r["name"]
    for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
]

dest = psycopg.connect(DATABASE_URL, row_factory=dict_row)
total_rows = 0

try:
    for table in tables:
        cols = [r["name"] for r in src.execute(f"PRAGMA table_info({table})").fetchall()]
        rows = src.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        if not rows:
            print(f"  {table}: 0 rows")
            continue

        col_list = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join(["%s"] * len(cols))
        insert_sql = (
            f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) '
            "ON CONFLICT DO NOTHING"
        )
        with dest.cursor() as cur:
            cur.executemany(insert_sql, [tuple(r[c] for c in cols) for r in rows])
        dest.commit()
        total_rows += len(rows)
        print(f"  {table}: {len(rows)} rows")

        # Reset the autoincrement sequence so new inserts never collide
        if "id" in cols:
            try:
                with dest.cursor() as cur:
                    cur.execute(
                        f'SELECT setval(pg_get_serial_sequence(\'"{table}"\', \'id\'), '
                        f'GREATEST((SELECT COALESCE(MAX(id), 1) FROM "{table}"), 1), true)'
                    )
            except Exception as e:
                print(f"    (sequence reset skipped: {e})")
            dest.commit()
finally:
    src.close()
    dest.close()

print(f"\n✅ Migration complete — {total_rows} rows copied.")
print(
    "⚠️  Verify the ENCRYPTION_KEY on Render matches the one used to encrypt "
    "these keys, then redeploy the bot with DATABASE_URL set."
)
