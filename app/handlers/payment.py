"""Buy / pay flow."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import PaymentStatus, ProductKind, User
from app.keyboards.main import back_to_menu, buy_menu, pay_buttons
from app.repos import logs as logs_repo
from app.repos import payments as payments_repo
from app.repos import subscriptions as subs_repo
from app.repos import users as users_repo
from app.services.images import ASSETS, generate_all
from app.services.payment import CryptoBotClient

logger = logging.getLogger(__name__)
router = Router(name="payment")


@router.callback_query(F.data == "buy:menu")
async def cb_buy_menu(cb: CallbackQuery) -> None:
    p = ASSETS / "order.png"
    if not p.exists():
        generate_all()
    text = (
        "<b>Купить хостинг</b>\n\n"
        f"• FunPay Cardinal — <b>{settings.price_cardinal_rub} ₽</b> / 30 дней\n"
        f"• Кастомный скрипт — <b>{settings.price_script_rub} ₽</b> / 30 дней\n\n"
        "Оплата через CryptoBot. Подписка активируется автоматически."
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


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_product(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    if not cb.data:
        return
    suffix = cb.data.split(":", 1)[1]
    if suffix == "menu":
        return  # handled elsewhere
    try:
        product = ProductKind(suffix)
    except ValueError:
        await cb.answer("Неверный продукт", show_alert=True)
        return
    price = (
        settings.price_cardinal_rub
        if product == ProductKind.CARDINAL
        else settings.price_script_rub
    )

    client = CryptoBotClient()
    if not client.enabled:
        await cb.answer("CryptoBot не настроен. Свяжитесь с админом.", show_alert=True)
        return

    try:
        invoice = await client.create_invoice(
            amount_rub=price,
            description=f"Mi Host · {product.value} · {settings.subscription_days} дней",
            payload=f"{user.id}:{product.value}",
            paid_btn_url=f"https://t.me/{(await cb.bot.get_me()).username}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_invoice failed: %s", exc)
        await cb.answer("Ошибка создания счёта. Попробуйте позже.", show_alert=True)
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

    text = (
        "<b>Счёт создан</b>\n\n"
        f"Продукт: {product.value}\n"
        f"Сумма: {price} ₽ ≈ {invoice.get('amount')} {invoice.get('asset')}\n\n"
        "Нажмите «Оплатить» — после оплаты подписка активируется автоматически."
    )
    pay_url = invoice.get("pay_url") or invoice.get("bot_invoice_url")
    if cb.message:
        await cb.message.answer(
            text, parse_mode="HTML", reply_markup=pay_buttons(pay_url or "https://t.me/CryptoBot")
        )
    await cb.answer()


@router.callback_query(F.data == "pay:check")
async def cb_pay_check(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    """Manual fallback when webhook is not available — poll CryptoBot."""
    client = CryptoBotClient()
    if not client.enabled:
        await cb.answer("CryptoBot недоступен", show_alert=True)
        return
    # Find latest pending invoice for this user.
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
    await cb.message.answer(
        f"✓ Оплата подтверждена. Подписка {payment.product.value} продлена на {settings.subscription_days} дней.",
        reply_markup=back_to_menu(),
    )
    await cb.answer()


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
    days = settings.subscription_days
    # Bonus days from referral on first payment
    user = await users_repo.by_id(session, user_id)
    if user and user.referrer_id and not _has_paid_before(session, user_id, exclude=invoice_id):
        days += settings.referral_bonus_days
        # also reward referrer
        await users_repo.add_coins(session, user.referrer_id, 10)
        await subs_repo.extend(
            session, user.referrer_id, product, settings.referral_bonus_days
        )
    await subs_repo.extend(session, user_id, product, days)
    await users_repo.add_xp(session, user_id, 50)
    await logs_repo.write(
        session,
        kind="payment.paid",
        message=f"+{days} days for {product.value}",
        user_id=user_id,
        meta={"amount_rub": amount_rub, "invoice_id": invoice_id},
    )


async def _has_paid_before(
    session: AsyncSession, user_id: int, *, exclude: str
) -> bool:
    from sqlalchemy import select
    from app.db.models import Payment

    res = await session.execute(
        select(Payment).where(
            Payment.user_id == user_id,
            Payment.status == PaymentStatus.PAID,
            Payment.invoice_id != exclude,
        )
    )
    return res.scalars().first() is not None


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
