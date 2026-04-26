"""Instance repository."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Instance, InstanceStatus, ProductKind


async def create(
    session: AsyncSession,
    *,
    user_id: int,
    product: ProductKind,
    name: str,
    config: Optional[dict] = None,
) -> Instance:
    inst = Instance(
        user_id=user_id, product=product, name=name, config=config or {}
    )
    session.add(inst)
    await session.flush()
    return inst


async def by_id(session: AsyncSession, inst_id: int) -> Optional[Instance]:
    return await session.get(Instance, inst_id)


async def list_for_user(
    session: AsyncSession, user_id: int, product: Optional[ProductKind] = None
) -> list[Instance]:
    q = select(Instance).where(
        Instance.user_id == user_id, Instance.status != InstanceStatus.DELETED
    )
    if product is not None:
        q = q.where(Instance.product == product)
    res = await session.execute(q.order_by(Instance.created_at.desc()))
    return list(res.scalars())


async def list_alive(session: AsyncSession) -> list[Instance]:
    res = await session.execute(
        select(Instance).where(
            Instance.status.in_(
                [
                    InstanceStatus.PENDING,
                    InstanceStatus.DEPLOYING,
                    InstanceStatus.LIVE,
                    InstanceStatus.SUSPENDED,
                    InstanceStatus.FAILED,
                ]
            )
        )
    )
    return list(res.scalars())
