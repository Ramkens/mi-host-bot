"""Log repository (audit trail)."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LogEntry


async def write(
    session: AsyncSession,
    *,
    kind: str,
    message: str,
    user_id: Optional[int] = None,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    session.add(
        LogEntry(kind=kind, message=message, user_id=user_id, meta=meta or {})
    )
