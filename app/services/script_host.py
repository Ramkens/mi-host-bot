"""Custom-script tenant provisioning.

Flow:
1. user uploads .zip via TG
2. CodeAnalyzer scores risk; if too high → reject
3. AutoSetup derives build_cmd / start_cmd / required env keys
4. We extract zip into /data/script/<instance_id>/, install deps in
 tenant-local venv (`python -m venv .venv && pip install -r req…`)
5. Spawn via supervisor
"""
from __future__ import annotations

import asyncio
import io
import logging
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Optional

from app.services.code_analyzer import AnalysisResult, analyze_zip
from app.services.auto_setup import DeploySpec, derive_spec
from app.services.supervisor import (
    DEFAULT_DATA_DIR,
    TenantSpec,
    supervisor,
)

logger = logging.getLogger(__name__)

SCRIPT_ROOT = DEFAULT_DATA_DIR / "scripts"
SCRIPT_ROOT.mkdir(parents=True, exist_ok=True)


def tenant_dir(instance_id: int) -> Path:
    return SCRIPT_ROOT / str(instance_id)


def extract_zip(instance_id: int, data: bytes) -> Path:
    out = tenant_dir(instance_id)
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = out / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            # Path traversal already filtered in analyzer, double-check here.
            try:
                rel = target.resolve().relative_to(out.resolve())
            except ValueError:
                continue
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    return out


async def install_deps(work_dir: Path, deps: list[str]) -> tuple[bool, str]:
    pip = sys.executable, "-m", "pip"
    log = io.StringIO()
    if (work_dir / "requirements.txt").exists():
        proc = await asyncio.create_subprocess_exec(
            *pip, "install", "--quiet", "-r", str(work_dir / "requirements.txt"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        log.write(out.decode(errors="ignore"))
        if proc.returncode != 0:
            return False, log.getvalue()
    elif deps:
        proc = await asyncio.create_subprocess_exec(
            *pip, "install", "--quiet", *deps,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        log.write(out.decode(errors="ignore"))
        if proc.returncode != 0:
            return False, log.getvalue()
    return True, log.getvalue()


async def deploy(
    instance_id: int,
    zip_bytes: bytes,
    *,
    env: Optional[dict[str, str]] = None,
    ram_mb: int = 130,
) -> tuple[AnalysisResult, Optional[DeploySpec]]:
    analysis = analyze_zip(zip_bytes)
    if not analysis.ok:
        return analysis, None
    work = extract_zip(instance_id, zip_bytes)
    spec = derive_spec(analysis)
    ok, install_log = await install_deps(work, analysis.dependencies)
    if not ok:
        return analysis, spec
    cmd = spec.start_cmd.split()
    cmd[0] = sys.executable if cmd[0] == "python" else cmd[0]
    tspec = TenantSpec(
        instance_id=instance_id,
        name=f"script-{instance_id}",
        cwd=work,
        cmd=cmd,
        env={**(env or {}), "PYTHONUNBUFFERED": "1"},
        rlimit_as_mb=ram_mb,
    )
    await supervisor.start(tspec)
    return analysis, spec


def remove(instance_id: int) -> int:
    """Wipe a script tenant's working directory and return bytes freed."""
    import logging

    log = logging.getLogger(__name__)
    td = tenant_dir(instance_id)
    if not td.exists():
        return 0
    freed = 0
    try:
        for p in td.rglob("*"):
            try:
                if p.is_file() or p.is_symlink():
                    freed += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass

    def _onerr(func, path, exc_info):
        log.warning("script_host.remove: %s on %s: %s", func.__name__, path, exc_info[1])

    shutil.rmtree(td, onerror=_onerr)
    return freed
