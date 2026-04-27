"""Список и управление серверами пользователя."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Instance, InstanceStatus, ProductKind, User
from app.keyboards.main import back_to_menu, instance_actions, instance_cfg_menu
from app.repos import instances as inst_repo
from app.services.supervisor import supervisor

logger = logging.getLogger(__name__)
router = Router(name="instances")


def status_dot(inst: Instance, alive: bool) -> str:
    """Status indicator — only emoji we keep is the colored circle."""
    if inst.status == InstanceStatus.LIVE and alive:
        return "🟢"
    if inst.status in (InstanceStatus.PENDING, InstanceStatus.DEPLOYING):
        return "🟡"
    if inst.status in (InstanceStatus.FAILED, InstanceStatus.SUSPENDED):
        return "🔴"
    if inst.status == InstanceStatus.LIVE and not alive:
        # marked LIVE in DB but the process is missing — yellow (auto-restart pending)
        return "🟡"
    return "🔴"


async def _render_user_instances(
    target: Message, session: AsyncSession, user: User
) -> None:
    items = await inst_repo.list_for_user(session, user.id)
    if not items:
        await target.answer(
            "<b>Мои серверы</b>\n\nУ тебя нет серверов. Нажми «Купить сервер» в меню.",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return
    lines = ["<b>Мои серверы</b>", ""]
    rows: list[list[InlineKeyboardButton]] = []
    for inst in items:
        s = supervisor.status(inst.id)
        dot = status_dot(inst, bool(s.get("alive")))
        lines.append(f"{dot} #{inst.id} · {inst.product.value} · {inst.status.value}")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{dot} #{inst.id}",
                    callback_data=f"inst:open:{inst.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« В меню", callback_data="menu")])
    text = "\n".join(lines)
    await target.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "instances")
async def cb_instances(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    items = await inst_repo.list_for_user(session, user.id)
    if not items:
        text = (
            "<b>Мои серверы</b>\n\n"
            "У тебя нет серверов. Нажми «Купить сервер» в меню."
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

    lines = ["<b>Мои серверы</b>", ""]
    rows: list[list[InlineKeyboardButton]] = []
    for inst in items:
        s = supervisor.status(inst.id)
        dot = status_dot(inst, bool(s.get("alive")))
        lines.append(f"{dot} #{inst.id} · {inst.product.value} · {inst.status.value}")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{dot} #{inst.id}",
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
    # Авто-восстановление: если в БД статус LIVE, а процесса нет —
    # запускаем заново на месте, чтобы пользователю не приходилось жать кнопки.
    if (
        inst.status == InstanceStatus.LIVE
        and not s.get("alive")
        and inst.product == ProductKind.CARDINAL
        and inst.shard_id is None
    ):
        cfg = inst.config or {}
        gk = cfg.get("golden_key")
        if gk:
            try:
                from app.services.cardinal import start_tenant

                await start_tenant(
                    inst.id,
                    golden_key=gk,
                    telegram_token=cfg.get("telegram_token") or "",
                    telegram_secret=cfg.get("telegram_secret") or "",
                    locale=cfg.get("locale") or "ru",
                )
                s = supervisor.status(inst_id)
            except Exception:  # noqa: BLE001
                logger.exception("auto-restart on open failed")
    dot = status_dot(inst, bool(s.get("alive")))
    text = (
        f"<b>Сервер #{inst.id}</b> {dot}\n"
        f"Продукт: {inst.product.value}\n"
        f"Статус: {inst.status.value}\n"
        f"Процесс: {'живой' if s.get('alive') else 'нет'}\n"
        f"PID: {s.get('pid') or '—'}\n"
        f"Аптайм: {s.get('uptime', 0)} сек\n"
        f"Перезапусков: {s.get('restart_count', 0)}\n"
    )
    if cb.message:
        try:
            await cb.message.edit_caption(
                caption=text,
                parse_mode="HTML",
                reply_markup=instance_actions(inst.id),
            )
        except Exception:
            await cb.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=instance_actions(inst.id),
            )
    await cb.answer()


@router.callback_query(F.data.startswith("inst:cfg:menu:"))
async def cb_inst_cfg_menu(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[3])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    if inst.product != ProductKind.CARDINAL:
        await cb.answer("Только для Cardinal", show_alert=True)
        return
    text = (
        f"<b>Конфиги сервера #{inst.id}</b>\n\n"
        "Выбери файл, чтобы залить новый или посмотреть текущий.\n"
        "Формат _main.cfg — INI с разделителем «:» (как у FunPayCardinal)."
    )
    if cb.message:
        try:
            await cb.message.edit_caption(
                caption=text, parse_mode="HTML",
                reply_markup=instance_cfg_menu(inst.id),
            )
        except Exception:
            await cb.message.answer(
                text, parse_mode="HTML",
                reply_markup=instance_cfg_menu(inst.id),
            )
    await cb.answer()


@router.callback_query(F.data.startswith("inst:restart:"))
async def cb_inst_restart(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    inst_id = int(cb.data.split(":")[2])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id:
        await cb.answer("Не найдено", show_alert=True)
        return
    if supervisor.tenants.get(inst.id):
        await supervisor.restart(inst.id)
    else:
        # Супервизор ещё не знает про инстанс (например, после рестарта
        # сервиса) — поднимаем его с нуля по конфигу из БД.
        if inst.product == ProductKind.CARDINAL:
            from app.services.cardinal import start_tenant

            cfg = inst.config or {}
            gk = cfg.get("golden_key")
            if not gk:
                await cb.answer("Сначала задайте golden_key", show_alert=True)
                return
            await start_tenant(
                inst.id,
                golden_key=gk,
                telegram_token=cfg.get("telegram_token") or "",
                telegram_secret=cfg.get("telegram_secret") or "",
                locale=cfg.get("locale") or "ru",
            )
    inst.status = InstanceStatus.LIVE
    inst.desired_state = "live"
    inst.actual_state = "live"
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
    import html as _html

    lines = supervisor.tail(inst.id, lines=80)
    body = "\n".join(lines) if lines else "Логов пока нет"
    text = (
        "<b>Логи (последние 80 строк)</b>\n\n<pre>"
        + _html.escape(body)
        + "</pre>"
    )
    if cb.message:
        await cb.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=instance_actions(inst.id),
        )
    await cb.answer()


@router.callback_query(F.data.startswith("inst:status:"))
async def cb_inst_status(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    await cb_inst_open(cb, session, user)
