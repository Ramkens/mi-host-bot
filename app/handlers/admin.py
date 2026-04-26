"""Admin panel: stats, broadcast, add/remove admin, brand channel, post now."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.keyboards.main import admin_menu, back_to_menu
from app.repos import users as users_repo
from app.services.admin import is_admin, stats_dashboard
from app.services.channel import auto_brand, post_one

logger = logging.getLogger(__name__)
router = Router(name="admin")


class AdminFSM(StatesGroup):
    awaiting_broadcast = State()
    awaiting_new_admin = State()


async def _require_admin(session: AsyncSession, user: User) -> bool:
    return await is_admin(session, user.id)


@router.callback_query(F.data == "admin")
async def cb_admin(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    text = "<b>Админка Mi Host</b>\nВыберите действие:"
    if cb.message:
        try:
            await cb.message.edit_caption(
                caption=text, parse_mode="HTML", reply_markup=admin_menu()
            )
        except Exception:
            await cb.message.answer(
                text, parse_mode="HTML", reply_markup=admin_menu()
            )
    await cb.answer()


@router.callback_query(F.data == "admin:stats")
async def cb_stats(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    s = await stats_dashboard(session)
    text = (
        "<b>Статистика</b>\n\n"
        f"Юзеров: <b>{s['users_total']}</b> (24h: {s['users_active_24h']})\n"
        f"Платящих: <b>{s['paying_users']}</b> (конверсия {s['conversion_pct']}%)\n"
        f"Активных подписок: <b>{s['active_subs']}</b>\n\n"
        f"Доход всего: <b>{s['revenue_total_rub']} ₽</b>\n"
        f"Доход 30д: <b>{s['revenue_30d_rub']} ₽</b>"
    )
    if cb.message:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=admin_menu())
    await cb.answer()


@router.callback_query(F.data == "admin:broadcast")
async def cb_broadcast(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_broadcast)
    if cb.message:
        await cb.message.answer(
            "Пришлите текст рассылки одним сообщением.\nДля отмены: /cancel"
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_broadcast)
async def do_broadcast(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await state.clear()
        return
    if msg.text == "/cancel":
        await state.clear()
        await msg.answer("Отменено")
        return
    text = msg.html_text or msg.text or ""
    if not text.strip():
        await msg.answer("Пустое сообщение, отмена.")
        await state.clear()
        return
    res = await session.execute(select(User.id).where(User.is_blocked.is_(False)))
    ids = [r for (r,) in res.all()]
    sent, failed = 0, 0
    for uid in ids:
        try:
            await msg.bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.04)  # ~25 msg/s, well below TG limit
        except Exception:
            failed += 1
    await msg.answer(f"Готово. Отправлено: {sent}, ошибок: {failed}")
    await state.clear()


@router.callback_query(F.data == "admin:add_admin")
async def cb_add_admin(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_new_admin)
    if cb.message:
        await cb.message.answer(
            "Пришлите Telegram ID нового админа одним сообщением.\nДля отмены: /cancel"
        )
    await cb.answer()


@router.message(Command("addadmin"))
async def cmd_add_admin(
    msg: Message, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await msg.answer("Использование: /addadmin <user_id>")
        return
    target_id = int(parts[1])
    target = await users_repo.by_id(session, target_id)
    if not target:
        # Pre-create the user record so the flag sticks even before they /start.
        target = User(id=target_id, is_admin=True)
        session.add(target)
    else:
        await users_repo.set_admin(session, target_id, True)
    await msg.answer(f"✓ Админ {target_id} добавлен.")


@router.message(Command("rmadmin"))
async def cmd_rm_admin(
    msg: Message, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await msg.answer("Использование: /rmadmin <user_id>")
        return
    target_id = int(parts[1])
    if target_id == user.id:
        await msg.answer("Нельзя снять админку с себя.")
        return
    await users_repo.set_admin(session, target_id, False)
    await msg.answer(f"✓ {target_id} больше не админ.")


@router.message(AdminFSM.awaiting_new_admin)
async def receive_new_admin(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if msg.text == "/cancel":
        await state.clear()
        await msg.answer("Отменено")
        return
    text = (msg.text or "").strip()
    if not text.isdigit():
        await msg.answer("ID должен быть числом.")
        return
    target_id = int(text)
    target = await users_repo.by_id(session, target_id)
    if not target:
        target = User(id=target_id, is_admin=True)
        session.add(target)
    else:
        await users_repo.set_admin(session, target_id, True)
    await msg.answer(f"✓ Админ {target_id} добавлен.")
    await state.clear()


@router.callback_query(F.data == "admin:brand")
async def cb_brand(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    ok = await auto_brand(cb.bot)
    if cb.message:
        await cb.message.answer(
            "✓ Канал брендирован" if ok else "Канал не настроен (CHANNEL_ID пуст).",
            reply_markup=back_to_menu(),
        )
    await cb.answer()


@router.callback_query(F.data == "admin:post_now")
async def cb_post_now(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    mid = await post_one(cb.bot)
    if cb.message:
        await cb.message.answer(
            f"✓ Опубликовано (msg_id={mid})" if mid else "Канал не настроен.",
            reply_markup=back_to_menu(),
        )
    await cb.answer()


@router.message(Command("stats"))
async def cmd_stats(msg: Message, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        return
    s = await stats_dashboard(session)
    await msg.answer(
        f"Юзеров: {s['users_total']} | Платящих: {s['paying_users']} ({s['conversion_pct']}%)\n"
        f"Активных подписок: {s['active_subs']}\n"
        f"Доход всего: {s['revenue_total_rub']}₽ | 30д: {s['revenue_30d_rub']}₽"
    )
