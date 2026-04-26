"""Anti-abuse heuristics."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LogEntry, User
from app.utils.time import now_utc


async def is_suspicious_new_account(session: AsyncSession, user: User) -> bool:
    """Detect new accounts trying to abuse referral / mini-game bonuses."""
    age = now_utc() - user.created_at
    if age < timedelta(minutes=10):
        return True
    return False


async def too_many_referrals_from_ip(session: AsyncSession, user_id: int) -> bool:
    """Placeholder — IP signal is not present in this minimal stack."""
    return False


async def recent_minigame_attempts(
    session: AsyncSession, user_id: int, minutes: int = 5
) -> int:
    threshold = now_utc() - timedelta(minutes=minutes)
    res = await session.execute(
        select(LogEntry).where(
            LogEntry.user_id == user_id,
            LogEntry.kind == "minigame.attempt",
            LogEntry.created_at >= threshold,
        )
    )
    return len(list(res.scalars()))
