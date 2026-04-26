"""Admin panel: stats, broadcast, add/remove admin, brand channel, post now."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProductKind, User
from app.keyboards.main import admin_menu, back_to_menu
from app.repos import users as users_repo
from app.services.admin import is_admin, stats_dashboard
from app.services.channel import auto_brand, post_one

logger = logging.getLogger(__name__)
router = Router(name="admin")


class AdminFSM(StatesGroup):
    awaiting_broadcast = State()
    awaiting_new_admin = State()


async def _require_admin(session: AsyncSession, user: User) -> bool:
    return await is_admin(session, user.id)


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
    await msg.answer(f"✓ Админ {target_id} добавлен.")


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
    await msg.answer(f"✓ {target_id} больше не админ.")


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
    await msg.answer(f"✓ Админ {target_id} добавлен.")
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
            "✓ Канал брендирован" if ok else "Канал не настроен (CHANNEL_ID пуст).",
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
            f"✓ Опубликовано (msg_id={mid})" if mid else "Канал не настроен.",
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
    await msg.answer("⏳ Запускаю ротацию Postgres…")
    from app.services.db_rotation import rotate_now

    result = await rotate_now(msg.bot, force=True)
    await msg.answer(f"<pre>{result}</pre>", parse_mode="HTML")


# --- Sharding ---

@router.message(Command("add_shard"))
async def cmd_add_shard(msg: Message, session: AsyncSession, user: User) -> None:
    """Register a new shard:  /add_shard <name> <render_api_key> [capacity]"""
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
        await msg.answer(f"❌ API key недействителен: <code>{exc}</code>", parse_mode="HTML")
        return
    if not owner_id:
        await msg.answer("❌ Не нашёл owner у этого API key.")
        return

    existing = await shards_repo.by_name(session, name)
    if existing:
        await msg.answer(f"❌ Шард с именем <b>{name}</b> уже есть.", parse_mode="HTML")
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
        f"✅ Шард <b>{name}</b> зарегистрирован (id={shard.id}).\n"
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
            f"🚀 Воркер деплоится: <code>{result.get('service_id')}</code>\n"
            f"URL: {result.get('service_url')}\n\n"
            "Жди ~3 минуты до первого heartbeat.",
            parse_mode="HTML",
        )
    else:
        await msg.answer(
            f"⚠️ Не получилось задеплоить воркер: <code>{result.get('reason')}</code>",
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
        marker = "🟢" if alive else "🔴"
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
    await msg.answer(f"⏸ Шард <b>{sh.name}</b> на паузе.", parse_mode="HTML")


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
    await msg.answer(f"▶ Шард <b>{sh.name}</b> снова активен.", parse_mode="HTML")


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
    await msg.answer(f"◾ Шард <b>{sh.name}</b> удалён.", parse_mode="HTML")


# --- Coupons ---

@router.message(Command("create_coupon"))
async def cmd_create_coupon(msg: Message, session: AsyncSession, user: User) -> None:
    """/create_coupon <product=cardinal|script> [days=30] [expires_in_days=30]"""
    if not await _require_admin(session, user):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer(
            "Использование: <code>/create_coupon &lt;cardinal|script&gt; [days=30] [expires_in_days=30]</code>",
            parse_mode="HTML",
        )
        return
    try:
        product = ProductKind(parts[1].strip().lower())
    except ValueError:
        await msg.answer("Продукт должен быть <code>cardinal</code> или <code>script</code>.", parse_mode="HTML")
        return
    days = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 30
    expires_in = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 30

    from app.repos import coupons as coupons_repo

    cp = await coupons_repo.create(
        session,
        product=product,
        days=days,
        issued_by=user.id,
        expires_in_days=expires_in,
    )
    await session.commit()
    await msg.answer(
        f"<b>Купон создан</b>\n\n"
        f"◾ Код: <code>{cp.code}</code>\n"
        f"◾ Продукт: {product.value}\n"
        f"◾ Срок: {days} дн.\n"
        f"◾ Активен: {expires_in} дн.\n\n"
        f"Юзер вводит этот код в /menu → Купить → «У меня купон».",
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
        used = f"used by {cp.used_by}" if cp.used_by else "free"
        exp = "—" if not cp.expires_at else cp.expires_at.strftime("%Y-%m-%d")
        lines.append(
            f"  ◇ <code>{cp.code}</code> · {cp.product.value} · {cp.days}d · до {exp} · {used}"
        )
    if len(rows) > 30:
        lines.append(f"\n<i>+ ещё {len(rows) - 30}</i>")
    await msg.answer("\n".join(lines), parse_mode="HTML")


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
    await session.commit()
    await msg.answer(
        f"▣ Юзеру <code>{uid}</code> выдана подписка <b>{product.value}</b> до "
        f"<code>{sub.expires_at.strftime('%Y-%m-%d %H:%M')}</code> (+{days} дн.)",
        parse_mode="HTML",
    )
    try:
        await msg.bot.send_message(
            uid,
            f"▣ Админ выдал тебе подписку <b>{product.value}</b> на {days} дн.\n"
            f"Активна до {sub.expires_at.strftime('%Y-%m-%d %H:%M')}.",
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
        f"◾ С юзера <code>{uid}</code> снято {days} дн. {product.value}. "
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
    await msg.answer(f"◾ Подписка {product.value} у <code>{uid}</code> отозвана.", parse_mode="HTML")


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
        f"◾ Имя: {target.first_name or '—'} (@{target.username or '—'})",
        f"◾ Админ: {target.is_admin} · Бан: {target.is_blocked}",
        f"◾ XP: {target.xp} · Coins: {target.coins} · Lvl: {target.level}",
        "",
        "<b>Подписки:</b>",
    ]
    if subs:
        for s in subs:
            lines.append(f"  ◆ {s.product.value} → {s.expires_at.strftime('%Y-%m-%d %H:%M')}")
    else:
        lines.append("  ◇ нет")
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
        "<code>/coupons</code>  ·  <code>/del_coupon CODE</code>\n\n"
        "<b>Шарды:</b>\n"
        "<code>/add_shard NAME RENDER_API_KEY [capacity=4]</code>\n"
        "<code>/shards</code> · <code>/pause_shard NAME</code> · <code>/resume_shard NAME</code> · <code>/drop_shard NAME</code>\n\n"
        "<b>База / админы:</b>\n"
        "<code>/rotate_db</code>  ·  <code>/addadmin user_id</code>  ·  <code>/stats</code>\n\n"
        "<b>Контент:</b>\n"
        "<code>/post_now</code>  ·  <code>/brand_channel</code>",
        parse_mode="HTML",
    )
