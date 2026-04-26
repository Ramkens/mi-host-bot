"""CryptoBot integration.

Docs: https://help.crypt.bot/crypto-pay-api

Strategy:
* Convert RUB -> USDT amount via /getExchangeRates so user always pays a stable USD value.
* Create invoice with payload = "<user_id>:<product>" and store invoice_id locally.
* Either rely on webhook (preferred) or poll /getInvoices to confirm payment.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API = "https://pay.crypt.bot/api"


class CryptoBotError(RuntimeError):
    pass


class CryptoBotClient:
    def __init__(self, token: Optional[str] = None, timeout: float = 20.0) -> None:
        self.token = token or settings.cryptobot_token
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        return {
            "Crypto-Pay-API-Token": self.token,
            "Content-Type": "application/json",
        }

    async def _req(self, method: str, *, params=None, json_body=None) -> dict:
        if not self.enabled:
            raise CryptoBotError("CryptoBot token not configured")
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(
                f"{API}/{method}",
                headers=self._headers(),
                params=params,
                json=json_body,
            )
        data = r.json()
        if not data.get("ok"):
            raise CryptoBotError(f"{method}: {data}")
        return data["result"]

    async def get_me(self) -> dict:
        return await self._req("getMe")

    async def get_exchange_rates(self) -> list[dict]:
        return await self._req("getExchangeRates")

    async def rub_to_usdt(self, amount_rub: float) -> float:
        """Convert RUB -> USDT using CryptoBot's official rates."""
        rates = await self.get_exchange_rates()
        # Find USDT/RUB
        rate: Optional[float] = None
        for r in rates:
            if r.get("source") == "USDT" and r.get("target") == "RUB":
                rate = float(r["rate"])
                break
        if rate is None or rate <= 0:
            # Fallback rough rate; better than failing
            rate = 95.0
        return round(amount_rub / rate, 2)

    async def create_invoice(
        self,
        *,
        amount_rub: int,
        description: str,
        payload: str,
        paid_btn_name: str = "callback",
        paid_btn_url: Optional[str] = None,
        expires_in: int = 3600,
        asset: str = "USDT",
    ) -> dict:
        amount = await self.rub_to_usdt(amount_rub)
        body = {
            "asset": asset,
            "amount": str(amount),
            "description": description,
            "payload": payload,
            "expires_in": expires_in,
            "allow_anonymous": False,
            "allow_comments": False,
        }
        if paid_btn_url:
            body["paid_btn_name"] = paid_btn_name
            body["paid_btn_url"] = paid_btn_url
        return await self._req("createInvoice", json_body=body)

    async def get_invoices(
        self, invoice_ids: list[str], status: Optional[str] = None
    ) -> list[dict]:
        body: dict = {"invoice_ids": ",".join(invoice_ids)}
        if status:
            body["status"] = status
        res = await self._req("getInvoices", json_body=body)
        return res.get("items", [])

    @staticmethod
    def parse_payload(payload: str) -> Optional[tuple[int, str]]:
        try:
            uid, product = payload.split(":", 1)
            return int(uid), product
        except Exception:
            return None
