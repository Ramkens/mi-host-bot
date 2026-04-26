"""Auto-replies / FAQ + chat moderation hooks."""
from __future__ import annotations

import re
from typing import Optional

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

router = Router(name="support")

FAQ = [
    (
        re.compile(r"\b(цен|стои|сколько|тариф)", re.I),
        "Cardinal — 40₽/мес, кастом-скрипты — 50₽/мес. Оплата CryptoBot. Подписка 30 дней.",
    ),
    (
        re.compile(r"\b(оплат|crypto|usdt|купить)", re.I),
        "Оплата только через CryptoBot. Откройте /menu → «Купить хостинг».",
    ),
    (
        re.compile(r"\b(golden|key|ключ funpay)", re.I),
        "golden_key — это токен FunPay. На funpay.com → DevTools → Cookies → golden_key.",
    ),
    (
        re.compile(r"\b(возврат|refund|вернуть)", re.I),
        "Возвраты возможны в течение 24 часов после оплаты, если инстанс не запускался.",
    ),
    (
        re.compile(r"\b(не работает|упал|сломал|ошибк)", re.I),
        "Откройте /menu → «Мои инстансы» → выберите ваш → «Логи». Чаще всего ответ там.",
    ),
]


@router.message(F.text & F.chat.type == "private")
async def auto_reply(msg: Message, session: AsyncSession, user: User) -> None:
    text = msg.text or ""
    if text.startswith("/"):
        return  # commands handled by other routers
    for pattern, reply in FAQ:
        if pattern.search(text):
            await msg.answer(reply)
            return
    # Otherwise, gentle prompt to use the menu.
    await msg.answer(
        "Откройте меню: /menu\n"
        "Если есть вопрос по конкретному инстансу — «Мои инстансы» → «Логи».",
    )
