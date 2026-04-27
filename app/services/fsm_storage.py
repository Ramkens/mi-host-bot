"""Postgres-backed FSM storage.

Persists aiogram FSM state + data across bot restarts so users don't lose
their place mid-wizard (buy flow, settings editor, self-delete, ...).

Works on both Postgres (uses JSONB + UPSERT) and SQLite (falls back to TEXT
column with JSON-encoded data; SQLite is only used in local dev).
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

from app.db.base import SessionLocal, engine
from sqlalchemy import text

_IS_SQLITE = "sqlite" in engine.url.drivername


def _ddl() -> list[str]:
    if _IS_SQLITE:
        return [
            """CREATE TABLE IF NOT EXISTS fsm_states (
                key TEXT PRIMARY KEY,
                state TEXT,
                data TEXT NOT NULL DEFAULT '{}',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        ]
    return [
        """CREATE TABLE IF NOT EXISTS fsm_states (
            key TEXT PRIMARY KEY,
            state TEXT,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
    ]


async def ensure_fsm_table() -> None:
    async with engine.begin() as conn:
        for stmt in _ddl():
            await conn.execute(text(stmt))


def _key_to_str(key: StorageKey) -> str:
    return (
        f"{key.bot_id}:{key.chat_id}:{key.user_id}"
        f":{key.thread_id or 0}:{key.destiny or ''}"
    )


class PgStorage(BaseStorage):
    """SQLAlchemy-backed FSM storage. Safe to call concurrently."""

    async def set_state(
        self, key: StorageKey, state: State | str | None = None
    ) -> None:
        if isinstance(state, State):
            state_str: str | None = state.state
        else:
            state_str = state
        k = _key_to_str(key)
        async with SessionLocal() as s:
            if _IS_SQLITE:
                await s.execute(
                    text(
                        "INSERT INTO fsm_states(key, state, data) "
                        "VALUES (:k, :st, '{}') "
                        "ON CONFLICT(key) DO UPDATE SET state=:st, "
                        "updated_at=CURRENT_TIMESTAMP"
                    ),
                    {"k": k, "st": state_str},
                )
            else:
                await s.execute(
                    text(
                        "INSERT INTO fsm_states(key, state) "
                        "VALUES (:k, :st) "
                        "ON CONFLICT (key) DO UPDATE SET state = EXCLUDED.state, "
                        "updated_at = NOW()"
                    ),
                    {"k": k, "st": state_str},
                )
            await s.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        k = _key_to_str(key)
        async with SessionLocal() as s:
            res = await s.execute(
                text("SELECT state FROM fsm_states WHERE key=:k"), {"k": k}
            )
            row = res.first()
        return row[0] if row else None

    async def set_data(
        self, key: StorageKey, data: Mapping[str, Any]
    ) -> None:
        k = _key_to_str(key)
        payload = json.dumps(dict(data), ensure_ascii=False)
        async with SessionLocal() as s:
            if _IS_SQLITE:
                await s.execute(
                    text(
                        "INSERT INTO fsm_states(key, data) "
                        "VALUES (:k, :d) "
                        "ON CONFLICT(key) DO UPDATE SET data=:d, "
                        "updated_at=CURRENT_TIMESTAMP"
                    ),
                    {"k": k, "d": payload},
                )
            else:
                await s.execute(
                    text(
                        "INSERT INTO fsm_states(key, data) "
                        "VALUES (:k, CAST(:d AS JSONB)) "
                        "ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, "
                        "updated_at = NOW()"
                    ),
                    {"k": k, "d": payload},
                )
            await s.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        k = _key_to_str(key)
        async with SessionLocal() as s:
            res = await s.execute(
                text("SELECT data FROM fsm_states WHERE key=:k"), {"k": k}
            )
            row = res.first()
        if not row or row[0] is None:
            return {}
        val = row[0]
        if isinstance(val, str):
            try:
                return json.loads(val)
            except Exception:  # noqa: BLE001
                return {}
        if isinstance(val, dict):
            return val
        return {}

    async def close(self) -> None:  # pragma: no cover
        return None
