"""Bot singleton + dispatcher factory."""
from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from app.config import settings
from app.handlers import build_root_router
from app.middlewares.db import DbMiddleware
from app.middlewares.throttle import ThrottleMiddleware

logger = logging.getLogger(__name__)

_bot: Optional[Bot] = None


def bot_singleton() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(
            settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    db_mw = DbMiddleware()
    th_mw = ThrottleMiddleware()
    for observer in (
        dp.message,
        dp.callback_query,
        dp.edited_message,
        dp.channel_post,
        dp.chat_member,
        dp.my_chat_member,
    ):
        observer.middleware(db_mw)
        observer.middleware(th_mw)
    dp.include_router(build_root_router())

    @dp.errors()
    async def _on_error(event: ErrorEvent) -> bool:
        exc = event.exception
        # Swallow noisy expected Telegram errors (callback expired,
        # message can't be edited because it's identical, etc.) — they
        # should never crash the polling loop.
        if isinstance(exc, TelegramBadRequest):
            msg = str(exc).lower()
            if (
                "query is too old" in msg
                or "message is not modified" in msg
                or "message to edit not found" in msg
                or "message can't be edited" in msg
            ):
                logger.debug("ignored TelegramBadRequest: %s", exc)
                return True
        logger.exception("Unhandled bot error: %s", exc)
        return True

    return dp
