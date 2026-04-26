"""Buy + payment flow.

Order: choose product → collect settings (golden_key / .zip) → confirm
summary → invoice (USDT only) OR coupon redemption. Other crypto goes
through /support.
"""
from __future__ import annotations

import logging
from io import BytesIO
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Document, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import InstanceStatus, PaymentStatus, ProductKind, User
from app.keyboards.main import (
    back_to_menu,
    buy_confirm_tier,
    buy_menu,
    pay_buttons,
)
from app.repos import coupons as coupons_repo
from app.repos import instances as inst_repo
from app.repos import logs as logs_repo
from app.repos import payments as payments_repo
from app.repos import subscriptions as subs_repo
from app.repos import users as users_repo
from app.services import script_host
from app.services.images import ASSETS, generate_all
from app.services.payment import CryptoBotClient

logger = logging.getLogger(__name__)
router = Router(name="payment")


class BuyFSM(StatesGroup):
    awaiting_golden_key = State()
    awaiting_tg_token = State()
    awaiting_tg_password = State()
    awaiting_proxy = State()
    awaiting_zip = State()
    awaiting_coupon = State()
    awaiting_any_coupon = State()  # /coupon — product inferred from the code


# --- Step 1: choose product menu ---


@router.callback_query(F.data == "buy:menu")
async def cb_buy_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    p = ASSETS / "order.png"
    if not p.exists():
        generate_all()
    text = (
        "<b>Выбери что хостить</b>\n\n"
        f"💠 <b>FunPay Cardinal</b> · {settings.price_cardinal_rub} ₽ / 30 дней\n"
        "    автозапуск, авторестарт, смена golden_key прямо в боте\n\n"
        f"💠 <b>Кастом-скрипт</b> · {settings.price_script_rub} ₽ / 30 дней\n"
        "    .zip с твоим Python-проектом, автоанализ + автодеплой\n\n"
        "🔹 Сначала соберём настройки, потом выставлю счёт."
    )
    if cb.message:
        try:
            await cb.message.edit_caption(
                caption=text, parse_mode="HTML", reply_markup=buy_menu()
            )
        except Exception:
            await cb.message.answer_photo(
                FSInputFile(str(p)),
                caption=text,
                parse_mode="HTML",
                reply_markup=buy_menu(),
            )
    await cb.answer()


# --- Step 2: collect settings BEFORE invoice ---


@router.callback_query(F.data.startswith("buy:start:"))
async def cb_buy_start(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not cb.data:
        return
    parts = cb.data.split(":")
    product = ProductKind(parts[2])
    tier = parts[3] if len(parts) > 3 else "std"
    await state.clear()
    await state.update_data(product=product.value, tier=tier)
    if cb.message:
        if product == ProductKind.CARDINAL:
            await cb.message.answer(
                "<b>💠 Настройка Cardinal · Шаг 1/4</b>\n\n"
                "Пришли свой <code>golden_key</code> (32 символа) от FunPay "
                "одним сообщением. Он используется только для запуска твоего "
                "инстанса и сразу удаляется из чата.\n\n"
                "<i>Где взять:</i> funpay.com → DevTools → Application → "
                "Cookies → <code>golden_key</code>.\n\n"
                "Дальше: Telegram-бот → пароль → прокси.\n"
                "« Отменить — /menu",
                parse_mode="HTML",
            )
            await state.set_state(BuyFSM.awaiting_golden_key)
        else:
            ram = (
                settings.script_pro_ram_mb if tier == "pro"
                else settings.script_std_ram_mb
            )
            await cb.message.answer(
                f"<b>Настройка скрипта · {tier.upper()} · {ram} MB</b>\n\n"
                "Пришли .zip-архив с твоим Python-проектом одним документом.\n"
                "Внутри: <code>main.py</code> (или другой entrypoint) и опц. "
                "<code>requirements.txt</code>. До 25 MB.\n\n"
                f"<i>Лимит RAM в рантайме: {ram} MB. "
                f"{'Целый сервер выделен под тебя.' if tier == 'pro' else 'Если упирается — переходи на PRO (512 MB / 150 ₽).'}</i>\n\n"
                "« Отменить — /menu",
                parse_mode="HTML",
            )
            await state.set_state(BuyFSM.awaiting_zip)
    await cb.answer()


@router.message(BuyFSM.awaiting_golden_key)
async def receive_golden_key(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    key = (msg.text or "").strip()
    if len(key) != 32:
        await msg.answer(
            "❌ golden_key должен быть ровно 32 символа. Скопируй cookie "
            "<code>golden_key</code> с funpay.com полностью и пришли ещё раз.",
            parse_mode="HTML",
        )
        return
    await state.update_data(golden_key=key)
    # Try to delete user's message so the secret disappears from chat.
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    await state.set_state(BuyFSM.awaiting_tg_token)
    await msg.answer(
        "<b>Шаг 2/4 · Telegram-бот Cardinal</b>\n\n"
        "Пришли токен своего Telegram-бота (<code>@BotFather</code> → "
        "<code>/newbot</code>) одним сообщением — Cardinal будет слать через "
        "него уведомления и принимать команды.\n\n"
        "Если пока не нужен — отправь <code>-</code> (пропустить).",
        parse_mode="HTML",
    )


@router.message(BuyFSM.awaiting_tg_token)
async def receive_tg_token(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    from app.services.cardinal_config import validate_tg_token

    raw = (msg.text or "").strip()
    if raw == "-" or raw == "":
        await state.update_data(tg_token="", tg_pw_hash="")
        try:
            await msg.delete()
        except Exception:  # noqa: BLE001
            pass
        await state.set_state(BuyFSM.awaiting_proxy)
        await msg.answer(
            "<b>Шаг 4/4 · IPv4-прокси (опционально)</b>\n\n"
            "Если хочешь гонять FunPay через прокси — пришли его в формате "
            "<code>scheme://login:pass@ip:port</code>, <code>login:pass@ip:port</code> "
            "или просто <code>ip:port</code>.\n\n"
            "Если не нужен — отправь <code>-</code>.",
            parse_mode="HTML",
        )
        return
    if not validate_tg_token(raw):
        await msg.answer(
            "❌ Токен не похож на настоящий. Формат: <code>123456:ABC-DEF...</code>.\n"
            "Или отправь <code>-</code> чтобы пропустить.",
            parse_mode="HTML",
        )
        return
    await state.update_data(tg_token=raw)
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    await state.set_state(BuyFSM.awaiting_tg_password)
    await msg.answer(
        "<b>Шаг 3/4 · Пароль Cardinal</b>\n\n"
        "Придумай пароль для входа в Telegram-бота Cardinal "
        "(спросит при первом <code>/start</code>).\n\n"
        "Требования: минимум 8 символов, заглавные + строчные буквы, "
        "хотя бы одна цифра. Например: <code>CatShop2025</code>.",
        parse_mode="HTML",
    )


@router.message(BuyFSM.awaiting_tg_password)
async def receive_tg_password(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    from app.services.cardinal_config import hash_password, validate_password

    pw = (msg.text or "").strip()
    ok, err = validate_password(pw)
    if not ok:
        await msg.answer(f"❌ {err} Пришли другой пароль.")
        return
    await state.update_data(tg_pw_hash=hash_password(pw))
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    await state.set_state(BuyFSM.awaiting_proxy)
    await msg.answer(
        "<b>Шаг 4/4 · IPv4-прокси (опционально)</b>\n\n"
        "Если хочешь гонять FunPay через прокси — пришли его в формате "
        "<code>scheme://login:pass@ip:port</code>, <code>login:pass@ip:port</code> "
        "или просто <code>ip:port</code>.\n\n"
        "Если не нужен — отправь <code>-</code>.",
        parse_mode="HTML",
    )


@router.message(BuyFSM.awaiting_proxy)
async def receive_proxy(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    from app.services.cardinal_config import validate_proxy

    raw = (msg.text or "").strip()
    if raw == "-" or raw == "":
        await state.update_data(proxy="")
    else:
        ok, normalized = validate_proxy(raw)
        if not ok:
            await msg.answer(
                "❌ Не распознал прокси. Формат: "
                "<code>scheme://login:pass@ip:port</code>, "
                "<code>login:pass@ip:port</code> или <code>ip:port</code>.\n"
                "Или отправь <code>-</code> чтобы пропустить.",
                parse_mode="HTML",
            )
            return
        await state.update_data(proxy=normalized)
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    await _show_summary(msg, state, session, user, ProductKind.CARDINAL)


@router.message(BuyFSM.awaiting_zip, F.document)
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
    await state.update_data(zip_bytes=data, zip_name=doc.file_name, zip_size=len(data))
    await _show_summary(msg, state, session, user, ProductKind.SCRIPT)


@router.message(BuyFSM.awaiting_zip)
async def reject_non_zip(msg: Message) -> None:
    await msg.answer("Пришли .zip как документ.")


def _price_for(product: ProductKind, tier: str) -> int:
    if product == ProductKind.CARDINAL:
        return settings.price_cardinal_rub
    return settings.price_script_pro_rub if tier == "pro" else settings.price_script_rub


async def _show_summary(
    msg: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    product: ProductKind,
) -> None:
    data = await state.get_data()
    tier = data.get("tier", "std")
    price = _price_for(product, tier)
    if product == ProductKind.CARDINAL:
        tg_line = (
            f"💠 Telegram-бот: <code>{data['tg_token'].split(':')[0]}…</code>\n"
            if data.get("tg_token") else "💠 Telegram-бот: ⏩ пропущен\n"
        )
        proxy_line = (
            f"💠 Прокси: <code>{data['proxy']}</code>\n"
            if data.get("proxy") else "💠 Прокси: ⏩ пропущен\n"
        )
        details = (
            f"💠 Cardinal-инстанс\n"
            f"💠 golden_key: <code>***{data['golden_key'][-4:]}</code>\n"
            f"{tg_line}{proxy_line}"
            f"💠 Срок: 30 дней\n"
        )
    else:
        size_kb = data.get("zip_size", 0) // 1024
        ram = (
            settings.script_pro_ram_mb if tier == "pro"
            else settings.script_std_ram_mb
        )
        details = (
            f"💠 Кастом-скрипт · {tier.upper()} · {ram} MB\n"
            f"💠 Архив: <code>{data['zip_name']}</code> · {size_kb} KB\n"
            f"💠 Срок: 30 дней\n"
        )
    text = (
        "<b>Проверь заказ</b>\n\n"
        f"{details}\n"
        f"<b>К оплате: {price} ₽</b>\n\n"
        "Оплата только в <b>USDT через CryptoBot</b>.\n"
        "Хочешь другой криптой → жми «🔹 Другая крипта → саппорт» на следующем экране, "
        "или сразу пиши в <a href=\"tg://user?id={admin}\">саппорт</a>.\n\n"
        "Есть бесплатный купон? Жми «У меня купон»."
    ).format(admin=settings.admin_ids_list[0] if settings.admin_ids_list else 0)
    await msg.answer(text, parse_mode="HTML", reply_markup=buy_confirm_tier(product.value, tier))


# --- Step 3a: invoice ---


@router.callback_query(F.data.startswith("buy:invoice:"))
async def cb_buy_invoice(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not cb.data:
        return
    parts = cb.data.split(":")
    product = ProductKind(parts[2])
    tier = parts[3] if len(parts) > 3 else "std"
    data = await state.get_data()
    if product == ProductKind.CARDINAL and not data.get("golden_key"):
        await cb.answer("Сначала пришли golden_key", show_alert=True)
        return
    if product == ProductKind.SCRIPT and not data.get("zip_bytes"):
        await cb.answer("Сначала пришли .zip", show_alert=True)
        return
    await state.update_data(tier=tier)

    price = _price_for(product, tier)

    client = CryptoBotClient()
    if not client.enabled:
        await cb.answer("CryptoBot не настроен — напиши в /support", show_alert=True)
        return

    try:
        invoice = await client.create_invoice(
            amount_rub=price,
            description=f"Mi Host · {product.value} · 30 дней",
            payload=f"{user.id}:{product.value}",
            paid_btn_url=f"https://t.me/{(await cb.bot.get_me()).username}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_invoice failed: %s", exc)
        await cb.answer("Не удалось создать счёт. Попробуй ещё раз.", show_alert=True)
        return

    await payments_repo.create(
        session,
        user_id=user.id,
        product=product,
        invoice_id=str(invoice["invoice_id"]),
        amount_rub=price,
        asset=invoice.get("asset"),
        amount_crypto=invoice.get("amount"),
        pay_url=invoice.get("pay_url") or invoice.get("bot_invoice_url"),
    )
    await logs_repo.write(
        session,
        kind="payment.created",
        message=f"invoice {invoice['invoice_id']} for {product.value}",
        user_id=user.id,
        meta={"amount_rub": price},
    )
    await session.commit()

    text = (
        "<b>Счёт</b>\n\n"
        f"💠 Продукт: <b>{product.value}</b>\n"
        f"💠 Сумма: <b>{price} ₽</b> ≈ {invoice.get('amount')} {invoice.get('asset')}\n\n"
        "🔹 Оплата только в USDT через @CryptoBot.\n"
        "🔹 Другая крипта (TON/BTC/ETH/…) → жми кнопку «🔹 Другая крипта → саппорт».\n\n"
        "После оплаты — нажми «Я оплатил» или дождись авто-проверки."
    )
    pay_url = invoice.get("pay_url") or invoice.get("bot_invoice_url")
    if cb.message:
        await cb.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=pay_buttons(pay_url or "https://t.me/CryptoBot"),
            disable_web_page_preview=True,
        )
    await cb.answer()


# --- Step 3b: coupon path ---


@router.callback_query(F.data.startswith("buy:coupon:"))
async def cb_buy_coupon(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if cb.data:
        parts = cb.data.split(":")
        if len(parts) > 3:
            await state.update_data(tier=parts[3])
    await state.set_state(BuyFSM.awaiting_coupon)
    if cb.message:
        await cb.message.answer(
            "Пришли код купона одним сообщением (формат <code>MH-XXXXXXXX</code>).",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(Command("coupon"))
async def cmd_coupon(
    msg: Message, command: CommandObject, state: FSMContext,
    session: AsyncSession, user: User,
) -> None:
    """/coupon <CODE> — redeem a coupon without going through the buy flow."""
    arg = (command.args or "").strip().upper() if command else ""
    if arg:
        await _redeem_coupon_and_provision(msg, session, user, arg, None, {}, state)
        return
    await state.set_state(BuyFSM.awaiting_any_coupon)
    await msg.answer(
        "🎟️ Пришли код купона одним сообщением (формат <code>MH-XXXXXXXX</code>).\n"
        "/cancel — отмена.",
        parse_mode="HTML",
    )


@router.message(BuyFSM.awaiting_any_coupon)
async def receive_any_coupon(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    text = (msg.text or "").strip()
    if text == "/cancel":
        await state.clear()
        await msg.answer("Отменено.")
        return
    code = text.upper()
    await _redeem_coupon_and_provision(msg, session, user, code, None, {}, state)


@router.message(BuyFSM.awaiting_coupon)
async def receive_coupon(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    code = (msg.text or "").strip().upper()
    data = await state.get_data()
    product_str = data.get("product")
    # Allow coupon redemption even if user never picked a product (e.g. admin
    # handed them a raw coupon code): we'll infer product from the coupon.
    await _redeem_coupon_and_provision(
        msg, session, user, code, product_str, data, state
    )


async def _redeem_coupon_and_provision(
    msg: Message,
    session: AsyncSession,
    user: User,
    code: str,
    product_str: str | None,
    data: dict,
    state: FSMContext,
) -> None:
    ok, message, coupon = await coupons_repo.redeem(session, code, user.id)
    if not ok or not coupon:
        await msg.answer(f"🔹 {message}")
        return
    if product_str:
        try:
            picked = ProductKind(product_str)
        except ValueError:
            picked = coupon.product
        if coupon.product != picked:
            await msg.answer(
                f"🔹 Купон на <b>{coupon.product.value}</b>, а ты покупал "
                f"<b>{picked.value}</b>. Начни заново через /menu → 💎 Купить.",
                parse_mode="HTML",
            )
            return
    product = coupon.product
    tier = coupon.tier or "std"
    hours = coupons_repo.duration_hours(coupon)
    await subs_repo.extend_hours(session, user.id, product, hours=hours)
    await users_repo.add_xp(session, user.id, 10)
    await logs_repo.write(
        session,
        kind="coupon.redeemed",
        message=f"{code} · +{hours}h {product.value}{' PRO' if tier == 'pro' else ''}",
        user_id=user.id,
        meta={"code": code, "tier": tier, "hours": hours},
    )
    # Always ensure a placeholder instance row exists so the user sees their
    # server in "Мои серверы" even if they haven't uploaded golden_key / zip.
    data = {**(data or {}), "tier": tier}
    label = f"{product.value}{' PRO' if tier == 'pro' else ''}"
    span = f"{hours // 24} дн" if hours % 24 == 0 else f"{hours} ч"
    await msg.answer(
        f"✨ Купон применён: +{span} <b>{label}</b>.",
        parse_mode="HTML",
    )
    # Try to provision from FSM-collected settings (golden_key / zip); if
    # none available, fall through to a placeholder instance row.
    provisioned = False
    try:
        if (product == ProductKind.CARDINAL and data.get("golden_key")) or (
            product == ProductKind.SCRIPT and data.get("zip_bytes")
        ):
            await _provision_instance(session, user.id, product, data)
            provisioned = True
    except Exception as exc:  # noqa: BLE001
        logger.exception("provision after coupon failed")
        await msg.answer(
            f"🔹 Подписка активна, но запуск сервера упал: {exc}\n"
            "Напиши /support, поможем.",
        )
    if not provisioned:
        await _ensure_placeholder_instance(session, user.id, product, tier)
        await msg.answer(
            "🖥️ Сервер добавлен в «Мои серверы» в статусе <b>PENDING</b>.\n"
            "Зайди: /menu → 🖥️ Мои серверы → ⚙️ Настроить — и "
            f"{'пришли golden_key' if product == ProductKind.CARDINAL else 'загрузи .zip'}, "
            "сервер запустится автоматически.",
            parse_mode="HTML",
        )
    await session.commit()
    await state.clear()


async def _ensure_placeholder_instance(
    session: AsyncSession,
    user_id: int,
    product: ProductKind,
    tier: str = "std",
) -> None:
    """Ensure the user has at least one Instance row for this product so the
    server is visible in the 'Мои серверы' screen even before they upload
    their config. Idempotent."""
    from app.db.models import InstanceStatus

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


# --- Step 4: pay-check (manual + webhook) ---


@router.callback_query(F.data == "pay:check")
async def cb_pay_check(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    """Manual fallback when the webhook hasn't fired yet — poll CryptoBot."""
    client = CryptoBotClient()
    if not client.enabled:
        await cb.answer("CryptoBot недоступен — пиши в /support", show_alert=True)
        return
    from sqlalchemy import select
    from app.db.models import Payment

    res = await session.execute(
        select(Payment)
        .where(Payment.user_id == user.id, Payment.status == PaymentStatus.CREATED)
        .order_by(Payment.created_at.desc())
    )
    payment = res.scalars().first()
    if not payment:
        await cb.answer("Нет активных счетов", show_alert=True)
        return
    try:
        items = await client.get_invoices([payment.invoice_id])
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_invoices: %s", exc)
        await cb.answer("Не удалось проверить", show_alert=True)
        return
    if not items:
        await cb.answer("Счёт не найден", show_alert=True)
        return
    inv = items[0]
    if inv.get("status") != "paid":
        await cb.answer("Оплата ещё не получена", show_alert=False)
        return

    await _activate(session, user.id, payment.product, payment.invoice_id, payment.amount_rub)
    # Provision instance using FSM settings (still in state).
    data = await state.get_data()
    try:
        await _provision_instance(session, user.id, payment.product, data)
    except Exception as exc:  # noqa: BLE001
        logger.exception("provision after pay failed")
    await session.commit()

    await cb.message.answer(
        f"✨ Оплата подтверждена. Подписка <b>{payment.product.value}</b> "
        f"продлена на {settings.subscription_days} дней.",
        parse_mode="HTML",
        reply_markup=back_to_menu(),
    )
    await state.clear()
    await cb.answer()


# --- Provisioning helpers ---


async def _provision_instance(
    session: AsyncSession,
    user_id: int,
    product: ProductKind,
    data: dict,
) -> None:
    """Create + start the tenant subprocess based on the FSM-collected data."""
    from app.services.cardinal import start_tenant
    from app.services.supervisor import TenantSpec, supervisor
    import sys

    tier = data.get("tier", "std")
    if product == ProductKind.CARDINAL:
        gk = data.get("golden_key")
        if not gk:
            return
        tg_token = data.get("tg_token") or ""
        tg_pw_hash = data.get("tg_pw_hash") or ""
        proxy = data.get("proxy") or ""
        new_cfg_extras = {
            "golden_key": gk,
            "tier": tier,
            "tg_token": tg_token,
            "tg_secret_hash": tg_pw_hash,
            "proxy": proxy,
        }
        existing = await inst_repo.list_for_user(session, user_id, ProductKind.CARDINAL)
        if existing:
            inst = existing[0]
            inst.config = {**(inst.config or {}), **new_cfg_extras}
        else:
            inst = await inst_repo.create(
                session,
                user_id=user_id,
                product=ProductKind.CARDINAL,
                name=f"cardinal-{user_id}",
                config=new_cfg_extras,
            )
        inst.status = InstanceStatus.DEPLOYING
        inst.desired_state = "live"
        await session.flush()
        # Master-side direct start (works when shard_id is None or master).
        if inst.shard_id is None:
            try:
                await start_tenant(
                    inst.id,
                    golden_key=gk,
                    telegram_token=tg_token,
                    secret_key_hash=tg_pw_hash or None,
                    proxy=proxy,
                )
                inst.status = InstanceStatus.LIVE
                inst.actual_state = "live"
            except Exception:  # noqa: BLE001
                logger.exception("start cardinal failed")
                inst.status = InstanceStatus.FAILED
        # else: worker on the shard will reconcile within ~10s.
    else:
        zip_bytes = data.get("zip_bytes")
        if not zip_bytes:
            return
        existing = await inst_repo.list_for_user(session, user_id, ProductKind.SCRIPT)
        if existing:
            inst = existing[0]
            inst.config = {**(inst.config or {}), "tier": tier}
        else:
            inst = await inst_repo.create(
                session,
                user_id=user_id,
                product=ProductKind.SCRIPT,
                name=f"script-{user_id}",
                config={"tier": tier},
            )
        inst.status = InstanceStatus.DEPLOYING
        inst.desired_state = "live"
        await session.flush()
        ram_mb = (
            settings.script_pro_ram_mb if tier == "pro" else settings.script_std_ram_mb
        )
        try:
            analysis, spec = await script_host.deploy(inst.id, zip_bytes, ram_mb=ram_mb)
            inst.risk_score = analysis.risk_score
            inst.risk_report = analysis.report
            if not analysis.ok:
                inst.status = InstanceStatus.FAILED
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
        except Exception:  # noqa: BLE001
            logger.exception("deploy script failed")
            inst.status = InstanceStatus.FAILED


async def _activate(
    session: AsyncSession,
    user_id: int,
    product: ProductKind,
    invoice_id: str,
    amount_rub: int,
) -> None:
    from app.repos.payments import by_invoice, mark_paid

    payment = await by_invoice(session, invoice_id)
    if payment and payment.status != PaymentStatus.PAID:
        await mark_paid(session, payment)
    await subs_repo.extend(session, user_id, product, settings.subscription_days)
    await users_repo.add_xp(session, user_id, 50)
    await logs_repo.write(
        session,
        kind="payment.paid",
        message=f"+{settings.subscription_days} days for {product.value}",
        user_id=user_id,
        meta={"amount_rub": amount_rub, "invoice_id": invoice_id},
    )


# Exported for the CryptoBot webhook handler.
async def activate_payment(
    session: AsyncSession,
    *,
    user_id: int,
    product: ProductKind,
    invoice_id: str,
    amount_rub: int,
) -> None:
    await _activate(session, user_id, product, invoice_id, amount_rub)
