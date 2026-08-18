"""
persistence.py — Restart-safe state management for DuysBot
===========================================================

PTB persistence stores bot_data in PostgreSQL (not local files), so it
works on Render's ephemeral filesystem and survives redeploys.

State that survives restarts (stored in the ``bot_state`` table via the
``PostgresPersistence`` class):
  ┌─ PENDING_INPUT         {uid: {field, ...}}   multi-step wizard state
  ├─ RECENTLY_SUGGESTED    {uid: {symbol: ts}}   signal notification cooldowns
  ├─ ARB_SEEN              {uid: {fp: ts}}        arb fingerprint cooldowns
  └─ ARB_SEL               {uid: [symbols]}       in-progress token picker

State that does NOT need persistence (cleared cleanly on restart):
  • _pending_confirms  — trade confirmations expire in 30s; not worth persisting
  • _suggestion/_arb/_key counters — just tick counts; restarting from 0 is fine
"""

import asyncio
import json
import logging

from telegram.ext import BasePersistence, PersistenceInput

from db import get_conn

logger = logging.getLogger(__name__)

# ── bot_data keys ─────────────────────────────────────────────────────────────
K_PENDING_INPUT      = "pending_input"       # {uid: {field:..., ...}}
K_RECENTLY_SUGGESTED = "recently_suggested"  # {uid: {symbol: float_ts}}
K_ARB_SEEN           = "arb_seen"            # {uid: {fingerprint: float_ts}}
K_ARB_SEL            = "arb_sel"             # {uid: [str, ...]}  token picker draft


# ── Synchronous DB helpers (run in worker threads via asyncio.to_thread) ──────

def _db_load(key: str, default=None):
    """Read a JSON blob from the ``bot_state`` table."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value_json FROM bot_state WHERE key = %s", (key,)
        ).fetchone()
    if row is None:
        return default
    return json.loads(row["value_json"])


def _db_save(key: str, data) -> None:
    """UPSERT a JSON blob into the ``bot_state`` table."""
    payload = json.dumps(data, default=str, ensure_ascii=False)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_state (key, value_json, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET
                value_json   = EXCLUDED.value_json,
                updated_at   = CURRENT_TIMESTAMP
            """,
            (key, payload),
        )


# ── Async wrappers (non-blocking for PTB's event loop) ────────────────────────

async def _load(key: str, default=None):
    try:
        return await asyncio.to_thread(_db_load, key, default)
    except Exception as exc:
        logger.warning("PostgresPersistence load %r failed: %s", key, exc)
        return default


async def _save(key: str, data) -> None:
    try:
        await asyncio.to_thread(_db_save, key, data)
    except Exception as exc:
        logger.warning("PostgresPersistence save %r failed: %s", key, exc)


# ── PostgreSQL-backed PTB persistence ────────────────────────────────────────

class PostgresPersistence(BasePersistence):
    """PTB ``BasePersistence`` implementation backed by PostgreSQL.

    All state is serialised to JSON and stored in the ``bot_state`` table
    (created by ``database.init_db()``). Every update is written immediately,
    so nothing is lost on crash or restart.
    """

    def __init__(self, update_interval: float = 60):
        super().__init__(
            store_data=PersistenceInput(
                bot_data=True,
                user_data=False,
                chat_data=False,
                callback_data=False,
            ),
            update_interval=update_interval,
        )

    # ── bot_data ───────────────────────────────────────────────────────────

    async def get_bot_data(self) -> dict:
        data = await _load("bot_data", default={})
        return data if isinstance(data, dict) else {}

    async def update_bot_data(self, data: dict) -> None:
        await _save("bot_data", data)

    async def refresh_bot_data(self, bot_data: dict) -> None:
        return

    # ── user_data / chat_data (interface completeness) ─────────────────────

    async def get_user_data(self) -> dict:
        data = await _load("user_data", default={})
        return data if isinstance(data, dict) else {}

    async def update_user_data(self, user_id: int, data: dict) -> None:
        all_data = await self.get_user_data()
        all_data[int(user_id)] = data
        await _save("user_data", all_data)

    async def drop_user_data(self, user_id: int) -> None:
        all_data = await self.get_user_data()
        all_data.pop(int(user_id), None)
        await _save("user_data", all_data)

    async def refresh_user_data(self, user_id: int, user_data: dict) -> None:
        return

    async def get_chat_data(self) -> dict:
        data = await _load("chat_data", default={})
        return data if isinstance(data, dict) else {}

    async def update_chat_data(self, chat_id: int, data: dict) -> None:
        all_data = await self.get_chat_data()
        all_data[int(chat_id)] = data
        await _save("chat_data", all_data)

    async def drop_chat_data(self, chat_id: int) -> None:
        all_data = await self.get_chat_data()
        all_data.pop(int(chat_id), None)
        await _save("chat_data", all_data)

    async def refresh_chat_data(self, chat_id: int, chat_data: dict) -> None:
        return

    # ── callback_data ──────────────────────────────────────────────────────

    async def get_callback_data(self):
        data = await _load("callback_data", default=None)
        return data if isinstance(data, tuple) else None

    async def update_callback_data(self, data) -> None:
        await _save("callback_data", data)

    # ── conversations ──────────────────────────────────────────────────────

    async def get_conversations(self, name: str) -> dict:
        data = await _load(f"conversations:{name}", default={})
        return data if isinstance(data, dict) else {}

    async def update_conversation(self, name: str, key, new_state) -> None:
        all_data = await self.get_conversations(name)
        key_repr = repr(tuple(key)) if isinstance(key, (list, tuple)) else str(key)
        if new_state is None:
            all_data.pop(key_repr, None)
        else:
            all_data[key_repr] = new_state
        await _save(f"conversations:{name}", all_data)

    # ── flush ──────────────────────────────────────────────────────────────

    async def flush(self) -> None:
        """Every update is written immediately, so there is nothing to flush."""
        return


def build_persistence() -> PostgresPersistence:
    """Return a PostgreSQL-backed persistence instance."""
    return PostgresPersistence(update_interval=60)


# ── Typed accessors ───────────────────────────────────────────────────────────

def get_pending_input(bot_data: dict) -> dict:
    return bot_data.setdefault(K_PENDING_INPUT, {})


def set_pending_input_for(bot_data: dict, uid: int, value: dict | None) -> None:
    store = bot_data.setdefault(K_PENDING_INPUT, {})
    if value is None:
        store.pop(uid, None)
    else:
        store[uid] = value


def get_recently_suggested(bot_data: dict) -> dict:
    return bot_data.setdefault(K_RECENTLY_SUGGESTED, {})


def get_arb_seen(bot_data: dict) -> dict:
    return bot_data.setdefault(K_ARB_SEEN, {})


def get_arb_sel(bot_data: dict, uid: int) -> list | None:
    return bot_data.get(K_ARB_SEL, {}).get(uid)


def set_arb_sel(bot_data: dict, uid: int, value: list | None) -> None:
    store = bot_data.setdefault(K_ARB_SEL, {})
    if value is None:
        store.pop(uid, None)
    else:
        store[uid] = value


# ── Startup restore ───────────────────────────────────────────────────────────

def restore_in_memory_state(bot_data: dict) -> None:
    """
    Called once in post_init after PTB loads persisted bot_data.

    1. Attaches PENDING_INPUT (PersistedDict) to bot_data — all future
       writes to PENDING_INPUT are automatically mirrored into bot_data.
    2. Populates PENDING_INPUT with previously saved wizard state.
    3. Restores _recently_suggested and _arb_seen in scheduler.
    """
    import utils
    import scheduler

    # ── 1 & 2. PENDING_INPUT — attach first, then populate ───────────────────
    utils.PENDING_INPUT.attach(bot_data, K_PENDING_INPUT)
    persisted_pi = bot_data.get(K_PENDING_INPUT, {})
    if persisted_pi:
        # Use super().update() to avoid triggering a redundant _push
        dict.update(utils.PENDING_INPUT, {int(k): v for k, v in persisted_pi.items()})
        logger.info(f"[PERSIST] Restored PENDING_INPUT: {len(utils.PENDING_INPUT)} entries")
    else:
        logger.info("[PERSIST] PENDING_INPUT: no saved state (clean start)")

    # ── 3. Scheduler cooldown dicts ───────────────────────────────────────────
    persisted_rs = bot_data.get(K_RECENTLY_SUGGESTED, {})
    if persisted_rs:
        scheduler._recently_suggested.update({int(k): dict(v) for k, v in persisted_rs.items()})
        logger.info(f"[PERSIST] Restored _recently_suggested: {len(scheduler._recently_suggested)} users")

    persisted_arb = bot_data.get(K_ARB_SEEN, {})
    if persisted_arb:
        scheduler._arb_seen.update({int(k): dict(v) for k, v in persisted_arb.items()})
        logger.info(f"[PERSIST] Restored _arb_seen: {len(scheduler._arb_seen)} users")


def sync_to_bot_data(bot_data: dict) -> None:
    """
    Called periodically by the scheduler to write the live module-level dicts
    back into bot_data so PostgresPersistence can persist them.

    PENDING_INPUT is synced automatically because we've patched utils.py
    to write through both the dict AND bot_data.  This function handles
    the scheduler dicts which are written to directly.
    """
    import scheduler
    bot_data[K_RECENTLY_SUGGESTED] = {
        str(uid): dict(v) for uid, v in scheduler._recently_suggested.items()
    }
    bot_data[K_ARB_SEEN] = {
        str(uid): dict(v) for uid, v in scheduler._arb_seen.items()
    }
