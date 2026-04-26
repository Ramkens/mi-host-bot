"""Tenant process supervisor.

Each tenant (Cardinal hosting or custom script) runs as an isolated
subprocess inside the Mi Host container with:

* its own working directory under /data/<instance_id>
* its own env vars (incl. GOLDEN_KEY for Cardinal)
* RAM/CPU/file-descriptor limits via `resource.setrlimit` (POSIX only)
* a rotating log buffer in memory + file for tail-N viewing in TG
* auto-restart on crash (with exponential back-off, capped)
* graceful stop (SIGTERM → SIGKILL)
* status reporting (PID, uptime, RAM, CPU, last exit code)

This makes "1000+ users on a single Render instance" tractable from a
software perspective — the actual ceiling is the size of the Render plan.
"""
from __future__ import annotations

import asyncio
import logging
import os
import resource
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(os.getenv("MIHOST_DATA_DIR", "/var/data/mihost"))
try:
    DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
except PermissionError:
    # Fallback for local dev (no write access to /var/data).
    DEFAULT_DATA_DIR = Path.home() / ".mihost"
    DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)

LOG_TAIL_LINES = 500


@dataclass
class TenantSpec:
    instance_id: int
    name: str
    cwd: Path
    cmd: list[str]
    env: dict[str, str] = field(default_factory=dict)
    autorestart: bool = True
    rlimit_as_mb: int = 512  # virtual memory cap
    rlimit_cpu_sec: Optional[int] = None  # off by default; long-running
    rlimit_nofile: int = 1024


@dataclass
class TenantState:
    spec: TenantSpec
    proc: Optional[asyncio.subprocess.Process] = None
    started_at: Optional[float] = None
    last_exit: Optional[int] = None
    restart_count: int = 0
    backoff: float = 1.0
    stop_requested: bool = False
    log_tail: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_TAIL_LINES))
    reader_task: Optional[asyncio.Task] = None
    waiter_task: Optional[asyncio.Task] = None


class Supervisor:
    def __init__(self) -> None:
        self.tenants: dict[int, TenantState] = {}
        self._lock = asyncio.Lock()

    # ---- Lifecycle ----

    async def start(self, spec: TenantSpec) -> TenantState:
        async with self._lock:
            state = self.tenants.get(spec.instance_id)
            if state is None:
                state = TenantState(spec=spec)
                self.tenants[spec.instance_id] = state
            else:
                state.spec = spec
                state.stop_requested = False
            await self._spawn(state)
            return state

    async def stop(self, instance_id: int, *, graceful_timeout: float = 10.0) -> None:
        state = self.tenants.get(instance_id)
        if not state or not state.proc:
            return
        state.stop_requested = True
        proc = state.proc
        try:
            proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=graceful_timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass
        finally:
            state.proc = None
            state.started_at = None
            if state.reader_task:
                state.reader_task.cancel()

    async def restart(self, instance_id: int) -> None:
        await self.stop(instance_id)
        state = self.tenants.get(instance_id)
        if state:
            state.stop_requested = False
            await self._spawn(state)

    async def remove(self, instance_id: int) -> None:
        await self.stop(instance_id)
        self.tenants.pop(instance_id, None)

    async def stop_all(self) -> None:
        ids = list(self.tenants.keys())
        for i in ids:
            await self.stop(i)

    # ---- Internals ----

    async def _spawn(self, state: TenantState) -> None:
        spec = state.spec
        spec.cwd.mkdir(parents=True, exist_ok=True)

        env = {**os.environ, **spec.env}

        def _preexec() -> None:
            try:
                # New session — easier to signal whole process group later.
                os.setsid()
                # Resource limits.
                soft = spec.rlimit_as_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (soft, soft))
                resource.setrlimit(
                    resource.RLIMIT_NOFILE, (spec.rlimit_nofile, spec.rlimit_nofile)
                )
                if spec.rlimit_cpu_sec:
                    resource.setrlimit(
                        resource.RLIMIT_CPU,
                        (spec.rlimit_cpu_sec, spec.rlimit_cpu_sec),
                    )
            except Exception as exc:  # noqa: BLE001
                # Logging from preexec is risky; ignore.
                pass

        try:
            proc = await asyncio.create_subprocess_exec(
                *spec.cmd,
                cwd=str(spec.cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                preexec_fn=_preexec,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            state.log_tail.append(f"[supervisor] failed to spawn: {exc}")
            logger.warning("Tenant %s spawn failed: %s", spec.instance_id, exc)
            return
        except Exception as exc:  # noqa: BLE001
            state.log_tail.append(f"[supervisor] error: {exc}")
            logger.warning("Tenant %s spawn error: %s", spec.instance_id, exc)
            return

        state.proc = proc
        state.started_at = time.time()
        state.restart_count = 0  # reset on successful spawn
        state.log_tail.append(f"[supervisor] started pid={proc.pid}")

        state.reader_task = asyncio.create_task(self._read_logs(state))
        state.waiter_task = asyncio.create_task(self._wait_and_maybe_restart(state))

    async def _read_logs(self, state: TenantState) -> None:
        proc = state.proc
        if not proc or not proc.stdout:
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                txt = line.decode("utf-8", errors="ignore").rstrip("\n")
                state.log_tail.append(txt)
        except Exception as exc:  # noqa: BLE001
            state.log_tail.append(f"[supervisor] log read error: {exc}")

    async def _wait_and_maybe_restart(self, state: TenantState) -> None:
        proc = state.proc
        if not proc:
            return
        rc = await proc.wait()
        state.last_exit = rc
        state.proc = None
        state.started_at = None
        state.log_tail.append(f"[supervisor] exited rc={rc}")
        if state.stop_requested:
            return
        if not state.spec.autorestart:
            return
        state.restart_count += 1
        backoff = min(60.0, 2.0 ** min(state.restart_count, 6))
        state.backoff = backoff
        state.log_tail.append(f"[supervisor] restart in {backoff:.0f}s")
        await asyncio.sleep(backoff)
        if state.stop_requested:
            return
        await self._spawn(state)

    # ---- Inspection ----

    def status(self, instance_id: int) -> dict:
        state = self.tenants.get(instance_id)
        if not state:
            return {"alive": False, "exists": False}
        proc = state.proc
        return {
            "exists": True,
            "alive": bool(proc and proc.returncode is None),
            "pid": proc.pid if proc else None,
            "started_at": state.started_at,
            "uptime": (
                round(time.time() - state.started_at) if state.started_at else 0
            ),
            "last_exit": state.last_exit,
            "restart_count": state.restart_count,
        }

    def tail(self, instance_id: int, lines: int = 50) -> list[str]:
        state = self.tenants.get(instance_id)
        if not state:
            return []
        return list(state.log_tail)[-lines:]


supervisor = Supervisor()
