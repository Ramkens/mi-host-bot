"""Custom-script upload + deploy."""
from __future__ import annotations

import logging
from io import BytesIO

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Document, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstanceStatus, ProductKind, User
from app.keyboards.main import back_to_menu, instance_actions
from app.repos import instances as inst_repo
from app.repos import subscriptions as subs_repo
from app.services import script_host

logger = logging.getLogger(__name__)
router = Router(name="script")


class ScriptUpload(StatesGroup):
    awaiting_zip = State()


@router.message(Command("script"))
async def cmd_script(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    sub = await subs_repo.get(session, user.id, ProductKind.SCRIPT)
    from app.utils.time import now_utc

    if not sub or sub.expires_at <= now_utc():
        await msg.answer(
            "Нет активной подписки на «Скрипты». Купите её через /menu → «Купить хостинг»."
        )
        return
    await msg.answer(
        "<b>Загрузка скрипта</b>\n\n"
        "Пришлите .zip-архив с вашим Python-проектом одним документом.\n"
        "Внутри должны быть: <code>main.py</code> (или подобный entrypoint) "
        "и опционально <code>requirements.txt</code>. Размер до 25 MB.",
        parse_mode="HTML",
    )
    await state.set_state(ScriptUpload.awaiting_zip)


@router.message(ScriptUpload.awaiting_zip, F.document)
async def receive_zip(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    doc: Document = msg.document  # type: ignore[assignment]
    if not doc.file_name or not doc.file_name.lower().endswith(".zip"):
        await msg.answer("Нужен .zip файл.")
        return
    if doc.file_size and doc.file_size > 25 * 1024 * 1024:
        await msg.answer("Слишком большой архив (>25 MB).")
        return

    bio = BytesIO()
    await msg.bot.download(doc, destination=bio)
    data = bio.getvalue()

    inst = await inst_repo.create(
        session,
        user_id=user.id,
        product=ProductKind.SCRIPT,
        name=f"script-{user.id}",
    )
    inst.status = InstanceStatus.DEPLOYING
    await session.flush()

    await msg.answer("⏳ Анализирую код…")
    try:
        analysis, spec = await script_host.deploy(inst.id, data)
    except Exception as exc:  # noqa: BLE001
        inst.status = InstanceStatus.FAILED
        await msg.answer(f"Ошибка деплоя: {exc}")
        await state.clear()
        return

    inst.risk_score = analysis.risk_score
    inst.risk_report = analysis.report

    if not analysis.ok:
        inst.status = InstanceStatus.FAILED
        await msg.answer(
            f"❌ Скрипт заблокирован.\n\n<pre>{analysis.report}</pre>",
            parse_mode="HTML",
        )
        await state.clear()
        return

    if spec is not None:
        inst.config = {
            **(inst.config or {}),
            "build_cmd": spec.build_cmd,
            "start_cmd": spec.start_cmd,
            "env_keys": list(spec.env_template.keys()),
            "entrypoint": analysis.entrypoint,
        }
    inst.status = InstanceStatus.LIVE
    await msg.answer(
        f"✓ Скрипт загружен и запущен. Инстанс #{inst.id}.\n\n"
        f"<pre>{analysis.report}</pre>",
        parse_mode="HTML",
        reply_markup=instance_actions(inst.id, inst.product.value),
    )
    await state.clear()


@router.message(ScriptUpload.awaiting_zip)
async def receive_not_zip(msg: Message) -> None:
    await msg.answer("Пришлите .zip как документ.")
