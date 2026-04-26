"""Moscow-time helpers (UTC+3, no DST)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def now_msk() -> datetime:
    return datetime.now(tz=MSK)


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def to_msk(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK)


def fmt_msk(dt: datetime, with_seconds: bool = False) -> str:
    dt = to_msk(dt)
    fmt = "%d.%m.%Y %H:%M"
    if with_seconds:
        fmt = "%d.%m.%Y %H:%M:%S"
    return dt.strftime(fmt) + "МСК"


def humanize_delta(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "сейчас"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes and not days:
        parts.append(f"{minutes} мин")
    return "".join(parts) or "<1 мин"
