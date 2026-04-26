"""Cardinal: golden_key rotation + custom config upload.

Buy/setup flow itself lives in payment.py; this module owns post-purchase
config management (rotate golden_key, upload _main.cfg / auto_response.cfg /
auto_delivery.cfg).
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProductKind, User
from app.keyboards.main import instance_actions
from app.repos import instances as inst_repo
from app.services.cardinal import (
    read_main_cfg,
    update_golden_key,
    write_user_aux_cfg,
    write_user_main_cfg,
)

logger = logging.getLogger(__name__)
router = Router(name="cardinal")


class CardinalCfg(StatesGroup):
    awaiting_new_key = State()
    awaiting_main_cfg = State()
    awaiting_resp_cfg = State()
    awaiting_deliv_cfg = State()


# --- golden_key rotation -----------------------------------------------------

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
    if inst.product != ProductKind.CARDINAL:
        await cb.answer("Только для Cardinal-инстансов", show_alert=True)
        return
    await state.update_data(inst_id=inst_id)
    await state.set_state(CardinalCfg.awaiting_new_key)
    if cb.message:
        await cb.message.answer(
            "Пришли новый <code>golden_key</code> одним сообщением.\n/cancel — отмена.",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(CardinalCfg.awaiting_new_key)
async def receive_new_key(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.")
        return
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
        await msg.answer("Ключ выглядит некорректно. /cancel — отмена.")
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


# --- _main.cfg / auto_response.cfg / auto_delivery.cfg upload ---------------

def _cfg_starter(state_cls: State, prompt: str):
    """Build a callback handler that puts FSM into `state_cls` & sends prompt."""

    async def handler(
        cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
    ) -> None:
        if not cb.data:
            return
        inst_id = int(cb.data.split(":")[3])
        inst = await inst_repo.by_id(session, inst_id)
        if not inst or inst.user_id != user.id:
            await cb.answer("Не найдено", show_alert=True)
            return
        if inst.product != ProductKind.CARDINAL:
            await cb.answer("Только для Cardinal-инстансов", show_alert=True)
            return
        await state.update_data(inst_id=inst_id)
        await state.set_state(state_cls)
        if cb.message:
            await cb.message.answer(prompt, parse_mode="HTML")
        await cb.answer()

    return handler


cb_cfg_main = _cfg_starter(
    CardinalCfg.awaiting_main_cfg,
    (
        "Пришли файл <code>_main.cfg</code> (как .cfg-документ) ИЛИ его содержимое "
        "одним сообщением. Формат — INI с разделителем <code>:</code>. Будут "
        "переписаны все 9 секций.\n\n"
        "/cancel — отмена."
    ),
)
cb_cfg_resp = _cfg_starter(
    CardinalCfg.awaiting_resp_cfg,
    (
        "Пришли файл <code>auto_response.cfg</code> (как .cfg-документ) ИЛИ его "
        "содержимое одним сообщением.\n\n/cancel — отмена."
    ),
)
cb_cfg_deliv = _cfg_starter(
    CardinalCfg.awaiting_deliv_cfg,
    (
        "Пришли файл <code>auto_delivery.cfg</code> (как .cfg-документ) ИЛИ его "
        "содержимое одним сообщением.\n\n/cancel — отмена."
    ),
)

router.callback_query(F.data.startswith("inst:cfg:main:"))(cb_cfg_main)
router.callback_query(F.data.startswith("inst:cfg:resp:"))(cb_cfg_resp)
router.callback_query(F.data.startswith("inst:cfg:deliv:"))(cb_cfg_deliv)


@router.callback_query(F.data.startswith("inst:cfg:show:"))
async def cb_cfg_show(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not cb.data:
        return
    inst_id = int(cb.data.split(":")[3])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    if inst.product != ProductKind.CARDINAL:
        await cb.answer("Только для Cardinal", show_alert=True)
        return
    sections = read_main_cfg(inst.id)
    if not sections:
        await cb.answer("Конфиг ещё не создан", show_alert=True)
        return
    # Build a compact view (sensitive fields masked)
    lines = [f"<b>_main.cfg · #{inst.id}</b>", ""]
    for sect, kv in sections.items():
        lines.append(f"<b>[{sect}]</b>")
        for k, v in kv.items():
            if k in {"golden_key", "token", "secretKeyHash"}:
                v = (v[:6] + "…") if v else "(empty)"
            lines.append(f"  {k} = {v}")
        lines.append("")
    text = "\n".join(lines)
    if len(text) > 3500:
        # If it's huge, send as a file instead.
        from app.services.cardinal_config import render_main_cfg

        raw = render_main_cfg(sections).encode("utf-8")
        if cb.message:
            await cb.message.answer_document(
                BufferedInputFile(raw, filename=f"_main.cfg.{inst.id}.txt"),
                caption=f"_main.cfg · инстанс #{inst.id}",
            )
    else:
        if cb.message:
            await cb.message.answer(text, parse_mode="HTML")
    await cb.answer()


async def _read_cfg_payload(msg: Message) -> tuple[bool, str | None, str]:
    """Return (ok, content, error). Accepts either text or .cfg/.txt document."""
    if msg.document:
        if (
            msg.document.file_size
            and msg.document.file_size > 256 * 1024
        ):
            return False, None, "Файл больше 256 KB. Урежь, пожалуйста."
        try:
            f = await msg.bot.download(msg.document)
            raw = f.read() if hasattr(f, "read") else bytes(f)  # type: ignore[arg-type]
            return True, raw.decode("utf-8", errors="replace"), ""
        except Exception as exc:  # noqa: BLE001
            return False, None, f"Не смог скачать файл: {exc}"
    if msg.text:
        return True, msg.text, ""
    return False, None, "Пришли текст или .cfg-файл."


@router.message(CardinalCfg.awaiting_main_cfg)
async def receive_main_cfg(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.")
        return
    data = await state.get_data()
    inst_id = data.get("inst_id")
    if not inst_id:
        await state.clear()
        return
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await state.clear()
        return
    ok, content, err = await _read_cfg_payload(msg)
    if not ok or content is None:
        await msg.answer(f"◇ {err}")
        return
    success, message = await write_user_main_cfg(inst.id, content)
    if success:
        await msg.answer(
            f"▣ {message}\nCardinal #{inst.id} перезапущен.",
            reply_markup=instance_actions(inst.id, inst.product.value),
        )
        await state.clear()
    else:
        await msg.answer(f"◇ {message}\nИсправь и пришли ещё раз, или /cancel.")


@router.message(CardinalCfg.awaiting_resp_cfg)
async def receive_resp_cfg(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.")
        return
    data = await state.get_data()
    inst_id = data.get("inst_id")
    if not inst_id:
        await state.clear()
        return
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await state.clear()
        return
    ok, content, err = await _read_cfg_payload(msg)
    if not ok or content is None:
        await msg.answer(f"◇ {err}")
        return
    success, message = await write_user_aux_cfg(inst.id, "auto_response.cfg", content)
    await msg.answer(
        f"{'▣' if success else '◇'} {message}",
        reply_markup=instance_actions(inst.id, inst.product.value)
        if success
        else None,
    )
    if success:
        await state.clear()


@router.message(CardinalCfg.awaiting_deliv_cfg)
async def receive_deliv_cfg(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.")
        return
    data = await state.get_data()
    inst_id = data.get("inst_id")
    if not inst_id:
        await state.clear()
        return
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await state.clear()
        return
    ok, content, err = await _read_cfg_payload(msg)
    if not ok or content is None:
        await msg.answer(f"◇ {err}")
        return
    success, message = await write_user_aux_cfg(inst.id, "auto_delivery.cfg", content)
    await msg.answer(
        f"{'▣' if success else '◇'} {message}",
        reply_markup=instance_actions(inst.id, inst.product.value)
        if success
        else None,
    )
    if success:
        await state.clear()
