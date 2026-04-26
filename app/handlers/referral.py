"""Referral system."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import ReferralEvent, User
from app.keyboards.main import back_to_menu

router = Router(name="referral")


@router.callback_query(F.data == "referral")
async def cb_referral(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    me = await cb.bot.get_me()
    link = f"https://t.me/{me.username}?start={user.id}"
    invited = await session.execute(
        select(func.count())
        .select_from(ReferralEvent)
        .where(ReferralEvent.referrer_id == user.id)
    )
    rewarded = await session.execute(
        select(func.count())
        .select_from(ReferralEvent)
        .where(
            ReferralEvent.referrer_id == user.id,
            ReferralEvent.rewarded.is_(True),
        )
    )
    text = (
        "<b>Реферальная программа</b>\n\n"
        f"Ваша ссылка:\n<code>{link}</code>\n\n"
        f"Приглашено: <b>{int(invited.scalar_one())}</b>\n"
        f"Принесли оплату: <b>{int(rewarded.scalar_one())}</b>\n\n"
        f"За каждого оплатившего вы получаете <b>+{settings.referral_bonus_days} дн.</b> "
        "к подписке (и +10 coins). Ваш реферал получает столько же."
    )
    if cb.message:
        try:
            await cb.message.edit_caption(
                caption=text, parse_mode="HTML", reply_markup=back_to_menu()
            )
        except Exception:
            await cb.message.answer(
                text, parse_mode="HTML", reply_markup=back_to_menu()
            )
    await cb.answer()
