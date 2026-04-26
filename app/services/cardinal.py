"""FunPay Cardinal provisioning.

We clone the upstream repo on first use into a shared cache dir, and
each tenant gets its own working copy under the data dir with a tenant-
specific config.cfg & golden_key. Mi Host then runs the Cardinal entry
point as a subprocess via the Supervisor.

Upstream: https://github.com/sidor0912/FunPayCardinal
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from app.services.supervisor import (
    DEFAULT_DATA_DIR,
    TenantSpec,
    supervisor,
)

logger = logging.getLogger(__name__)

CARDINAL_REPO = "https://github.com/sidor0912/FunPayCardinal.git"
CARDINAL_CACHE = DEFAULT_DATA_DIR / "_cache" / "FunPayCardinal"


async def ensure_cardinal_cache() -> Path:
    if CARDINAL_CACHE.exists() and (CARDINAL_CACHE / "main.py").exists():
        return CARDINAL_CACHE
    CARDINAL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if CARDINAL_CACHE.exists():
        shutil.rmtree(CARDINAL_CACHE, ignore_errors=True)
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", CARDINAL_REPO, str(CARDINAL_CACHE),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed: {out.decode(errors='ignore')[:300]}")
    # Optional: install requirements into a per-tenant venv would be safer,
    # but on free tier we just install into the main venv at first deploy.
    req = CARDINAL_CACHE / "requirements.txt"
    if req.exists():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--quiet", "-r", str(req),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "Cardinal pip install warnings: %s",
                out.decode(errors="ignore")[:300],
            )
    return CARDINAL_CACHE


async def provision_tenant(
    instance_id: int, *, golden_key: str, user_agent: Optional[str] = None
) -> Path:
    cache = await ensure_cardinal_cache()
    tenant_dir = DEFAULT_DATA_DIR / "cardinal" / str(instance_id)
    if not tenant_dir.exists():
        tenant_dir.mkdir(parents=True, exist_ok=True)
        # copy minimal fileset (avoid duplicating .git)
        for child in cache.iterdir():
            if child.name in {".git", "__pycache__"}:
                continue
            target = tenant_dir / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
    # Write/update _config/auth.cfg or similar — Cardinal reads multiple
    # config files; we override the most likely path used by current
    # versions. The fallback is the env var GOLDEN_KEY which Mi Host's
    # bootstrap (run.py shim, see below) injects.
    cfg_dir = tenant_dir / "configs"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "auth.cfg").write_text(
        f"[FunPay]\ngolden_key = {golden_key}\nuser_agent = {user_agent or ''}\n",
        encoding="utf-8",
    )
    # Mi Host shim that ensures env-driven golden_key always wins.
    shim = tenant_dir / "_mihost_run.py"
    shim.write_text(_SHIM_PY, encoding="utf-8")
    return tenant_dir


_SHIM_PY = """\
'''Mi Host bootstrap for Cardinal: inject golden_key from env if present.'''
import os, sys, runpy
from pathlib import Path

key = os.environ.get('GOLDEN_KEY')
ua = os.environ.get('USER_AGENT', '')
if key:
    cfg = Path(__file__).parent / 'configs' / 'auth.cfg'
    cfg.write_text(f'[FunPay]\\ngolden_key = {key}\\nuser_agent = {ua}\\n', encoding='utf-8')
sys.argv = ['main.py']
try:
    runpy.run_path('main.py', run_name='__main__')
except SystemExit:
    raise
except Exception as exc:
    print(f'[mihost] cardinal crashed: {exc!r}')
    raise
"""


async def start_tenant(instance_id: int, *, golden_key: str) -> dict:
    tenant_dir = await provision_tenant(instance_id, golden_key=golden_key)
    spec = TenantSpec(
        instance_id=instance_id,
        name=f"cardinal-{instance_id}",
        cwd=tenant_dir,
        cmd=[sys.executable, "_mihost_run.py"],
        env={"GOLDEN_KEY": golden_key, "PYTHONUNBUFFERED": "1"},
    )
    await supervisor.start(spec)
    return supervisor.status(instance_id)


async def update_golden_key(instance_id: int, golden_key: str) -> None:
    """Update the key and force a restart so Cardinal re-reads it."""
    tenant_dir = DEFAULT_DATA_DIR / "cardinal" / str(instance_id)
    if not tenant_dir.exists():
        await provision_tenant(instance_id, golden_key=golden_key)
        await start_tenant(instance_id, golden_key=golden_key)
        return
    (tenant_dir / "configs" / "auth.cfg").write_text(
        f"[FunPay]\ngolden_key = {golden_key}\nuser_agent = \n",
        encoding="utf-8",
    )
    state = supervisor.tenants.get(instance_id)
    if state:
        state.spec.env["GOLDEN_KEY"] = golden_key
    await supervisor.restart(instance_id)


def remove_tenant_dir(instance_id: int) -> None:
    tenant_dir = DEFAULT_DATA_DIR / "cardinal" / str(instance_id)
    shutil.rmtree(tenant_dir, ignore_errors=True)
