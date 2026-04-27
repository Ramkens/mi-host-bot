"""/start, /menu, support."""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User
from app.keyboards.main import main_menu
from app.repos import subscriptions as subs_repo
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
    name = (user.first_name or "").strip() or "друг"
    lines = [
        f"<b>Привет, {name}!</b>",
        "",
        "<b>MI HOST</b> — аренда FunPay Cardinal под ключ.",
        f"<i>{settings.price_cardinal_rub} ₽ / 30 дней · авто-запуск · авто-рестарт</i>",
        "",
        f"Свободных серверов сейчас: <b>{free_card}</b>",
    ]
    active_subs = [s for s in subs if s.expires_at > now_utc()]
    if active_subs:
        lines.append("")
        lines.append("<b>Твои подписки</b>")
        for s in active_subs:
            lines.append(
                f"  · {s.product.value} — до <code>{fmt_msk(s.expires_at)}</code>"
            )
        lines.append("")
        lines.append("<i>Открой «Мои серверы», чтобы управлять.</i>")
    else:
        lines.append("")
        lines.append("<i>Пока нет активных подписок — жми «Купить сервер».</i>")
    return "\n".join(lines)


async def _send_menu(target: Message, session: AsyncSession, user: User) -> None:
    img = _ensure_assets()
    text = await _greeting_text(session, user)
    admin = await is_admin(session, user.id)
    await target.answer_photo(
        photo=FSInputFile(str(img)),
        caption=text,
        parse_mode="HTML",
        reply_markup=main_menu(is_admin=admin),
    )


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(
    msg: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
) -> None:
    await _send_menu(msg, session, user)


@router.message(Command("menu"))
async def cmd_menu(msg: Message, session: AsyncSession, user: User) -> None:
    await _send_menu(msg, session, user)


@router.message(Command("servers"))
async def cmd_servers(msg: Message, session: AsyncSession, user: User) -> None:
    """Шорткат на список серверов."""
    from app.handlers.instances import _render_user_instances

    await _render_user_instances(msg, session, user)


@router.message(Command("buy"))
async def cmd_buy(msg: Message, session: AsyncSession, user: User) -> None:
    """Шорткат на меню покупки."""
    from app.keyboards.main import buy_menu as _buy_menu

    text = (
        "<b>Хостинг FunPay Cardinal</b>\n\n"
        f"<b>{settings.price_cardinal_rub} ₽ / 30 дней</b>\n"
        "Авто-запуск, авто-рестарт, смена golden_key и заливка конфигов прямо в боте.\n\n"
        "Сначала нужно прислать настройки, потом выставлю счёт."
    )
    await msg.answer(text, parse_mode="HTML", reply_markup=_buy_menu())


@router.message(Command("support"))
async def cmd_support(msg: Message) -> None:
    await _send_support(msg)


@router.callback_query(F.data == "menu")
async def cb_menu(
    cb: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Return to main menu — always re-render with the banner image.

    Try editing the existing photo caption first (cheap, no flicker). If the
    source message wasn't a photo (e.g. user came from a text-only screen),
    drop it and send a fresh photo message so the banner is always visible.
    Also clears any in-flight FSM wizard — «В меню» is an escape hatch.
    """
    await state.clear()
    if cb.message:
        text = await _greeting_text(session, user)
        admin = await is_admin(session, user.id)
        edited = False
        try:
            await cb.message.edit_caption(
                caption=text,
                parse_mode="HTML",
                reply_markup=main_menu(is_admin=admin),
            )
            edited = True
        except Exception:
            pass
        if not edited:
            try:
                await cb.message.delete()
            except Exception:
                pass
            await _send_menu(cb.message, session, user)
    await cb.answer()


@router.callback_query(F.data == "support")
async def cb_support(cb: CallbackQuery) -> None:
    if cb.message:
        await _send_support(cb.message)
    await cb.answer()


def _support_url() -> str:
    return settings.support_url or (
        f"tg://user?id={settings.admin_ids_list[0]}"
        if settings.admin_ids_list
        else "https://t.me/"
    )


async def _send_support(msg: Message) -> None:
    url = _support_url()
    text = (
        "<b>Поддержка</b>\n\n"
        f"Админ: <a href=\"{url}\">написать в Telegram</a>\n"
        "Время ответа: до 12 часов\n\n"
        "Перед обращением — посмотри «Мои серверы» → «Логи»."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Написать в Telegram", url=url)],
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]
    )
    await msg.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
