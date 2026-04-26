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

    def _keepalive_body(self, *, title: str, url: str, every_minutes: int = 1) -> dict:
        """Build a cron-job.org request body for a keep-alive pinger.

        ``every_minutes=1`` uses the ``[-1]`` wildcard so the job fires every
        minute (minute-resolution is the finest cron-job.org supports).
        Notifications on failure are disabled so a flurry of 5xx errors from
        Render during a cold start doesn't auto-disable the job or spam the
        owner's mailbox. ``onDisable`` stays on so manual disables still ping.
        """
        if every_minutes <= 1:
            minutes: list[int] = [-1]
        else:
            minutes = list(range(0, 60, every_minutes))
        return {
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
                "requestTimeout": 30,
                "redirectSuccess": False,
                "notification": {
                    "onFailure": False,
                    "onSuccess": False,
                    "onDisable": True,
                },
            }
        }

    async def create_keepalive_job(
        self, *, title: str, url: str, every_minutes: int = 1
    ) -> int:
        body = self._keepalive_body(title=title, url=url, every_minutes=every_minutes)
        data = await self._req("PUT", "/jobs", json=body)
        return int(data.get("jobId"))

    async def update_job(
        self, job_id: int, *, title: str, url: str, every_minutes: int = 1
    ) -> None:
        body = self._keepalive_body(title=title, url=url, every_minutes=every_minutes)
        await self._req("PATCH", f"/jobs/{job_id}", json=body)

    async def ensure_keepalive(
        self, *, title: str, url: str, every_minutes: int = 1
    ) -> int:
        """Idempotently create-or-update a single keep-alive job for ``url``.

        Returns the ``jobId``. On every call we re-apply the desired
        ``enabled=True`` + 1-minute schedule + no-failure-notifications
        configuration, so if cron-job.org or the user ever turned it off the
        next bot boot restores it.
        """
        jobs = await self.list_jobs()
        # Prefer an existing job targeting the same URL.
        match = next((j for j in jobs if j.get("url") == url), None)
        if match is None:
            return await self.create_keepalive_job(
                title=title, url=url, every_minutes=every_minutes
            )
        job_id = int(match["jobId"])
        await self.update_job(job_id, title=title, url=url, every_minutes=every_minutes)
        return job_id
