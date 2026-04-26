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


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("DB schema ensured")


async def healthcheck_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("DB healthcheck failed: %s", exc)
        return False
