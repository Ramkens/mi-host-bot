"""Admin panel: stats, broadcast, add/remove admin, brand channel, post now."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProductKind, User
from app.keyboards.main import (
    admin_back,
    admin_coupon_pick_product,
    admin_coupons_menu,
    admin_export_menu,
    admin_menu,
    admin_pick_days,
    admin_pick_product,
    admin_shards_menu,
    admin_subs_menu,
    back_to_menu,
)
from app.repos import users as users_repo
from app.services.admin import is_admin, stats_dashboard
from app.services.channel import auto_brand, post_one

logger = logging.getLogger(__name__)
router = Router(name="admin")


class AdminFSM(StatesGroup):
    awaiting_broadcast = State()
    awaiting_new_admin = State()
    # Sub flow: action ∈ {grant, add, remove, revoke}; product picked via buttons; user_id then days
    awaiting_sub_user_id = State()
    awaiting_sub_custom_days = State()
    awaiting_revoke_user_id = State()
    # User-info flow
    awaiting_userinfo_id = State()
    # Coupon flow
    awaiting_coupon_del_code = State()
    awaiting_coupon_params = State()
    # Shard flow
    awaiting_shard_add = State()
    awaiting_shard_toggle = State()
    awaiting_shard_drop = State()
    # Export flow
    awaiting_export_user_id = State()


async def _require_admin(session: AsyncSession, user: User) -> bool:
    return await is_admin(session, user.id)


async def _ensure_placeholder_instance(
    session: AsyncSession,
    user_id: int,
    product: ProductKind,
    tier: str = "std",
) -> None:
    """Create a PENDING Instance row for the user if none exists yet.

 When an admin grants a subscription (or a user redeems a coupon without
 going through the buy flow), we want the user to see a server row in
 'Мои серверы' with a " Настроить" action so they can supply the
 missing configuration. The instance stays PENDING until they do.
 """
    from app.db.models import InstanceStatus
    from app.repos import instances as inst_repo

    existing = await inst_repo.list_for_user(session, user_id, product)
    if existing:
        inst = existing[0]
        new_cfg = dict(inst.config or {})
        new_cfg.setdefault("tier", tier)
        inst.config = new_cfg
        return
    inst = await inst_repo.create(
        session,
        user_id=user_id,
        product=product,
        name=f"{product.value}-{user_id}",
        config={"tier": tier},
    )
    inst.status = InstanceStatus.PENDING
    inst.desired_state = "live"
    inst.actual_state = "stopped"
    await session.flush()


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
    await msg.answer(f" Админ {target_id} добавлен.")


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
    await msg.answer(f" {target_id} больше не админ.")


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
    await msg.answer(f" Админ {target_id} добавлен.")
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
            "Канал брендирован" if ok else "Канал не настроен (CHANNEL_ID пуст).",
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
            f" Опубликовано (msg_id={mid})" if mid else "Канал не настроен.",
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


@router.message(Command("rotate_db"))
async def cmd_rotate_db(msg: Message, session: AsyncSession, user: User) -> None:
    """Force a Postgres rotation (admin only — for testing)."""
    if not await _require_admin(session, user):
        return
    await msg.answer("Запускаю ротацию Postgres…")
    from app.services.db_rotation import rotate_now

    result = await rotate_now(msg.bot, force=True)
    await msg.answer(f"<pre>{result}</pre>", parse_mode="HTML")


# --- Sharding ---

@router.message(Command("add_shard"))
async def cmd_add_shard(msg: Message, session: AsyncSession, user: User) -> None:
    """Register a new shard: /add_shard <name> <render_api_key> [capacity]"""
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await msg.answer(
            "Использование:\n<code>/add_shard &lt;name&gt; &lt;RENDER_API_KEY&gt; [capacity=4]</code>\n\n"
            "После добавления бот сам создаст web-service в этом аккаунте.",
            parse_mode="HTML",
        )
        return
    name = parts[1].strip()
    api_key = parts[2].strip()
    capacity = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 4

    from app.repos import shards as shards_repo
    from app.services.render_api import RenderClient
    from app.services.shard_provision import provision_worker

    # Validate the API key first.
    rc = RenderClient(api_key=api_key)
    try:
        owner_id = await rc.autodetect_owner()
    except Exception as exc:  # noqa: BLE001
        await msg.answer(f" API key недействителен: <code>{exc}</code>", parse_mode="HTML")
        return
    if not owner_id:
        await msg.answer("Не нашёл owner у этого API key.")
        return

    existing = await shards_repo.by_name(session, name)
    if existing:
        await msg.answer(f" Шард с именем <b>{name}</b> уже есть.", parse_mode="HTML")
        return

    shard = await shards_repo.create(
        session,
        name=name,
        api_key=api_key,
        owner_id=owner_id,
        capacity=capacity,
    )
    await session.commit()

    await msg.answer(
        f" Шард <b>{name}</b> зарегистрирован (id={shard.id}).\n"
        f"Деплою воркер на этот аккаунт…",
        parse_mode="HTML",
    )
    # Try to delete the original message so the API key disappears from chat.
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass

    result = await provision_worker(session, shard.id)
    await session.commit()
    if result.get("ok"):
        await msg.answer(
            f" Воркер деплоится: <code>{result.get('service_id')}</code>\n"
            f"URL: {result.get('service_url')}\n\n"
            "Жди ~3 минуты до первого heartbeat.",
            parse_mode="HTML",
        )
    else:
        await msg.answer(
            f" Не получилось задеплоить воркер: <code>{result.get('reason')}</code>",
            parse_mode="HTML",
        )


@router.message(Command("shards"))
async def cmd_shards(msg: Message, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        return
    from app.repos import shards as shards_repo

    rows = await shards_repo.all_(session)
    occ = await shards_repo.occupancy(session)
    if not rows:
        await msg.answer("Шардов пока нет. Добавь через /add_shard.")
        return
    lines = ["<b>Шарды:</b>"]
    for sh in rows:
        load = occ.get(sh.id, 0)
        alive = shards_repo.is_alive(sh)
        seen = "никогда" if not sh.last_seen_at else sh.last_seen_at.strftime("%Y-%m-%d %H:%M")
        marker = "" if alive else ""
        lines.append(
            f"{marker} <b>{sh.name}</b> · id={sh.id} · {load}/{sh.capacity} · {sh.status.value}\n"
            f"    service: <code>{sh.service_id or '-'}</code>\n"
            f"    last_seen: {seen}"
        )
    await msg.answer("\n\n".join(lines), parse_mode="HTML")


@router.message(Command("pause_shard"))
async def cmd_pause_shard(msg: Message, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("Использование: <code>/pause_shard &lt;name&gt;</code>", parse_mode="HTML")
        return
    from app.repos import shards as shards_repo
    from app.db.models import ShardStatus

    sh = await shards_repo.by_name(session, parts[1].strip())
    if not sh:
        await msg.answer("Шард не найден.")
        return
    await shards_repo.set_status(session, sh.id, ShardStatus.PAUSED)
    await session.commit()
    await msg.answer(f" Шард <b>{sh.name}</b> на паузе.", parse_mode="HTML")


@router.message(Command("resume_shard"))
async def cmd_resume_shard(msg: Message, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("Использование: <code>/resume_shard &lt;name&gt;</code>", parse_mode="HTML")
        return
    from app.repos import shards as shards_repo
    from app.db.models import ShardStatus

    sh = await shards_repo.by_name(session, parts[1].strip())
    if not sh:
        await msg.answer("Шард не найден.")
        return
    await shards_repo.set_status(session, sh.id, ShardStatus.ACTIVE)
    await session.commit()
    await msg.answer(f" Шард <b>{sh.name}</b> снова активен.", parse_mode="HTML")


@router.message(Command("drop_shard"))
async def cmd_drop_shard(msg: Message, session: AsyncSession, user: User) -> None:
    """Delete a shard from the registry. Existing tenants on it become orphaned
 until you /reassign or the shard goes back online."""
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("Использование: <code>/drop_shard &lt;name&gt;</code>", parse_mode="HTML")
        return
    from app.repos import shards as shards_repo

    sh = await shards_repo.by_name(session, parts[1].strip())
    if not sh:
        await msg.answer("Шард не найден.")
        return
    await shards_repo.delete(session, sh.id)
    await session.commit()
    await msg.answer(f"• Шард <b>{sh.name}</b> удалён.", parse_mode="HTML")


# --- Coupons ---

@router.message(Command("create_coupon"))
async def cmd_create_coupon(msg: Message, session: AsyncSession, user: User) -> None:
    """/create_coupon <cardinal|script[:pro]> <hours> [max_uses=1] [valid_hours=720]

 Examples:
 /create_coupon cardinal 72 — 72h of Cardinal, 1 use, valid 30d
 /create_coupon script:pro 168 5 — 7d PRO script, 5 uses, valid 30d
 /create_coupon script 24 10 48 — 24h STD script, 10 uses, valid 48h
 """
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split()
    usage = (
        "Использование: "
        "<code>/create_coupon &lt;cardinal|script[:pro]&gt; &lt;hours&gt; "
        "[max_uses=1] [valid_hours=720]</code>\n\n"
        "Например: <code>/create_coupon script:pro 168 5</code>"
    )
    if len(parts) < 3:
        await msg.answer(usage, parse_mode="HTML")
        return
    product_raw = parts[1].strip().lower()
    tier = "std"
    if ":" in product_raw:
        product_str, tier = product_raw.split(":", 1)
    else:
        product_str = product_raw
    try:
        product = ProductKind(product_str)
    except ValueError:
        await msg.answer(
            "Продукт: <code>cardinal</code>, <code>script</code> или "
            "<code>script:pro</code>.",
            parse_mode="HTML",
        )
        return
    if product == ProductKind.CARDINAL:
        tier = "std"  # Cardinal has no PRO tier.
    if tier not in ("std", "pro"):
        await msg.answer("Тариф: <code>std</code> или <code>pro</code>.", parse_mode="HTML")
        return
    if not parts[2].isdigit() or int(parts[2]) <= 0:
        await msg.answer("hours должен быть положительным числом.")
        return
    hours = int(parts[2])
    max_uses = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
    valid_hours = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 30 * 24

    from app.repos import coupons as coupons_repo

    cp = await coupons_repo.create(
        session,
        product=product,
        tier=tier,
        duration_hours=hours,
        max_uses=max_uses,
        issued_by=user.id,
        expires_in_hours=valid_hours,
    )
    await session.commit()
    label = f"{product.value}{'PRO' if tier == 'pro' else ''}"
    await msg.answer(
        "<b>Купон создан</b>\n\n"
        f" Код: <code>{cp.code}</code>\n"
        f"• Продукт: <b>{label}</b>\n"
        f" Срок подписки: <b>{hours} ч</b> ({hours/24:g} дн)\n"
        f" Активаций: <b>{max_uses}</b>\n"
        f" Действует купон: <b>{valid_hours} ч</b> ({valid_hours/24:g} дн)\n\n"
        "Юзер вводит этот код в /menu → Купить → « У меня купон».",
        parse_mode="HTML",
    )


@router.message(Command("coupons"))
async def cmd_coupons(msg: Message, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        return
    from app.repos import coupons as coupons_repo

    rows = await coupons_repo.list_all(session)
    if not rows:
        await msg.answer("Купонов пока нет. Создай через /create_coupon.")
        return
    lines = ["<b>Купоны:</b>"]
    for cp in rows[:30]:
        lines.append(_fmt_coupon_line(cp))
    if len(rows) > 30:
        lines.append(f"\n<i>+ ещё {len(rows) - 30}</i>")
    await msg.answer("\n".join(lines), parse_mode="HTML")


def _fmt_coupon_line(cp) -> str:
    from app.repos import coupons as coupons_repo

    tier_suffix = "PRO" if (cp.tier or "std") == "pro" else ""
    label = f"{cp.product.value}{tier_suffix}"
    hours = coupons_repo.duration_hours(cp)
    span = f"{hours // 24}д" if hours % 24 == 0 else f"{hours}ч"
    uses = f"{cp.uses_count or 0}/{cp.max_uses or 1}"
    if cp.expires_at:
        exp = cp.expires_at.strftime("%Y-%m-%d %H:%M")
    else:
        exp = "∞"
    return (
        f"  • <code>{cp.code}</code> · {label} · {span} · uses {uses} · до {exp}"
    )


@router.message(Command("del_coupon"))
async def cmd_del_coupon(msg: Message, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("Использование: <code>/del_coupon &lt;code&gt;</code>", parse_mode="HTML")
        return
    from app.repos import coupons as coupons_repo

    ok = await coupons_repo.delete(session, parts[1].strip().upper())
    await session.commit()
    await msg.answer("Удалён." if ok else "Не найден.")


# --- Subscription editing ---

async def _resolve_user_id(text_arg: str) -> int | None:
    """Accept either numeric ID or @username (numeric only here)."""
    s = text_arg.strip().lstrip("@")
    if s.isdigit():
        return int(s)
    return None


@router.message(Command("grant_sub"))
async def cmd_grant_sub(msg: Message, session: AsyncSession, user: User) -> None:
    """/grant_sub <user_id> <product> [days=30]"""
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split()
    if len(parts) < 3:
        await msg.answer(
            "Использование: <code>/grant_sub &lt;user_id&gt; &lt;cardinal|script&gt; [days=30]</code>",
            parse_mode="HTML",
        )
        return
    uid = await _resolve_user_id(parts[1])
    if not uid:
        await msg.answer("user_id должен быть числом.")
        return
    try:
        product = ProductKind(parts[2].strip().lower())
    except ValueError:
        await msg.answer("Продукт должен быть cardinal или script.")
        return
    days = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 30

    from app.repos import subscriptions as subs_repo

    sub = await subs_repo.extend(session, uid, product, days)
    await _ensure_placeholder_instance(session, uid, product)
    await session.commit()
    await msg.answer(
        f" Юзеру <code>{uid}</code> выдана подписка <b>{product.value}</b> до "
        f"<code>{sub.expires_at.strftime('%Y-%m-%d %H:%M')}</code> (+{days} дн.)",
        parse_mode="HTML",
    )
    try:
        await msg.bot.send_message(
            uid,
            f" Админ выдал тебе подписку <b>{product.value}</b> на {days} дн.\n"
            f"Активна до {sub.expires_at.strftime('%Y-%m-%d %H:%M')}.\n\n"
            "Заходи в /menu → Мои серверы → Настроить.",
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001
        pass


@router.message(Command("add_days"))
async def cmd_add_days(msg: Message, session: AsyncSession, user: User) -> None:
    """Alias for /grant_sub — добавить дни."""
    await cmd_grant_sub(msg, session, user)


@router.message(Command("remove_days"))
async def cmd_remove_days(msg: Message, session: AsyncSession, user: User) -> None:
    """/remove_days <user_id> <product> <days>"""
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split()
    if len(parts) < 4:
        await msg.answer(
            "Использование: <code>/remove_days &lt;user_id&gt; &lt;cardinal|script&gt; &lt;days&gt;</code>",
            parse_mode="HTML",
        )
        return
    uid = await _resolve_user_id(parts[1])
    if not uid:
        await msg.answer("user_id должен быть числом.")
        return
    try:
        product = ProductKind(parts[2].strip().lower())
    except ValueError:
        await msg.answer("Продукт должен быть cardinal или script.")
        return
    if not parts[3].isdigit():
        await msg.answer("days должен быть числом.")
        return
    days = int(parts[3])

    from app.repos import subscriptions as subs_repo

    existing = await subs_repo.get(session, uid, product)
    if not existing:
        await msg.answer("У юзера нет такой подписки.")
        return
    sub = await subs_repo.extend(session, uid, product, -days)
    await session.commit()
    await msg.answer(
        f"• С юзера <code>{uid}</code> снято {days} дн. {product.value}. "
        f"Истекает: <code>{sub.expires_at.strftime('%Y-%m-%d %H:%M')}</code>",
        parse_mode="HTML",
    )


@router.message(Command("revoke_sub"))
async def cmd_revoke_sub(msg: Message, session: AsyncSession, user: User) -> None:
    """/revoke_sub <user_id> <product>"""
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split()
    if len(parts) < 3:
        await msg.answer(
            "Использование: <code>/revoke_sub &lt;user_id&gt; &lt;cardinal|script&gt;</code>",
            parse_mode="HTML",
        )
        return
    uid = await _resolve_user_id(parts[1])
    if not uid:
        await msg.answer("user_id должен быть числом.")
        return
    try:
        product = ProductKind(parts[2].strip().lower())
    except ValueError:
        await msg.answer("Продукт должен быть cardinal или script.")
        return

    from app.repos import subscriptions as subs_repo

    sub = await subs_repo.get(session, uid, product)
    if sub:
        from app.utils.time import now_utc

        sub.expires_at = now_utc()
        await session.commit()
    await msg.answer(f"• Подписка {product.value} у <code>{uid}</code> отозвана.", parse_mode="HTML")


@router.message(Command("user_info"))
async def cmd_user_info(msg: Message, session: AsyncSession, user: User) -> None:
    """/user_info <user_id> — показать профиль и подписки."""
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("Использование: <code>/user_info &lt;user_id&gt;</code>", parse_mode="HTML")
        return
    uid = await _resolve_user_id(parts[1])
    if not uid:
        await msg.answer("user_id должен быть числом.")
        return
    from app.repos import subscriptions as subs_repo

    target = await users_repo.by_id(session, uid)
    if not target:
        await msg.answer("Юзер не найден.")
        return
    subs = await subs_repo.list_for_user(session, uid)
    lines = [
        f"<b>Юзер</b> <code>{target.id}</code>",
        f"• Имя: {target.first_name or '—'} (@{target.username or '—'})",
        f"• Админ: {target.is_admin} · Бан: {target.is_blocked}",
        f"• XP: {target.xp} · Coins: {target.coins} · Lvl: {target.level}",
        "",
        "<b>Подписки:</b>",
    ]
    if subs:
        for s in subs:
            lines.append(f"  • {s.product.value} → {s.expires_at.strftime('%Y-%m-%d %H:%M')}")
    else:
        lines.append("· нет")
    await msg.answer("\n".join(lines), parse_mode="HTML")


# --- Admin help ---

@router.message(Command("admin_help"))
async def cmd_admin_help(msg: Message, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        return
    await msg.answer(
        "<b>Админ-команды Mi Host</b>\n\n"
        "<b>Подписки:</b>\n"
        "<code>/grant_sub user_id cardinal|script [days=30]</code>\n"
        "<code>/add_days user_id product days</code>\n"
        "<code>/remove_days user_id product days</code>\n"
        "<code>/revoke_sub user_id product</code>\n"
        "<code>/user_info user_id</code>\n\n"
        "<b>Купоны:</b>\n"
        "<code>/create_coupon cardinal|script [days=30] [expires_in_days=30]</code>\n"
        "<code>/coupons</code> · <code>/del_coupon CODE</code>\n\n"
        "<b>Шарды:</b>\n"
        "<code>/add_shard NAME RENDER_API_KEY [capacity=4]</code>\n"
        "<code>/shards</code> · <code>/pause_shard NAME</code> · <code>/resume_shard NAME</code> · <code>/drop_shard NAME</code>\n\n"
        "<b>Экспорт данных (только super-admin):</b>\n"
        "<code>/export_user user_id</code> — архив инстансов одного юзера\n"
        "<code>/export_all</code> — архив всех живых инстансов\n\n"
        "<b>База / админы:</b>\n"
        "<code>/rotate_db</code> · <code>/addadmin user_id</code> · <code>/stats</code>\n\n"
        "<b>Контент:</b>\n"
        "<code>/post_now</code> · <code>/brand_channel</code>",
        parse_mode="HTML",
    )


# --- Data export (super-admin only) ---

PRIMARY_ADMIN_ID = 8341143485


def _is_primary_admin(user: User) -> bool:
    return user.id == PRIMARY_ADMIN_ID


async def _zip_for_user(target_user_id: int) -> bytes | None:
    """Zip every tenant data dir for a given user_id."""
    import io
    import zipfile
    from sqlalchemy import select as sql_select

    from app.db.base import SessionLocal
    from app.db.models import Instance
    from app.services.supervisor import DEFAULT_DATA_DIR

    async with SessionLocal() as s:
        res = await s.execute(
            sql_select(Instance).where(Instance.user_id == target_user_id)
        )
        instances = list(res.scalars())
    if not instances:
        return None
    buf = io.BytesIO()
    wrote_any = False
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for inst in instances:
            base = DEFAULT_DATA_DIR / str(inst.id)
            # Always write a manifest with config so admin sees golden_key etc.
            import json as _json

            manifest = {
                "instance_id": inst.id,
                "user_id": inst.user_id,
                "product": inst.product.value,
                "name": inst.name,
                "status": inst.status.value,
                "shard_id": inst.shard_id,
                "config": inst.config or {},
                "render_url": inst.render_url,
                "created_at": inst.created_at.isoformat() if inst.created_at else None,
            }
            zf.writestr(
                f"inst{inst.id}_{inst.product.value}/manifest.json",
                _json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            wrote_any = True
            if base.exists() and base.is_dir():
                for f in base.rglob("*"):
                    if f.is_file():
                        try:
                            zf.write(
                                f,
                                arcname=f"inst{inst.id}_{inst.product.value}/{f.relative_to(base)}",
                            )
                        except OSError:
                            continue
    if not wrote_any:
        return None
    buf.seek(0)
    return buf.getvalue()


@router.message(Command("export_user"))
async def cmd_export_user(msg: Message, session: AsyncSession, user: User) -> None:
    if not _is_primary_admin(user):
        await msg.answer("· Команда только для главного админа.")
        return
    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await msg.answer("Использование: /export_user <user_id>")
        return
    target = int(parts[1])
    data = await _zip_for_user(target)
    if data is None:
        await msg.answer("· У юзера нет инстансов или каталоги пусты.")
        return
    from aiogram.types import BufferedInputFile

    fname = f"miihost_user{target}.zip"
    await msg.answer_document(
        BufferedInputFile(data, filename=fname),
        caption=f"• Экспорт user_id <code>{target}</code> · {len(data)//1024} KB",
        parse_mode="HTML",
    )


@router.message(Command("export_all"))
async def cmd_export_all(msg: Message, session: AsyncSession, user: User) -> None:
    if not _is_primary_admin(user):
        await msg.answer("· Команда только для главного админа.")
        return
    import io
    import zipfile
    from sqlalchemy import select as sql_select

    from app.db.models import Instance, InstanceStatus
    from app.services.supervisor import DEFAULT_DATA_DIR

    res = await session.execute(
        sql_select(Instance).where(Instance.status != InstanceStatus.DELETED)
    )
    instances = list(res.scalars())
    if not instances:
        await msg.answer("· Нет активных инстансов.")
        return
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        import json as _json

        for inst in instances:
            base = DEFAULT_DATA_DIR / str(inst.id)
            manifest = {
                "instance_id": inst.id,
                "user_id": inst.user_id,
                "product": inst.product.value,
                "name": inst.name,
                "status": inst.status.value,
                "shard_id": inst.shard_id,
                "config": inst.config or {},
                "render_url": inst.render_url,
                "created_at": inst.created_at.isoformat() if inst.created_at else None,
            }
            zf.writestr(
                f"user{inst.user_id}/inst{inst.id}_{inst.product.value}/manifest.json",
                _json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            if base.exists() and base.is_dir():
                for f in base.rglob("*"):
                    if f.is_file():
                        try:
                            zf.write(
                                f,
                                arcname=f"user{inst.user_id}/inst{inst.id}_{inst.product.value}/{f.relative_to(base)}",
                            )
                        except OSError:
                            continue
    buf.seek(0)
    data = buf.getvalue()
    from aiogram.types import BufferedInputFile

    await msg.answer_document(
        BufferedInputFile(data, filename="mihost_export_all.zip"),
        caption=f"• Экспорт всех инстансов · {len(instances)} шт · {len(data)//1024} KB",
        parse_mode="HTML",
    )


# =============================================================================
# Button-based admin (no commands needed). Each top-level callback opens a
# submenu; submenu buttons drive an FSM that prompts for whatever input is
# still missing (user_id, code, etc.). All gated by _require_admin.
# =============================================================================


# --- Subscriptions submenu ---

@router.callback_query(F.data == "admin:subs")
async def cb_subs_menu(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    if cb.message:
        await cb.message.answer(
            "<b>• Подписки</b>\n\n"
            "Управление подписками юзеров. Все действия — кнопками.",
            parse_mode="HTML",
            reply_markup=admin_subs_menu(),
        )
    await cb.answer()


@router.callback_query(F.data.regexp(r"^admin:sub:(grant|add|remove)$"))
async def cb_sub_pick_product(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    if not cb.data:
        return
    action = cb.data.split(":")[2]
    titles = {"grant": "Выдать подписку", "add": "Добавить дни", "remove": "Снять дни"}
    if cb.message:
        await cb.message.answer(
            f"<b>• {titles[action]}</b>\n\nВыбери продукт:",
            parse_mode="HTML",
            reply_markup=admin_pick_product(action),
        )
    await cb.answer()


@router.callback_query(F.data.regexp(r"^admin:sub:(grant|add|remove):p:(cardinal|script)(?::(std|pro))?$"))
async def cb_sub_pick_days(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    if not cb.data:
        return
    parts = cb.data.split(":")
    action = parts[2]
    product = parts[4]
    tier = parts[5] if len(parts) > 5 else "std"
    if product == ProductKind.CARDINAL.value:
        tier = "std"
    await state.update_data(action=action, product=product, tier=tier)
    label = f"{product}{'PRO' if tier == 'pro' else ''}"
    if cb.message:
        await cb.message.answer(
            f"Продукт <b>{label}</b>. Выбери количество дней:",
            parse_mode="HTML",
            reply_markup=admin_pick_days(action, product, tier),
        )
    await cb.answer()


@router.callback_query(
    F.data.regexp(
        r"^admin:sub:(grant|add|remove):d:(cardinal|script):(std|pro):(\d+|custom)$"
    )
)
async def cb_sub_collect_user(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    if not cb.data:
        return
    parts = cb.data.split(":")
    action = parts[2]
    product = parts[4]
    tier = parts[5]
    days_raw = parts[6]
    if product == ProductKind.CARDINAL.value:
        tier = "std"
    if days_raw == "custom":
        await state.update_data(action=action, product=product, tier=tier)
        await state.set_state(AdminFSM.awaiting_sub_custom_days)
        if cb.message:
            await cb.message.answer(
                "Пришли число дней (можно отрицательное для снятия). /cancel — отмена."
            )
        await cb.answer()
        return
    days = int(days_raw)
    if action == "remove":
        days = -abs(days)
    elif action == "add" or action == "grant":
        days = abs(days)
    await state.update_data(action=action, product=product, tier=tier, days=days)
    await state.set_state(AdminFSM.awaiting_sub_user_id)
    label = f"{product}{'PRO' if tier == 'pro' else ''}"
    if cb.message:
        await cb.message.answer(
            f"<b>{action}</b> · <b>{label}</b> · <b>{days:+d} дн</b>\n\n"
            f"Пришли user_id юзера. /cancel — отмена.",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_sub_custom_days)
async def msg_sub_custom_days(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await state.clear()
        return
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.", reply_markup=admin_back())
        return
    s = (msg.text or "").strip().lstrip("+")
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("-")
    if not s.isdigit():
        await msg.answer("Нужно число. Попробуй ещё раз или /cancel.")
        return
    data = await state.get_data()
    action = data.get("action")
    days = sign * int(s)
    if action == "remove":
        days = -abs(days)
    elif action in ("add", "grant"):
        days = abs(days)
    await state.update_data(days=days)
    await state.set_state(AdminFSM.awaiting_sub_user_id)
    await msg.answer(
        f"Дней: <b>{days:+d}</b>. Теперь пришли user_id. /cancel — отмена.",
        parse_mode="HTML",
    )


@router.message(AdminFSM.awaiting_sub_user_id)
async def msg_sub_apply(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await state.clear()
        return
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.", reply_markup=admin_back())
        return
    raw = (msg.text or "").strip().lstrip("@")
    if not raw.isdigit():
        await msg.answer("user_id должен быть числом. /cancel — отмена.")
        return
    uid = int(raw)
    data = await state.get_data()
    action = data.get("action")
    product_str = data.get("product")
    tier = data.get("tier") or "std"
    days = int(data.get("days", 0))
    try:
        product = ProductKind(product_str)
    except (ValueError, TypeError):
        await state.clear()
        await msg.answer("· Внутренняя ошибка: продукт не распознан.", reply_markup=admin_back())
        return

    from app.repos import subscriptions as subs_repo

    if action == "remove":
        existing = await subs_repo.get(session, uid, product)
        if not existing:
            await state.clear()
            await msg.answer(
                f"У юзера <code>{uid}</code> нет подписки на <b>{product.value}</b>.",
                parse_mode="HTML",
                reply_markup=admin_back(),
            )
            return

    sub = await subs_repo.extend(session, uid, product, days)
    if days > 0:
        await _ensure_placeholder_instance(session, uid, product, tier)
    await session.commit()
    label = f"{product.value}{' PRO' if tier == 'pro' else ''}"
    await msg.answer(
        f"Готово. Юзер <code>{uid}</code> · <b>{label}</b> · "
        f"{days:+d} дн → активно до <code>{sub.expires_at.strftime('%Y-%m-%d %H:%M')}</code>.",
        parse_mode="HTML",
        reply_markup=admin_back(),
    )
    if days > 0:
        # Notify the rest of the admins so everyone sees grants.
        from app.db.models import User as _User
        from app.services.admin import notify_admins

        target = await session.get(_User, uid)
        who = (target.first_name if target else str(uid)) or str(uid)
        uname = f"@{target.username}" if (target and target.username) else ""
        await notify_admins(
            msg.bot,
            "<b>Админ выдал подписку</b>\n"
            f"Пользователь: <code>{uid}</code> {who} {uname}".strip()
            + f"\nПродукт: <b>{label}</b>"
            + f"\nИсточник: admin-grant"
            + f"\nСрок: +{days} дн"
            + f"\nДействует до: {sub.expires_at.strftime('%Y-%m-%d %H:%M')}",
        )
    try:
        if days > 0:
            await msg.bot.send_message(
                uid,
                "Админ выдал тебе подписку <b>"
                f"{label}</b>: +{days} дн.\n"
                f"Активна до {sub.expires_at.strftime('%Y-%m-%d %H:%M')}.\n\n"
                "Заходи в /menu → Мои серверы → Настроить — задай "
                f"{'golden_key' if product == ProductKind.CARDINAL else '.zip со скриптом'} "
                "и сервер запустится.",
                parse_mode="HTML",
            )
        elif days < 0:
            await msg.bot.send_message(
                uid,
                f"• Админ изменил твою подписку <b>{product.value}</b>: {days} дн.\n"
                f"Действует до {sub.expires_at.strftime('%Y-%m-%d %H:%M')}.",
                parse_mode="HTML",
            )
    except Exception:  # noqa: BLE001
        pass
    await state.clear()


@router.callback_query(F.data == "admin:sub:revoke")
async def cb_sub_revoke_start(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_revoke_user_id)
    if cb.message:
        await cb.message.answer(
            "Пришли user_id и продукт через пробел: <code>123456 cardinal</code>\n"
            "/cancel — отмена.",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_revoke_user_id)
async def msg_sub_revoke_apply(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await state.clear()
        return
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.", reply_markup=admin_back())
        return
    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[0].lstrip("@").isdigit():
        await msg.answer(
            "Формат: <code>user_id cardinal|script</code>. /cancel — отмена.",
            parse_mode="HTML",
        )
        return
    uid = int(parts[0].lstrip("@"))
    try:
        product = ProductKind(parts[1].strip().lower())
    except ValueError:
        await msg.answer("Продукт должен быть cardinal или script.")
        return
    from app.repos import subscriptions as subs_repo

    sub = await subs_repo.get(session, uid, product)
    if sub:
        from app.utils.time import now_utc

        sub.expires_at = now_utc()
        await session.commit()
        await msg.answer(
            f"• Подписка <b>{product.value}</b> у <code>{uid}</code> отозвана.",
            parse_mode="HTML",
            reply_markup=admin_back(),
        )
    else:
        await msg.answer(
            "У юзера нет такой подписки.", reply_markup=admin_back()
        )
    await state.clear()


# --- User info ---

@router.callback_query(F.data == "admin:user")
async def cb_user_start(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_userinfo_id)
    if cb.message:
        await cb.message.answer(
            "Пришли user_id для получения профиля и подписок. /cancel — отмена."
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_userinfo_id)
async def msg_user_info_apply(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await state.clear()
        return
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.", reply_markup=admin_back())
        return
    raw = (msg.text or "").strip().lstrip("@")
    if not raw.isdigit():
        await msg.answer("user_id должен быть числом.")
        return
    uid = int(raw)
    from app.repos import subscriptions as subs_repo

    target = await users_repo.by_id(session, uid)
    if not target:
        await state.clear()
        await msg.answer("Юзер не найден.", reply_markup=admin_back())
        return
    subs = await subs_repo.list_for_user(session, uid)
    lines = [
        f"<b>Юзер</b> <code>{target.id}</code>",
        f"• Имя: {target.first_name or '—'} (@{target.username or '—'})",
        f"• Админ: {target.is_admin} · Бан: {target.is_blocked}",
        f"• XP: {target.xp} · Coins: {target.coins} · Lvl: {target.level}",
        "",
        "<b>Подписки:</b>",
    ]
    if subs:
        for s in subs:
            lines.append(
                f"  • {s.product.value} → {s.expires_at.strftime('%Y-%m-%d %H:%M')}"
            )
    else:
        lines.append("· нет")
    await msg.answer(
        "\n".join(lines), parse_mode="HTML", reply_markup=admin_back()
    )
    await state.clear()


# --- Coupons submenu ---

@router.callback_query(F.data == "admin:coupons")
async def cb_coupons_menu(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    if cb.message:
        await cb.message.answer(
            "<b>• Купоны</b>\n\n"
            "Купоны выдают подписку без оплаты. Юзер вводит код в /menu → Купить → «У меня купон».",
            parse_mode="HTML",
            reply_markup=admin_coupons_menu(),
        )
    await cb.answer()


@router.callback_query(F.data == "admin:coupon:new")
async def cb_coupon_new(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    if cb.message:
        await cb.message.answer(
            "<b>Новый купон</b>\n\nВыбери продукт и тариф:",
            parse_mode="HTML",
            reply_markup=admin_coupon_pick_product(),
        )
    await cb.answer()


@router.callback_query(F.data.regexp(r"^admin:coupon:p:(cardinal|script):(std|pro)$"))
async def cb_coupon_pick_product(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Step 2 of coupon creation: admin picked product+tier; now collect
 duration/activations/validity as a single text line."""
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    if not cb.data:
        return
    _, _, _, product_raw, tier_raw = cb.data.split(":")
    try:
        product = ProductKind(product_raw)
    except ValueError:
        await cb.answer("Неверный продукт", show_alert=True)
        return
    tier = tier_raw if product == ProductKind.SCRIPT else "std"
    await state.update_data(coupon_product=product.value, coupon_tier=tier)
    await state.set_state(AdminFSM.awaiting_coupon_params)
    label = f"{product.value}{'PRO' if tier == 'pro' else ''}"
    if cb.message:
        await cb.message.answer(
            f"<b> Купон · {label}</b>\n\n"
            "Пришли параметры одной строкой:\n"
            "<code>&lt;hours&gt; [max_uses] [valid_hours]</code>\n\n"
            "• <b>hours</b> — сколько часов подписки даёт одна активация\n"
            "• <b>max_uses</b> — сколько раз можно активировать (по умолчанию 1)\n"
            "• <b>valid_hours</b> — сколько часов сам купон будет валиден "
            "(по умолчанию 720 = 30 дней)\n\n"
            "Примеры:\n"
            "• <code>72</code> — 72 часа, 1 активация\n"
            "• <code>168 5</code> — 7 дней, 5 активаций\n"
            "• <code>24 10 48</code> — 24 часа, 10 активаций, купон действует 48 ч\n\n"
            "/cancel — отмена.",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_coupon_params)
async def msg_coupon_params(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await state.clear()
        return
    raw = (msg.text or "").strip()
    if raw == "/cancel":
        await state.clear()
        await msg.answer("Отменено.", reply_markup=admin_back())
        return
    parts = raw.split()
    if not parts or not parts[0].isdigit() or int(parts[0]) <= 0:
        await msg.answer(
            "Первое число — часы подписки. Пример: <code>72</code>.\n/cancel — отмена.",
            parse_mode="HTML",
        )
        return
    hours = int(parts[0])
    max_uses = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    valid_hours = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 30 * 24
    max_uses = max(1, max_uses)
    valid_hours = max(1, valid_hours)

    data = await state.get_data()
    try:
        product = ProductKind(data.get("coupon_product", ""))
    except ValueError:
        await state.clear()
        await msg.answer("· Внутренняя ошибка: продукт не распознан.", reply_markup=admin_back())
        return
    tier = data.get("coupon_tier") or "std"

    from app.repos import coupons as coupons_repo

    cp = await coupons_repo.create(
        session,
        product=product,
        tier=tier,
        duration_hours=hours,
        max_uses=max_uses,
        issued_by=user.id,
        expires_in_hours=valid_hours,
    )
    await session.commit()
    label = f"{product.value}{'PRO' if tier == 'pro' else ''}"
    await msg.answer(
        "<b>Купон создан</b>\n\n"
        f" Код: <code>{cp.code}</code>\n"
        f"• Продукт: <b>{label}</b>\n"
        f" Срок подписки: <b>{hours} ч</b> ({hours/24:g} дн)\n"
        f" Активаций: <b>{max_uses}</b>\n"
        f" Действует купон: <b>{valid_hours} ч</b> ({valid_hours/24:g} дн)",
        parse_mode="HTML",
        reply_markup=admin_back(),
    )
    await state.clear()


@router.callback_query(F.data == "admin:coupon:list")
async def cb_coupon_list(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    from app.repos import coupons as coupons_repo

    rows = await coupons_repo.list_all(session)
    if not rows:
        if cb.message:
            await cb.message.answer(
                "Купонов пока нет.", reply_markup=admin_back()
            )
        await cb.answer()
        return
    lines = ["<b>Купоны:</b>"]
    for cp in rows[:30]:
        lines.append(_fmt_coupon_line(cp))
    if len(rows) > 30:
        lines.append(f"\n<i>+ ещё {len(rows) - 30}</i>")
    if cb.message:
        await cb.message.answer(
            "\n".join(lines), parse_mode="HTML", reply_markup=admin_back()
        )
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
        await cb.message.answer(
            "Пришли код купона для удаления (формат <code>MH-XXXXXXXX</code>). /cancel — отмена.",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_coupon_del_code)
async def msg_coupon_del_apply(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await state.clear()
        return
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.", reply_markup=admin_back())
        return
    code = (msg.text or "").strip().upper()
    from app.repos import coupons as coupons_repo

    ok = await coupons_repo.delete(session, code)
    await session.commit()
    await msg.answer(
        "Удалён." if ok else "Купон не найден.",
        reply_markup=admin_back(),
    )
    await state.clear()


# --- Shards submenu ---

@router.callback_query(F.data == "admin:shards")
async def cb_shards_menu(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    if cb.message:
        await cb.message.answer(
            "<b>• Шарды</b>\n\nShard = отдельный Render-аккаунт, на котором крутятся "
            "тенанты. Для добавления нужен Render API key с GitHub-OAuth-аккаунта.",
            parse_mode="HTML",
            reply_markup=admin_shards_menu(),
        )
    await cb.answer()


@router.callback_query(F.data == "admin:shard:list")
async def cb_shard_list(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    from app.repos import shards as shards_repo

    rows = await shards_repo.all_(session)
    occ = await shards_repo.occupancy(session)
    if not rows:
        if cb.message:
            await cb.message.answer(
                "Шардов пока нет.", reply_markup=admin_back()
            )
        await cb.answer()
        return
    from app.repos import shards as shards_repo2  # avoid shadowing

    lines = ["<b>Шарды:</b>"]
    for sh in rows:
        load = occ.get(sh.id, 0)
        last = (
            sh.last_seen_at.strftime("%Y-%m-%d %H:%M")
            if getattr(sh, "last_seen_at", None)
            else "—"
        )
        alive = shards_repo2.is_alive(sh)
        marker = "" if alive else ""
        lines.append(
            f"  {marker} <b>{sh.name}</b> · {load}/{sh.capacity} · "
            f"{sh.status.value} · last_seen={last}"
        )
    if cb.message:
        await cb.message.answer(
            "\n".join(lines), parse_mode="HTML", reply_markup=admin_back()
        )
    await cb.answer()


@router.callback_query(F.data == "admin:shard:add")
async def cb_shard_add_start(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_shard_add)
    if cb.message:
        await cb.message.answer(
            "Пришли строку: <code>name render_api_key [capacity=3]</code>\n"
            "Пример: <code>shard-5 rnd_xxx 3</code>\n\n"
            "/cancel — отмена.",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_shard_add)
async def msg_shard_add_apply(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await state.clear()
        return
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.", reply_markup=admin_back())
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("Нужно минимум name и api_key. /cancel — отмена.")
        return
    name = parts[0].strip()
    api_key = parts[1].strip()
    capacity = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 3

    from app.repos import shards as shards_repo
    from app.services.render_api import RenderClient
    from app.services.shard_provision import provision_worker

    rc = RenderClient(api_key=api_key)
    try:
        owner_id = await rc.autodetect_owner()
    except Exception as exc:  # noqa: BLE001
        await msg.answer(
            f" API key недействителен: <code>{exc}</code>",
            parse_mode="HTML",
            reply_markup=admin_back(),
        )
        await state.clear()
        return
    if not owner_id:
        await msg.answer("Не нашёл owner у этого API key.", reply_markup=admin_back())
        await state.clear()
        return
    existing = await shards_repo.by_name(session, name)
    if existing:
        await msg.answer(
            f" Шард <b>{name}</b> уже есть.",
            parse_mode="HTML",
            reply_markup=admin_back(),
        )
        await state.clear()
        return

    shard = await shards_repo.create(
        session, name=name, api_key=api_key, owner_id=owner_id, capacity=capacity
    )
    await session.commit()
    await msg.answer(
        f" Шард <b>{name}</b> зарегистрирован (id={shard.id}). Деплою воркер…",
        parse_mode="HTML",
    )
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass

    result = await provision_worker(session, shard.id)
    await session.commit()
    if result.get("ok"):
        await msg.answer(
            f" Воркер деплоится: <code>{result.get('service_id')}</code>\n"
            f"URL: {result.get('service_url')}",
            parse_mode="HTML",
            reply_markup=admin_back(),
        )
    else:
        await msg.answer(
            f" Не получилось задеплоить: <code>{result.get('reason')}</code>",
            parse_mode="HTML",
            reply_markup=admin_back(),
        )
    await state.clear()


@router.callback_query(F.data == "admin:shard:toggle")
async def cb_shard_toggle_start(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_shard_toggle)
    if cb.message:
        await cb.message.answer(
            "Пришли имя шарда (он переключится между paused/active). /cancel — отмена."
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_shard_toggle)
async def msg_shard_toggle_apply(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await state.clear()
        return
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.", reply_markup=admin_back())
        return
    name = (msg.text or "").strip()
    from app.repos import shards as shards_repo

    from app.db.models import ShardStatus

    sh = await shards_repo.by_name(session, name)
    if not sh:
        await msg.answer("Шард не найден.", reply_markup=admin_back())
        await state.clear()
        return
    new_status = (
        ShardStatus.ACTIVE if sh.status == ShardStatus.PAUSED else ShardStatus.PAUSED
    )
    await shards_repo.set_status(session, sh.id, new_status)
    await session.commit()
    await msg.answer(
        f" <b>{name}</b> теперь: <b>{new_status.value}</b>",
        parse_mode="HTML",
        reply_markup=admin_back(),
    )
    await state.clear()


@router.callback_query(F.data == "admin:shard:drop")
async def cb_shard_drop_start(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await cb.answer("Только для админа", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_shard_drop)
    if cb.message:
        await cb.message.answer(
            "Пришли имя шарда для удаления. Render-сервис тоже будет удалён, "
            "тенанты на нём — переброшены при следующем reconcile. /cancel — отмена."
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_shard_drop)
async def msg_shard_drop_apply(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not await _require_admin(session, user):
        await state.clear()
        return
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.", reply_markup=admin_back())
        return
    name = (msg.text or "").strip()
    from app.repos import shards as shards_repo
    from app.services.render_api import RenderClient

    sh = await shards_repo.by_name(session, name)
    if not sh:
        await msg.answer("Шард не найден.", reply_markup=admin_back())
        await state.clear()
        return
    if sh.service_id and sh.api_key_enc:
        try:
            api_key = await shards_repo.get_api_key(session, sh.id)
            if api_key:
                rc = RenderClient(api_key=api_key)
                await rc.delete_service(sh.service_id)
        except Exception as exc:  # noqa: BLE001
            await msg.answer(
                f" Render service удаление: <code>{exc}</code>",
                parse_mode="HTML",
            )
    await shards_repo.delete(session, sh.id)
    await session.commit()
    await msg.answer(
        f" Шард <b>{name}</b> удалён.",
        parse_mode="HTML",
        reply_markup=admin_back(),
    )
    await state.clear()


# --- Export submenu ---

@router.callback_query(F.data == "admin:export")
async def cb_export_menu(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not _is_primary_admin(user):
        await cb.answer("Только для главного админа", show_alert=True)
        return
    if cb.message:
        await cb.message.answer(
            "<b>• Экспорт данных</b>\n\n"
            "Скачать конфиги тенантов одним архивом. Доступно только главному админу.",
            parse_mode="HTML",
            reply_markup=admin_export_menu(),
        )
    await cb.answer()


@router.callback_query(F.data == "admin:export:user")
async def cb_export_user_start(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not _is_primary_admin(user):
        await cb.answer("Только для главного админа", show_alert=True)
        return
    await state.set_state(AdminFSM.awaiting_export_user_id)
    if cb.message:
        await cb.message.answer(
            "Пришли user_id для экспорта. /cancel — отмена."
        )
    await cb.answer()


@router.message(AdminFSM.awaiting_export_user_id)
async def msg_export_user_apply(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not _is_primary_admin(user):
        await state.clear()
        return
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Отменено.", reply_markup=admin_back())
        return
    raw = (msg.text or "").strip().lstrip("@")
    if not raw.lstrip("-").isdigit():
        await msg.answer("user_id должен быть числом.")
        return
    target = int(raw)
    data = await _zip_for_user(target)
    if data is None:
        await msg.answer(
            "· У юзера нет инстансов или каталоги пусты.",
            reply_markup=admin_back(),
        )
        await state.clear()
        return
    from aiogram.types import BufferedInputFile

    fname = f"miihost_user{target}.zip"
    await msg.answer_document(
        BufferedInputFile(data, filename=fname),
        caption=f"• Экспорт user_id <code>{target}</code> · {len(data)//1024} KB",
        parse_mode="HTML",
    )
    await state.clear()


@router.callback_query(F.data == "admin:export:all")
async def cb_export_all_btn(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not _is_primary_admin(user):
        await cb.answer("Только для главного админа", show_alert=True)
        return
    # Re-use the existing /export_all command body; easiest is to fake a Message.
    if cb.message:
        await cb.message.answer("Готовлю экспорт всех инстансов…")
        # Adapt cmd_export_all by passing the callback message + acting like /export_all
        cb.message.from_user = cb.from_user  # type: ignore[attr-defined]
        # We can't safely call cmd_export_all directly because it pulls msg.text
        # so we replicate the body inline:
        import io
        import zipfile
        from sqlalchemy import select as sql_select

        from app.db.models import Instance, InstanceStatus
        from app.services.supervisor import DEFAULT_DATA_DIR

        res = await session.execute(
            sql_select(Instance).where(Instance.status != InstanceStatus.DELETED)
        )
        instances = list(res.scalars())
        if not instances:
            await cb.message.answer(
                "· Нет активных инстансов.", reply_markup=admin_back()
            )
            await cb.answer()
            return
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            import json as _json

            for inst in instances:
                base = DEFAULT_DATA_DIR / str(inst.id)
                manifest = {
                    "instance_id": inst.id,
                    "user_id": inst.user_id,
                    "product": inst.product.value,
                    "name": inst.name,
                    "status": inst.status.value,
                    "shard_id": inst.shard_id,
                    "config": inst.config or {},
                    "render_url": inst.render_url,
                    "created_at": inst.created_at.isoformat() if inst.created_at else None,
                }
                zf.writestr(
                    f"user{inst.user_id}/inst{inst.id}_{inst.product.value}/manifest.json",
                    _json.dumps(manifest, ensure_ascii=False, indent=2),
                )
                if base.exists() and base.is_dir():
                    for f in base.rglob("*"):
                        if f.is_file():
                            try:
                                zf.write(
                                    f,
                                    arcname=f"user{inst.user_id}/inst{inst.id}_{inst.product.value}/{f.relative_to(base)}",
                                )
                            except OSError:
                                continue
        buf.seek(0)
        data = buf.getvalue()
        from aiogram.types import BufferedInputFile

        await cb.message.answer_document(
            BufferedInputFile(data, filename="mihost_export_all.zip"),
            caption=f"• Экспорт всех инстансов · {len(instances)} шт · {len(data)//1024} KB",
            parse_mode="HTML",
        )
    await cb.answer()


# ──────────────────────────── Admin: hostings ───────────────────────────────
#
# Global view over every user's instance with start/stop/restart/logs/drop +
# "view config" actions that reuse the functions already used by the user
# side. Paginated, 6 rows per page.

_HOSTINGS_PAGE = 6


def _hostings_status_icon(status_value: str) -> str:
    return {
        "live": "🟢",
        "deploying": "🟡",
        "pending": "🟡",
        "suspended": "🟡",
        "failed": "🔴",
        "deleted": "🔴",
    }.get(status_value, "🟡")


async def _render_hostings_page(session: AsyncSession, page: int) -> tuple[str, InlineKeyboardMarkup]:
    from app.db.models import Instance, InstanceStatus as _St
    from sqlalchemy import select as _select

    q = _select(Instance).where(Instance.status != _St.DELETED).order_by(Instance.id.desc())
    res = await session.execute(q)
    all_inst = list(res.scalars())
    total = len(all_inst)
    pages = max(1, (total + _HOSTINGS_PAGE - 1) // _HOSTINGS_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = all_inst[page * _HOSTINGS_PAGE:(page + 1) * _HOSTINGS_PAGE]

    lines = [f"<b>Хостинги пользователей</b> · {total} · стр. {page + 1}/{pages}"]
    rows: list[list[InlineKeyboardButton]] = []
    for inst in chunk:
        tier = ((inst.config or {}).get("tier") or "std").lower()
        tier_suf = " PRO" if tier == "pro" else ""
        ico = _hostings_status_icon(inst.status.value)
        lines.append(
            f"\n{ico} #{inst.id} · uid <code>{inst.user_id}</code> · "
            f"{inst.product.value}{tier_suf} · {inst.status.value}"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{ico} #{inst.id} · uid {inst.user_id} · {inst.product.value}{tier_suf}",
                    callback_data=f"admin:host:open:{inst.id}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="« назад", callback_data=f"admin:hostings:{page - 1}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text="вперёд »", callback_data=f"admin:hostings:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="В админку", callback_data="admin")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("admin:hostings:"))
async def cb_admin_hostings(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await is_admin(session, user.id):
        await cb.answer("Только для админов", show_alert=True)
        return
    try:
        page = int((cb.data or "admin:hostings:0").split(":")[2])
    except ValueError:
        page = 0
    text, kb = await _render_hostings_page(session, page)
    if cb.message:
        try:
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()


def _host_actions_kb(inst_id: int, product_value: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="Старт", callback_data=f"admin:host:start:{inst_id}"),
            InlineKeyboardButton(text="Стоп", callback_data=f"admin:host:stop:{inst_id}"),
            InlineKeyboardButton(text="Рестарт", callback_data=f"admin:host:restart:{inst_id}"),
        ],
        [
            InlineKeyboardButton(text="Логи", callback_data=f"admin:host:logs:{inst_id}"),
            InlineKeyboardButton(text="Статус", callback_data=f"admin:host:status:{inst_id}"),
        ],
        [
            InlineKeyboardButton(text="Удалить", callback_data=f"admin:host:drop:{inst_id}"),
        ],
        [InlineKeyboardButton(text="К списку", callback_data="admin:hostings:0")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("admin:host:open:"))
async def cb_admin_host_open(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not await is_admin(session, user.id):
        await cb.answer("Только для админов", show_alert=True)
        return
    from app.repos import instances as inst_repo
    from app.services.supervisor import supervisor

    inst_id = int((cb.data or "").rsplit(":", 1)[-1])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst:
        await cb.answer("Не найдено", show_alert=True)
        return
    s = supervisor.status(inst.id) or {}
    tier = ((inst.config or {}).get("tier") or "std").lower()
    tier_suf = " PRO" if tier == "pro" else ""
    cfg_summary = []
    if inst.product == ProductKind.CARDINAL:
        gk = (inst.config or {}).get("golden_key") or ""
        tok = (inst.config or {}).get("tg_token") or ""
        cfg_summary.append(f"golden_key: {'*'*6 + gk[-4:] if gk else '(пусто)'}")
        cfg_summary.append(f"tg_token: {tok[:10] + '…' if tok else '(пусто)'}")
    else:
        ep = (inst.config or {}).get("entrypoint") or "?"
        cfg_summary.append(f"entrypoint: {ep}")
    text = (
        f"<b>Инстанс #{inst.id}</b>\n"
        f"Пользователь: <code>{inst.user_id}</code>\n"
        f"Продукт: {inst.product.value}{tier_suf}\n"
        f"Статус: {_hostings_status_icon(inst.status.value)} {inst.status.value}\n"
        f"Процесс: {'жив' if s.get('alive') else 'нет'} · PID {s.get('pid') or '—'} · "
        f"uptime {s.get('uptime', 0)} с · restart {s.get('restart_count', 0)}\n"
        + "\n".join(cfg_summary)
    )
    if cb.message:
        try:
            await cb.message.edit_text(
                text, parse_mode="HTML", reply_markup=_host_actions_kb(inst.id, inst.product.value)
            )
        except Exception:
            await cb.message.answer(
                text, parse_mode="HTML", reply_markup=_host_actions_kb(inst.id, inst.product.value)
            )
    await cb.answer()


async def _host_act(
    cb: CallbackQuery, session: AsyncSession, user: User, verb: str
) -> None:
    if not await is_admin(session, user.id):
        await cb.answer("Только для админов", show_alert=True)
        return
    from app.db.models import InstanceStatus as _St
    from app.repos import instances as inst_repo
    from app.services.supervisor import supervisor

    inst_id = int((cb.data or "").rsplit(":", 1)[-1])
    inst = await inst_repo.by_id(session, inst_id)
    if not inst:
        await cb.answer("Не найдено", show_alert=True)
        return

    if verb == "start":
        # Re-create the tenant from stored config if needed.
        cfg = inst.config or {}
        if inst.product == ProductKind.CARDINAL and cfg.get("golden_key"):
            try:
                from app.services.cardinal import start_tenant

                await start_tenant(
                    inst.id,
                    golden_key=cfg.get("golden_key", ""),
                    telegram_token=cfg.get("tg_token", ""),
                    secret_key_hash=cfg.get("tg_secret_hash", ""),
                )
                inst.status = _St.LIVE
                inst.actual_state = "live"
            except Exception:
                logger.exception("admin start cardinal failed")
                inst.status = _St.FAILED
        else:
            # Script: reuse cached spec via restart (supervisor.start requires a TenantSpec).
            try:
                await supervisor.restart(inst.id)
                inst.status = _St.LIVE
                inst.actual_state = "live"
            except Exception:
                logger.exception("admin start script failed")
                inst.status = _St.FAILED
        msg = "Стартую."
    elif verb == "stop":
        try:
            await supervisor.stop(inst.id)
        except Exception:
            logger.exception("admin stop failed")
        inst.status = _St.SUSPENDED
        inst.actual_state = "stopped"
        msg = "Остановил."
    elif verb == "restart":
        try:
            await supervisor.restart(inst.id)
            inst.status = _St.LIVE
            inst.actual_state = "live"
        except Exception:
            logger.exception("admin restart failed")
        msg = "Перезапускаю."
    elif verb == "drop":
        try:
            await supervisor.remove(inst.id)
        except Exception:
            logger.exception("drop: supervisor.remove failed")
        freed = 0
        if inst.product == ProductKind.CARDINAL:
            try:
                from app.services.cardinal import remove_tenant_dir

                freed = remove_tenant_dir(inst.id) or 0
            except Exception:
                logger.exception("drop cardinal dir failed")
        else:
            try:
                from app.services.script_host import remove as remove_script

                freed = remove_script(inst.id) or 0
            except Exception:
                logger.exception("drop script dir failed")
        inst.status = _St.DELETED
        inst.desired_state = "stopped"
        inst.actual_state = "deleted"
        msg = f"Удалил. Освобождено {freed // 1024} KB."
    else:
        msg = "?"
    await session.commit()
    await cb.answer(msg)
    # Refresh the admin view.
    fake_data = f"admin:host:open:{inst.id}"
    cb.model_config if False else None  # no-op
    cb.__dict__["data"] = fake_data
    await cb_admin_host_open(cb, session, user)


@router.callback_query(F.data.startswith("admin:host:start:"))
async def _h_start(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await _host_act(cb, session, user, "start")


@router.callback_query(F.data.startswith("admin:host:stop:"))
async def _h_stop(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await _host_act(cb, session, user, "stop")


@router.callback_query(F.data.startswith("admin:host:restart:"))
async def _h_restart(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await _host_act(cb, session, user, "restart")


@router.callback_query(F.data.startswith("admin:host:drop:"))
async def _h_drop(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await _host_act(cb, session, user, "drop")


@router.callback_query(F.data.startswith("admin:host:logs:"))
async def _h_logs(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not await is_admin(session, user.id):
        await cb.answer("Только для админов", show_alert=True)
        return
    from app.services.supervisor import supervisor

    inst_id = int((cb.data or "").rsplit(":", 1)[-1])
    raw = supervisor.tail(inst_id, lines=40)
    logs = "\n".join(raw) if raw else "(пусто)"
    if cb.message:
        await cb.message.answer(
            f"<b>Логи #{inst_id}</b>\n<pre>{logs[-3500:]}</pre>",
            parse_mode="HTML",
        )
    await cb.answer()


@router.callback_query(F.data.startswith("admin:host:status:"))
async def _h_status(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    # Reuse the same open view (contains all status fields).
    inst_id = (cb.data or "").rsplit(":", 1)[-1]
    cb.__dict__["data"] = f"admin:host:open:{inst_id}"
    await cb_admin_host_open(cb, session, user)
