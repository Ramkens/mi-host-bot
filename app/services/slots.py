"""Capacity / free-slot accounting across master + shards."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Instance, ProductKind, Shard, ShardStatus


async def free_cardinal_slots(session: AsyncSession) -> int:
    """Roughly: how many more Cardinal hosts can we accept right now.

    A Cardinal eats most of a free-tier server (~150-200 MB). One Cardinal
    per slot is the safe assumption: master_capacity + Σ shard.capacity
    minus current LIVE Cardinal instances.
    """
    # Total capacity = master + active shards
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
