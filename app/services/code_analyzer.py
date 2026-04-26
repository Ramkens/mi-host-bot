"""Code analyzer (heuristics + bandit + signatures).

Used when a user uploads a .zip with a custom script.
We refuse to deploy code that scores too high on the risk meter.

`analyze_zip` returns a `(risk_score, report, deps, entrypoint, env_keys)` tuple.
"""
from __future__ import annotations

import io
import json
import logging
import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Hard signatures: any match adds a heavy penalty.
HARD_SIGNATURES: tuple[tuple[str, str, int], ...] = (
    ("private key dump", r"id_rsa|\.ssh/authorized_keys|/etc/shadow", 80),
    ("crypto-stealer", r"wallet\.dat|seed_phrase|metamask|electrum", 80),
    ("browser cookie scraper", r"Cookies(?:\.|/)|Login Data", 70),
    ("ransomware", r"AES.*encrypt.*walk\(", 75),
    ("self-replication", r"smtplib.*\.sendmail.*for.*range", 60),
    ("network scan", r"nmap|masscan|portscan", 50),
    ("subprocess.*shell=True", r"subprocess\.(?:Popen|run|call|check_call|check_output)\([^\)]*shell\s*=\s*True", 30),
    ("os.system", r"\bos\.system\s*\(", 20),
    ("eval/exec", r"\b(?:eval|exec)\s*\(", 20),
    ("base64 + exec", r"base64\.b64decode\s*\([^)]*\)\s*\)", 15),
    ("requests to suspicious paste", r"hastebin\.com|pastebin\.com/raw", 25),
    ("dropping into /etc", r"open\([\"']/etc/", 25),
)

REQUIREMENTS_FILES = ("requirements.txt", "Pipfile", "pyproject.toml")
ENTRYPOINT_HINTS = (
    "main.py",
    "bot.py",
    "app.py",
    "run.py",
    "start.py",
    "__main__.py",
)


@dataclass
class AnalysisResult:
    ok: bool
    risk_score: int  # 0..100
    report: str
    dependencies: list[str] = field(default_factory=list)
    entrypoint: Optional[str] = None
    env_keys: list[str] = field(default_factory=list)
    extra_files: list[str] = field(default_factory=list)
    bytes_total: int = 0
    files_total: int = 0


def _scan_text(text: str, findings: list[str], score: list[int]) -> None:
    for label, pattern, weight in HARD_SIGNATURES:
        if re.search(pattern, text):
            findings.append(f" {label} (+{weight})")
            score[0] += weight


def _detect_entrypoint(names: Iterable[str]) -> Optional[str]:
    name_set = {n.split("/")[-1] for n in names}
    for hint in ENTRYPOINT_HINTS:
        for n in names:
            if n.split("/")[-1] == hint:
                return n
    # fallback: any top-level .py
    py_files = [n for n in names if n.endswith(".py") and "/" not in n]
    if py_files:
        return py_files[0]
    return None


def _parse_requirements(text: str) -> list[str]:
    deps: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        deps.append(line)
    return deps


def _detect_env_keys(text: str) -> list[str]:
    """Look for os.getenv("X") / os.environ["X"] hints."""
    keys: set[str] = set()
    for m in re.finditer(
        r"os\.(?:getenv|environ\.get)\(\s*[\"']([A-Z][A-Z0-9_]{1,63})[\"']", text
    ):
        keys.add(m.group(1))
    for m in re.finditer(
        r"os\.environ\[\s*[\"']([A-Z][A-Z0-9_]{1,63})[\"']\s*\]", text
    ):
        keys.add(m.group(1))
    # exclude obvious system vars
    keys -= {"PATH", "HOME", "PWD", "USER"}
    return sorted(keys)


def analyze_zip(data: bytes, *, max_bytes: int = 25 * 1024 * 1024) -> AnalysisResult:
    if len(data) > max_bytes:
        return AnalysisResult(
            ok=False,
            risk_score=100,
            report=f"Архив слишком большой: {len(data)} > {max_bytes} байт",
        )
    findings: list[str] = []
    score = [0]
    deps: list[str] = []
    env_keys: set[str] = set()
    files_total = 0
    bytes_total = 0
    names: list[str] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return AnalysisResult(
            ok=False, risk_score=100, report="Не удалось распаковать архив"
        )

    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            names.append(info.filename)
            files_total += 1
            bytes_total += info.file_size
            # Path traversal
            if info.filename.startswith("/") or ".." in info.filename:
                findings.append(f" path traversal: {info.filename} (+50)")
                score[0] += 50
                continue
            # binaries — flag larger ones
            if info.filename.endswith((".exe", ".so", ".dll", ".bin")):
                findings.append(f" binary: {info.filename} (+10)")
                score[0] += 10
                continue
            try:
                content = zf.read(info).decode("utf-8", errors="ignore")
            except Exception:
                continue
            base = info.filename.lower().split("/")[-1]
            if base in REQUIREMENTS_FILES or base.endswith("requirements.txt"):
                deps.extend(_parse_requirements(content))
            if info.filename.endswith(".py"):
                _scan_text(content, findings, score)
                env_keys.update(_detect_env_keys(content))

    entrypoint = _detect_entrypoint(names)
    if not entrypoint:
        findings.append("entrypoint не найден (+15)")
        score[0] += 15

    risk = min(100, score[0])
    ok = risk < 60 and entrypoint is not None
    report_lines = [
        f"Файлов: {files_total}, размер: {bytes_total/1024:.1f} KB",
        f"Entrypoint: {entrypoint or '—'}",
        f"Зависимостей: {len(deps)}",
        f"Переменных окружения: {len(env_keys)}",
        "",
        f"Риск-оценка: {risk}/100",
    ]
    if findings:
        report_lines.append("")
        report_lines.append("Сигнатуры:")
        report_lines.extend(f"• {f}" for f in findings[:20])
        if len(findings) > 20:
            report_lines.append(f"…и ещё {len(findings) - 20}")

    return AnalysisResult(
        ok=ok,
        risk_score=risk,
        report="\n".join(report_lines),
        dependencies=deps,
        entrypoint=entrypoint,
        env_keys=sorted(env_keys),
        extra_files=names[:50],
        bytes_total=bytes_total,
        files_total=files_total,
    )


def run_bandit(path: Path) -> tuple[int, str]:
    """Optionally run bandit if installed; non-fatal if missing."""
    try:
        proc = subprocess.run(
            ["bandit", "-r", str(path), "-f", "json", "-q"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        report = proc.stdout or proc.stderr
        try:
            j = json.loads(report)
            results = j.get("results", [])
            score = 0
            for r in results:
                sev = r.get("issue_severity", "LOW")
                score += {"HIGH": 20, "MEDIUM": 8, "LOW": 2}.get(sev, 0)
            return min(100, score), f"bandit: {len(results)} issues"
        except json.JSONDecodeError:
            return 0, "bandit: skipped"
    except FileNotFoundError:
        return 0, "bandit: not installed"
    except subprocess.TimeoutExpired:
        return 0, "bandit: timeout"
