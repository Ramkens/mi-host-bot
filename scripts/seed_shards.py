"""One-off: seed shards into prod DB and provision workers.

Usage (locally with envs):
    DATABASE_URL=... SECRET_KEY=... \
    SHARD_KEYS_FILE=keys.txt \
    python -m scripts.seed_shards

keys.txt: one Render API key per line (optionally name=key).
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.base import SessionLocal
from app.db import models  # noqa: F401  ensure mappers registered
from app.repos import shards as shards_repo
from app.services.shard_provision import provision_worker


async def main() -> None:
    keys_path = os.environ["SHARD_KEYS_FILE"]
    pairs: list[tuple[str, str]] = []
    with open(keys_path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            if "=" in line:
                name, key = line.split("=", 1)
            else:
                name, key = f"shard-{i+1}", line
            pairs.append((name.strip(), key.strip()))

    async with SessionLocal() as s:
        for name, key in pairs:
            existing = await shards_repo.by_name(s, name)
            if existing:
                print(f"[skip] {name} already exists (id={existing.id})")
                continue
            shard = await shards_repo.create(
                s, name=name, api_key=key, capacity=4
            )
            print(f"[seed] {name} id={shard.id}")
            await s.commit()

            print(f"[provision] {name} ...", flush=True)
            try:
                result = await provision_worker(s, shard.id)
                print(f"  -> {result}")
                await s.commit()
            except Exception as exc:  # noqa: BLE001
                print(f"  ! provision failed: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
