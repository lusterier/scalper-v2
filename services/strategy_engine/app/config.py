"""Pydantic Settings model for strategy-engine (§5.11, §9.4).

All env-backed configuration lives in one typed model. Validation
happens at startup: a missing or malformed value fails the lifespan
before uvicorn binds the port, not at first request. Mirrors the
execution-service pattern (T-214), itself a mirror of feature-engine
(T-109) / market-data-svc (T-100) / signal-gateway (T-015a).

T-309 ships the skeleton with three strategy-specific fields beyond
the standard service-identity + DSN set:

* ``bot_id`` — required, no default. One container per active bot per
  BRIEF §9.4:1530. Loads ``configs/bots/<bot_id>.yaml`` at startup.
* ``bot_config_dir`` — directory containing per-bot YAML files;
  defaults to ``/app/configs/bots`` matching the Dockerfile WORKDIR
  + COPY layout.
* ``plugin_registry_path`` — path to plugin registry YAML (§10.6);
  defaults to ``/app/configs/plugin_registry.yaml``.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["LogLevel", "Settings"]


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Env-sourced configuration for the strategy-engine service.

    Field names are lowercase-with-underscores; env vars are the
    uppercase equivalent (pydantic-settings handles the mapping).
    ``extra="ignore"`` tolerates sibling env vars (``POSTGRES_USER``,
    ``MARKET_DATA_SYMBOLS``, ``BOT_<ID>_BYBIT_API_KEY``, …) that live
    in ``.env.example`` or ``secrets.env`` but do not belong to this
    skeleton.
    """

    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "strategy-engine"
    log_level: LogLevel = "INFO"
    http_port: int = 8000

    # Message bus — NATS JetStream (§8, §16.6).
    nats_url: str = "nats://nats:4222"

    # asyncpg DSN; scheme-validated by packages.db.create_pool at pool init.
    database_url: str

    # T-309 — bot identity. Required (no default) per BRIEF §9.4:1530:
    # "One Docker container per active bot, parameterized by BOT_ID env var."
    # Missing → Pydantic ValidationError at lifespan startup.
    bot_id: str

    # T-309 — bot config + plugin registry paths (§9.4 + §10.6).
    # Defaults match the Dockerfile WORKDIR /app + COPY configs/ layout.
    bot_config_dir: str = "/app/configs/bots"
    plugin_registry_path: str = "/app/configs/plugin_registry.yaml"

    # T-310b — signal_id lookup window for select_signal_id_by_idempotency_key
    # (Timescale chunk pruning per T-310a WG#3). 600s default = 10 min covers
    # signals.ttl_seconds=120 (§B.1) + retry budget + clock skew.
    signal_max_age_seconds: int = 600
