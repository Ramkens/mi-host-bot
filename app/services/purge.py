"""Auto-purge tenant data after `purge_grace_days` past sub expiry.

Before deleting, the tenant's working dir is zipped and DM'd to the
super-admin so the operator can manually restore the user later.

Run from the scheduler daily.
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

from aiogram.types import BufferedInputFile

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import Instance, InstanceStatus, ProductKind, Subscription
from app.services import script_host
from app.services.supervisor import DEFAULT_DATA_DIR, supervisor
from app.utils.time import fmt_msk, now_utc

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

PRIMARY_ADMIN_ID = 8341143485


def _zip_tenant_dir(instance_id: int, *, max_size_mb: int = 45) -> bytes | None:
    """Zip the tenant's working directory in-memory. Skip if missing/empty."""
    base: Path = DEFAULT_DATA_DIR / str(instance_id)
    if not base.exists() or not base.is_dir():
        return None
    buf = io.BytesIO()
    total = 0
    cap = max_size_mb * 1024 * 1024
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in base.rglob("*"):
            if f.is_file():
                try:
                    sz = f.stat().st_size
                except OSError:
                    continue
                if total + sz > cap:
                    zf.writestr(
                        "_TRUNCATED.txt",
                        f"Backup truncated at {cap // (1024*1024)} MB. Full data on master.",
                    )
                    break
                try:
                    zf.write(f, arcname=f.relative_to(base))
                    total += sz
                except OSError as exc:
                    logger.debug("skip %s: %s", f, exc)
    if total == 0:
        return None
    buf.seek(0)
    return buf.getvalue()


async def purge_expired_tenants(bot: "Bot") -> int:
    """Find expired-by-N-days subs, archive tenant dir to admin DM, then delete data."""
    purged = 0
    grace = max(1, settings.purge_grace_days)
    cutoff = now_utc() - timedelta(days=grace)
    async with SessionLocal() as s:
        # All subs that expired more than `grace` days ago AND have a tenant
        # not yet purged.
        res = await s.execute(
            select(Subscription).where(Subscription.expires_at <= cutoff)
        )
        subs = list(res.scalars())
        for sub in subs:
            ires = await s.execute(
                select(Instance).where(
                    Instance.user_id == sub.user_id,
                    Instance.product == sub.product,
                )
            )
            inst = ires.scalar_one_or_none()
            if inst is None:
                continue
            if inst.status == InstanceStatus.DELETED:
                continue
            # Stop running tenant if any.
            try:
                await supervisor.stop(inst.id)
            except Exception:  # noqa: BLE001
                logger.debug("supervisor.stop(%s) failed", inst.id)

            # Archive to primary admin.
            data = _zip_tenant_dir(inst.id)
            if data is not None:
                fname = (
                    f"backup_user{sub.user_id}_{sub.product.value}_inst{inst.id}_"
                    f"{fmt_msk(now_utc()).replace(' ', '_').replace(':','')}.zip"
                )
                try:
                    await bot.send_document(
                        PRIMARY_ADMIN_ID,
                        BufferedInputFile(data, filename=fname),
                        caption=(
                            f"💠 Авто-бэкап перед удалением\n"
                            f"user_id: <code>{sub.user_id}</code>\n"
                            f"продукт: {sub.product.value}\n"
                            f"истекла: {fmt_msk(sub.expires_at)} (≥{grace} дн.)\n"
                            f"инстанс #{inst.id}"
                        ),
                        parse_mode="HTML",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("send backup to admin failed")
            # Wipe tenant data dir.
            try:
                if sub.product == ProductKind.SCRIPT:
                    script_host.remove(inst.id)
                else:
                    base = DEFAULT_DATA_DIR / str(inst.id)
                    import shutil
                    shutil.rmtree(base, ignore_errors=True)
            except Exception:  # noqa: BLE001
                logger.exception("wipe instance %s failed", inst.id)

            inst.status = InstanceStatus.DELETED
            inst.desired_state = "stopped"
            inst.actual_state = "stopped"
            inst.config = {**(inst.config or {}), "purged_at": now_utc().isoformat()}
            try:
                await bot.send_message(
                    sub.user_id,
                    "💠 <b>Данные хоста удалены</b>\n\n"
                    f"Подписка <b>{sub.product.value}</b> истекла больше {grace} дней назад. "
                    "Все файлы инстанса удалены. Бэкап сохранён у админа — "
                    "для восстановления напиши в /support.\n\n"
                    "Заказать новый хост — /menu.",
                    parse_mode="HTML",
                )
            except Exception:  # noqa: BLE001
                pass
            purged += 1
        await s.commit()
    return purged
