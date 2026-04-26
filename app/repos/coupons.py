"""Coupon repository: create / redeem / list.

Coupons are multi-use: a single code can be activated up to ``max_uses``
times. Each activation grants ``duration_hours`` of the target product
(Cardinal / Script STD / Script PRO) to the redeeming user.
"""
from __future__ import annotations

import secrets
import string
from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Coupon, ProductKind
from app.utils.time import now_utc


def _gen_code(prefix: str = "MH") -> str:
    body = "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8)
    )
    return f"{prefix}-{body}"


async def create(
    session: AsyncSession,
    *,
    product: ProductKind,
    tier: str = "std",
    duration_hours: int = 30 * 24,
    max_uses: int = 1,
    issued_by: Optional[int] = None,
    expires_in_hours: Optional[int] = 30 * 24,
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
        now_utc() + timedelta(hours=expires_in_hours)
        if expires_in_hours
        else None
    )
    # Keep legacy `days` populated for older code paths that read it.
    legacy_days = max(1, round(duration_hours / 24))
    cp = Coupon(
        code=code,
        product=product,
        tier=tier or "std",
        days=legacy_days,
        duration_hours=max(1, int(duration_hours)),
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


async def list_all(
    session: AsyncSession, *, only_unused: bool = False
) -> list[Coupon]:
    q = select(Coupon).order_by(Coupon.created_at.desc())
    res = await session.execute(q)
    rows = list(res.scalars())
    if only_unused:
        rows = [r for r in rows if (r.uses_count or 0) < (r.max_uses or 1)]
    return rows


def _is_exhausted(cp: Coupon) -> bool:
    return (cp.uses_count or 0) >= (cp.max_uses or 1)


async def redeem(
    session: AsyncSession, code: str, user_id: int
) -> tuple[bool, str, Optional[Coupon]]:
    """Returns (success, message, coupon).

 Does NOT commit — caller is expected to commit/rollback inside the
 surrounding request transaction.
 """
    cp = await by_code(session, code.strip().upper())
    if not cp:
        return False, "Купон не найден.", None
    if _is_exhausted(cp):
        return False, "Купон уже полностью использован.", None
    exp = cp.expires_at
    if exp is not None:
        # SQLite loses tz; compare naive-vs-naive in that case.
        if exp.tzinfo is None:
            now_naive = now_utc().replace(tzinfo=None)
            if exp < now_naive:
                return False, "Срок купона истёк.", None
        elif exp < now_utc():
            return False, "Срок купона истёк.", None

    cp.uses_count = (cp.uses_count or 0) + 1
    cp.used_by = user_id
    cp.used_at = now_utc()
    await session.flush()

    hours = cp.duration_hours or (cp.days or 30) * 24
    if hours % 24 == 0:
        span = f"{hours // 24} дн."
    else:
        span = f"{hours} ч."
    return True, f"Купон активирован: +{span} {cp.product.value}.", cp


async def delete(session: AsyncSession, code: str) -> bool:
    cp = await by_code(session, code)
    if not cp:
        return False
    await session.delete(cp)
    await session.flush()
    return True


def duration_hours(cp: Coupon) -> int:
    """Source-of-truth hours granted by a coupon activation."""
    return cp.duration_hours or (cp.days or 30) * 24


def remaining_uses(cp: Coupon) -> int:
    return max(0, (cp.max_uses or 1) - (cp.uses_count or 0))
