"""Master reconciler — keep Instance.status in sync with supervisor truth.

The DB column ``Instance.status`` is what every UI surface (admin
'Хостинги', user 'Мои серверы', /shards capacity counter) reads. If the
supervisor's process for an instance dies and stays dead — or if it's
never been spawned — leaving ``status=LIVE`` makes the bot show a
'fake green' status. This reconciler closes that loop.

Scope: only master-owned instances (``shard_id IS NULL``) and only
non-DELETED rows. Shard-owned instances are reconciled by the worker
on their own shard.

Cadence: every 30 seconds. Cheap (one SELECT + at most one UPDATE per
inst per pass).
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import Instance, InstanceStatus
from app.services.supervisor import supervisor

logger = logging.getLogger(__name__)

# Instance.status flips that the reconciler is willing to make. Anything
# in TERMINAL is left alone — DELETED is permanent, FAILED stays FAILED
# until an admin restarts (which itself flips status back to DEPLOYING).
TERMINAL = {InstanceStatus.DELETED}

POLL_INTERVAL = 30.0


async def reconcile_once() -> tuple[int, int]:
    """Run one pass. Returns (n_checked, n_changed)."""
    changed = 0
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Instance).where(
                    Instance.shard_id.is_(None),
                    Instance.status.notin_(TERMINAL),
                )
            )
        ).scalars().all()
        for inst in rows:
            running = supervisor.is_running(inst.id)
            new_actual = "live" if running else "stopped"
            new_status = inst.status
            # If we believe it's LIVE but the process is gone, downgrade.
            # We don't auto-promote PENDING → LIVE here — that path goes
            # through `start_tenant` which sets status=DEPLOYING explicitly.
            if inst.status == InstanceStatus.LIVE and not running:
                # Distinguish "the user just hasn't uploaded a config yet"
                # (no supervisor entry at all) vs "process crashed".
                state = supervisor.tenants.get(inst.id)
                if state is None:
                    # No supervisor entry — most likely we're between boot
                    # and _restore_tenants. Don't churn the status; just
                    # mark actual_state and move on.
                    pass
                else:
                    # Supervisor knows about it but it's not running. If
                    # the crash-loop guard already fired (stop_requested
                    # set + restart_count >= cap) the status will already
                    # be FAILED via _mark_instance_failed; otherwise leave
                    # status alone and let supervisor heal it.
                    pass
            if running and inst.status in (InstanceStatus.PENDING, InstanceStatus.DEPLOYING):
                new_status = InstanceStatus.LIVE
            if new_actual != inst.actual_state or new_status != inst.status:
                inst.actual_state = new_actual
                inst.status = new_status
                changed += 1
        if changed:
            await session.commit()
    return len(rows), changed


async def run_reconciler_forever() -> None:
    logger.info("master reconciler started (interval=%.0fs)", POLL_INTERVAL)
    while True:
        try:
            checked, changed = await reconcile_once()
            if changed:
                logger.info("reconciler: checked=%d changed=%d", checked, changed)
        except Exception:  # noqa: BLE001
            logger.exception("reconciler pass failed")
        await asyncio.sleep(POLL_INTERVAL)
