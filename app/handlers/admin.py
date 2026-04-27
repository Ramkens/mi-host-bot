"""Admin panel: stats, server management, users, coupons, broadcast.

Designed to be small and focused — rotates around the inline menu
(`admin_menu`). Heavy commands (export, shards inspection, ad-hoc grants)
are still available as text commands for power users.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Coupon,
    Instance,
    InstanceStatus,
    ProductKind,
    Shard,
    Subscription,
    User,
)
from app.keyboards.main import (
    admin_back,
    admin_confirm,
    admin_coupon_days,
    admin_coupon_uses,
    admin_coupons_menu,
    admin_menu,
    admin_server_actions,
    admin_user_actions,
)
from app.repos import coupons as coupons_repo
from app.repos import instances as inst_repo
from app.repos import logs as logs_repo
from app.repos import subscriptions as subs_repo
from app.repos import users as users_repo
from app.services.admin import is_admin, stats_dashboard
from app.services.slots import free_cardinal_slots
from app.services.supervisor import supervisor
from app.utils.time import fmt_msk, now_utc

logger = logging.getLogger(__name__)
router = Router(name="admin")


class AdminFSM(StatesGroup):
    awaiting_broadcast = State()
    awaiting_new_admin = State()
    awaiting_user_id = State()
    awaiting_coupon_del_code = State()
    awaiting_coupon_days_custom = State()
    awaiting_coupon_uses_custom = State()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_admin(session: AsyncSession, user: User) -> bool:
    return await is_admin(session, user.id)


async def _send_or_edit(
    cb: CallbackQuery, text: str, reply_markup=None
) -> None:
    if not cb.message:
        return
    try:
        await cb.message.edit_caption(
            caption=text, parse_mode="HTML", reply_markup=reply_markup
        )
    except Exception:
        try:
            await cb.message.edit_text(
                text, parse_mode="HTML", reply_markup=reply_markup
            )
        except Exception:
            await cb.message.answer(
                text, parse_mode="HTML", reply_markup=reply_markup
            )


# ---------------------------------------------------------------------------
# /admin entry
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin")
async def cb_admin(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    text = "<b>Админка</b>\n\nВыбери раздел."
    await _send_or_edit(cb, text, admin_menu())
    await cb.answer()


@router.message(Command("admin"))
async def cmd_admin(msg: Message, session: AsyncSession, user: User) -> None:
    if not await is_admin(session, user.id):
        return
    await msg.answer("<b>Админка</b>", parse_mode="HTML", reply_markup=admin_menu())


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin:stats")
async def cb_stats(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    s = await stats_dashboard(session)
    free = await free_cardinal_slots(session)
    text = (
        "<b>Статистика</b>\n\n"
        f"Юзеров всего: <b>{s['users_total']}</b>\n"
        f"Активных за 24ч: <b>{s['users_active_24h']}</b>\n"
        f"Платящих: <b>{s['paying_users']}</b>\n"
        f"Активных подписок: <b>{s['active_subs']}</b>\n"
        f"Конверсия: <b>{s['conversion_pct']}%</b>\n\n"
        f"Выручка за 30 дней: <b>{s['revenue_30d_rub']} ₽</b>\n"
        f"Всего: <b>{s['revenue_total_rub']} ₽</b>\n\n"
        f"Свободных слотов Cardinal: <b>{free}</b>"
    )
    await _send_or_edit(cb, text, admin_back())
    await cb.answer()


@router.message(Command("stats"))
async def cmd_stats(msg: Message, session: AsyncSession, user: User) -> None:
    if not await is_admin(session, user.id):
        return
    s = await stats_dashboard(session)
    await msg.answer(
        f"<b>Статистика</b>\n\n"
        f"Юзеров: {s['users_total']}\n"
        f"Активных 24ч: {s['users_active_24h']}\n"
        f"Платящих: {s['paying_users']}\n"
        f"Подписок: {s['active_subs']}\n"
        f"Выручка 30д: {s['revenue_30d_rub']} ₽\n"
        f"Всего: {s['revenue_total_rub']} ₽",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Server (instance) management
# ---------------------------------------------------------------------------


def _status_dot(inst: Instance, alive: bool) -> str:
    if inst.status == InstanceStatus.LIVE and alive:
        return "🟢"
    if inst.status in (InstanceStatus.PENDING, InstanceStatus.DEPLOYING):
        return "🟡"
    if inst.status in (InstanceStatus.FAILED, InstanceStatus.SUSPENDED):
        return "🔴"
    if inst.status == InstanceStatus.LIVE and not alive:
        return "🟡"
    return "🔴"


@router.callback_query(F.data == "admin:servers")
async def cb_servers(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    res = await session.execute(
        select(Instance)
        .where(Instance.status != InstanceStatus.DELETED)
        .order_by(Instance.id.desc())
        .limit(50)
    )
    items = res.scalars().all()
    if not items:
        await _send_or_edit(cb, "<b>Серверы</b>\n\nПусто.", admin_back())
        await cb.answer()
        return
    rows: list[list[InlineKeyboardButton]] = []
    lines = ["<b>Серверы (последние 50)</b>", ""]
    for inst in items:
        s = supervisor.status(inst.id)
        dot = _status_dot(inst, bool(s.get("alive")))
        lines.append(
            f"{dot} #{inst.id} · u{inst.user_id} · {inst.product.value} · {inst.status.value}"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{dot} #{inst.id} · u{inst.user_id}",
                    callback_data=f"adm:srv:open:{inst.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« В админку", callback_data="admin")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    text = "\n".join(lines)
    await _send_or_edit(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:srv:open:"))
async def cb_server_open(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    inst_id = int(cb.data.split(":")[3])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst:
        await cb.answer("Не найден", show_alert=True)
        return
    s = supervisor.status(inst.id)
    dot = _status_dot(inst, bool(s.get("alive")))
    text = (
        f"<b>Сервер #{inst.id}</b> {dot}\n"
        f"Юзер: <code>{inst.user_id}</code>\n"
        f"Продукт: {inst.product.value}\n"
        f"Статус (БД): {inst.status.value}\n"
        f"Процесс: {'живой' if s.get('alive') else 'нет'}\n"
        f"PID: {s.get('pid') or '—'}\n"
        f"Аптайм: {s.get('uptime', 0)} сек\n"
        f"Перезапусков: {s.get('restart_count', 0)}\n"
    )
    await _send_or_edit(cb, text, admin_server_actions(inst.id))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:srv:restart:"))
async def cb_server_restart(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    inst_id = int(cb.data.split(":")[3])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst:
        await cb.answer("Не найден", show_alert=True)
        return
    if supervisor.tenants.get(inst.id):
        await supervisor.restart(inst.id)
    elif inst.product == ProductKind.CARDINAL and inst.shard_id is None:
        from app.services.cardinal import start_tenant

        gk = (inst.config or {}).get("golden_key")
        if gk:
            await start_tenant(inst.id, golden_key=gk)
    inst.status = InstanceStatus.LIVE
    inst.desired_state = "live"
    await session.commit()
    await cb.answer("Перезапущено")
    await cb_server_open(cb, session, user)


@router.callback_query(F.data.startswith("adm:srv:stop:"))
async def cb_server_stop(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    inst_id = int(cb.data.split(":")[3])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst:
        await cb.answer("Не найден", show_alert=True)
        return
    if supervisor.tenants.get(inst.id):
        await supervisor.stop(inst.id)
    inst.status = InstanceStatus.SUSPENDED
    inst.desired_state = "stopped"
    await session.commit()
    await cb.answer("Остановлено")
    await cb_server_open(cb, session, user)


@router.callback_query(F.data.startswith("adm:srv:logs:"))
async def cb_server_logs(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    """Send full Cardinal logs to admin.

    Combines:
      * supervisor's stdout/stderr tail (in-memory, 5000 lines)
      * Cardinal's persistent ``logs/log.log`` from the tenant dir
        (rotating file handler, last ~5 MB)

    Short outputs are sent inline as ``<pre>`` HTML; longer outputs are
    uploaded as a ``.log`` file so nothing gets truncated.
    """
    from app.services.cardinal import read_full_logs

    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    inst_id = int(cb.data.split(":")[3])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst:
        await cb.answer("Не найден", show_alert=True)
        return
    tail_lines = supervisor.tail(inst.id, lines=5000)
    file_log = read_full_logs(inst.id)
    parts: list[bytes] = []
    if tail_lines:
        parts.append(("=== supervisor (stdout/stderr) ===\n").encode())
        parts.append(("\n".join(tail_lines) + "\n").encode())
    if file_log:
        parts.append(("\n=== cardinal logs/log.log ===\n").encode())
        parts.append(file_log)
    blob = b"".join(parts)
    if not blob:
        if cb.message:
            await cb.message.answer(
                "Логов пока нет (сервер не запускался или только что стартовал).",
                reply_markup=admin_server_actions(inst.id),
            )
        await cb.answer()
        return
    # Telegram message text limit is ~4096 chars; below ~3500 chars we
    # send inline, otherwise upload as a file so admin gets full output.
    if len(blob) <= 3500:
        text = (
            "<b>Логи сервера</b>\n\n<pre>"
            + blob.decode("utf-8", errors="replace").replace("<", "&lt;").replace(">", "&gt;")
            + "</pre>"
        )
        if cb.message:
            await cb.message.answer(
                text, parse_mode="HTML", reply_markup=admin_server_actions(inst.id)
            )
    else:
        if cb.message:
            await cb.message.answer_document(
                BufferedInputFile(blob, filename=f"server-{inst.id}.log"),
                caption=f"Логи сервера #{inst.id} ({len(blob)} байт)",
                reply_markup=admin_server_actions(inst.id),
            )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:srv:delete:"))
async def cb_server_delete(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    inst_id = int(cb.data.split(":")[3])
    text = f"Удалить сервер #{inst_id}? Подписка не возвращается."
    await _send_or_edit(
        cb,
        text,
        admin_confirm(
            yes_data=f"adm:srv:delete_yes:{inst_id}",
            no_data=f"adm:srv:open:{inst_id}",
        ),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:srv:delete_yes:"))
async def cb_server_delete_yes(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    inst_id = int(cb.data.split(":")[3])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst:
        await cb.answer("Не найден", show_alert=True)
        return
    # Stop the running subprocess (if any) before marking deleted, so the
    # slot is freed both in supervisor.tenants and in our DB accounting.
    try:
        if supervisor.tenants.get(inst.id):
            await supervisor.stop(inst.id)
    except Exception:  # noqa: BLE001
        logger.exception("supervisor.stop failed during admin delete")
    inst.status = InstanceStatus.DELETED
    inst.desired_state = "stopped"
    inst.shard_id = None
    inst.actual_state = "stopped"
    await session.commit()
    await cb.answer("Удалено")
    await cb_servers(cb, session, user)


# ---------------------------------------------------------------------------
# Hosts (shards) view
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin:hosts")
async def cb_hosts(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    res = await session.execute(select(Shard).order_by(Shard.id))
    shards = res.scalars().all()
    if not shards:
        await _send_or_edit(
            cb,
            "<b>Хосты</b>\n\nНет. Они подгружаются из MIHOST_PRESEED_SHARDS.",
            admin_back(),
        )
        await cb.answer()
        return
    # Count live cardinals per shard
    res = await session.execute(
        select(Instance.shard_id, Instance.id).where(
            Instance.product == ProductKind.CARDINAL,
            Instance.status == InstanceStatus.LIVE,
        )
    )
    counts: dict[int, int] = {}
    for sh_id, _ in res.all():
        if sh_id is None:
            continue
        counts[sh_id] = counts.get(sh_id, 0) + 1
    lines = ["<b>Хосты</b>", ""]
    total_cap = 0
    total_used = 0
    for sh in shards:
        used = counts.get(sh.id, 0)
        cap = sh.capacity
        total_cap += cap
        total_used += used
        status_word = sh.status.value if hasattr(sh.status, "value") else str(sh.status)
        lines.append(
            f"#{sh.id} · {sh.name} · {status_word} · {used}/{cap}"
        )
    lines.append("")
    lines.append(f"Итого: <b>{total_used}/{total_cap}</b> Cardinal")
    await _send_or_edit(cb, "\n".join(lines), admin_back())
    await cb.answer()


# ---------------------------------------------------------------------------
# User search / actions
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin:user")
async def cb_user_start(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_user_id)
    if cb.message:
        await cb.message.answer(
            "Пришли <code>id</code> юзера или <code>@username</code>.",
            parse_mode="HTML",
        )
    await cb.answer()


async def _resolve_user_id(text_arg: str, session: AsyncSession) -> Optional[int]:
    text_arg = text_arg.strip()
    if text_arg.isdigit():
        return int(text_arg)
    if text_arg.startswith("@"):
        text_arg = text_arg[1:]
    res = await session.execute(select(User).where(User.username == text_arg))
    u = res.scalars().first()
    return u.id if u else None


@router.message(AdminFSM.awaiting_user_id)
async def msg_user_lookup(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await is_admin(session, user.id):
        return
    target_id = await _resolve_user_id(msg.text or "", session)
    await state.clear()
    if not target_id:
        await msg.answer("Юзер не найден.", reply_markup=admin_back())
        return
    await _show_user(msg, session, target_id)


async def _show_user(msg: Message, session: AsyncSession, target_id: int) -> None:
    u = await session.get(User, target_id)
    if not u:
        await msg.answer("Юзер не найден.", reply_markup=admin_back())
        return
    subs = await subs_repo.list_for_user(session, target_id)
    insts = await inst_repo.list_for_user(session, target_id)
    lines = [
        f"<b>Юзер</b> <code>{u.id}</code>",
        f"Имя: {u.first_name or '—'}",
        f"@{u.username}" if u.username else "username: —",
        f"Создан: {fmt_msk(u.created_at)}",
        f"Активен: {fmt_msk(u.last_seen_at)}",
        f"Заблокирован: {'да' if u.is_blocked else 'нет'}",
        f"Админ: {'да' if u.is_admin else 'нет'}",
        "",
        "<b>Подписки</b>",
    ]
    if subs:
        for s in subs:
            mark = "активна" if s.expires_at > now_utc() else "истекла"
            lines.append(f"  · {s.product.value} — до {fmt_msk(s.expires_at)} ({mark})")
    else:
        lines.append("  —")
    lines.append("")
    lines.append(f"<b>Серверы:</b> {len(insts)}")
    await msg.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=admin_user_actions(target_id),
    )


@router.callback_query(F.data.regexp(r"^admin:user:grant:(\d+):(\d+)$"))
async def cb_user_grant(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    parts = cb.data.split(":")
    target_id = int(parts[3])
    days = int(parts[4])
    await subs_repo.extend(session, target_id, ProductKind.CARDINAL, days)
    await logs_repo.write(
        session,
        kind="admin.grant",
        message=f"+{days}d to user {target_id}",
        user_id=user.id,
    )
    await session.commit()
    await cb.answer(f"+{days} дн.")


@router.callback_query(F.data.startswith("admin:user:revoke:"))
async def cb_user_revoke(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    target_id = int(cb.data.split(":")[3])
    res = await session.execute(
        select(Subscription).where(Subscription.user_id == target_id)
    )
    for s in res.scalars().all():
        s.expires_at = now_utc()
    await logs_repo.write(
        session,
        kind="admin.revoke",
        message=f"revoke subs for user {target_id}",
        user_id=user.id,
    )
    await session.commit()
    await cb.answer("Подписка снята")


@router.callback_query(F.data.startswith("admin:user:ban:"))
async def cb_user_ban(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    target_id = int(cb.data.split(":")[3])
    target = await session.get(User, target_id)
    if target:
        target.is_blocked = True
        await session.commit()
    await cb.answer("Заблокирован")


@router.callback_query(F.data.startswith("admin:user:unban:"))
async def cb_user_unban(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    target_id = int(cb.data.split(":")[3])
    target = await session.get(User, target_id)
    if target:
        target.is_blocked = False
        await session.commit()
    await cb.answer("Разблокирован")


# ---------------------------------------------------------------------------
# Coupons
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin:coupons")
async def cb_coupons_menu(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await _send_or_edit(cb, "<b>Купоны</b>", admin_coupons_menu())
    await cb.answer()


@router.callback_query(F.data == "admin:coupon:new")
async def cb_coupon_new(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await state.clear()
    await _send_or_edit(
        cb, "<b>Купон · шаг 1/2</b>\nНа сколько дней?", admin_coupon_days()
    )
    await cb.answer()


@router.callback_query(F.data == "admin:coupon:days:custom")
async def cb_coupon_days_custom(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_coupon_days_custom)
    if cb.message:
        await cb.message.answer(
            "Введи число дней (1–3650). /cancel — отмена.",
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_coupon_days_custom)
async def msg_coupon_days_custom(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await is_admin(session, user.id):
        return
    raw = (msg.text or "").strip()
    if raw == "/cancel":
        await state.clear()
        await msg.answer("Отменено.", reply_markup=admin_coupons_menu())
        return
    try:
        days = int(raw)
    except ValueError:
        await msg.answer("Нужно целое число дней. /cancel — отмена.")
        return
    if days < 1 or days > 3650:
        await msg.answer("Допустимо 1–3650 дней.")
        return
    await state.update_data(coupon_days=days)
    await msg.answer(
        f"<b>Купон · шаг 2/2</b>\nДней: {days}\nСколько активаций?",
        parse_mode="HTML",
        reply_markup=admin_coupon_uses(),
    )


@router.callback_query(F.data.regexp(r"^admin:coupon:days:(\d+)$"))
async def cb_coupon_days_preset(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    days = int(cb.data.split(":")[3])
    await state.update_data(coupon_days=days)
    await _send_or_edit(
        cb,
        f"<b>Купон · шаг 2/2</b>\nДней: {days}\nСколько активаций?",
        admin_coupon_uses(),
    )
    await cb.answer()


@router.callback_query(F.data == "admin:coupon:uses:custom")
async def cb_coupon_uses_custom(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    data = await state.get_data()
    if "coupon_days" not in data:
        await cb.answer("Сначала выбери число дней", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_coupon_uses_custom)
    if cb.message:
        await cb.message.answer(
            "Введи число активаций (1–10000). /cancel — отмена.",
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_coupon_uses_custom)
async def msg_coupon_uses_custom(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await is_admin(session, user.id):
        return
    raw = (msg.text or "").strip()
    if raw == "/cancel":
        await state.clear()
        await msg.answer("Отменено.", reply_markup=admin_coupons_menu())
        return
    try:
        uses = int(raw)
    except ValueError:
        await msg.answer("Нужно целое число. /cancel — отмена.")
        return
    if uses < 1 or uses > 10000:
        await msg.answer("Допустимо 1–10000 активаций.")
        return
    await _finalize_coupon(msg, state, session, user, uses=uses)


@router.callback_query(F.data.regexp(r"^admin:coupon:uses:(\d+)$"))
async def cb_coupon_uses_preset(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    uses = int(cb.data.split(":")[3])
    await _finalize_coupon(cb, state, session, user, uses=uses)
    await cb.answer()


async def _finalize_coupon(
    src: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    *,
    uses: int,
) -> None:
    data = await state.get_data()
    days = int(data.get("coupon_days") or 30)
    coupon = await coupons_repo.create(
        session,
        product=ProductKind.CARDINAL,
        days=days,
        max_uses=uses,
        issued_by=user.id,
    )
    await session.commit()
    await state.clear()
    uses_label = "1 (одноразовый)" if uses == 1 else f"{uses}"
    text = (
        "<b>Купон создан</b>\n\n"
        f"Код: <code>{coupon.code}</code>\n"
        f"Продукт: cardinal\n"
        f"Дней: {days}\n"
        f"Активаций: {uses_label}"
    )
    if isinstance(src, CallbackQuery):
        await _send_or_edit(src, text, admin_coupons_menu())
    else:
        await src.answer(text, parse_mode="HTML", reply_markup=admin_coupons_menu())


@router.callback_query(F.data == "admin:coupon:list")
async def cb_coupon_list(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    res = await session.execute(
        select(Coupon).order_by(Coupon.created_at.desc()).limit(30)
    )
    items = res.scalars().all()
    lines = ["<b>Купоны (последние 30)</b>", ""]
    if not items:
        lines.append("—")
    for c in items:
        max_uses = getattr(c, "max_uses", 1) or 1
        used = getattr(c, "uses_count", 0) or 0
        if used >= max_uses:
            tag = "исчерпан"
        elif used > 0:
            tag = f"{used}/{max_uses}"
        else:
            tag = f"свободен ({max_uses})"
        lines.append(
            f"<code>{c.code}</code> · {c.days}д · {tag}"
        )
    await _send_or_edit(cb, "\n".join(lines), admin_coupons_menu())
    await cb.answer()


@router.callback_query(F.data == "admin:coupon:del")
async def cb_coupon_del_start(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_coupon_del_code)
    if cb.message:
        await cb.message.answer("Пришли код купона для удаления.")
    await cb.answer()


@router.message(AdminFSM.awaiting_coupon_del_code)
async def msg_coupon_del(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await is_admin(session, user.id):
        return
    code = (msg.text or "").strip().upper()
    res = await session.execute(select(Coupon).where(Coupon.code == code))
    c = res.scalars().first()
    if not c:
        await msg.answer("Не найден", reply_markup=admin_back())
    else:
        await session.delete(c)
        await session.commit()
        await msg.answer("Удалён", reply_markup=admin_back())
    await state.clear()


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin:broadcast")
async def cb_broadcast_start(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_broadcast)
    if cb.message:
        await cb.message.answer(
            "Пришли текст рассылки одним сообщением. HTML поддерживается."
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_broadcast)
async def do_broadcast(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await is_admin(session, user.id):
        return
    text = msg.text or ""
    await state.clear()
    if not text:
        await msg.answer("Пусто", reply_markup=admin_back())
        return
    res = await session.execute(select(User).where(User.is_blocked == False))
    targets = [u.id for u in res.scalars().all()]
    sent = 0
    for uid in targets:
        try:
            await msg.bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.04)  # ~25 msg/s — under Telegram's global limit
    await msg.answer(
        f"Рассылка завершена: {sent}/{len(targets)}", reply_markup=admin_back()
    )


# ---------------------------------------------------------------------------
# Add / remove admin (commands only)
# ---------------------------------------------------------------------------


@router.message(Command("addadmin"))
async def cmd_add_admin(
    msg: Message, command: CommandObject, session: AsyncSession, user: User
) -> None:
    if not await is_admin(session, user.id):
        return
    arg = (command.args or "").strip()
    if not arg or not arg.isdigit():
        await msg.answer("Использование: /addadmin &lt;user_id&gt;")
        return
    target = await session.get(User, int(arg))
    if not target:
        await msg.answer("Не найден")
        return
    target.is_admin = True
    await session.commit()
    await msg.answer(f"Готово: {target.id} теперь админ.")


@router.message(Command("rmadmin"))
async def cmd_rm_admin(
    msg: Message, command: CommandObject, session: AsyncSession, user: User
) -> None:
    if not await is_admin(session, user.id):
        return
    arg = (command.args or "").strip()
    if not arg or not arg.isdigit():
        await msg.answer("Использование: /rmadmin &lt;user_id&gt;")
        return
    target = await session.get(User, int(arg))
    if target:
        target.is_admin = False
        await session.commit()
    await msg.answer("Готово")


# ---------------------------------------------------------------------------
# Coupon CLI commands (kept for power-users)
# ---------------------------------------------------------------------------


@router.message(Command("create_coupon"))
async def cmd_create_coupon(
    msg: Message, command: CommandObject, session: AsyncSession, user: User
) -> None:
    if not await is_admin(session, user.id):
        return
    parts = (command.args or "").split()
    if not parts:
        await msg.answer("Использование: /create_coupon &lt;days&gt; [uses=1]")
        return
    try:
        days = int(parts[0])
        uses = int(parts[1]) if len(parts) >= 2 else 1
    except ValueError:
        await msg.answer("days и uses должны быть числами")
        return
    coupon = await coupons_repo.create(
        session,
        product=ProductKind.CARDINAL,
        days=days,
        max_uses=uses,
        issued_by=user.id,
    )
    await session.commit()
    await msg.answer(
        f"Купон <code>{coupon.code}</code> · {days}д · {uses} акт.",
        parse_mode="HTML",
    )


@router.message(Command("coupons"))
async def cmd_coupons(msg: Message, session: AsyncSession, user: User) -> None:
    if not await is_admin(session, user.id):
        return
    res = await session.execute(
        select(Coupon).order_by(Coupon.created_at.desc()).limit(30)
    )
    items = res.scalars().all()
    if not items:
        await msg.answer("—")
        return
    lines = []
    for c in items:
        max_uses = getattr(c, "max_uses", 1) or 1
        used = getattr(c, "uses_count", 0) or 0
        tag = "исчерпан" if used >= max_uses else f"{used}/{max_uses}"
        lines.append(
            f"<code>{c.code}</code> · {c.days}д · {tag}"
        )
    await msg.answer("\n".join(lines), parse_mode="HTML")


# ---------------------------------------------------------------------------
# Servers (CLI shortcut)
# ---------------------------------------------------------------------------


@router.message(Command("shards"))
async def cmd_shards(msg: Message, session: AsyncSession, user: User) -> None:
    """Quick shard inventory view (read-only)."""
    if not await is_admin(session, user.id):
        return
    res = await session.execute(select(Shard).order_by(Shard.id))
    items = res.scalars().all()
    if not items:
        await msg.answer("Нет шардов. Они подгружаются из MIHOST_PRESEED_SHARDS.")
        return
    lines = ["<b>Шарды</b>", ""]
    for sh in items:
        lines.append(
            f"#{sh.id} · {sh.name} · {sh.status.value} · cap {sh.capacity}"
        )
    await msg.answer("\n".join(lines), parse_mode="HTML")
