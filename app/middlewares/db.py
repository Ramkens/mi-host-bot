"""DB session + user upsert middleware."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser

from app.db.base import SessionLocal
from app.repos import users as users_repo

logger = logging.getLogger(__name__)


class DbMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with SessionLocal() as session:
            data["session"] = session
            tg_user: TgUser | None = data.get("event_from_user")
            if tg_user is not None and not tg_user.is_bot:
                user, _ = await users_repo.get_or_create(
                    session,
                    tg_user.id,
                    username=tg_user.username,
                    first_name=tg_user.first_name,
                    language_code=tg_user.language_code,
                )
                data["user"] = user
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
