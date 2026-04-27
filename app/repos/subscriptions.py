"""Subscription repository."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProductKind, Subscription
from app.utils.time import now_utc


async def get(
    session: AsyncSession, user_id: int, product: ProductKind
) -> Optional[Subscription]:
    res = await session.execute(
        select(Subscription).where(
            Subscription.user_id == user_id, Subscription.product == product
        )
    )
    return res.scalar_one_or_none()


async def is_active_any(session: AsyncSession, user_id: int) -> bool:
    res = await session.execute(
        select(func.count())
        .select_from(Subscription)
        .where(
            Subscription.user_id == user_id, Subscription.expires_at > now_utc()
        )
    )
    return int(res.scalar_one()) > 0


async def extend(
    session: AsyncSession,
    user_id: int,
    product: ProductKind,
    days: int,
) -> Subscription:
    """Stacking extend: used for paid purchases. Adds ``days`` on top of the
    current expiry (or `now` if no sub / expired)."""
    sub = await get(session, user_id, product)
    base = max(now_utc(), sub.expires_at) if sub and sub.expires_at else now_utc()
    new_expires = base + timedelta(days=days)
    if sub is None:
        sub = Subscription(
            user_id=user_id, product=product, expires_at=new_expires
        )
        session.add(sub)
    else:
        sub.expires_at = new_expires
    await session.flush()
    return sub


async def ensure_at_least(
    session: AsyncSession,
    user_id: int,
    product: ProductKind,
    days: int,
) -> tuple[Subscription, int]:
    """Non-stacking: set expiry to max(now + days, current_expires).

    Returns ``(subscription, granted_days)`` where ``granted_days`` is how
    many extra days were actually added. Used for coupons so users can't
    accumulate many small free coupons into one long subscription.
    """
    sub = await get(session, user_id, product)
    target = now_utc() + timedelta(days=days)
    if sub is None:
        sub = Subscription(user_id=user_id, product=product, expires_at=target)
        session.add(sub)
        granted = days
    else:
        current = sub.expires_at or now_utc()
        if target > current:
            granted_delta = target - max(current, now_utc())
            granted = int(granted_delta.total_seconds() // 86400)
            sub.expires_at = target
        else:
            granted = 0
    await session.flush()
    return sub, granted


async def list_for_user(session: AsyncSession, user_id: int) -> list[Subscription]:
    res = await session.execute(
        select(Subscription).where(Subscription.user_id == user_id)
    )
    return list(res.scalars())


async def expiring_soon(session: AsyncSession, hours: int = 24) -> list[Subscription]:
    upper = now_utc() + timedelta(hours=hours)
    res = await session.execute(
        select(Subscription).where(
            Subscription.expires_at > now_utc(),
            Subscription.expires_at <= upper,
        )
    )
    return list(res.scalars())


async def churned(session: AsyncSession, days_since_expiry: int = 3) -> list[Subscription]:
    """Return subs that expired recently (between N and N+1 days ago)."""
    lo = now_utc() - timedelta(days=days_since_expiry + 1)
    hi = now_utc() - timedelta(days=days_since_expiry)
    res = await session.execute(
        select(Subscription).where(
            Subscription.expires_at > lo,
            Subscription.expires_at <= hi,
        )
    )
    return list(res.scalars())
