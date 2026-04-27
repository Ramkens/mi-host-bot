"""Shard repository — CRUD for the multi-account shard registry."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Instance, InstanceStatus, Shard, ShardStatus
from app.utils.crypto import decrypt, encrypt
from app.utils.time import now_utc


async def create(
    session: AsyncSession,
    *,
    name: str,
    api_key: str,
    owner_id: Optional[str] = None,
    region: str = "frankfurt",
    capacity: int = 4,
    notes: Optional[str] = None,
) -> Shard:
    shard = Shard(
        name=name,
        api_key_enc=encrypt(api_key),
        owner_id=owner_id,
        region=region,
        capacity=capacity,
        notes=notes,
        status=ShardStatus.ACTIVE,
    )
    session.add(shard)
    await session.flush()
    return shard


async def by_id(session: AsyncSession, sid: int) -> Optional[Shard]:
    return await session.get(Shard, sid)


async def by_name(session: AsyncSession, name: str) -> Optional[Shard]:
    res = await session.execute(select(Shard).where(Shard.name == name))
    return res.scalar_one_or_none()


async def by_service_id(session: AsyncSession, service_id: str) -> Optional[Shard]:
    res = await session.execute(select(Shard).where(Shard.service_id == service_id))
    return res.scalar_one_or_none()


async def all_(session: AsyncSession) -> list[Shard]:
    res = await session.execute(select(Shard).order_by(Shard.id))
    return list(res.scalars())


async def active(session: AsyncSession) -> list[Shard]:
    res = await session.execute(
        select(Shard).where(Shard.status == ShardStatus.ACTIVE)
    )
    return list(res.scalars())


async def update_service_meta(
    session: AsyncSession,
    shard_id: int,
    *,
    service_id: Optional[str] = None,
    service_url: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> None:
    shard = await by_id(session, shard_id)
    if not shard:
        return
    if service_id is not None:
        shard.service_id = service_id
    if service_url is not None:
        shard.service_url = service_url
    if owner_id is not None:
        shard.owner_id = owner_id
    await session.flush()


async def heartbeat(session: AsyncSession, shard_id: int) -> None:
    shard = await by_id(session, shard_id)
    if not shard:
        return
    shard.last_seen_at = now_utc()
    await session.flush()


async def set_status(
    session: AsyncSession, shard_id: int, status: ShardStatus
) -> None:
    shard = await by_id(session, shard_id)
    if not shard:
        return
    shard.status = status
    await session.flush()


async def delete(session: AsyncSession, shard_id: int) -> None:
    shard = await by_id(session, shard_id)
    if shard:
        await session.delete(shard)
        await session.flush()


async def get_api_key(session: AsyncSession, shard_id: int) -> Optional[str]:
    shard = await by_id(session, shard_id)
    if not shard:
        return None
    return decrypt(shard.api_key_enc)


async def occupancy(session: AsyncSession) -> dict[int, int]:
    """Return {shard_id: active_instance_count}.

    Excludes DELETED / SUSPENDED rows so admin-deleted instances free
    their slot immediately.
    """
    res = await session.execute(
        select(Instance.shard_id, func.count(Instance.id))
        .where(Instance.shard_id.is_not(None))
        .where(Instance.desired_state == "live")
        .where(
            Instance.status.notin_((InstanceStatus.DELETED, InstanceStatus.SUSPENDED))
        )
        .group_by(Instance.shard_id)
    )
    return {sid: cnt for sid, cnt in res.all()}


async def pick_least_loaded(session: AsyncSession) -> Optional[Shard]:
    """Pick the active shard with the most free capacity (load < capacity)."""
    shards = await active(session)
    if not shards:
        return None
    occ = await occupancy(session)
    best: Optional[Shard] = None
    best_free = -1
    for shard in shards:
        free = shard.capacity - occ.get(shard.id, 0)
        if free <= 0:
            continue
        if free > best_free:
            best_free = free
            best = shard
    return best


def is_alive(shard: Shard, *, threshold_seconds: int = 600) -> bool:
    """Heuristic: shard is 'alive' if it heartbeated within the threshold."""
    if shard.last_seen_at is None:
        return False
    return (now_utc() - shard.last_seen_at).total_seconds() < threshold_seconds
