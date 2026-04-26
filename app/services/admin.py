"""Admin utilities."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    Payment,
    PaymentStatus,
    Subscription,
    User,
)
from app.repos import payments as payments_repo
from app.repos import users as users_repo


async def is_admin(session: AsyncSession, user_id: int) -> bool:
    if user_id in settings.admin_ids_list:
        return True
    user = await session.get(User, user_id)
    return bool(user and user.is_admin)


async def stats_dashboard(session: AsyncSession) -> dict:
    total_users = await users_repo.total_users(session)
    active_24h = await users_repo.active_users_24h(session)
    revenue_total = await payments_repo.revenue_total(session)
    revenue_30d = await payments_repo.revenue_30d(session)
    paid_users = await session.execute(
        select(Payment.user_id).where(Payment.status == PaymentStatus.PAID).distinct()
    )
    paying = len(paid_users.scalars().all())
    active_subs = await session.execute(
        select(Subscription)
    )
    active_subs_list = active_subs.scalars().all()
    from app.utils.time import now_utc

    now = now_utc()
    active_count = sum(1 for s in active_subs_list if s.expires_at > now)
    return {
        "users_total": total_users,
        "users_active_24h": active_24h,
        "revenue_total_rub": revenue_total,
        "revenue_30d_rub": revenue_30d,
        "paying_users": paying,
        "active_subs": active_count,
        "conversion_pct": round(100 * paying / max(1, total_users), 2),
    }
