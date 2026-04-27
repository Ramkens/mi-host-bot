"""Mi Host entrypoint.

* FastAPI hosts:
    - /healthz         (used by Render & cron-job.org)
    - /tg/webhook      (Telegram webhook target)
    - /webhooks/cryptobot (CryptoBot payment notifications)
* Aiogram dispatcher is wired to FastAPI via aiogram.webhook.
* Background scheduler runs autoposts/funnel/keepalive.
* Supervisor restores tenants on boot from the DB.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request

from app.bot import bot_singleton, build_dispatcher
from app.config import settings
from app.db.base import SessionLocal
from app.db.init import init_db
from app.repos import users as users_repo
from app.scheduler import setup_scheduler
from app.utils.log import setup_logging
from app.webhooks.cryptobot import router as cryptobot_router

setup_logging()
logger = logging.getLogger(__name__)


async def _bootstrap_admins(bot: Bot, dp: Dispatcher) -> None:
    """Sync env-listed admins into the DB so /addadmin etc. work."""
    async with SessionLocal() as s:
        for aid in settings.admin_ids_list:
            user = await users_repo.by_id(s, aid)
            if user is None:
                # Pre-create a placeholder user; will be overwritten on /start.
                from app.db.models import User

                s.add(User(id=aid, is_admin=True))
            else:
                await users_repo.set_admin(s, aid, True)
        await s.commit()


async def _restore_tenants() -> None:
    """Re-spawn LIVE instances after a restart."""
    from sqlalchemy import select
    from app.db.models import Instance, InstanceStatus, ProductKind
    from app.services.cardinal import start_tenant
    from app.services.script_host import tenant_dir
    from app.services.supervisor import TenantSpec, supervisor
    import sys

    async with SessionLocal() as s:
        # Only restore tenants belonging to this (master) process — shard-
        # assigned tenants are owned by the corresponding worker.
        res = await s.execute(
            select(Instance).where(
                Instance.status == InstanceStatus.LIVE,
                Instance.shard_id.is_(None),
            )
        )
        items = list(res.scalars())
    for inst in items:
        try:
            if inst.product == ProductKind.CARDINAL:
                gk = (inst.config or {}).get("golden_key")
                if gk:
                    await start_tenant(inst.id, golden_key=gk)
            else:
                td = tenant_dir(inst.id)
                if td.exists():
                    cmd = ((inst.config or {}).get("start_cmd") or "python main.py").split()
                    cmd[0] = sys.executable if cmd[0] == "python" else cmd[0]
                    await supervisor.start(
                        TenantSpec(
                            instance_id=inst.id,
                            name=f"script-{inst.id}",
                            cwd=td,
                            cmd=cmd,
                            env={"PYTHONUNBUFFERED": "1"},
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("restore tenant %s: %s", inst.id, exc)


async def _tenant_watchdog() -> None:
    """Periodically ensure every LIVE master-side tenant has a running process.

    Если процесс упал и супервизор не смог его поднять (например, мы перезагрузились
    в момент падения), вотчдог сам перезапустит инстанс по данным из БД.
    """
    from sqlalchemy import select
    from app.db.models import Instance, InstanceStatus, ProductKind
    from app.services.cardinal import start_tenant
    from app.services.supervisor import supervisor

    while True:
        try:
            async with SessionLocal() as s:
                res = await s.execute(
                    select(Instance).where(
                        Instance.status == InstanceStatus.LIVE,
                        Instance.shard_id.is_(None),
                    )
                )
                items = list(res.scalars())
            for inst in items:
                if supervisor.is_running(inst.id):
                    continue
                if inst.product != ProductKind.CARDINAL:
                    continue
                gk = (inst.config or {}).get("golden_key")
                if not gk:
                    continue
                try:
                    await start_tenant(inst.id, golden_key=gk)
                    logger.info("watchdog: restarted tenant %s", inst.id)
                except Exception:  # noqa: BLE001
                    logger.exception("watchdog start failed for %s", inst.id)
        except Exception:  # noqa: BLE001
            logger.exception("tenant watchdog loop error")
        await asyncio.sleep(30)


async def _preseed_shards() -> None:
    """Auto-create shard rows from MIHOST_PRESEED_SHARDS env (JSON list).

    Format: [{"name": "host1", "api_key": "rnd_...", "capacity": 4}, ...]
    Idempotent: existing shards (by name) are skipped.
    """
    raw = (settings.mihost_preseed_shards or "").strip()
    if not raw:
        return
    import json as _json

    try:
        items = _json.loads(raw)
    except Exception:  # noqa: BLE001
        logger.warning("MIHOST_PRESEED_SHARDS is not valid JSON, skipping")
        return
    if not isinstance(items, list):
        return
    from app.repos import shards as shards_repo

    async with SessionLocal() as s:
        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            api_key = (item.get("api_key") or "").strip()
            if not name or not api_key:
                continue
            existing = await shards_repo.by_name(s, name)
            if existing:
                continue
            try:
                await shards_repo.create(
                    s,
                    name=name,
                    api_key=api_key,
                    region=item.get("region", "frankfurt"),
                    capacity=int(item.get("capacity", 4)),
                    notes=item.get("notes"),
                )
                logger.info("preseeded shard %s", name)
            except Exception:  # noqa: BLE001
                logger.exception("failed to preseed shard %s", name)
        await s.commit()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    await init_db()

    if settings.mihost_role == "worker":
        # Headless mode: only the reconciliation loop. No Telegram, no
        # scheduler, no admin handlers.
        from app.services.shard_worker import run_worker_forever

        worker_task = asyncio.create_task(run_worker_forever())
        app.state.worker_task = worker_task
        logger.info("Started in WORKER mode (shard=%s)", settings.mihost_shard_name)
        try:
            yield
        finally:
            worker_task.cancel()
            from app.services.supervisor import supervisor

            await supervisor.stop_all()
        return

    # ---- Master role (default) ----
    bot = bot_singleton()
    dp = build_dispatcher()
    app.state.bot = bot
    app.state.dp = dp
    await _bootstrap_admins(bot, dp)

    # Set webhook on Telegram
    if settings.public_url and "localhost" not in settings.public_url:
        try:
            await bot.set_webhook(
                url=settings.webhook_url,
                secret_token=settings.webhook_secret,
                drop_pending_updates=False,
                allowed_updates=dp.resolve_used_update_types(),
            )
            logger.info("Webhook set: %s", settings.webhook_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not set webhook: %s", exc)

    sched = setup_scheduler(bot)
    sched.start()
    app.state.scheduler = sched

    # Auto-seed shards from MIHOST_PRESEED_SHARDS env (idempotent).
    await _preseed_shards()

    # Restore tenants in the background. (Only for tenants assigned to master;
    # shard-assigned tenants are restored by the worker on their shard.)
    asyncio.create_task(_restore_tenants())

    # Periodically auto-restart any LIVE master-side tenants whose process died.
    app.state.watchdog_task = asyncio.create_task(_tenant_watchdog())

    # If a DB rotation just completed, announce "готово".
    from app.services.db_rotation import announce_done_if_pending

    asyncio.create_task(announce_done_if_pending(bot))

    try:
        yield
    finally:
        try:
            sched.shutdown(wait=False)
        except Exception:
            pass
        wd = getattr(app.state, "watchdog_task", None)
        if wd:
            wd.cancel()
        from app.services.supervisor import supervisor

        await supervisor.stop_all()
        await bot.session.close()


app = FastAPI(lifespan=lifespan, title="Mi Host", version="1.0.0")
app.include_router(cryptobot_router)


@app.get("/")
async def root() -> dict:
    return {"name": "Mi Host", "ok": True}


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/ping")
async def ping() -> dict:
    return {"pong": True}


@app.post(settings.webhook_path)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    if (
        settings.webhook_secret
        and x_telegram_bot_api_secret_token != settings.webhook_secret
    ):
        raise HTTPException(status_code=401, detail="bad secret")
    body = await request.json()
    update = Update.model_validate(body)
    bot: Bot = request.app.state.bot
    dp: Dispatcher = request.app.state.dp
    await dp.feed_update(bot, update)
    return {"ok": True}


def run_uvicorn() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run_uvicorn()
