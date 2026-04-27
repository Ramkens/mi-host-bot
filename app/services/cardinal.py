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


def _hash_password(password: str) -> str:
    """Bcrypt-hash a password the same way Cardinal's first_setup does."""
    try:
        import bcrypt  # type: ignore[import-untyped]
    except ImportError:
        return ""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _write_main_cfg(
    tenant_dir: Path,
    *,
    golden_key: str,
    user_agent: str = "",
    telegram_token: str = "",
    telegram_secret: str = "",
    locale: str = "ru",
    overrides: Optional[dict[str, dict[str, str]]] = None,
) -> None:
    """(Re)write ``configs/_main.cfg`` from the default + user overrides."""
    cfg_dir = tenant_dir / "configs"
    cfg_dir.mkdir(exist_ok=True)
    cfg_kwargs: dict[str, object] = {
        "golden_key": golden_key,
        "user_agent": user_agent,
        "telegram_token": telegram_token,
        "telegram_enabled": bool(telegram_token),
        "locale": locale,
    }
    if telegram_token and telegram_secret:
        h = _hash_password(telegram_secret)
        if h:
            cfg_kwargs["secret_key_hash"] = h
    base = default_main_cfg(**cfg_kwargs)  # type: ignore[arg-type]
    sections = merge_overrides(base, overrides)
    # Make sure golden_key/user_agent in [FunPay] always reflect the
    # latest values, even if the user-supplied override forgot them.
    sections.setdefault("FunPay", {})["golden_key"] = golden_key
    if user_agent:
        sections["FunPay"]["user_agent"] = user_agent
    if telegram_token:
        sections.setdefault("Telegram", {})["token"] = telegram_token
        sections["Telegram"]["enabled"] = "1"
        if telegram_secret:
            sections["Telegram"]["secretKeyHash"] = _hash_password(telegram_secret)
    if locale:
        sections.setdefault("FunPay", {})["locale"] = locale
        sections.setdefault("Other", {})["language"] = locale
    (cfg_dir / "_main.cfg").write_text(render_main_cfg(sections), encoding="utf-8")
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
    telegram_secret: str = "",
    locale: str = "ru",
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
        telegram_secret=telegram_secret,
        locale=locale,
        overrides=overrides,
    )
    # Mi Host shim that re-applies env-driven golden_key on every restart.
    shim = tenant_dir / "_mihost_run.py"
    shim.write_text(_SHIM_PY, encoding="utf-8")
    return tenant_dir


# Shim runs inside the tenant process. It re-injects ``GOLDEN_KEY`` into
# ``configs/_main.cfg`` (preserving every other section the user may have
# customized) and then hands control to Cardinal's ``main.py``.
_SHIM_PY = """\
'''Mi Host bootstrap for Cardinal: refresh golden_key from env, then run.'''
import codecs, os, runpy, sys
from configparser import ConfigParser
from pathlib import Path

cfg_path = Path(__file__).parent / 'configs' / '_main.cfg'
key = os.environ.get('GOLDEN_KEY', '')
ua = os.environ.get('USER_AGENT', '')
if cfg_path.exists() and key:
    cp = ConfigParser(delimiters=(':',), interpolation=None)
    cp.optionxform = str
    cp.read_file(codecs.open(str(cfg_path), 'r', 'utf8'))
    if not cp.has_section('FunPay'):
        cp.add_section('FunPay')
    cp.set('FunPay', 'golden_key', key)
    if ua:
        cp.set('FunPay', 'user_agent', ua)
    with cfg_path.open('w', encoding='utf-8') as f:
        cp.write(f, space_around_delimiters=True)

sys.argv = ['main.py']
try:
    runpy.run_path('main.py', run_name='__main__')
except SystemExit:
    raise
except Exception as exc:
    print(f'[mihost] cardinal crashed: {exc!r}')
    raise
"""


async def start_tenant(
    instance_id: int,
    *,
    golden_key: str,
    telegram_token: str = "",
    telegram_secret: str = "",
    locale: str = "ru",
    overrides: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    tenant_dir = await provision_tenant(
        instance_id,
        golden_key=golden_key,
        telegram_token=telegram_token,
        telegram_secret=telegram_secret,
        locale=locale,
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


def remove_tenant_dir(instance_id: int) -> None:
    """Wipe the tenant's working directory (used on purge / unsubscribe)."""
    tenant_dir = _tenant_dir(instance_id)
    shutil.rmtree(tenant_dir, ignore_errors=True)


def read_full_logs(instance_id: int, *, max_bytes: int = 5 * 1024 * 1024) -> bytes:
    """Read Cardinal's persistent log file (``logs/log.log``) for a tenant.

    Cardinal uses ``RotatingFileHandler`` (20 MB × 25 backups). We read up
    to ``max_bytes`` from the *tail* of the current file so admin gets the
    most recent activity without dumping multi-GB history.
    """
    log_path = _tenant_dir(instance_id) / "logs" / "log.log"
    if not log_path.exists():
        return b""
    try:
        size = log_path.stat().st_size
        with open(log_path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                # Skip partial first line.
                f.readline()
            return f.read()
    except OSError:
        return b""


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
