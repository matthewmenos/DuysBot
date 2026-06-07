"""
persistence.py — Restart-safe state management for DuysBot
===========================================================

PTB PicklePersistence stores bot_data and user_data to disk automatically.
This module provides:

  • A single source of truth for all bot-level and user-level state keys
  • Typed helpers that read/write through bot_data with safe defaults
  • A restore() hook called once at startup to re-populate the module-level
    dicts that scheduler.py and utils.py use (backwards-compatibility shim)

State that survives restarts (stored in PTB bot_data via PicklePersistence):
  ┌─ PENDING_INPUT         {uid: {field, ...}}   multi-step wizard state
  ├─ RECENTLY_SUGGESTED    {uid: {symbol: ts}}   signal notification cooldowns
  ├─ ARB_SEEN              {uid: {fp: ts}}        arb fingerprint cooldowns
  └─ ARB_SEL               {uid: [symbols]}       in-progress token picker

State that does NOT need persistence (cleared cleanly on restart):
  • _pending_confirms  — trade confirmations expire in 30s; not worth persisting
  • _suggestion/_arb/_key counters — just tick counts; restarting from 0 is fine

Storage file: bot_persistence.pickle (same directory as main.py)
"""

import os
import logging
from telegram.ext import PicklePersistence

logger = logging.getLogger(__name__)

try:
    from config import PERSISTENCE_FILE
except ImportError:
    PERSISTENCE_FILE = os.getenv("PERSISTENCE_FILE", "bot_persistence.pickle")

# ── bot_data keys ─────────────────────────────────────────────────────────────
K_PENDING_INPUT      = "pending_input"       # {uid: {field:..., ...}}
K_RECENTLY_SUGGESTED = "recently_suggested"  # {uid: {symbol: float_ts}}
K_ARB_SEEN           = "arb_seen"            # {uid: {fingerprint: float_ts}}
K_ARB_SEL            = "arb_sel"             # {uid: [str, ...]}  token picker draft


def build_persistence() -> PicklePersistence:
    """
    Return a configured PicklePersistence instance.
    Only bot_data is persisted (user settings live in SQLite; user_data unused).
    """
    from telegram.ext._basepersistence import PersistenceInput

    return PicklePersistence(
        filepath=PERSISTENCE_FILE,
        store_data=PersistenceInput(
            bot_data=True,
            user_data=False,
            chat_data=False,
            callback_data=False,
        ),
        update_interval=30,   # flush to disk every 30 s
    )


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
    back into bot_data so PicklePersistence can flush them.

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
