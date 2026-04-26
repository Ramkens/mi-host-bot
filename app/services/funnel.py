"""Sales funnel + auto-return logic."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import ProductKind, Setting, Subscription
from app.repos import payments as payments_repo
from app.repos import subscriptions as subs_repo
from app.repos import users as users_repo
from app.utils.time import fmt_msk, humanize_delta, now_utc

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


async def _reminded_key(user_id: int, kind: str, sub_id: int) -> str:
    return f"reminded:{kind}:{user_id}:{sub_id}"


async def _was_reminded(s, key: str) -> bool:
    res = await s.execute(select(Setting).where(Setting.key == key))
    return res.scalar_one_or_none() is not None


async def _mark_reminded(s, key: str) -> None:
    s.add(Setting(key=key, value="1"))
    await s.flush()


async def remind_expiring_subs(bot: "Bot") -> int:
    """Send a 3-days-before reminder asking the user to back up their data.

    Sent once per (user, sub, expires_at) so we don't spam.
    """
    sent = 0
    async with SessionLocal() as s:
        # 3-day window: subs expiring in (3, 3.5) days from now → fires once a
        # day from the 6h-cadence scheduler thanks to the dedup setting key.
        upper = now_utc() + timedelta(days=3)
        lower = now_utc() + timedelta(days=2, hours=20)
        res = await s.execute(
            select(Subscription).where(
                Subscription.expires_at > lower,
                Subscription.expires_at <= upper,
            )
        )
        subs = list(res.scalars())
        for sub in subs:
            key = await _reminded_key(sub.user_id, "expiry3d", sub.id)
            if await _was_reminded(s, key):
                continue
            text = _expiry_message(sub)
            try:
                await bot.send_message(sub.user_id, text)
                await _mark_reminded(s, key)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug("remind expiring: %s", exc)
        await s.commit()
    return sent


def _expiry_message(sub: Subscription) -> str:
    base = (
        "◾ <b>Подписка скоро истечёт</b>\n\n"
        f"Продукт: <b>{sub.product.value}</b>\n"
        f"Истекает: <code>{fmt_msk(sub.expires_at)}</code> "
        f"(через {humanize_delta(sub.expires_at - now_utc())})\n\n"
    )
    if sub.product == ProductKind.CARDINAL:
        base += (
            "<b>Важно:</b> после окончания подписки ваш Cardinal-инстанс и его данные "
            "будут удалены. До этого момента — <b>сохраните локально</b>:\n"
            "• <code>configs/auth.cfg</code>\n"
            "• <code>configs/main.cfg</code>\n"
            "• любые свои конфиги/правила/шаблоны автоответов\n\n"
            "Получить файлы можно через «Мои инстансы» → «Логи»/«Статус» (детально — у админа).\n\n"
        )
    else:
        base += (
            "<b>Важно:</b> после окончания подписки ваш скрипт и его данные будут удалены. "
            "<b>Сохраните локально</b> исходники и любые конфиги, которые добавляли.\n\n"
        )
    base += "Продлить — /menu → «Купить хостинг»."
    return base


async def reach_out_to_churned(bot: "Bot") -> int:
    sent = 0
    async with SessionLocal() as s:
        subs = await subs_repo.churned(s, days_since_expiry=3)
    for sub in subs:
        try:
            await bot.send_message(
                sub.user_id,
                "◇ Скучаем.\n\n"
                "Подписка Mi Host истекла 3 дня назад. Можешь продлить в любой момент — /menu.",
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
                "◾ У тебя есть неоплаченный счёт.\n\n"
                f"Сумма: {p.amount_rub}₽\n"
                f"Оплата: {p.pay_url}\n\n"
                "После оплаты подписка активируется автоматически.",
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("remind unpaid: %s", exc)
    return sent
