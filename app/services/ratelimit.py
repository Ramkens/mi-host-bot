"""Simple per-user token bucket rate limiter."""
from __future__ import annotations

import time
from collections import defaultdict


class TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: float) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._buckets: dict[int, tuple[float, float]] = defaultdict(
            lambda: (capacity, time.time())
        )

    def allow(self, key: int, cost: float = 1.0) -> bool:
        tokens, last = self._buckets[key]
        now = time.time()
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)
        if tokens < cost:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - cost, now)
        return True


# Generic public-action limiter (e.g. "create invoice", "start mini-game")
public_limiter = TokenBucket(capacity=5, refill_per_sec=1.0)
