"""Auto-rotation of the Render free Postgres before it expires.

Render's free Postgres lives 30 days, then is suspended for 30, then deleted.
This module:

1. Reads expiry of the current DATABASE_URL via the Render API.
2. ~3 days before expiry, provisions a *new* free Postgres on Render.
3. Copies every row from old → new using asyncpg (no pg_dump needed; the
   schema is recreated by SQLAlchemy on the next boot).
4. Updates the `DATABASE_URL` env var on this service.
5. Triggers a redeploy (which restarts the bot pointing at the new DB).
6. Notifies users with active subscriptions: "месяц прошёл, обновление
   серверов…" before the swap, "готово" after the new instance is live.

The orchestration is best-effort and idempotent — if a step fails, the
scheduler will try again on the next tick.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import asyncpg
from sqlalchemy import select

from app.config import settings
from app.db.base import SessionLocal, engine
from app.db.models import Base, Setting, Subscription
from app.services.render_api import RenderClient
from app.utils.time import now_utc

logger = logging.getLogger(__name__)

ROTATION_LEAD_DAYS = 3
SETTING_LAST_ROTATED = "db_rotation:last_completed_at"
SETTING_PG_ID = "db_rotation:current_pg_id"


def _normalize_asyncpg(url: str) -> str:
    """asyncpg.connect doesn't understand the +asyncpg suffix."""
    return re.sub(r"^postgresql\+[a-z]+://", "postgresql://", url)


def _extract_pg_host_id(url: str) -> Optional[str]:
    """The Render postgres host looks like dpg-XXX-a.frankfurt-postgres.render.com.
    The Render *service id* is `dpg-XXX-a`.
    """
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return None
    m = re.match(r"(dpg-[a-z0-9]+-a)", host)
    return m.group(1) if m else None


async def _get_current_pg_id() -> Optional[str]:
    async with SessionLocal() as s:
        res = await s.execute(select(Setting).where(Setting.key == SETTING_PG_ID))
        row = res.scalar_one_or_none()
    if row:
        return row.value
    return _extract_pg_host_id(settings.database_url)


async def _set_current_pg_id(pg_id: str) -> None:
    async with SessionLocal() as s:
        res = await s.execute(select(Setting).where(Setting.key == SETTING_PG_ID))
        row = res.scalar_one_or_none()
        if row is None:
            s.add(Setting(key=SETTING_PG_ID, value=pg_id))
        else:
            row.value = pg_id
        await s.commit()


async def _maybe_get_expiry(pg_id: str) -> Optional[datetime]:
    rc = RenderClient()
    if not rc.enabled:
        return None
    try:
        info = await rc.get_postgres(pg_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_postgres %s: %s", pg_id, exc)
        return None
    expires = info.get("expiresAt")
    if not expires:
        return None
    try:
        return datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except Exception:
        return None


async def _notify_active(
    bot, message: str, *, only_user_ids: Optional[set[int]] = None
) -> int:
    """Notify everyone with at least one active subscription."""
    sent = 0
    async with SessionLocal() as s:
        res = await s.execute(
            select(Subscription.user_id)
            .where(Subscription.expires_at > now_utc())
            .distinct()
        )
        ids = {r for (r,) in res.all()}
    if only_user_ids:
        ids &= only_user_ids
    for uid in ids:
        try:
            await bot.send_message(uid, message)
            sent += 1
            await asyncio.sleep(0.04)
        except Exception as exc:  # noqa: BLE001
            logger.debug("notify %s: %s", uid, exc)
    return sent


async def _copy_data(src_url: str, dst_url: str) -> None:
    """Bulk-copy every row from each ORM-mapped table from src to dst.

    Schema in `dst` must already exist (we create it via SQLAlchemy first).
    """
    src = await asyncpg.connect(src_url)
    dst = await asyncpg.connect(dst_url)
    try:
        # Order tables by FK dependencies so referential integrity holds.
        sorted_tables = list(Base.metadata.sorted_tables)
        async with dst.transaction():
            # Defer all FK constraints inside the transaction.
            await dst.execute("SET CONSTRAINTS ALL DEFERRED")
            for table in sorted_tables:
                cols = [c.name for c in table.columns]
                rows = await src.fetch(
                    f'SELECT {", ".join(cols)} FROM "{table.name}"'
                )
                if not rows:
                    continue
                # Truncate first to avoid PK collisions on rerun.
                await dst.execute(f'TRUNCATE TABLE "{table.name}" CASCADE')
                values = [tuple(r[c] for c in cols) for r in rows]
                await dst.copy_records_to_table(
                    table.name, records=values, columns=cols
                )
                # Bump sequences.
                for col in table.columns:
                    if col.autoincrement and col.primary_key:
                        await dst.execute(
                            f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', '{col.name}'), "
                            f"COALESCE((SELECT MAX(\"{col.name}\") FROM \"{table.name}\"), 1), true)"
                        )
    finally:
        await src.close()
        await dst.close()


async def _create_schema(dst_url: str) -> None:
    """Create all tables in the destination DB."""
    from sqlalchemy.ext.asyncio import create_async_engine

    asyncpg_url = re.sub(r"^postgresql://", "postgresql+asyncpg://", dst_url)
    eng = create_async_engine(asyncpg_url, echo=False)
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await eng.dispose()


async def rotate_now(bot, *, force: bool = False) -> dict:
    """Run a full rotation. Returns a dict with status info."""
    rc = RenderClient()
    if not rc.enabled:
        return {"ok": False, "reason": "Render API not configured"}
    if not settings.render_service_id_self:
        return {"ok": False, "reason": "RENDER_SERVICE_ID env not set"}

    old_pg_id = await _get_current_pg_id()
    if not old_pg_id:
        return {"ok": False, "reason": "cannot infer current PG id from DATABASE_URL"}

    # Step 1 — create a new PG.
    logger.info("[db_rotation] creating new postgres…")
    new_pg = await rc.create_postgres(
        name=f"mi-host-db-{int(now_utc().timestamp())}",
        plan="free",
        region="frankfurt",
        database_name="mihost",
        database_user="mihost",
    )
    new_pg_id = new_pg.get("id")
    if not new_pg_id:
        return {"ok": False, "reason": f"create_postgres response: {new_pg}"}

    # Step 2 — wait for it to be available.
    logger.info("[db_rotation] waiting for %s to be available…", new_pg_id)
    ok = await rc.wait_for_postgres_available(new_pg_id, timeout_seconds=600)
    if not ok:
        return {"ok": False, "reason": "new PG never became available"}

    info = await rc.get_postgres_connection_info(new_pg_id)
    new_url = info.get("externalConnectionString") or info.get("connectionString")
    if not new_url:
        return {"ok": False, "reason": f"no connection string for {new_pg_id}"}

    # Step 3 — notify users that maintenance is starting.
    await _notify_active(
        bot,
        "<b>Обновление серверов</b>\n\n"
        "Месяц хоста прошёл, переносим вас на новый сервер. "
        "Это займёт несколько минут — инстансы могут быть недоступны. "
        "Как только закончим, я напишу.",
    )

    # Step 4 — copy data via asyncpg.
    src_url = _normalize_asyncpg(settings.database_url)
    dst_url = _normalize_asyncpg(new_url)
    try:
        logger.info("[db_rotation] creating schema in new DB…")
        await _create_schema(dst_url)
        logger.info("[db_rotation] copying data…")
        await _copy_data(src_url, dst_url)
    except Exception as exc:  # noqa: BLE001
        logger.exception("db copy failed")
        await _notify_active(
            bot,
            "Сбой обновления серверов. Попробуем снова автоматически. "
            "Извините за неудобства.",
        )
        return {"ok": False, "reason": f"copy failed: {exc}"}

    # Step 5 — flip DATABASE_URL on the Render service.
    asyncpg_url = re.sub(r"^postgresql://", "postgresql+asyncpg://", new_url)
    logger.info("[db_rotation] updating DATABASE_URL on service…")
    await rc.update_env_var(
        settings.render_service_id_self, "DATABASE_URL", asyncpg_url
    )

    # Step 6 — record the new id BEFORE we restart.
    await _set_current_pg_id(new_pg_id)
    async with SessionLocal() as s:
        s.add(Setting(key=SETTING_LAST_ROTATED, value=now_utc().isoformat()))
        await s.commit()

    # Step 7 — trigger redeploy. The container will restart with the new env.
    logger.info("[db_rotation] triggering deploy…")
    try:
        await rc.trigger_deploy(settings.render_service_id_self)
    except Exception as exc:  # noqa: BLE001
        logger.warning("trigger_deploy: %s", exc)

    # Step 8 — schedule "готово" once the service is live again. We can't await
    # past the redeploy (the container will be killed), so instead persist a
    # flag and the *new* boot will read it and notify everyone.
    async with SessionLocal() as s:
        s.add(Setting(key="db_rotation:announce_done", value="1"))
        await s.commit()

    return {"ok": True, "new_pg_id": new_pg_id, "expected_url": asyncpg_url}


async def maybe_rotate(bot) -> None:
    """Called by the scheduler — kicks off rotation if the current PG is near expiry."""
    pg_id = await _get_current_pg_id()
    if not pg_id:
        return
    expiry = await _maybe_get_expiry(pg_id)
    if not expiry:
        return
    if expiry - datetime.now(timezone.utc) > timedelta(days=ROTATION_LEAD_DAYS):
        return
    logger.warning(
        "[db_rotation] %s expires at %s — starting rotation", pg_id, expiry
    )
    await rotate_now(bot)


async def announce_done_if_pending(bot) -> None:
    """On bot startup, if rotation just completed, message active users."""
    async with SessionLocal() as s:
        res = await s.execute(
            select(Setting).where(Setting.key == "db_rotation:announce_done")
        )
        row = res.scalar_one_or_none()
        if row is None:
            return
        await s.delete(row)
        await s.commit()
    await _notify_active(
        bot,
        "<b>Готово</b>\n\n"
        "Серверы обновлены, ваш инстанс снова работает. Спасибо за терпение.",
    )
