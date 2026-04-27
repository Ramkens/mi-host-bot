"""Capacity / free-slot accounting across master + shards."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Instance, InstanceStatus, ProductKind, Shard, ShardStatus

# Statuses that should be treated as freeing the slot. DELETED + SUSPENDED
# instances no longer run a Cardinal subprocess, so they must not count
# against capacity (otherwise admin "Удалить" leaves a phantom slot).
_INACTIVE_STATUSES = (InstanceStatus.DELETED, InstanceStatus.SUSPENDED)


async def free_cardinal_slots(session: AsyncSession) -> int:
    """Roughly: how many more Cardinal hosts can we accept right now.

    A Cardinal eats most of a free-tier server (~150-200 MB). One Cardinal
    per slot is the safe assumption: master_capacity + Σ shard.capacity
    minus currently-active Cardinal instances. We exclude DELETED /
    SUSPENDED rows so admin-deleted servers free up their slot
    immediately.
    """
    res = await session.execute(
        select(func.coalesce(func.sum(Shard.capacity), 0)).where(
            Shard.status == ShardStatus.ACTIVE
        )
    )
    shard_cap = int(res.scalar_one() or 0)
    total_cap = settings.master_capacity + shard_cap

    res = await session.execute(
        select(func.count(Instance.id)).where(
            Instance.product == ProductKind.CARDINAL,
            Instance.desired_state == "live",
            Instance.status.notin_(_INACTIVE_STATUSES),
        )
    )
    live_cardinals = int(res.scalar_one() or 0)
    return max(0, total_cap - live_cardinals)


async def free_script_slots(session: AsyncSession) -> int:
    """Each shard can host ~4-8 STD scripts. We use Shard.capacity as
    soft slot count and subtract live scripts on shards. Master keeps 1 slot
    for scripts when not used by Cardinal."""
    res = await session.execute(
        select(func.coalesce(func.sum(Shard.capacity * 4), 0)).where(
            Shard.status == ShardStatus.ACTIVE
        )
    )
    shard_cap = int(res.scalar_one() or 0)
    total_cap = settings.master_capacity * 4 + shard_cap

    res = await session.execute(
        select(func.count(Instance.id)).where(
            Instance.product == ProductKind.SCRIPT,
            Instance.desired_state == "live",
        )
    )
    live_scripts = int(res.scalar_one() or 0)
    return max(0, total_cap - live_scripts)
