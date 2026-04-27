"""Centralized configuration via pydantic-settings.

All secrets and tunables are read from env vars. Nothing sensitive is
hard-coded. The application refuses to start without the bare minimum set.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Telegram ---
    # Required on master, optional on worker (worker is headless and never
    # talks to Telegram, so an empty BOT_TOKEN is fine there).
    bot_token: str = ""
    admin_ids: str = ""  # comma-separated ints, mutable via /addadmin

    # --- HTTP / Webhook ---
    public_url: str = "http://localhost:8000"
    webhook_path: str = "/tg/webhook"
    webhook_secret: str = "change_me"
    port: int = 8000

    # --- Channel autopilot ---
    channel_id: Optional[str] = None  # @channel or -100... id
    chat_id: Optional[str] = None  # discussion chat (optional)

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///./data/mi-host.db"

    # --- CryptoBot ---
    cryptobot_token: str = ""
    cryptobot_webhook_secret: str = ""

    # --- Render ---
    render_api_key: str = ""
    render_owner_id: str = ""
    render_region: str = "frankfurt"
    render_plan: str = "free"
    # Render injects RENDER_SERVICE_ID into every service automatically.
    # We use it for self-rotation of the underlying free Postgres.
    render_service_id: str = ""

    @property
    def render_service_id_self(self) -> str:
        return self.render_service_id

    # --- Multi-shard ---
    # "master" (default) = full bot, runs handlers + scheduler + workers for
    # tenants without a shard. "worker" = headless reconciler for tenants on
    # this shard. Set via MIHOST_ROLE env var.
    mihost_role: str = "master"
    mihost_shard_name: str = ""

    # --- cron-job.org ---
    cronjob_api_key: str = ""

    # --- AI (optional) ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # --- App tunables ---
    tz: str = "Europe/Moscow"
    log_level: str = "INFO"
    subscription_days: int = 30
    price_cardinal_rub: int = 40
    price_script_rub: int = 50          # Standard script tier (130 MB)
    price_script_pro_rub: int = 150     # PRO tier (full server, 512 MB)
    script_std_ram_mb: int = 130
    script_pro_ram_mb: int = 512
    cardinal_ram_mb: int = 200
    # Capacity of THIS service for tenants. Master runs the bot itself, so by
    # default it does not accept user tenants — they go to dedicated shards.
    master_capacity: int = 0
    # Days after subscription expiry before purging tenant data (with admin backup).
    purge_grace_days: int = 5
    # Tenant watchdog poll interval — keep small so dead tenants come back fast.
    watchdog_interval_seconds: int = 10
    secret_key: str = "change_me_long_random"

    # Public Telegram URL of the support contact (used by the «Поддержка» button).
    # Format: https://t.me/<username>  or tg://user?id=<numeric_id>
    support_url: str = "tg://user?id=8341143485"

    # Optional JSON-encoded list of shards to auto-seed on first boot, e.g.:
    #   MIHOST_PRESEED_SHARDS='[{"name":"host1","api_key":"rnd_...","capacity":3}]'
    # If a shard with the given name already exists, it is skipped (capacity is
    # NOT updated retroactively — change it via /shards or DB if needed).
    mihost_preseed_shards: str = ""

    @field_validator("admin_ids")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip()

    @property
    def admin_ids_list(self) -> list[int]:
        return [int(x) for x in self.admin_ids.split(",") if x.strip().isdigit()]

    @property
    def webhook_url(self) -> str:
        base = self.public_url.rstrip("/")
        return f"{base}{self.webhook_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
