"""Per-user throttling middleware."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.services.ratelimit import public_limiter


class ThrottleMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None and not public_limiter.allow(user.id, cost=1.0):
            if isinstance(event, CallbackQuery):
                await event.answer("Слишком часто. Подождите секунду.", show_alert=False)
                return None
            if isinstance(event, Message):
                # silently drop spam
                return None
        return await handler(event, data)
