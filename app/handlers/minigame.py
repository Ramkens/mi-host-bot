"""Mini-game: 12h cooldown, +1 day to all active subs, anti-abuse."""
from __future__ import annotations

import logging
import random
from datetime import timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User
from app.keyboards.main import back_to_menu
from app.repos import logs as logs_repo
from app.repos import subscriptions as subs_repo
from app.repos import users as users_repo
from app.services.antiabuse import (
    is_suspicious_new_account,
    recent_minigame_attempts,
)
from app.services.images import ASSETS, generate_all
from app.utils.time import humanize_delta, now_utc

logger = logging.getLogger(__name__)
router = Router(name="minigame")


@router.callback_query(F.data == "minigame")
async def cb_minigame(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    p = ASSETS / "minigame.png"
    if not p.exists():
        generate_all()
    now = now_utc()
    cooldown = timedelta(hours=settings.minigame_cooldown_hours)

    # Subscription gate.
    if not await subs_repo.is_active_any(session, user.id):
        text = (
            "<b>Мини-игра</b>\n\n"
            "Доступна только при активной подписке.\n"
            "Сначала оформите хостинг — потом каждый раз +1 день к подписке."
        )
        await _reply(cb, text)
        return

    # Anti-abuse.
    if await is_suspicious_new_account(session, user):
        await cb.answer("Аккаунт слишком новый, попробуйте позже.", show_alert=True)
        return
    if await recent_minigame_attempts(session, user.id, minutes=2) > 3:
        await cb.answer("Слишком много попыток, подождите.", show_alert=True)
        return

    # Cooldown.
    if user.last_minigame_at:
        elapsed = now - user.last_minigame_at
        if elapsed < cooldown:
            wait = cooldown - elapsed
            await cb.answer(
                f"Ещё рано. Через {humanize_delta(wait)}.", show_alert=True
            )
            return

    # Roll. ~85% you "win" the +1 day, 15% nothing (keeps it gamey, no negative).
    won = random.random() < 0.85
    user.last_minigame_at = now
    await logs_repo.write(
        session,
        kind="minigame.attempt",
        message="won" if won else "loss",
        user_id=user.id,
    )
    if won:
        # Extend each currently active subscription by 1 day.
        subs = await subs_repo.list_for_user(session, user.id)
        extended_any = False
        for s in subs:
            if s.expires_at > now:
                await subs_repo.extend(session, user.id, s.product, settings.minigame_bonus_days)
                extended_any = True
        await users_repo.add_xp(session, user.id, 10)
        await users_repo.add_coins(session, user.id, 5)
        text = (
            "<b>🎲 Мини-игра</b>\n\n"
            f"Победа! +{settings.minigame_bonus_days} день ко всем активным подпискам.\n"
            "Возвращайтесь через 12 часов!"
        )
        if not extended_any:
            text += "\n\n<i>Активных подписок нет — продлевать нечего.</i>"
    else:
        text = (
            "<b>🎲 Мини-игра</b>\n\n"
            "Не повезло — но не расстраивайтесь, через 12 часов попробуете снова."
        )
    await _reply(cb, text, photo=p)


async def _reply(cb: CallbackQuery, text: str, *, photo=None) -> None:
    if cb.message:
        try:
            await cb.message.edit_caption(
                caption=text, parse_mode="HTML", reply_markup=back_to_menu()
            )
        except Exception:
            if photo:
                await cb.message.answer_photo(
                    FSInputFile(str(photo)),
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=back_to_menu(),
                )
            else:
                await cb.message.answer(
                    text, parse_mode="HTML", reply_markup=back_to_menu()
                )
    await cb.answer()
