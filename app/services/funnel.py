"""Sales funnel + auto-return logic."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config import settings
from app.db.base import SessionLocal
from app.repos import payments as payments_repo
from app.repos import subscriptions as subs_repo
from app.repos import users as users_repo
from app.utils.time import fmt_msk, humanize_delta, now_utc

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


async def remind_expiring_subs(bot: "Bot") -> int:
    sent = 0
    async with SessionLocal() as s:
        subs = await subs_repo.expiring_soon(s, hours=24)
    for sub in subs:
        try:
            await bot.send_message(
                sub.user_id,
                "⏰ <b>Подписка истекает</b>\n\n"
                f"Продукт: {sub.product.value}\n"
                f"Истекает: <code>{fmt_msk(sub.expires_at)}</code> "
                f"(через {humanize_delta(sub.expires_at - now_utc())})\n\n"
                "Продлить — /menu → Купить.",
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("remind expiring: %s", exc)
    return sent


async def reach_out_to_churned(bot: "Bot") -> int:
    sent = 0
    async with SessionLocal() as s:
        subs = await subs_repo.churned(s, days_since_expiry=3)
    for sub in subs:
        try:
            await bot.send_message(
                sub.user_id,
                "👋 Скучаем!\n\n"
                "Ваша подписка Mi Host истекла 3 дня назад. Возвращайтесь — "
                "вернём цену со скидкой и +3 бонусных дня. Жми /menu.",
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("reach churned: %s", exc)
    return sent


async def remind_unpaid_invoices(bot: "Bot") -> int:
    sent = 0
    async with SessionLocal() as s:
        pending = await payments_repo.list_pending(s)
    for p in pending:
        # Only nudge invoices >30 min old, <24h old, once.
        age = (now_utc() - p.created_at).total_seconds()
        if age < 1800 or age > 86400:
            continue
        try:
            await bot.send_message(
                p.user_id,
                "💳 У вас есть неоплаченный счёт.\n\n"
                f"Сумма: {p.amount_rub}₽\n"
                f"Оплата: {p.pay_url}\n\n"
                "После оплаты подписка активируется автоматически.",
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("remind unpaid: %s", exc)
    return sent
