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


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Best-effort cleanup of legacy columns/tables. We don't fail the boot
        # if any of these statements raise (e.g. on SQLite where DROP COLUMN
        # IF EXISTS is unsupported on older versions).
        for stmt in _LEGACY_DROPS:
            try:
                await conn.execute(text(stmt))
            except Exception as exc:  # noqa: BLE001
                logger.debug("legacy cleanup skipped (%s): %s", stmt, exc)
    logger.info("DB schema ensured")


async def healthcheck_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("DB healthcheck failed: %s", exc)
        return False
