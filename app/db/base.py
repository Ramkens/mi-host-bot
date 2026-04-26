"""SQLAlchemy async engine + session factory."""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


def _normalize_url(url: str) -> str:
    # Render's "DATABASE_URL" usually starts with postgres:// — convert.
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


_db_url = _normalize_url(settings.database_url)
_engine_kwargs: dict = {"echo": False}
if "sqlite" not in _db_url:
    _engine_kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=20)
engine = create_async_engine(_db_url, **_engine_kwargs)

SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as s:
        yield s
