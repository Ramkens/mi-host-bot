"""CryptoBot webhook handler.

Docs: https://help.crypt.bot/crypto-pay-api#webhooks
Signature: HMAC-SHA-256(API_TOKEN, body) hex == header `crypto-pay-api-signature`.
We use sha256(API_TOKEN_AS_KEY) per docs.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import ProductKind
from app.handlers.payment import activate_payment
from app.repos import payments as payments_repo

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify(body: bytes, signature: str) -> bool:
    if not settings.cryptobot_token:
        return False
    secret = hashlib.sha256(settings.cryptobot_token.encode()).digest()
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature or "")


@router.post("/webhooks/cryptobot")
async def cryptobot_webhook(request: Request) -> dict:
    body = await request.body()
    signature = request.headers.get("crypto-pay-api-signature", "")
    if settings.cryptobot_token and not _verify(body, signature):
        # Soft-fail in dev (no token), strict in prod.
        logger.warning("cryptobot webhook signature mismatch")
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        data = json.loads(body.decode())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))

    update_type = data.get("update_type")
    payload = data.get("payload") or {}
    if update_type != "invoice_paid":
        return {"ok": True, "ignored": True}

    invoice_id = str(payload.get("invoice_id"))
    p_payload = payload.get("payload") or ""
    user_id = None
    product = None
    if ":" in p_payload:
        try:
            uid, prod = p_payload.split(":", 1)
            user_id = int(uid)
            product = ProductKind(prod)
        except Exception:
            user_id = None

    async with SessionLocal() as session:
        payment = await payments_repo.by_invoice(session, invoice_id)
        if payment is None:
            logger.warning("cryptobot webhook: unknown invoice %s", invoice_id)
            return {"ok": True, "unknown": True}
        await activate_payment(
            session,
            user_id=payment.user_id,
            product=payment.product,
            invoice_id=invoice_id,
            amount_rub=payment.amount_rub,
        )
        await session.commit()
        # Notify user.
        try:
            from app.bot import bot_singleton

            bot = bot_singleton()
            await bot.send_message(
                payment.user_id,
                f"✓ Оплата получена. Подписка <b>{payment.product.value}</b> продлена.",
                parse_mode="HTML",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("cryptobot notify: %s", exc)
    return {"ok": True}
