"""Auto-setup helper: turn an analysis result into a deployable spec."""
from __future__ import annotations

from dataclasses import dataclass

from app.services.code_analyzer import AnalysisResult


@dataclass
class DeploySpec:
    runtime: str
    build_cmd: str
    start_cmd: str
    env_template: dict[str, str]


def derive_spec(analysis: AnalysisResult) -> DeploySpec:
    deps = analysis.dependencies or []
    has_aiogram = any("aiogram" in d.lower() for d in deps)
    has_pyrogram = any("pyrogram" in d.lower() for d in deps)
    has_fastapi = any("fastapi" in d.lower() or "uvicorn" in d.lower() for d in deps)

    build_parts = ["pip install --upgrade pip"]
    if deps:
        build_parts.append("pip install -r requirements.txt")
    elif has_aiogram or has_pyrogram:
        build_parts.append("pip install aiogram pyrogram tgcrypto")
    elif has_fastapi:
        build_parts.append("pip install fastapi uvicorn")
    build_cmd = " && ".join(build_parts)

    if analysis.entrypoint:
        if has_fastapi and analysis.entrypoint.endswith(".py"):
            module = analysis.entrypoint.replace("/", ".").removesuffix(".py")
            start_cmd = f"uvicorn {module}:app --host 0.0.0.0 --port $PORT"
        else:
            start_cmd = f"python {analysis.entrypoint}"
    else:
        start_cmd = "python main.py"

    env: dict[str, str] = {}
    for key in analysis.env_keys:
        env[key] = ""  # placeholder, user will be prompted to fill

    return DeploySpec(
        runtime="python",
        build_cmd=build_cmd,
        start_cmd=start_cmd,
        env_template=env,
    )
