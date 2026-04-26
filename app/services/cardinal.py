"""FunPay Cardinal provisioning.

We clone the upstream repo on first use into a shared cache dir, and
each tenant gets its own working copy under the data dir with a tenant-
specific ``configs/_main.cfg``. Mi Host then runs the Cardinal entry
point as a subprocess via the Supervisor.

Upstream: https://github.com/sidor0912/FunPayCardinal

Why we pre-generate ``_main.cfg`` ourselves: Cardinal's ``main.py``
does ``if not os.path.exists("configs/_main.cfg"): first_setup()``,
and ``first_setup()`` is interactive (``input()`` prompts for proxy,
golden_key, etc.) — that hangs forever inside a managed subprocess.
So we hand it a fully-valid config built by ``cardinal_config.py``.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

from app.services.cardinal_config import (
    default_main_cfg,
    merge_overrides,
    render_main_cfg,
)
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
    # Cardinal's runtime deps (psutil/lxml/bcrypt/...) are pinned in
    # mi-host-bot's own requirements.txt now, so they're already in the
    # venv at this point. We only need a `pip install` fallback if a
    # future Cardinal commit adds something new.
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


def _tenant_dir(instance_id: int) -> Path:
    return DEFAULT_DATA_DIR / "cardinal" / str(instance_id)


def _write_main_cfg(
    tenant_dir: Path,
    *,
    golden_key: str,
    user_agent: str = "",
    telegram_token: str = "",
    secret_key_hash: Optional[str] = None,
    proxy: str = "",
    overrides: Optional[dict[str, dict[str, str]]] = None,
) -> None:
    """(Re)write ``configs/_main.cfg`` from the default + user overrides."""
    cfg_dir = tenant_dir / "configs"
    cfg_dir.mkdir(exist_ok=True)
    kwargs: dict[str, Any] = {
        "golden_key": golden_key,
        "user_agent": user_agent,
        "telegram_token": telegram_token,
        "telegram_enabled": bool(telegram_token),
        "proxy": proxy,
    }
    if secret_key_hash:
        kwargs["secret_key_hash"] = secret_key_hash
    base = default_main_cfg(**kwargs)
    sections = merge_overrides(base, overrides)
    # Ensure live values survive user-supplied overrides that may have
    # forgotten them.
    sections.setdefault("FunPay", {})["golden_key"] = golden_key
    if user_agent:
        sections["FunPay"]["user_agent"] = user_agent
    if telegram_token:
        sections.setdefault("Telegram", {})
        sections["Telegram"]["token"] = telegram_token
        sections["Telegram"]["enabled"] = "1"
        if secret_key_hash:
            sections["Telegram"]["secretKeyHash"] = secret_key_hash
    if proxy:
        sections.setdefault("Proxy", {})
        sections["Proxy"]["proxy"] = proxy
        sections["Proxy"]["enable"] = "1"
    (cfg_dir / "_main.cfg").write_text(render_main_cfg(sections), encoding="utf-8")
    logger.info(
        "_write_main_cfg: tenant=%s gk_len=%d tok_len=%d hash_set=%s proxy_set=%s",
        tenant_dir.name, len(golden_key or ""), len(telegram_token or ""),
        bool(secret_key_hash), bool(proxy),
    )
    # Cardinal also expects two empty optional configs; create if missing.
    for fname in ("auto_response.cfg", "auto_delivery.cfg"):
        p = cfg_dir / fname
        if not p.exists():
            p.write_text("", encoding="utf-8")


async def provision_tenant(
    instance_id: int,
    *,
    golden_key: str,
    user_agent: Optional[str] = None,
    telegram_token: str = "",
    secret_key_hash: Optional[str] = None,
    proxy: str = "",
    overrides: Optional[dict[str, dict[str, str]]] = None,
) -> Path:
    cache = await ensure_cardinal_cache()
    tenant_dir = _tenant_dir(instance_id)
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
    _write_main_cfg(
        tenant_dir,
        golden_key=golden_key,
        user_agent=user_agent or "",
        telegram_token=telegram_token,
        secret_key_hash=secret_key_hash,
        proxy=proxy,
        overrides=overrides,
    )
    # Mi Host shim that re-applies env-driven golden_key on every restart.
    shim = tenant_dir / "_mihost_run.py"
    shim.write_text(_SHIM_PY, encoding="utf-8")
    return tenant_dir


# Shim runs inside the tenant process. It re-injects ``GOLDEN_KEY`` into
# ``configs/_main.cfg`` (preserving every other section the user may have
# customized) and then hands control to Cardinal's ``main.py``.
_SHIM_PY = (
    "'''Mi Host bootstrap for Cardinal: refresh golden_key from env, then run.'''\n"
    "import codecs, os, runpy, sys\n"
    "from configparser import ConfigParser\n"
    "from pathlib import Path\n"
    "\n"
    "cfg_path = Path(__file__).parent / 'configs' / '_main.cfg'\n"
    "key = os.environ.get('GOLDEN_KEY', '')\n"
    "ua = os.environ.get('USER_AGENT', '')\n"
    "if cfg_path.exists() and key:\n"
    "    cp = ConfigParser(delimiters=(':',), interpolation=None)\n"
    "    cp.optionxform = str\n"
    "    cp.read_file(codecs.open(str(cfg_path), 'r', 'utf8'))\n"
    "    if not cp.has_section('FunPay'):\n"
    "        cp.add_section('FunPay')\n"
    "    cp.set('FunPay', 'golden_key', key)\n"
    "    if ua:\n"
    "        cp.set('FunPay', 'user_agent', ua)\n"
    "    with cfg_path.open('w', encoding='utf-8') as f:\n"
    "        cp.write(f, space_around_delimiters=True)\n"
    "\n"
    "sys.argv = ['main.py']\n"
    "try:\n"
    "    runpy.run_path('main.py', run_name='__main__')\n"
    "except SystemExit:\n"
    "    raise\n"
    "except Exception as exc:\n"
    "    print(f'[mihost] cardinal crashed: {exc!r}')\n"
    "    raise\n"
)


async def start_tenant(
    instance_id: int,
    *,
    golden_key: str,
    telegram_token: str = "",
    secret_key_hash: Optional[str] = None,
    proxy: str = "",
    overrides: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    tenant_dir = await provision_tenant(
        instance_id,
        golden_key=golden_key,
        telegram_token=telegram_token,
        secret_key_hash=secret_key_hash,
        proxy=proxy,
        overrides=overrides,
    )
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
    """Update the key (preserving other config) and force a restart."""
    tenant_dir = _tenant_dir(instance_id)
    if not tenant_dir.exists():
        await provision_tenant(instance_id, golden_key=golden_key)
        await start_tenant(instance_id, golden_key=golden_key)
        return
    # Read existing config so we don't blow away user customizations.
    overrides = read_main_cfg(instance_id)
    _write_main_cfg(tenant_dir, golden_key=golden_key, overrides=overrides)
    state = supervisor.tenants.get(instance_id)
    if state:
        state.spec.env["GOLDEN_KEY"] = golden_key
    await supervisor.restart(instance_id)


def read_main_cfg(instance_id: int) -> dict[str, dict[str, str]]:
    """Return the tenant's current ``_main.cfg`` as a section dict."""
    cfg_path = _tenant_dir(instance_id) / "configs" / "_main.cfg"
    if not cfg_path.exists():
        return {}
    from configparser import ConfigParser
    import codecs
    cp = ConfigParser(delimiters=(":",), interpolation=None)
    cp.optionxform = str  # type: ignore[assignment]
    cp.read_file(codecs.open(str(cfg_path), "r", "utf8"))
    return {sect: dict(cp[sect]) for sect in cp.sections()}


async def write_user_main_cfg(instance_id: int, raw: str) -> tuple[bool, str]:
    """Replace the tenant's ``_main.cfg`` with raw user-uploaded content.

 Returns (ok, message). Validates by parsing through ConfigParser; we
 don't run Cardinal's full ``load_main_config`` because that requires
 bcrypt-hashed secretKeyHash and other fields the user may legitimately
 want to defer.
 """
    import codecs
    import io
    from configparser import ConfigParser, Error as ConfigParserError

    cp = ConfigParser(delimiters=(":",), interpolation=None)
    cp.optionxform = str  # type: ignore[assignment]
    try:
        cp.read_file(io.StringIO(raw))
    except ConfigParserError as exc:
        return False, f"INI parse error: {exc}"

    tenant_dir = _tenant_dir(instance_id)
    if not tenant_dir.exists():
        return False, "Tenant not provisioned yet."

    cfg_dir = tenant_dir / "configs"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "_main.cfg").write_text(raw, encoding="utf-8")
    # Reload via the same path Cardinal uses to make sure it round-trips.
    try:
        cp2 = ConfigParser(delimiters=(":",), interpolation=None)
        cp2.optionxform = str  # type: ignore[assignment]
        cp2.read_file(codecs.open(str(cfg_dir / "_main.cfg"), "r", "utf8"))
    except ConfigParserError as exc:
        return False, f"Roundtrip parse error: {exc}"
    await supervisor.restart(instance_id)
    return True, f"_main.cfg updated ({len(cp2.sections())} sections)."


def remove_tenant_dir(instance_id: int) -> int:
    """Wipe the tenant's working directory and return the number of bytes freed.

    Errors during rmtree are logged (not silently swallowed) so a stuck
    file descriptor or permission problem actually surfaces in our logs.
    """
    tenant_dir = _tenant_dir(instance_id)
    if not tenant_dir.exists():
        return 0
    freed = 0
    try:
        for p in tenant_dir.rglob("*"):
            try:
                if p.is_file() or p.is_symlink():
                    freed += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass

    def _onerr(func, path, exc_info):
        logger.warning("remove_tenant_dir: %s on %s: %s", func.__name__, path, exc_info[1])

    shutil.rmtree(tenant_dir, onerror=_onerr)
    return freed


async def write_user_aux_cfg(
    instance_id: int, filename: str, raw: str
) -> tuple[bool, str]:
    """Write ``configs/auto_response.cfg`` or ``configs/auto_delivery.cfg``."""
    if filename not in {"auto_response.cfg", "auto_delivery.cfg"}:
        return False, f"Unsupported config: {filename}"
    tenant_dir = _tenant_dir(instance_id)
    if not tenant_dir.exists():
        return False, "Tenant not provisioned yet."
    (tenant_dir / "configs" / filename).write_text(raw, encoding="utf-8")
    await supervisor.restart(instance_id)
    return True, f"{filename} updated."
