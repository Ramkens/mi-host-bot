"""User instance overview + actions."""
from __future__ import annotations

import logging
from io import BytesIO

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Document, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import InstanceStatus, ProductKind, User
from app.keyboards.main import back_to_menu, instance_actions
from app.repos import instances as inst_repo
from app.services.cardinal import remove_tenant_dir
from app.services.script_host import remove as remove_script
from app.services.supervisor import supervisor

logger = logging.getLogger(__name__)
router = Router(name="instances")


class SetupFSM(StatesGroup):
    awaiting_golden_key = State()
    awaiting_zip = State()


def _status_icon(st: InstanceStatus) -> str:
    return {
        InstanceStatus.LIVE: "🟢",
        InstanceStatus.DEPLOYING: "🟡",
        InstanceStatus.PENDING: "⚪",
        InstanceStatus.SUSPENDED: "🟠",
        InstanceStatus.FAILED: "🔴",
        InstanceStatus.DELETED: "⚫",
    }.get(st, "⚪")


@router.callback_query(F.data == "instances")
async def cb_instances(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    items = await inst_repo.list_for_user(session, user.id)
    if not items:
        text = (
            "<b>🖥️ Мои серверы</b>\n\n"
            "Серверов пока нет. Купи подписку через /menu → 💎 Купить "
            "или активируй купон через /coupon."
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
        return
    lines = ["<b>🖥️ Мои серверы</b>", ""]
    rows = []
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    for inst in items:
        tier = ((inst.config or {}).get("tier") or "std").lower()
        tier_suffix = " PRO" if tier == "pro" else ""
        lines.append(
            f"{_status_icon(inst.status)} #{inst.id} · "
            f"{inst.product.value}{tier_suffix} · {inst.status.value}"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{_status_icon(inst.status)} #{inst.id} · "
                         f"{inst.product.value}{tier_suffix}",
                    callback_data=f"inst:open:{inst.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="menu")])
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    if cb.message:
        try:
            await cb.message.edit_caption(caption=text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("inst:open:"))
async def cb_inst_open(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[2])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    s = supervisor.status(inst_id)
    tier = ((inst.config or {}).get("tier") or "std").lower()
    tier_suffix = " PRO" if tier == "pro" else ""
    need_setup = inst.status == InstanceStatus.PENDING or (
        inst.product == ProductKind.CARDINAL
        and not (inst.config or {}).get("golden_key")
    )
    setup_hint = ""
    if need_setup:
        if inst.product == ProductKind.CARDINAL:
            setup_hint = (
                "\n\n⚙️ <b>Нужна настройка</b> — нажми «⚙️ Настроить» и пришли "
                "<code>golden_key</code>."
            )
        else:
            setup_hint = (
                "\n\n⚙️ <b>Нужна настройка</b> — нажми «⚙️ Настроить» и "
                "загрузи <code>.zip</code> со скриптом."
            )
    text = (
        f"<b>🖥️ Сервер #{inst.id}</b>\n"
        f"💠 Продукт: {inst.product.value}{tier_suffix}\n"
        f"📡 Статус (БД): {_status_icon(inst.status)} {inst.status.value}\n"
        f"⚙️ Процесс: {'жив' if s.get('alive') else 'нет'}\n"
        f"🧬 PID: {s.get('pid') or '—'}\n"
        f"⏱ Uptime: {s.get('uptime', 0)} сек\n"
        f"🔄 Перезапусков: {s.get('restart_count', 0)}\n"
        f"☁️ Render service: {inst.render_service_id or '—'}"
        f"{setup_hint}"
    )
    if cb.message:
        try:
            await cb.message.edit_caption(
                caption=text,
                parse_mode="HTML",
                reply_markup=instance_actions(inst.id, inst.product.value),
            )
        except Exception:
            await cb.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=instance_actions(inst.id, inst.product.value),
            )
    await cb.answer()


@router.callback_query(F.data.startswith("inst:start:"))
async def cb_inst_start(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[2])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    if inst.product == ProductKind.CARDINAL:
        from app.services.cardinal import start_tenant

        gk = inst.config.get("golden_key")
        if not gk:
            await cb.answer("Сначала задайте golden_key", show_alert=True)
            return
        await start_tenant(inst.id, golden_key=gk)
    else:
        # script: spawn from existing tenant dir if exists
        from app.services.script_host import tenant_dir
        from app.services.supervisor import TenantSpec
        import sys

        td = tenant_dir(inst.id)
        if not td.exists():
            await cb.answer("Сначала загрузите .zip", show_alert=True)
            return
        cmd = (inst.config.get("start_cmd") or "python main.py").split()
        cmd[0] = sys.executable if cmd[0] == "python" else cmd[0]
        await supervisor.start(
            TenantSpec(
                instance_id=inst.id,
                name=f"script-{inst.id}",
                cwd=td,
                cmd=cmd,
                env={"PYTHONUNBUFFERED": "1"},
            )
        )
    inst.status = InstanceStatus.LIVE
    await cb.answer("Запущено")
    await cb_inst_open(cb, session, user)


@router.callback_query(F.data.startswith("inst:stop:"))
async def cb_inst_stop(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[2])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    await supervisor.stop(inst.id)
    inst.status = InstanceStatus.SUSPENDED
    await cb.answer("Остановлено")
    await cb_inst_open(cb, session, user)


@router.callback_query(F.data.startswith("inst:restart:"))
async def cb_inst_restart(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[2])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    await supervisor.restart(inst.id)
    inst.status = InstanceStatus.LIVE
    await cb.answer("Перезапущено")
    await cb_inst_open(cb, session, user)


@router.callback_query(F.data.startswith("inst:logs:"))
async def cb_inst_logs(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[2])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    lines = supervisor.tail(inst.id, lines=40)
    text = "<b>Логи (последние 40 строк)</b>\n\n<pre>"
    text += "\n".join(lines) if lines else "Логов пока нет"
    text += "</pre>"
    if cb.message:
        await cb.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=instance_actions(inst.id, inst.product.value),
        )
    await cb.answer()


@router.callback_query(F.data.startswith("inst:status:"))
async def cb_inst_status(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    await cb_inst_open(cb, session, user)


# --- Setup flow: supply missing golden_key / .zip for PENDING instances ----

@router.callback_query(F.data.startswith("inst:setup:"))
async def cb_inst_setup(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[2])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    await state.update_data(setup_inst_id=inst.id)
    if inst.product == ProductKind.CARDINAL:
        await state.set_state(SetupFSM.awaiting_golden_key)
        if cb.message:
            await cb.message.answer(
                "🔑 Пришли <code>golden_key</code> одним сообщением — "
                "сервер запустится сразу.\n\n"
                "<i>Где взять:</i> funpay.com → DevTools → Application → "
                "Cookies → <code>golden_key</code>.\n\n"
                "/cancel — отмена.",
                parse_mode="HTML",
            )
    else:
        await state.set_state(SetupFSM.awaiting_zip)
        if cb.message:
            await cb.message.answer(
                "📦 Пришли .zip-архив с Python-проектом одним документом (до 25 MB).\n"
                "Внутри — <code>main.py</code> и опц. <code>requirements.txt</code>.\n\n"
                "/cancel — отмена.",
                parse_mode="HTML",
            )
    await cb.answer()


@router.message(SetupFSM.awaiting_golden_key)
async def setup_receive_key(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    text = (msg.text or "").strip()
    if text == "/cancel":
        await state.clear()
        await msg.answer("Отменено.")
        return
    if len(text) < 20:
        await msg.answer("Ключ выглядит некорректно. Пришли golden_key целиком.")
        return
    data = await state.get_data()
    inst_id = data.get("setup_inst_id")
    if not inst_id:
        await state.clear()
        return
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await state.clear()
        return
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    inst.config = {**(inst.config or {}), "golden_key": text}
    inst.status = InstanceStatus.DEPLOYING
    inst.desired_state = "live"
    await session.flush()
    # Direct-start (master-hosted tenants); worker-hosted ones reconcile async.
    if inst.shard_id is None:
        try:
            from app.services.cardinal import start_tenant

            await start_tenant(inst.id, golden_key=text)
            inst.status = InstanceStatus.LIVE
            inst.actual_state = "live"
        except Exception:  # noqa: BLE001
            logger.exception("start cardinal failed in setup")
            inst.status = InstanceStatus.FAILED
    await session.commit()
    await msg.answer(
        f"✨ Сервер #{inst.id} настроен и запущен.",
        reply_markup=instance_actions(inst.id, inst.product.value),
    )
    await state.clear()


@router.message(SetupFSM.awaiting_zip, F.document)
async def setup_receive_zip(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    doc: Document = msg.document  # type: ignore[assignment]
    if not doc.file_name or not doc.file_name.lower().endswith(".zip"):
        await msg.answer("Нужен .zip файл.")
        return
    if doc.file_size and doc.file_size > 25 * 1024 * 1024:
        await msg.answer("Слишком большой архив (>25 MB).")
        return
    data = await state.get_data()
    inst_id = data.get("setup_inst_id")
    if not inst_id:
        await state.clear()
        return
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await state.clear()
        return
    bio = BytesIO()
    await msg.bot.download(doc, destination=bio)
    zip_bytes = bio.getvalue()
    tier = ((inst.config or {}).get("tier") or "std").lower()
    ram_mb = settings.script_pro_ram_mb if tier == "pro" else settings.script_std_ram_mb

    from app.services import script_host

    inst.status = InstanceStatus.DEPLOYING
    inst.desired_state = "live"
    await session.flush()
    try:
        analysis, spec = await script_host.deploy(inst.id, zip_bytes, ram_mb=ram_mb)
        inst.risk_score = analysis.risk_score
        inst.risk_report = analysis.report
        if not analysis.ok:
            inst.status = InstanceStatus.FAILED
            await session.commit()
            await msg.answer(
                "🔴 Архив не прошёл безопасный анализ. "
                f"{analysis.report or ''}".strip(),
                reply_markup=instance_actions(inst.id, inst.product.value),
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
        inst.actual_state = "live"
    except Exception as exc:  # noqa: BLE001
        logger.exception("deploy script failed in setup")
        inst.status = InstanceStatus.FAILED
        await session.commit()
        await msg.answer(
            f"🔴 Не удалось развернуть: {exc}",
            reply_markup=instance_actions(inst.id, inst.product.value),
        )
        await state.clear()
        return
    await session.commit()
    await msg.answer(
        f"✨ Сервер #{inst.id} настроен и запущен.",
        reply_markup=instance_actions(inst.id, inst.product.value),
    )
    await state.clear()


@router.message(SetupFSM.awaiting_zip)
async def setup_reject_non_zip(msg: Message) -> None:
    await msg.answer("Пришли .zip как документ.")
