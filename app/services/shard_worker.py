"""Worker-mode reconciliation loop.

When MIHOST_ROLE=worker, the FastAPI process turns into a headless
subprocess-supervisor: it polls the DB for `Instance` rows whose
`shard_id` matches this shard, and ensures `actual_state` follows
`desired_state`. It also periodically heartbeats the shard's row so
the master can detect dead workers.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

from sqlalchemy import select

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import Instance, ProductKind, Shard
from app.repos import shards as shards_repo
from app.services.cardinal import start_tenant
from app.services.script_host import tenant_dir
from app.services.supervisor import TenantSpec, supervisor
from app.utils.time import now_utc

logger = logging.getLogger(__name__)

POLL_INTERVAL = 10.0  # seconds


async def _resolve_shard() -> Optional[Shard]:
    name = settings.mihost_shard_name
    service_id = settings.render_service_id
    async with SessionLocal() as s:
        # Prefer service_id (Render-injected, immutable per service).
        if service_id:
            sh = await shards_repo.by_service_id(s, service_id)
            if sh:
                return sh
        if name:
            sh = await shards_repo.by_name(s, name)
            if sh:
                # Bind to this service id if we have one.
                if service_id and not sh.service_id:
                    await shards_repo.update_service_meta(
                        s, sh.id, service_id=service_id
                    )
                    await s.commit()
                return sh
    return None


async def _start_instance(inst: Instance) -> None:
    """Spawn the subprocess for an instance whose desired_state=live."""
    if inst.product == ProductKind.CARDINAL:
        gk = (inst.config or {}).get("golden_key")
        if not gk:
            logger.warning("instance %s: no golden_key, skipping", inst.id)
            return
        await start_tenant(inst.id, golden_key=gk)
    else:
        td = tenant_dir(inst.id)
        if not td.exists():
            logger.warning("instance %s: tenant dir missing %s", inst.id, td)
            return
        cmd = ((inst.config or {}).get("start_cmd") or "python main.py").split()
        if cmd[0] == "python":
            cmd[0] = sys.executable
        await supervisor.start(
            TenantSpec(
                instance_id=inst.id,
                name=f"script-{inst.id}",
                cwd=td,
                cmd=cmd,
                env={"PYTHONUNBUFFERED": "1"},
            )
        )


async def _stop_instance(inst_id: int) -> None:
    await supervisor.stop(inst_id)


async def _reconcile_once(shard_id: int) -> None:
    async with SessionLocal() as s:
        await shards_repo.heartbeat(s, shard_id)
        res = await s.execute(
            select(Instance).where(Instance.shard_id == shard_id)
        )
        instances = list(res.scalars())
    for inst in instances:
        try:
            running = supervisor.is_running(inst.id)
            desired = inst.desired_state
            if desired == "live" and not running:
                await _start_instance(inst)
                async with SessionLocal() as s2:
                    obj = await s2.get(Instance, inst.id)
                    if obj:
                        obj.actual_state = "live"
                        await s2.commit()
            elif desired == "stopped" and running:
                await _stop_instance(inst.id)
                async with SessionLocal() as s2:
                    obj = await s2.get(Instance, inst.id)
                    if obj:
                        obj.actual_state = "stopped"
                        await s2.commit()
            else:
                # Already in desired state. Sync actual_state if drift.
                actual_now = "live" if running else "stopped"
                if inst.actual_state != actual_now:
                    async with SessionLocal() as s2:
                        obj = await s2.get(Instance, inst.id)
                        if obj:
                            obj.actual_state = actual_now
                            await s2.commit()
        except Exception:  # noqa: BLE001
            logger.exception("reconcile instance %s failed", inst.id)
            async with SessionLocal() as s2:
                obj = await s2.get(Instance, inst.id)
                if obj:
                    obj.actual_state = "error"
                    await s2.commit()


async def run_worker_forever() -> None:
    """Entry-point for MIHOST_ROLE=worker."""
    logger.info(
        "[shard_worker] starting; service_id=%s shard_name=%s",
        settings.render_service_id, settings.mihost_shard_name,
    )
    # Wait until the master has registered our shard row.
    while True:
        shard = await _resolve_shard()
        if shard:
            logger.info("[shard_worker] bound to shard id=%s name=%s",
                        shard.id, shard.name)
            break
        logger.info("[shard_worker] no shard row found yet, retrying…")
        await asyncio.sleep(15)

    while True:
        try:
            await _reconcile_once(shard.id)
        except Exception:  # noqa: BLE001
            logger.exception("[shard_worker] reconcile loop failed")
        await asyncio.sleep(POLL_INTERVAL)
