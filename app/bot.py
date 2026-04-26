"""Bot singleton + dispatcher factory."""
from __future__ import annotations

from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import settings
from app.handlers import build_root_router
from app.middlewares.db import DbMiddleware
from app.middlewares.throttle import ThrottleMiddleware

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
    return dp
