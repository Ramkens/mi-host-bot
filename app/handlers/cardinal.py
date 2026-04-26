"""Cardinal-specific flows: setup wizard (golden_key) + key rotation."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstanceStatus, ProductKind, User
from app.keyboards.main import back_to_menu, instance_actions
from app.repos import instances as inst_repo
from app.repos import subscriptions as subs_repo
from app.services.cardinal import start_tenant, update_golden_key

logger = logging.getLogger(__name__)
router = Router(name="cardinal")


class CardinalSetup(StatesGroup):
    awaiting_key = State()
    awaiting_new_key = State()  # rotation


@router.message(Command("cardinal"))
async def cmd_cardinal(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    sub = await subs_repo.get(session, user.id, ProductKind.CARDINAL)
    if not sub or sub.expires_at <= sub.created_at:
        await msg.answer(
            "Нет активной подписки на Cardinal. Купите её через /menu → «Купить хостинг»."
        )
        return
    inst_list = await inst_repo.list_for_user(
        session, user.id, ProductKind.CARDINAL
    )
    if inst_list:
        await msg.answer(
            f"У вас уже есть Cardinal-инстанс #{inst_list[0].id}. Откройте его в /menu → «Мои инстансы»."
        )
        return
    await msg.answer(
        "<b>Настройка Cardinal</b>\n\n"
        "Пришлите ваш <code>golden_key</code> от FunPay одним сообщением. "
        "Он шифруется в БД и используется только для запуска вашего инстанса.\n\n"
        "Получить ключ: на funpay.com → DevTools → Cookies → golden_key.",
        parse_mode="HTML",
    )
    await state.set_state(CardinalSetup.awaiting_key)


@router.message(CardinalSetup.awaiting_key)
async def receive_key(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    key = (msg.text or "").strip()
    if not key or len(key) < 20:
        await msg.answer("Ключ выглядит некорректно. Пришлите golden_key целиком.")
        return
    inst = await inst_repo.create(
        session,
        user_id=user.id,
        product=ProductKind.CARDINAL,
        name=f"cardinal-{user.id}",
        config={"golden_key": key},
    )
    inst.status = InstanceStatus.DEPLOYING
    await session.flush()
    try:
        await start_tenant(inst.id, golden_key=key)
        inst.status = InstanceStatus.LIVE
        await msg.answer(
            f"✓ Cardinal #{inst.id} запущен. Управление — /menu → «Мои инстансы».",
            reply_markup=back_to_menu(),
        )
    except Exception as exc:  # noqa: BLE001
        inst.status = InstanceStatus.FAILED
        logger.exception("cardinal start failed")
        await msg.answer(f"Ошибка запуска: {exc}")
    await state.clear()


@router.callback_query(F.data.startswith("inst:setkey:"))
async def cb_setkey(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[2])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    await state.update_data(inst_id=inst_id)
    await state.set_state(CardinalSetup.awaiting_new_key)
    if cb.message:
        await cb.message.answer(
            "Пришлите новый <code>golden_key</code> одним сообщением.",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(CardinalSetup.awaiting_new_key)
async def receive_new_key(
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
    if not key or len(key) < 20:
        await msg.answer("Ключ выглядит некорректно.")
        return
    inst.config = {**(inst.config or {}), "golden_key": key}
    await update_golden_key(inst.id, key)
    await msg.answer(
        f"✓ golden_key обновлён. Cardinal #{inst.id} перезапущен.",
        reply_markup=instance_actions(inst.id, inst.product.value),
    )
    await state.clear()
