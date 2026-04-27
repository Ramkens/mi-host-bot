"""Buy + payment flow.

Order: choose product → collect golden_key → confirm summary → invoice
(USDT through CryptoBot) OR coupon redemption.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import InstanceStatus, PaymentStatus, ProductKind, User
from app.keyboards.main import (
    back_to_menu,
    buy_cancel,
    buy_confirm,
    buy_locale,
    buy_menu,
    pay_buttons,
)
from app.repos import coupons as coupons_repo
from app.repos import instances as inst_repo
from app.repos import logs as logs_repo
from app.repos import payments as payments_repo
from app.repos import subscriptions as subs_repo
from app.services.images import ASSETS, generate_all
from app.services.payment import CryptoBotClient

logger = logging.getLogger(__name__)
router = Router(name="payment")


class BuyFSM(StatesGroup):
    awaiting_golden_key = State()
    awaiting_telegram_token = State()
    awaiting_telegram_secret = State()
    awaiting_locale = State()
    awaiting_coupon = State()


# --- Step 1: choose product menu ---


@router.callback_query(F.data == "buy:menu")
async def cb_buy_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    p = ASSETS / "order.png"
    if not p.exists():
        generate_all()
    text = (
        "<b>Хостинг FunPay Cardinal</b>\n\n"
        f"<b>{settings.price_cardinal_rub} ₽ / 30 дней</b>\n"
        "Авто-запуск, авто-рестарт, смена golden_key и заливка конфигов прямо в боте.\n\n"
        "Сначала пришли настройки, потом выставлю счёт."
    )
    kb = buy_menu(price_rub=settings.price_cardinal_rub)
    if cb.message:
        try:
            await cb.message.edit_caption(
                caption=text, parse_mode="HTML", reply_markup=kb
            )
        except Exception:
            await cb.message.answer_photo(
                FSInputFile(str(p)),
                caption=text,
                parse_mode="HTML",
                reply_markup=kb,
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
    if product != ProductKind.CARDINAL:
        await cb.answer("Доступен только хостинг FunPay Cardinal.", show_alert=True)
        return
    await state.clear()
    await state.update_data(product=product.value)
    if cb.message:
        await cb.message.answer(
            "<b>Настройка Cardinal</b>\n\n"
            "Пришли свой <code>golden_key</code> от FunPay одним сообщением.\n"
            "Он шифруется и используется только для запуска твоего сервера.\n\n"
            "<i>Где взять:</i> на funpay.com → DevTools → Application → Cookies → "
            "<code>golden_key</code>.\n\n"
            "Отмена — /menu",
            parse_mode="HTML",
        )
        await state.set_state(BuyFSM.awaiting_golden_key)
    await cb.answer()


@router.message(BuyFSM.awaiting_golden_key)
async def receive_golden_key(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    key = (msg.text or "").strip()
    if len(key) < 20:
        await msg.answer("Ключ выглядит некорректно. Пришли golden_key целиком.")
        return
    await state.update_data(golden_key=key)
    # Try to delete user's message so the secret disappears from chat.
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    await _ask_telegram_token(msg, state)


async def _ask_telegram_token(msg: Message, state: FSMContext) -> None:
    await state.set_state(BuyFSM.awaiting_telegram_token)
    await msg.answer(
        "<b>Telegram-бот Cardinal</b>\n\n"
        "Пришли токен своего Telegram-бота от @BotFather — через него "
        "будешь управлять FunPay-магазином.\n\n"
        "Нет бота? Открой @BotFather → /newbot → выбери имя → получи токен.\n"
        "Формат: <code>123456789:ABC...</code>",
        parse_mode="HTML",
        reply_markup=buy_cancel(),
    )


@router.message(BuyFSM.awaiting_telegram_token)
async def receive_telegram_token(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    token = (msg.text or "").strip()
    # BotFather tokens are roughly "<int>:<35-chars>".
    if ":" not in token or len(token) < 30:
        await msg.answer(
            "Неверный токен. Пришли токен от @BotFather целиком "
            "в формате <code>123456789:ABC...</code>.",
            parse_mode="HTML",
            reply_markup=buy_cancel(),
        )
        return
    await state.update_data(telegram_token=token)
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    await _ask_telegram_secret(msg, state)


async def _ask_telegram_secret(msg: Message, state: FSMContext) -> None:
    await state.set_state(BuyFSM.awaiting_telegram_secret)
    await msg.answer(
        "<b>Пароль доступа</b>\n\n"
        "Придумай пароль (минимум 4 символа). Он нужен:\n"
        "• в твоём Cardinal-боте при первом входе (команда /init);\n"
        "• чтобы удалить свой сервер из этого бота.\n\n"
        "<b>Запиши отдельно</b> — восстановить невозможно.",
        parse_mode="HTML",
        reply_markup=buy_cancel(),
    )


@router.message(BuyFSM.awaiting_telegram_secret)
async def receive_telegram_secret(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    secret = (msg.text or "").strip()
    if len(secret) < 4:
        await msg.answer(
            "Минимум 4 символа. Введи пароль.",
            reply_markup=buy_cancel(),
        )
        return
    await state.update_data(telegram_secret=secret)
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    await _ask_locale(msg, state)


async def _ask_locale(msg: Message, state: FSMContext) -> None:
    await state.set_state(BuyFSM.awaiting_locale)
    await msg.answer(
        "<b>Язык авто-сообщений Cardinal</b>",
        parse_mode="HTML",
        reply_markup=buy_locale(),
    )


@router.callback_query(F.data.startswith("buy:locale:"))
async def cb_buy_locale(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not cb.data:
        return
    locale = cb.data.split(":")[2]
    if locale not in {"ru", "en", "uk"}:
        await cb.answer("Bad locale", show_alert=True)
        return
    await state.update_data(locale=locale)
    if cb.message:
        await _show_summary(cb.message, state, session, user)
    await cb.answer()


async def _show_summary(
    msg: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    data = await state.get_data()
    price = settings.price_cardinal_rub
    tg_line = "Telegram-бот: подключён"
    locale = (data.get("locale") or "ru").upper()
    details = (
        "FunPay Cardinal\n"
        f"golden_key: <code>***{data['golden_key'][-4:]}</code>\n"
        f"{tg_line}\n"
        f"Локаль: {locale}\n"
        f"Срок: 30 дней\n"
    )
    text = (
        "<b>Проверь заказ</b>\n\n"
        f"{details}\n"
        f"<b>К оплате: {price} ₽</b>\n\n"
        "Оплата только в <b>USDT через CryptoBot</b>.\n"
        "Есть бесплатный купон? Жми «У меня есть купон»."
    )
    await msg.answer(text, parse_mode="HTML", reply_markup=buy_confirm())


# --- Step 3a: invoice ---


@router.callback_query(F.data == "buy:invoice")
async def cb_buy_invoice(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    if not data.get("golden_key"):
        await cb.answer("Сначала пришли golden_key", show_alert=True)
        return
    product = ProductKind.CARDINAL
    price = settings.price_cardinal_rub

    client = CryptoBotClient()
    if not client.enabled:
        await cb.answer("Оплата временно недоступна — пиши в поддержку", show_alert=True)
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
        f"Продукт: <b>{product.value}</b>\n"
        f"Сумма: <b>{price} ₽</b> ≈ {invoice.get('amount')} {invoice.get('asset')}\n\n"
        "Оплата только в USDT через @CryptoBot.\n\n"
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


@router.callback_query(F.data == "buy:coupon")
async def cb_buy_coupon(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    if not data.get("product"):
        await state.update_data(product=ProductKind.CARDINAL.value)
    await state.set_state(BuyFSM.awaiting_coupon)
    if cb.message:
        await cb.message.answer(
            "Пришли код купона одним сообщением (формат <code>MH-XXXXXXXX</code>).",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(BuyFSM.awaiting_coupon)
async def receive_coupon(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    code = (msg.text or "").strip().upper()
    data = await state.get_data()
    product_str = data.get("product") or ProductKind.CARDINAL.value
    product = ProductKind(product_str)
    ok, message, coupon = await coupons_repo.redeem(session, code, user.id)
    if not ok or not coupon:
        await msg.answer(f"Купон не подошёл: {message}")
        return
    if coupon.product != product:
        await msg.answer(
            f"Купон выдан под другой продукт ({coupon.product.value}). "
            "Запроси нужный купон у администратора.",
        )
        return
    # Activate as if paid; provision the instance using the saved settings.
    await subs_repo.extend(session, user.id, product, coupon.days)
    await logs_repo.write(
        session,
        kind="coupon.redeemed",
        message=f"{code} · +{coupon.days}d {product.value}",
        user_id=user.id,
    )
    await session.commit()
    await msg.answer(
        f"Купон применён: +{coupon.days} дней <b>{product.value}</b>.",
        parse_mode="HTML",
    )
    # Provision the instance immediately (need golden_key from FSM data).
    if not data.get("golden_key"):
        await msg.answer(
            "Подписка активна. Чтобы поднять сервер — нажми «Купить сервер» "
            "и пришли golden_key. Будет выдан без оплаты."
        )
        await state.clear()
        return
    try:
        await _provision_instance(session, user.id, product, data)
        await session.commit()
        await _notify_admins_about_purchase(
            msg, user, product, paid=False, amount_rub=0, days=coupon.days
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("provision after coupon failed")
        await msg.answer(
            f"Подписка активна, но запуск сервера упал: {exc}\n"
            "Напиши в поддержку — поможем."
        )
    await state.clear()


# --- Step 4: pay-check (manual + webhook) ---


@router.callback_query(F.data == "pay:check")
async def cb_pay_check(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Manual fallback when the webhook hasn't fired yet — poll CryptoBot."""
    client = CryptoBotClient()
    if not client.enabled:
        await cb.answer("CryptoBot недоступен — пиши в поддержку", show_alert=True)
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

    if cb.message:
        await cb.message.answer(
            f"Оплата подтверждена. Подписка <b>{payment.product.value}</b> "
            f"продлена на {settings.subscription_days} дней.",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
    await _notify_admins_about_purchase(
        cb.message,  # type: ignore[arg-type]
        user,
        payment.product,
        paid=True,
        amount_rub=payment.amount_rub,
        days=settings.subscription_days,
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

    if product != ProductKind.CARDINAL:
        return
    gk = data.get("golden_key")
    if not gk:
        return
    tg_token = (data.get("telegram_token") or "").strip()
    tg_secret = (data.get("telegram_secret") or "").strip()
    locale = (data.get("locale") or "ru").strip() or "ru"
    cfg_payload = {
        "golden_key": gk,
        "telegram_token": tg_token,
        "telegram_secret": tg_secret,
        "locale": locale,
    }
    # Reuse existing instance if any (idempotent renewal).
    existing = await inst_repo.list_for_user(session, user_id, ProductKind.CARDINAL)
    if existing:
        inst = existing[0]
        inst.config = {**(inst.config or {}), **cfg_payload}
    else:
        inst = await inst_repo.create(
            session,
            user_id=user_id,
            product=ProductKind.CARDINAL,
            name=f"cardinal-{user_id}",
            config=cfg_payload,
        )
    inst.status = InstanceStatus.DEPLOYING
    inst.desired_state = "live"
    await session.flush()
    # Master-side direct start. Runs the tenant on master whenever master
    # owns it: shard_id is NULL, or the assigned shard has no live worker.
    master_owns = inst.shard_id is None
    if inst.shard_id is not None:
        from app.repos import shards as shards_repo

        shard = await shards_repo.by_id(session, inst.shard_id)
        master_owns = not shard or not shards_repo.is_alive(shard)
    if master_owns:
        try:
            await start_tenant(
                inst.id,
                golden_key=gk,
                telegram_token=tg_token,
                telegram_secret=tg_secret,
                locale=locale,
            )
            inst.status = InstanceStatus.LIVE
            inst.actual_state = "live"
        except Exception:  # noqa: BLE001
            logger.exception("start cardinal failed")
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


async def _notify_admins_about_purchase(
    msg: Message | None,
    user: User,
    product: ProductKind,
    *,
    paid: bool,
    amount_rub: int,
    days: int,
) -> None:
    """Notify every configured admin about a successful purchase / coupon redemption."""
    if msg is None or not getattr(msg, "bot", None):
        return
    bot = msg.bot
    if bot is None:
        return
    name = user.first_name or user.username or "—"
    handle = f"@{user.username}" if user.username else f"id:{user.id}"
    kind = "Оплата" if paid else "Купон"
    sum_str = f"{amount_rub} ₽" if paid else "купон"
    text = (
        "<b>Новая покупка</b>\n\n"
        f"Тип: {kind}\n"
        f"Юзер: {name} ({handle})\n"
        f"Продукт: {product.value}\n"
        f"Сумма: {sum_str}\n"
        f"Срок: +{days} дн."
    )
    for admin_id in settings.admin_ids_list:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:  # noqa: BLE001
            logger.debug("admin notify failed for %s", admin_id)


# ---------------------------------------------------------------------------
# Renewal flow (Продление хостинга)
# ---------------------------------------------------------------------------


class RenewFSM(StatesGroup):
    awaiting_coupon = State()


def _fmt_expires(sub) -> str:
    if not sub or not sub.expires_at:
        return "нет подписки"
    from app.utils.time import now_utc

    delta = sub.expires_at - now_utc()
    if delta.total_seconds() <= 0:
        return f"истекла ({sub.expires_at.strftime('%d.%m.%Y')})"
    days = delta.days
    return f"до {sub.expires_at.strftime('%d.%m.%Y')} (осталось {days} дн.)"


@router.callback_query(F.data == "renew:menu")
async def cb_renew_menu(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await state.clear()
    items = await inst_repo.list_for_user(session, user.id, ProductKind.CARDINAL)
    if not items:
        if cb.message:
            await cb.message.answer(
                "<b>Продление хостинга</b>\n\n"
                "У тебя пока нет активного сервера. Сначала купи — кнопка «FunPay Cardinal».",
                parse_mode="HTML",
                reply_markup=back_to_menu(),
            )
        await cb.answer()
        return
    sub = await subs_repo.get(session, user.id, ProductKind.CARDINAL)
    rows: list[list[InlineKeyboardButton]] = []
    for inst in items:
        rows.append([
            InlineKeyboardButton(
                text=f"Сервер #{inst.id} · {_fmt_expires(sub)}",
                callback_data=f"renew:start:{inst.id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="« В меню", callback_data="menu")])
    text = (
        "<b>Продление хостинга</b>\n\n"
        f"Стоимость: <b>{settings.price_cardinal_rub} ₽</b> · "
        f"+{settings.subscription_days} дней.\n"
        "Выбери сервер, который хочешь продлить."
    )
    if cb.message:
        await cb.message.answer(
            text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    await cb.answer()


@router.callback_query(F.data.startswith("renew:start:"))
async def cb_renew_start(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if not cb.data:
        return
    try:
        inst_id = int(cb.data.split(":")[2])
    except Exception:
        await cb.answer("Не найдено", show_alert=True)
        return
    inst = await inst_repo.by_id(session, inst_id)
    if not inst or inst.user_id != user.id or inst.status == InstanceStatus.DELETED:
        await cb.answer("Сервер не найден", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        mode="renew",
        renew_instance_id=inst_id,
        product=ProductKind.CARDINAL.value,
    )
    sub = await subs_repo.get(session, user.id, ProductKind.CARDINAL)
    text = (
        "<b>Продление хостинга</b>\n\n"
        f"Сервер: <b>#{inst.id}</b>\n"
        f"Текущая подписка: {_fmt_expires(sub)}\n"
        f"Будет продлена на <b>{settings.subscription_days} дней</b>\n"
        f"К оплате: <b>{settings.price_cardinal_rub} ₽</b>\n\n"
        "Оплата через @CryptoBot в USDT. "
        "Если есть купон — нажми «У меня есть купон»."
    )
    if cb.message:
        await cb.message.answer(
            text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Оплатить",
                                      callback_data="renew:invoice")],
                [InlineKeyboardButton(text="У меня есть купон",
                                      callback_data="renew:coupon")],
                [InlineKeyboardButton(text="« Отмена", callback_data="menu")],
            ]),
        )
    await cb.answer()


@router.callback_query(F.data == "renew:invoice")
async def cb_renew_invoice(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    if data.get("mode") != "renew" or not data.get("renew_instance_id"):
        await cb.answer("Начни с «Продление хостинга»", show_alert=True)
        return
    product = ProductKind.CARDINAL
    price = settings.price_cardinal_rub
    client = CryptoBotClient()
    if not client.enabled:
        await cb.answer("Оплата временно недоступна — пиши в поддержку",
                        show_alert=True)
        return
    try:
        invoice = await client.create_invoice(
            amount_rub=price,
            description=f"Mi Host · Продление · {settings.subscription_days} дней",
            payload=f"{user.id}:{product.value}",
            paid_btn_url=f"https://t.me/{(await cb.bot.get_me()).username}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_invoice (renew) failed: %s", exc)
        await cb.answer("Не удалось создать счёт", show_alert=True)
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
        message=f"renew invoice {invoice['invoice_id']}",
        user_id=user.id,
        meta={"amount_rub": price, "mode": "renew",
              "instance_id": data.get("renew_instance_id")},
    )
    await session.commit()
    pay_url = invoice.get("pay_url") or invoice.get("bot_invoice_url")
    text = (
        "<b>Счёт на продление</b>\n\n"
        f"Сумма: <b>{price} ₽</b> ≈ "
        f"{invoice.get('amount')} {invoice.get('asset')}\n"
        f"+{settings.subscription_days} дней к подписке\n\n"
        "Оплата в USDT через @CryptoBot. После оплаты нажми «Я оплатил»."
    )
    if cb.message:
        await cb.message.answer(
            text, parse_mode="HTML",
            reply_markup=pay_buttons(pay_url or "https://t.me/CryptoBot"),
            disable_web_page_preview=True,
        )
    await cb.answer()


@router.callback_query(F.data == "renew:coupon")
async def cb_renew_coupon(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    if data.get("mode") != "renew":
        await cb.answer("Начни с «Продление хостинга»", show_alert=True)
        return
    await state.set_state(RenewFSM.awaiting_coupon)
    if cb.message:
        await cb.message.answer(
            "Пришли код купона одним сообщением "
            "(формат <code>MH-XXXXXXXX</code>).",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(RenewFSM.awaiting_coupon)
async def receive_renew_coupon(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    code = (msg.text or "").strip().upper()
    ok, message, coupon = await coupons_repo.redeem(session, code, user.id)
    if not ok or not coupon:
        await msg.answer(f"Купон не подошёл: {message}")
        return
    if coupon.product != ProductKind.CARDINAL:
        await msg.answer(
            "Купон выдан под другой продукт. "
            "Запроси у администратора подходящий."
        )
        return
    await subs_repo.extend(session, user.id, ProductKind.CARDINAL, coupon.days)
    await logs_repo.write(
        session,
        kind="coupon.redeemed",
        message=f"{code} · renew +{coupon.days}d",
        user_id=user.id,
    )
    await session.commit()
    await msg.answer(
        f"Купон применён: +{coupon.days} дней к подписке на хостинг.",
        parse_mode="HTML",
        reply_markup=back_to_menu(),
    )
    await _notify_admins_about_purchase(
        msg, user, ProductKind.CARDINAL,
        paid=False, amount_rub=0, days=coupon.days,
    )
    await state.clear()
