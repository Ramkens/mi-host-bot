"""Provisioning new shards (Render web services) via the shard's API key."""
from __future__ import annotations

import logging

from app.config import settings
from app.repos import shards as shards_repo
from app.services.render_api import RenderClient

logger = logging.getLogger(__name__)


GITHUB_REPO_URL = "https://github.com/Ramkens/mi-host-bot"


async def _master_external_db_url() -> str:
    """Fetch master's Postgres EXTERNAL connection string.

    Workers live on different Render accounts, so the internal short
    hostname (e.g. ``dpg-...-a``) won't resolve for them. We need the
    full FQDN form (``dpg-...-a.<region>-postgres.render.com``) with
    SSL enabled.
    """
    rc_master = RenderClient(api_key=settings.render_api_key)
    dbs = await rc_master.list_postgres()
    if not dbs:
        raise RuntimeError("no postgres on master account")
    db_id = dbs[0].get("id")
    info = await rc_master._req("GET", f"/postgres/{db_id}/connection-info")
    ext = info.get("externalConnectionString") or ""
    # Normalize to asyncpg form.
    if ext.startswith("postgresql://"):
        ext = ext.replace("postgresql://", "postgresql+asyncpg://", 1)
    # asyncpg uses `ssl=require` style; Render returns `?ssl=true` which
    # asyncpg also accepts. Leave as-is if present.
    return ext


def _master_branch() -> str:
    """Whatever branch the master is currently deployed from.

    Render injects RENDER_GIT_BRANCH at runtime; if absent (local dev)
    fall back to ``main``. Workers must pull from the same branch so
    they import the same code as master — a worker built from `main`
    while master runs a feature branch will speak a different DB
    schema and SHIM contract.
    """
    import os

    return (os.environ.get("RENDER_GIT_BRANCH") or "main").strip() or "main"


async def provision_worker(
    session, shard_id: int, *, repo_url: str = GITHUB_REPO_URL
) -> dict:
    """Deploy a new web service on the shard's Render account.

 The new service runs with MIHOST_ROLE=worker and shares this master's
 DATABASE_URL so it can read instance assignments and write heartbeat.
 """
    api_key = await shards_repo.get_api_key(session, shard_id)
    shard = await shards_repo.by_id(session, shard_id)
    if not api_key or not shard:
        return {"ok": False, "reason": "shard not found"}

    rc = RenderClient(api_key=api_key, owner_id=shard.owner_id)
    # Auto-detect owner if missing.
    owner_id = await rc.autodetect_owner()
    if not owner_id:
        return {"ok": False, "reason": "could not autodetect owner on shard's account"}
    if shard.owner_id != owner_id:
        await shards_repo.update_service_meta(session, shard_id, owner_id=owner_id)

    try:
        master_db_url = await _master_external_db_url()
    except Exception as exc:  # noqa: BLE001
        logger.exception("could not resolve master external DB URL")
        return {"ok": False, "reason": f"master DB URL: {exc}"}

    env_vars = {
        "MIHOST_ROLE": "worker",
        "MIHOST_SHARD_NAME": shard.name,
        # Pin Python so dependencies (pydantic-core, etc.) get prebuilt wheels;
        # default Render runtime moved to 3.14 which lacks some wheels.
        "PYTHON_VERSION": "3.11.9",
        # Workers share the master's data store. SECRET_KEY must match so
        # they can decrypt shard rows (they don't, but other secrets too).
        "DATABASE_URL": master_db_url,
        "SECRET_KEY": settings.secret_key,
        # No bot token / webhook on workers — they're headless.
        "BOT_TOKEN": "",
        "PUBLIC_URL": "",
        "WEBHOOK_SECRET": "",
        "ADMIN_IDS": settings.admin_ids,
        "MIHOST_DATA_DIR": "/tmp/mihost",
        "TZ": settings.tz,
    }

    try:
        service = await rc.create_web_service(
            name=f"mi-host-{shard.name}",
            repo=repo_url,
            branch=_master_branch(),
            runtime="python",
            build_cmd="pip install --upgrade pip && pip install -r requirements.txt",
            start_cmd="python -m app.main",
            env_vars=env_vars,
            plan="free",
            region=shard.region,
            health_check_path="/healthz",
            auto_deploy=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("create worker service failed")
        return {"ok": False, "reason": str(exc)}

    service_id = service.get("id")
    service_url = service.get("serviceDetails", {}).get("url") or service.get("url")
    await shards_repo.update_service_meta(
        session,
        shard_id,
        service_id=service_id,
        service_url=service_url,
    )
    return {"ok": True, "service_id": service_id, "service_url": service_url}
