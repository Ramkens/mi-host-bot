"""/start, /menu, profile, referral capture."""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import ReferralEvent, User
from app.keyboards.main import back_to_menu, main_menu
from app.repos import settings as settings_repo
from app.repos import subscriptions as subs_repo
from app.repos import users as users_repo
from app.services.admin import is_admin
from app.services.images import ASSETS, generate_all
from app.utils.time import fmt_msk, now_utc

logger = logging.getLogger(__name__)
router = Router(name="start")


def _ensure_assets() -> Path:
    p = ASSETS / "menu.png"
    if not p.exists():
        generate_all()
    return p


async def _greeting_text(session: AsyncSession, user: User) -> str:
    subs = await subs_repo.list_for_user(session, user.id)
    lines = [
        "<b>MI HOST</b>",
        "Хостинг FunPay Cardinal · 40 ₽/мес",
        "Хостинг кастом-скриптов · 50 ₽/мес",
        "",
        f"<b>Профиль:</b> {user.first_name or ''} #{user.id}",
        f"<b>Уровень:</b> {user.level} · <b>XP:</b> {user.xp} · <b>Coins:</b> {user.coins}",
    ]
    active_subs = [s for s in subs if s.expires_at > now_utc()]
    if active_subs:
        lines.append("")
        lines.append("<b>Активные подписки:</b>")
        for s in active_subs:
            lines.append(f"• {s.product.value} — до {fmt_msk(s.expires_at)}")
    else:
        lines.append("")
        lines.append("<i>У вас нет активных подписок.</i>")
    return "\n".join(lines)


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(
    msg: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
) -> None:
    # Referral capture from /start <ref_id>
    arg = (command.args or "").strip()
    if arg.isdigit() and int(arg) != user.id and user.referrer_id is None:
        try:
            ref_id = int(arg)
            referrer = await users_repo.by_id(session, ref_id)
            if referrer:
                user.referrer_id = ref_id
                session.add(
                    ReferralEvent(referrer_id=ref_id, referee_id=user.id)
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("referral capture: %s", exc)

    img = _ensure_assets()
    text = await _greeting_text(session, user)
    admin = await is_admin(session, user.id)
    await msg.answer_photo(
        photo=FSInputFile(str(img)),
        caption=text,
        parse_mode="HTML",
        reply_markup=main_menu(is_admin=admin),
    )


@router.message(Command("menu"))
async def cmd_menu(msg: Message, session: AsyncSession, user: User) -> None:
    img = _ensure_assets()
    text = await _greeting_text(session, user)
    admin = await is_admin(session, user.id)
    await msg.answer_photo(
        photo=FSInputFile(str(img)),
        caption=text,
        parse_mode="HTML",
        reply_markup=main_menu(is_admin=admin),
    )


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if cb.message:
        text = await _greeting_text(session, user)
        admin = await is_admin(session, user.id)
        try:
            await cb.message.edit_caption(
                caption=text,
                parse_mode="HTML",
                reply_markup=main_menu(is_admin=admin),
            )
        except Exception:
            await cb.message.answer(
                text, parse_mode="HTML", reply_markup=main_menu(is_admin=admin)
            )
    await cb.answer()


@router.callback_query(F.data == "profile")
async def cb_profile(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    p = ASSETS / "profile.png"
    if not p.exists():
        generate_all()
    text = await _greeting_text(session, user)
    if cb.message:
        try:
            await cb.message.edit_caption(
                caption=text, parse_mode="HTML", reply_markup=back_to_menu()
            )
        except Exception:
            await cb.message.answer_photo(
                FSInputFile(str(p)),
                caption=text,
                parse_mode="HTML",
                reply_markup=back_to_menu(),
            )
    await cb.answer()


@router.callback_query(F.data == "support")
async def cb_support(cb: CallbackQuery) -> None:
    text = (
        "<b>Поддержка</b>\n\n"
        f"Админ: tg://user?id={settings.admin_ids_list[0] if settings.admin_ids_list else ''}\n"
        "Время ответа: до 12 часов.\n"
        "Перед обращением проверьте раздел «Мои инстансы» — там есть логи и статус."
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
