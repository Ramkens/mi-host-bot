"""cron-job.org API client (used as a free 24/7 keep-alive pinger).

Docs: https://docs.cron-job.org/
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API = "https://api.cron-job.org"


class CronJobError(RuntimeError):
    pass


class CronJobClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 20.0) -> None:
        self.api_key = api_key or settings.cronjob_api_key
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _req(self, method: str, path: str, *, json=None) -> dict:
        if not self.enabled:
            raise CronJobError("cron-job.org API key not configured")
        url = f"{API}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.request(method, url, headers=self._headers(), json=json)
        if r.status_code >= 400:
            raise CronJobError(f"{method} {path} -> {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}

    async def list_jobs(self) -> list[dict]:
        data = await self._req("GET", "/jobs")
        return data.get("jobs", [])

    async def delete_job(self, job_id: int) -> None:
        await self._req("DELETE", f"/jobs/{job_id}")

    async def create_keepalive_job(
        self, *, title: str, url: str, every_minutes: int = 5
    ) -> int:
        minutes = list(range(0, 60, max(1, every_minutes)))
        body = {
            "job": {
                "url": url,
                "enabled": True,
                "saveResponses": False,
                "title": title,
                "schedule": {
                    "timezone": "Europe/Moscow",
                    "expiresAt": 0,
                    "hours": [-1],
                    "mdays": [-1],
                    "minutes": minutes,
                    "months": [-1],
                    "wdays": [-1],
                },
                "requestMethod": 0,  # GET
            }
        }
        data = await self._req("PUT", "/jobs", json=body)
        return int(data.get("jobId"))
