"""Payment repository."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Payment, PaymentStatus, ProductKind
from app.utils.time import now_utc


async def create(
    session: AsyncSession,
    *,
    user_id: int,
    product: ProductKind,
    invoice_id: str,
    amount_rub: int,
    asset: Optional[str],
    amount_crypto: Optional[str],
    pay_url: Optional[str],
) -> Payment:
    p = Payment(
        user_id=user_id,
        product=product,
        invoice_id=invoice_id,
        amount_rub=amount_rub,
        asset=asset,
        amount_crypto=amount_crypto,
        pay_url=pay_url,
    )
    session.add(p)
    await session.flush()
    return p


async def by_invoice(session: AsyncSession, invoice_id: str) -> Optional[Payment]:
    res = await session.execute(
        select(Payment).where(Payment.invoice_id == invoice_id)
    )
    return res.scalar_one_or_none()


async def mark_paid(session: AsyncSession, payment: Payment) -> None:
    payment.status = PaymentStatus.PAID
    payment.paid_at = now_utc()


async def revenue_total(session: AsyncSession) -> int:
    res = await session.execute(
        select(func.coalesce(func.sum(Payment.amount_rub), 0)).where(
            Payment.status == PaymentStatus.PAID
        )
    )
    return int(res.scalar_one())


async def revenue_30d(session: AsyncSession) -> int:
    threshold = now_utc() - timedelta(days=30)
    res = await session.execute(
        select(func.coalesce(func.sum(Payment.amount_rub), 0)).where(
            Payment.status == PaymentStatus.PAID,
            Payment.paid_at >= threshold,
        )
    )
    return int(res.scalar_one())


async def list_pending(session: AsyncSession) -> list[Payment]:
    res = await session.execute(
        select(Payment).where(Payment.status == PaymentStatus.CREATED)
    )
    return list(res.scalars())
