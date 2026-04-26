"""/start, /menu, profile."""
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
from app.db.models import User
from app.keyboards.main import back_to_menu, main_menu
from app.repos import instances as inst_repo
from app.repos import settings as settings_repo
from app.repos import subscriptions as subs_repo
from app.repos import users as users_repo
from app.services.admin import is_admin
from app.services.images import ASSETS, generate_all
from app.services.slots import free_cardinal_slots
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
    free_card = await free_cardinal_slots(session)
    instances = await inst_repo.list_for_user(session, user.id)
    lines = [
        "<b>💎  MI HOST  💎</b>",
        "<i>хостинг FunPay Cardinal · 40 ₽/мес</i>",
        "<i>хостинг кастом-скриптов · 50 ₽ (STD) / 150 ₽ (PRO)</i>",
        "",
        f"🧩 Свободных серверов: <b>{free_card}</b> (под Cardinal)",
        "",
        f"👤 <b>{user.first_name or 'юзер'}</b>  ·  id <code>{user.id}</code>",
        f"🏅 lvl {user.level}  ·  xp {user.xp}  ·  🪙 {user.coins}",
    ]
    active_subs = [s for s in subs if s.expires_at > now_utc()]
    if active_subs:
        lines.append("")
        lines.append("<b>💎 Активные подписки</b>")
        for s in active_subs:
            lines.append(
                f"  💠 {s.product.value} — до <code>{fmt_msk(s.expires_at)}</code>"
            )
    else:
        lines.append("")
        lines.append("<i>🔹 нет активных подписок · /menu → 💎 Купить</i>")
    if instances:
        lines.append("")
        lines.append("<b>🖥️ Серверы</b>")
        status_icon = {
            "live": "🟢", "deploying": "🟡", "pending": "⚪",
            "suspended": "🟠", "failed": "🔴", "deleted": "⚫",
        }
        for inst in instances[:5]:
            tier = ((inst.config or {}).get("tier") or "std").lower()
            tier_suffix = " PRO" if tier == "pro" else ""
            ico = status_icon.get(inst.status.value, "⚪")
            lines.append(
                f"  {ico} #{inst.id} · {inst.product.value}{tier_suffix} · "
                f"{inst.status.value}"
            )
        if len(instances) > 5:
            lines.append(f"  <i>+ ещё {len(instances) - 5}</i>")
    return "\n".join(lines)


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(
    msg: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
) -> None:
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
    admin_id = settings.admin_ids_list[0] if settings.admin_ids_list else 0
    text = (
        "<b>Поддержка</b>\n\n"
        f"💠 Админ: <a href=\"tg://user?id={admin_id}\">написать в чат</a>\n"
        "💠 Время ответа: до 12 часов\n\n"
        "<b>Хочешь оплатить другой криптой?</b>\n"
        "Напиши админу: «оплата TON/BTC/ETH/…», он скинет адрес и вручную выдаст подписку.\n\n"
        "<i>Перед обращением — глянь /menu → «Мои инстансы» → Логи/Статус.</i>"
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
