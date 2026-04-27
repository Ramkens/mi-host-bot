"""Anti-abuse heuristics."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.utils.time import now_utc


async def is_suspicious_new_account(session: AsyncSession, user: User) -> bool:
    """Reject brand-new accounts (under 10 minutes old) for sensitive actions."""
    age = now_utc() - user.created_at
    if age < timedelta(minutes=10):
        return True
    return False
