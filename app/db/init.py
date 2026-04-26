"""DB bootstrap.

For simplicity (and free-tier friendliness) we run `Base.metadata.create_all`
on startup. Alembic config is included in the repo for production migrations.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from app.db import models  # noqa: F401  (register mappers)
from app.db.base import Base, engine

logger = logging.getLogger(__name__)


# Lightweight idempotent schema upgrades for columns that were added after
# the first deploy. We keep them here rather than introducing alembic just
# for a handful of columns. Every statement uses IF NOT EXISTS / try/except
# so replaying on an already-migrated DB is a no-op.
_SCHEMA_PATCHES = (
    # Coupons: multi-use + hour-granularity + tier.
    "ALTER TABLE coupons ADD COLUMN IF NOT EXISTS tier VARCHAR(16) DEFAULT 'std'",
    "ALTER TABLE coupons ADD COLUMN IF NOT EXISTS duration_hours INTEGER DEFAULT 720",
    "ALTER TABLE coupons ADD COLUMN IF NOT EXISTS max_uses INTEGER DEFAULT 1",
    "ALTER TABLE coupons ADD COLUMN IF NOT EXISTS uses_count INTEGER DEFAULT 0",
    # Backfill from legacy rows that only had `days`.
    (
        "UPDATE coupons SET duration_hours = days * 24 "
        "WHERE duration_hours IS NULL OR duration_hours = 0"
    ),
    (
        "UPDATE coupons SET uses_count = 1 "
        "WHERE used_by IS NOT NULL AND (uses_count IS NULL OR uses_count = 0)"
    ),
)


async def _apply_patches() -> None:
    """Apply idempotent ALTERs. SQLite doesn't support IF NOT EXISTS on
 ADD COLUMN, so fall back to catching OperationalError on each stmt.
 """
    async with engine.begin() as conn:
        dialect = conn.dialect.name  # "postgresql" | "sqlite" | ...
        for stmt in _SCHEMA_PATCHES:
            sql = stmt
            if dialect == "sqlite" and "IF NOT EXISTS" in sql:
                sql = sql.replace("IF NOT EXISTS", "")
            try:
                await conn.execute(text(sql))
            except Exception as exc:  # noqa: BLE001
                # Column already exists / not-applicable-on-this-dialect — skip.
                logger.debug("schema patch skipped (%s): %s", exc, sql)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _apply_patches()
    logger.info("DB schema ensured")


async def healthcheck_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("DB healthcheck failed: %s", exc)
        return False
