"""Cardinal: golden_key rotation. Buy/setup flow lives in payment.py."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.keyboards.main import instance_actions
from app.repos import instances as inst_repo
from app.services.cardinal import update_golden_key

logger = logging.getLogger(__name__)
router = Router(name="cardinal")


class CardinalRotate(StatesGroup):
    awaiting_new_key = State()


@router.callback_query(F.data.startswith("inst:setkey:"))
async def cb_setkey(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not cb.data:
        return
    inst_id = int(cb.data.split(":")[2])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    await state.update_data(inst_id=inst_id)
    await state.set_state(CardinalRotate.awaiting_new_key)
    if cb.message:
        await cb.message.answer(
            "Пришли новый <code>golden_key</code> одним сообщением.",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(CardinalRotate.awaiting_new_key)
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
    try:
        await update_golden_key(inst.id, key)
    except Exception:  # noqa: BLE001
        logger.exception("update golden_key failed")
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    await msg.answer(
        f"▣ golden_key обновлён. Cardinal #{inst.id} перезапущен.",
        reply_markup=instance_actions(inst.id, inst.product.value),
    )
    await state.clear()
