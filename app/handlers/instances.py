"""User instance overview + actions."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstanceStatus, ProductKind, User
from app.keyboards.main import back_to_menu, instance_actions
from app.repos import instances as inst_repo
from app.services.cardinal import remove_tenant_dir
from app.services.script_host import remove as remove_script
from app.services.supervisor import supervisor

logger = logging.getLogger(__name__)
router = Router(name="instances")


@router.callback_query(F.data == "instances")
async def cb_instances(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    items = await inst_repo.list_for_user(session, user.id)
    if not items:
        text = (
            "<b>Мои инстансы</b>\n\n"
            "У вас нет инстансов. Купите подписку и создайте инстанс из меню «Купить»."
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
    lines = ["<b>Мои инстансы</b>", ""]
    rows = []
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    for inst in items:
        status_dot = "🟢" if inst.status == InstanceStatus.LIVE else "⚪"
        lines.append(f"{status_dot} #{inst.id} · {inst.product.value} · {inst.status.value}")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"#{inst.id} · {inst.product.value}",
                    callback_data=f"inst:open:{inst.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« В меню", callback_data="menu")])
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
    text = (
        f"<b>Инстанс #{inst.id}</b>\n"
        f"Продукт: {inst.product.value}\n"
        f"Статус (БД): {inst.status.value}\n"
        f"Процесс: {'жив' if s.get('alive') else 'нет'}\n"
        f"PID: {s.get('pid') or '—'}\n"
        f"Uptime: {s.get('uptime', 0)} сек\n"
        f"Перезапусков: {s.get('restart_count', 0)}\n"
        f"Render service: {inst.render_service_id or '—'}\n"
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
