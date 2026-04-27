"""User repository."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.utils.time import now_utc


async def get_or_create(
    session: AsyncSession,
    user_id: int,
    *,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    language_code: Optional[str] = None,
) -> tuple[User, bool]:
    user = await session.get(User, user_id)
    created = False
    if user is None:
        user = User(
            id=user_id,
            username=username,
            first_name=first_name,
            language_code=language_code,
        )
        session.add(user)
        await session.flush()
        created = True
    else:
        # refresh basic profile fields
        if username and user.username != username:
            user.username = username
        if first_name and user.first_name != first_name:
            user.first_name = first_name
        user.last_seen_at = now_utc()
    return user, created


async def by_id(session: AsyncSession, user_id: int) -> Optional[User]:
    return await session.get(User, user_id)


async def set_admin(session: AsyncSession, user_id: int, value: bool) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(is_admin=value)
    )


async def list_admins(session: AsyncSession) -> list[User]:
    rows = await session.execute(select(User).where(User.is_admin.is_(True)))
    return list(rows.scalars())


async def block(session: AsyncSession, user_id: int, value: bool = True) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(is_blocked=value)
    )


async def total_users(session: AsyncSession) -> int:
    res = await session.execute(select(func.count()).select_from(User))
    return int(res.scalar_one())


async def active_users_24h(session: AsyncSession) -> int:
    from datetime import timedelta

    threshold = now_utc() - timedelta(hours=24)
    res = await session.execute(
        select(func.count()).select_from(User).where(User.last_seen_at >= threshold)
    )
    return int(res.scalar_one())
