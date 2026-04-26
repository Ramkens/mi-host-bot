"""Render Cloud API client (subset used by Mi Host).

Docs: https://api-docs.render.com/reference/

Used for:
* deploying user instances (Cardinal hosting / custom scripts)
* monitoring & restart
* listing/deleting old services
* updating env vars (golden_key etc.)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API = "https://api.render.com/v1"


class RenderError(RuntimeError):
    pass


class RenderClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        owner_id: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or settings.render_api_key
        self.owner_id = owner_id or settings.render_owner_id
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _req(
        self, method: str, path: str, *, json: Any = None, params: Any = None
    ) -> Any:
        if not self.enabled:
            raise RenderError("Render API key not configured")
        url = f"{API}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.request(
                method, url, headers=self._headers(), json=json, params=params
            )
        if r.status_code >= 400:
            raise RenderError(f"{method} {path} -> {r.status_code} {r.text[:300]}")
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

    # ---- Owners ----

    async def get_owners(self) -> list[dict]:
        data = await self._req("GET", "/owners")
        # API returns [{"owner": {...}}, ...]
        return [item.get("owner", item) for item in (data or [])]

    async def autodetect_owner(self) -> Optional[str]:
        if self.owner_id:
            return self.owner_id
        owners = await self.get_owners()
        if owners:
            self.owner_id = owners[0]["id"]
            return self.owner_id
        return None

    # ---- Services ----

    async def list_services(self, limit: int = 100) -> list[dict]:
        data = await self._req("GET", "/services", params={"limit": limit})
        return [item.get("service", item) for item in (data or [])]

    async def delete_service(self, service_id: str) -> None:
        await self._req("DELETE", f"/services/{service_id}")

    async def create_web_service(
        self,
        *,
        name: str,
        repo: str,
        branch: str = "main",
        runtime: str = "python",
        build_cmd: str = "pip install -r requirements.txt",
        start_cmd: str = "python -m app.main",
        env_vars: Optional[dict[str, str]] = None,
        plan: Optional[str] = None,
        region: Optional[str] = None,
        health_check_path: Optional[str] = "/healthz",
        auto_deploy: bool = True,
    ) -> dict:
        owner_id = await self.autodetect_owner()
        if not owner_id:
            raise RenderError("No Render ownerId available")
        env_list = [
            {"key": k, "value": v} for k, v in (env_vars or {}).items()
        ]
        body = {
            "type": "web_service",
            "name": name,
            "ownerId": owner_id,
            "repo": repo,
            "branch": branch,
            "autoDeploy": "yes" if auto_deploy else "no",
            "envVars": env_list,
            "serviceDetails": {
                "env": runtime,
                "plan": plan or settings.render_plan,
                "region": region or settings.render_region,
                "healthCheckPath": health_check_path or "",
                "envSpecificDetails": {
                    "buildCommand": build_cmd,
                    "startCommand": start_cmd,
                },
            },
        }
        data = await self._req("POST", "/services", json=body)
        if isinstance(data, dict) and "service" in data:
            return data["service"]
        return data  # type: ignore[return-value]

    async def create_background_worker(
        self,
        *,
        name: str,
        repo: str,
        branch: str = "main",
        runtime: str = "python",
        build_cmd: str = "pip install -r requirements.txt",
        start_cmd: str = "python main.py",
        env_vars: Optional[dict[str, str]] = None,
        plan: Optional[str] = None,
        region: Optional[str] = None,
    ) -> dict:
        owner_id = await self.autodetect_owner()
        if not owner_id:
            raise RenderError("No Render ownerId available")
        env_list = [
            {"key": k, "value": v} for k, v in (env_vars or {}).items()
        ]
        body = {
            "type": "background_worker",
            "name": name,
            "ownerId": owner_id,
            "repo": repo,
            "branch": branch,
            "autoDeploy": "yes",
            "envVars": env_list,
            "serviceDetails": {
                "env": runtime,
                "plan": plan or settings.render_plan,
                "region": region or settings.render_region,
                "envSpecificDetails": {
                    "buildCommand": build_cmd,
                    "startCommand": start_cmd,
                },
            },
        }
        data = await self._req("POST", "/services", json=body)
        if isinstance(data, dict) and "service" in data:
            return data["service"]
        return data  # type: ignore[return-value]

    async def trigger_deploy(self, service_id: str, *, clear_cache: bool = False) -> dict:
        body = {"clearCache": "clear" if clear_cache else "do_not_clear"}
        return await self._req("POST", f"/services/{service_id}/deploys", json=body)

    async def list_deploys(self, service_id: str, limit: int = 5) -> list[dict]:
        data = await self._req(
            "GET", f"/services/{service_id}/deploys", params={"limit": limit}
        )
        return [item.get("deploy", item) for item in (data or [])]

    async def restart(self, service_id: str) -> None:
        await self._req("POST", f"/services/{service_id}/restart")

    async def suspend(self, service_id: str) -> None:
        await self._req("POST", f"/services/{service_id}/suspend")

    async def resume(self, service_id: str) -> None:
        await self._req("POST", f"/services/{service_id}/resume")

    async def get_service(self, service_id: str) -> dict:
        data = await self._req("GET", f"/services/{service_id}")
        if isinstance(data, dict) and "service" in data:
            return data["service"]
        return data  # type: ignore[return-value]

    async def update_env_vars(
        self, service_id: str, env_vars: dict[str, str]
    ) -> None:
        body = [{"key": k, "value": v} for k, v in env_vars.items()]
        await self._req("PUT", f"/services/{service_id}/env-vars", json=body)

    # ---- Postgres ----

    async def create_postgres(
        self,
        *,
        name: str,
        plan: str = "free",
        region: Optional[str] = None,
        version: int = 16,
        database_name: str = "mihost",
        database_user: str = "mihost",
    ) -> dict:
        owner_id = await self.autodetect_owner()
        if not owner_id:
            raise RenderError("No Render ownerId available")
        body = {
            "ownerId": owner_id,
            "name": name,
            "plan": plan,
            "region": region or settings.render_region,
            "version": str(version),
            "databaseName": database_name,
            "databaseUser": database_user,
        }
        return await self._req("POST", "/postgres", json=body)

    async def list_postgres(self) -> list[dict]:
        data = await self._req("GET", "/postgres")
        return [item.get("postgres", item) for item in (data or [])]

    async def get_postgres(self, db_id: str) -> dict:
        return await self._req("GET", f"/postgres/{db_id}")

    async def get_postgres_connection_info(self, db_id: str) -> dict:
        return await self._req("GET", f"/postgres/{db_id}/connection-info")

    async def wait_for_postgres_available(
        self, db_id: str, timeout_seconds: int = 600, interval: float = 8.0
    ) -> bool:
        elapsed = 0.0
        while elapsed < timeout_seconds:
            try:
                info = await self.get_postgres(db_id)
                if info.get("status") == "available":
                    return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("wait_for_postgres_available: %s", exc)
            await asyncio.sleep(interval)
            elapsed += interval
        return False

    async def update_env_var(
        self, service_id: str, key: str, value: str
    ) -> None:
        """Upsert a single env var on a service."""
        # Render API doesn't expose "patch single var" — fetch all, update, PUT.
        existing = await self._req(
            "GET", f"/services/{service_id}/env-vars"
        )
        env: dict[str, str] = {}
        for item in (existing or []):
            ev = item.get("envVar", item)
            if ev.get("key"):
                env[ev["key"]] = ev.get("value", "")
        env[key] = value
        body = [{"key": k, "value": v} for k, v in env.items()]
        await self._req("PUT", f"/services/{service_id}/env-vars", json=body)

    # ---- Convenience ----

    async def wait_for_live(
        self, service_id: str, timeout: float = 600.0, interval: float = 10.0
    ) -> bool:
        elapsed = 0.0
        while elapsed < timeout:
            try:
                deploys = await self.list_deploys(service_id, limit=1)
                if deploys:
                    status = deploys[0].get("status")
                    if status == "live":
                        return True
                    if status in {"build_failed", "update_failed", "canceled"}:
                        return False
            except Exception as exc:  # noqa: BLE001
                logger.warning("wait_for_live: %s", exc)
            await asyncio.sleep(interval)
            elapsed += interval
        return False
