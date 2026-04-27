"""Coupon repository: create / redeem / list."""
from __future__ import annotations

import secrets
import string
from datetime import timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Coupon, CouponRedemption, ProductKind
from app.utils.time import now_utc


def _gen_code(prefix: str = "MH") -> str:
    body = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    return f"{prefix}-{body}"


async def create(
    session: AsyncSession,
    *,
    product: ProductKind,
    days: int = 30,
    max_uses: int = 1,
    issued_by: Optional[int] = None,
    expires_in_days: Optional[int] = 30,
    note: Optional[str] = None,
    code: Optional[str] = None,
) -> Coupon:
    if not code:
        for _ in range(8):
            code = _gen_code()
            existing = await by_code(session, code)
            if not existing:
                break
    expires_at = (
        now_utc() + timedelta(days=expires_in_days) if expires_in_days else None
    )
    cp = Coupon(
        code=code,
        product=product,
        days=days,
        max_uses=max(1, int(max_uses)),
        uses_count=0,
        issued_by=issued_by,
        expires_at=expires_at,
        note=note,
    )
    session.add(cp)
    await session.flush()
    return cp


async def by_code(session: AsyncSession, code: str) -> Optional[Coupon]:
    res = await session.execute(select(Coupon).where(Coupon.code == code))
    return res.scalar_one_or_none()


async def list_all(session: AsyncSession, *, only_unused: bool = False) -> list[Coupon]:
    q = select(Coupon).order_by(Coupon.created_at.desc())
    if only_unused:
        q = q.where(Coupon.uses_count < Coupon.max_uses)
    res = await session.execute(q)
    return list(res.scalars())


async def redeem(
    session: AsyncSession, code: str, user_id: int
) -> tuple[bool, str, Optional[Coupon]]:
    """Returns (success, message, coupon).

    A multi-use coupon can be activated by up to ``max_uses`` DIFFERENT users;
    any single user can redeem it at most once.
    """
    cp = await by_code(session, code.strip().upper())
    if not cp:
        return False, "Купон не найден.", None
    if cp.uses_count >= cp.max_uses:
        return False, "Купон исчерпал лимит активаций.", None
    if cp.expires_at and cp.expires_at < now_utc():
        return False, "Срок купона истёк.", None
    # Per-user idempotency.
    existing = await session.execute(
        select(CouponRedemption).where(
            CouponRedemption.coupon_id == cp.id,
            CouponRedemption.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none():
        return False, "Ты уже активировал этот купон.", None
    session.add(CouponRedemption(coupon_id=cp.id, user_id=user_id))
    cp.uses_count = (cp.uses_count or 0) + 1
    cp.used_by = user_id
    cp.used_at = now_utc()
    await session.flush()
    return True, f"Купон активирован: +{cp.days} дн. {cp.product.value}.", cp


async def delete(session: AsyncSession, code: str) -> bool:
    cp = await by_code(session, code)
    if not cp:
        return False
    await session.delete(cp)
    await session.flush()
    return True
