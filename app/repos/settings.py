"""Mutable settings repo (key/value)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting


async def get(session: AsyncSession, key: str) -> Optional[str]:
    s = await session.get(Setting, key)
    return s.value if s else None


async def set_(session: AsyncSession, key: str, value: str) -> None:
    s = await session.get(Setting, key)
    if s is None:
        session.add(Setting(key=key, value=value))
    else:
        s.value = value


async def all_(session: AsyncSession) -> dict[str, str]:
    res = await session.execute(select(Setting))
    return {row.key: row.value for row in res.scalars()}
