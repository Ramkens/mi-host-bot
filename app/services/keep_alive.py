"""Keep-alive pinger.

Render free-plan services sleep after ~15 minutes of inactivity. This keeper
runs only on the master and, every ``interval`` seconds, does two things:

1. GET ``/healthz`` on its own public URL (self-ping).
2. GET ``/healthz`` on every ACTIVE shard's ``service_url`` (cached in the DB).
   If a shard has no ``service_url`` yet, query Render API with its API key,
   pick the first running web service, and persist the URL.

External HTTP traffic is what Render uses to decide "is this service awake",
so the ping must come from outside localhost. Master service ↔ shard service
is cross-account, so this is fine.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import ShardStatus
from app.repos import shards as shards_repo

logger = logging.getLogger(__name__)

RENDER_API = "https://api.render.com/v1"


async def _discover_service_url(api_key: str) -> Optional[str]:
    """Return the URL of the first web_service / background_worker on this account."""
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        ) as sess:
            async with sess.get(f"{RENDER_API}/services?limit=20") as r:
                if r.status != 200:
                    return None
                items = await r.json()
    except Exception:  # noqa: BLE001
        logger.exception("render list services failed")
        return None
    # Response is a list of {"service": {...}}.
    for item in items:
        svc = item.get("service") or {}
        url = svc.get("serviceDetails", {}).get("url") or svc.get("url")
        if url:
            return url.rstrip("/")
    return None


async def _ping(url: str) -> bool:
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as sess:
            async with sess.get(url) as r:
                return r.status < 500
    except Exception:  # noqa: BLE001
        return False


async def _tick() -> None:
    # Self-ping. settings.public_url is e.g. https://mi-host-bot.onrender.com.
    if settings.public_url and "localhost" not in settings.public_url:
        self_url = settings.public_url.rstrip("/") + "/healthz"
        await _ping(self_url)

    async with SessionLocal() as s:
        rows = await shards_repo.active(s)
        # Cache service_url for any shard that doesn't have one.
        for shard in rows:
            if shard.service_url:
                continue
            api_key = await shards_repo.get_api_key(s, shard.id)
            if not api_key:
                continue
            url = await _discover_service_url(api_key)
            if not url:
                continue
            await shards_repo.update_service_meta(s, shard.id, service_url=url)
            logger.info("keeper: discovered %s -> %s", shard.name, url)
        await s.commit()

        # Ping each shard.
        rows = await shards_repo.active(s)
        for shard in rows:
            if not shard.service_url:
                continue
            ok = await _ping(shard.service_url.rstrip("/") + "/healthz")
            if not ok:
                logger.debug("keeper: shard %s not responding", shard.name)


async def run_keeper_forever(interval: int = 60) -> None:
    """Run the keep-alive loop. Safe to cancel."""
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("keep-alive tick failed")
        await asyncio.sleep(interval)
