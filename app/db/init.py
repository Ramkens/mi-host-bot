"""DB bootstrap.

For simplicity (and free-tier friendliness) we run `Base.metadata.create_all`
on startup. Alembic config is included in the repo for production migrations.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from app.db.base import Base, engine
from app.db import models  # noqa: F401  (register mappers)

logger = logging.getLogger(__name__)


# Idempotent cleanup of legacy columns/tables that the model no longer
# references. SQLAlchemy's create_all() never drops anything, so we run a few
# DROP IF EXISTS statements on Postgres to keep the schema tidy. Safe on
# repeat runs and on SQLite (NO-OP errors are swallowed).
_LEGACY_DROPS = (
    "ALTER TABLE IF EXISTS users DROP COLUMN IF EXISTS referrer_id",
    "ALTER TABLE IF EXISTS users DROP COLUMN IF EXISTS bonus_days",
    "ALTER TABLE IF EXISTS users DROP COLUMN IF EXISTS coins",
    "ALTER TABLE IF EXISTS users DROP COLUMN IF EXISTS level",
    "ALTER TABLE IF EXISTS users DROP COLUMN IF EXISTS xp",
    "ALTER TABLE IF EXISTS users DROP COLUMN IF EXISTS last_minigame_at",
    "DROP INDEX IF EXISTS ix_users_referrer",
    "DROP TABLE IF EXISTS referral_events",
)


# Idempotent schema additions for columns that the model has but pre-existing
# DBs don't yet. SQLAlchemy create_all() doesn't run ALTER on existing tables.
_FORWARD_ADDS = (
    "ALTER TABLE IF EXISTS coupons ADD COLUMN IF NOT EXISTS max_uses INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE IF EXISTS coupons ADD COLUMN IF NOT EXISTS uses_count INTEGER NOT NULL DEFAULT 0",
    # Backfill uses_count from legacy used_by column on first run.
    "UPDATE coupons SET uses_count = 1 WHERE used_by IS NOT NULL AND uses_count = 0",
)


async def init_db() -> None:
    # FSM persistence table (Postgres/SQLite-compatible DDL).
    from app.services.fsm_storage import ensure_fsm_table

    await ensure_fsm_table()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Best-effort cleanup of legacy columns/tables + forward-add of
        # newly-introduced columns. We don't fail the boot if any of these
        # statements raise (e.g. on SQLite where IF NOT EXISTS / DROP COLUMN
        # IF EXISTS aren't always supported).
        for stmt in (*_LEGACY_DROPS, *_FORWARD_ADDS):
            try:
                await conn.execute(text(stmt))
            except Exception as exc:  # noqa: BLE001
                logger.debug("schema cleanup skipped (%s): %s", stmt, exc)
    logger.info("DB schema ensured")


async def healthcheck_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("DB healthcheck failed: %s", exc)
        return False
