"""User-side per-server settings editor + self-delete with password.

The buy wizard collects golden_key / Telegram bot token / access password /
locale once. After purchase the user can change any of these four fields
individually (no need to redo the whole setup), or delete the server by
re-entering their access password.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstanceStatus, ProductKind, User
from app.keyboards.main import (
    instance_actions,
    instance_edit_cancel,
    instance_locale_picker,
    instance_settings,
)
from app.repos import instances as inst_repo
from app.services.cardinal import update_tenant_config
from app.services.supervisor import supervisor

logger = logging.getLogger(__name__)
router = Router(name="server_settings")


class ServerEditFSM(StatesGroup):
    awaiting_new_gk = State()
    awaiting_new_tg_token = State()
    awaiting_new_password = State()
    awaiting_delete_password = State()


# ---------------------------------------------------------------------------
# Settings menu
# ---------------------------------------------------------------------------


def _fmt_settings(inst) -> str:
    cfg = inst.config or {}
    gk = cfg.get("golden_key", "")
    gk_view = (gk[:6] + "…" + gk[-4:]) if len(gk) >= 12 else "не задан"
    tg = "подключён" if cfg.get("telegram_token") else "не задан"
    pw = "задан" if cfg.get("telegram_secret") else "не задан"
    locale = (cfg.get("locale") or "ru").upper()
    return (
        f"<b>Настройки сервера #{inst.id}</b>\n\n"
        f"golden_key: <code>{gk_view}</code>\n"
        f"Telegram-бот: {tg}\n"
        f"Пароль доступа: {pw}\n"
        f"Язык: {locale}\n"
    )


@router.callback_query(F.data.startswith("inst:settings:"))
async def cb_settings(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not cb.data:
        return
    await state.clear()
    inst_id = int(cb.data.split(":")[2])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    if inst.product != ProductKind.CARDINAL:
        await cb.answer("Только для Cardinal", show_alert=True)
        return
    text = _fmt_settings(inst)
    if cb.message:
        try:
            await cb.message.edit_caption(
                caption=text,
                parse_mode="HTML",
                reply_markup=instance_settings(inst.id),
            )
        except Exception:  # noqa: BLE001
            await cb.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=instance_settings(inst.id),
            )
    await cb.answer()


# ---------------------------------------------------------------------------
# Edit individual fields
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("inst:edit:gk:"))
async def cb_edit_gk(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[3])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    await state.update_data(inst_id=inst_id)
    await state.set_state(ServerEditFSM.awaiting_new_gk)
    if cb.message:
        await cb.message.answer(
            "Пришли новый <code>golden_key</code> одним сообщением.",
            parse_mode="HTML",
            reply_markup=instance_edit_cancel(inst_id),
        )
    await cb.answer()


@router.message(ServerEditFSM.awaiting_new_gk)
async def msg_new_gk(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    inst_id = data.get("inst_id")
    if not inst_id:
        await state.clear()
        return
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await state.clear()
        return
    key = (msg.text or "").strip()
    if len(key) < 20:
        await msg.answer(
            "Ключ выглядит некорректно. Пришли golden_key целиком.",
            reply_markup=instance_edit_cancel(inst_id),
        )
        return
    inst.config = {**(inst.config or {}), "golden_key": key}
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    try:
        await update_tenant_config(inst.id, golden_key=key)
    except Exception:  # noqa: BLE001
        logger.exception("update golden_key failed")
        await msg.answer(
            "Не удалось обновить golden_key — попробуй ещё раз.",
            reply_markup=instance_actions(inst.id),
        )
        await state.clear()
        return
    await session.commit()
    await msg.answer(
        f"golden_key обновлён, сервер #{inst.id} перезапущен.",
        reply_markup=instance_actions(inst.id),
    )
    await state.clear()


@router.callback_query(F.data.startswith("inst:edit:tg:"))
async def cb_edit_tg(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[3])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    await state.update_data(inst_id=inst_id)
    await state.set_state(ServerEditFSM.awaiting_new_tg_token)
    if cb.message:
        await cb.message.answer(
            "Пришли новый <code>BOT_TOKEN</code> от @BotFather "
            "в формате <code>123456789:ABC...</code>.",
            parse_mode="HTML",
            reply_markup=instance_edit_cancel(inst_id),
        )
    await cb.answer()


@router.message(ServerEditFSM.awaiting_new_tg_token)
async def msg_new_tg_token(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    inst_id = data.get("inst_id")
    if not inst_id:
        await state.clear()
        return
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await state.clear()
        return
    token = (msg.text or "").strip()
    if ":" not in token or len(token) < 30:
        await msg.answer(
            "Неверный токен. Пришли токен от @BotFather целиком.",
            reply_markup=instance_edit_cancel(inst_id),
        )
        return
    inst.config = {**(inst.config or {}), "telegram_token": token}
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    try:
        await update_tenant_config(inst.id, telegram_token=token)
    except Exception:  # noqa: BLE001
        logger.exception("update tg token failed")
    await session.commit()
    await msg.answer(
        f"Telegram-бот обновлён, сервер #{inst.id} перезапущен.",
        reply_markup=instance_actions(inst.id),
    )
    await state.clear()


@router.callback_query(F.data.startswith("inst:edit:pw:"))
async def cb_edit_pw(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[3])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    await state.update_data(inst_id=inst_id)
    await state.set_state(ServerEditFSM.awaiting_new_password)
    if cb.message:
        await cb.message.answer(
            "Пришли новый <b>пароль доступа</b> (минимум 4 символа).\n"
            "Используется для входа в Cardinal-бот и для удаления сервера.",
            parse_mode="HTML",
            reply_markup=instance_edit_cancel(inst_id),
        )
    await cb.answer()


@router.message(ServerEditFSM.awaiting_new_password)
async def msg_new_password(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    inst_id = data.get("inst_id")
    if not inst_id:
        await state.clear()
        return
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await state.clear()
        return
    pw = (msg.text or "").strip()
    if len(pw) < 4:
        await msg.answer(
            "Минимум 4 символа.",
            reply_markup=instance_edit_cancel(inst_id),
        )
        return
    inst.config = {**(inst.config or {}), "telegram_secret": pw}
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    try:
        await update_tenant_config(inst.id, telegram_secret=pw)
    except Exception:  # noqa: BLE001
        logger.exception("update password failed")
    await session.commit()
    await msg.answer(
        f"Пароль обновлён, сервер #{inst.id} перезапущен.",
        reply_markup=instance_actions(inst.id),
    )
    await state.clear()


@router.callback_query(F.data.startswith("inst:edit:loc:"))
async def cb_edit_loc(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[3])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    if cb.message:
        await cb.message.answer(
            "Выбери язык авто-сообщений Cardinal:",
            reply_markup=instance_locale_picker(inst.id),
        )
    await cb.answer()


@router.callback_query(F.data.startswith("inst:setloc:"))
async def cb_setloc(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    parts = cb.data.split(":")
    inst_id = int(parts[2])
    locale = parts[3]
    if locale not in {"ru", "en", "uk"}:
        await cb.answer("Bad locale", show_alert=True)
        return
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    inst.config = {**(inst.config or {}), "locale": locale}
    try:
        await update_tenant_config(inst.id, locale=locale)
    except Exception:  # noqa: BLE001
        logger.exception("update locale failed")
    await session.commit()
    if cb.message:
        await cb.message.answer(
            f"Язык изменён на {locale.upper()}, сервер #{inst.id} перезапущен.",
            reply_markup=instance_actions(inst.id),
        )
    await cb.answer()


# ---------------------------------------------------------------------------
# Self-delete with password
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("inst:delete:"))
async def cb_delete(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[2])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    cfg = inst.config or {}
    if not cfg.get("telegram_secret"):
        await cb.answer(
            "У сервера не задан пароль — обратись в поддержку.", show_alert=True
        )
        return
    await state.update_data(inst_id=inst_id)
    await state.set_state(ServerEditFSM.awaiting_delete_password)
    if cb.message:
        await cb.message.answer(
            "<b>Удаление сервера</b>\n\n"
            "Это действие необратимо: сервер остановится, удалится из системы, "
            "подписка не вернётся.\n\n"
            "Пришли свой <b>пароль доступа</b> чтобы подтвердить.",
            parse_mode="HTML",
            reply_markup=instance_edit_cancel(inst_id),
        )
    await cb.answer()


@router.message(ServerEditFSM.awaiting_delete_password)
async def msg_delete_password(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    inst_id = data.get("inst_id")
    if not inst_id:
        await state.clear()
        return
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await state.clear()
        return
    cfg = inst.config or {}
    expected = (cfg.get("telegram_secret") or "").strip()
    got = (msg.text or "").strip()
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    if not expected or got != expected:
        await msg.answer(
            "Пароль не совпал. Удаление отменено.",
            reply_markup=instance_actions(inst.id),
        )
        await state.clear()
        return
    # Stop tenant first, then mark deleted and free the slot.
    try:
        if supervisor.tenants.get(inst.id):
            await supervisor.stop(inst.id)
    except Exception:  # noqa: BLE001
        logger.exception("supervisor.stop on self-delete failed")
    inst.status = InstanceStatus.DELETED
    inst.desired_state = "stopped"
    inst.actual_state = "stopped"
    inst.shard_id = None
    await session.commit()
    await msg.answer(
        f"Сервер #{inst.id} удалён. Спасибо, что был с нами.",
    )
    await state.clear()
