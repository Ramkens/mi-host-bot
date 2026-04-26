"""Structured logging setup."""
from __future__ import annotations

import logging
import sys

from app.config import settings


def setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(settings.log_level.upper())
    h = logging.StreamHandler(stream=sys.stdout)
    h.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(h)
    # Tame noisy libs
    for noisy in ("aiogram.event", "asyncio", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
