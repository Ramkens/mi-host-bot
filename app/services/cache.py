"""In-process TTL cache (process-local). Suitable for single-instance Render.

For multi-instance setups, swap with Redis. Interface kept minimal.
"""
from __future__ import annotations

import time
from typing import Any, Optional


class TTLCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        v = self._data.get(key)
        if not v:
            return None
        expires, value = v
        if expires < time.time():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: int = 60) -> None:
        self._data[key] = (time.time() + ttl, value)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


cache = TTLCache()
