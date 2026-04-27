"""Pydantic Settings model for execution-service (§5.11).

All env-backed configuration lives in one typed model. Validation
happens at startup: a missing or malformed value fails the lifespan
before uvicorn binds the port, not at first request. Mirrors the
feature-engine pattern (T-109), itself a mirror of market-data-svc
(T-100) and signal-gateway (T-015a).

T-214 ships only the minimum 5 fields (service identity + DSNs).
F2-specific fields land in their owner tasks per §0.8 anti-hypothetical:

* ``BOT_<ID>_BYBIT_API_KEY`` / ``BOT_<ID>_BYBIT_API_SECRET`` — T-215
  (adapter pool composition reads per H-022).
* ``POSITION_POLL_INTERVAL`` — T-217 PositionLifecycle FSM tick rate.
* ``RECONCILE_SLEEP_SECONDS`` — T-219 cumulative-delta closed-pnl
  snapshot delay (H-011 default 2s, configurable per L-001).
* ``AUDIT_LOOP_INTERVAL_SECONDS`` — T-220 APScheduler period.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["LogLevel", "Settings"]


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Env-sourced configuration for the execution-service.

    Field names are lowercase-with-underscores; env vars are the
    uppercase equivalent (pydantic-settings handles the mapping).
    ``extra="ignore"`` tolerates sibling env vars (``POSTGRES_USER``,
    ``MARKET_DATA_SYMBOLS``, ``SIGNAL_GATEWAY_HMAC_SECRET``,
    ``FEATURE_ENGINE_SYMBOLS``, ``BOT_<ID>_BYBIT_API_KEY/SECRET``, …)
    that live in ``.env.example`` or ``secrets.env`` but do not belong
    to this T-214 skeleton (the per-bot Bybit creds belong to T-215).
    """

    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "execution-service"
    log_level: LogLevel = "INFO"
    http_port: int = 8000

    # Message bus — NATS JetStream (§8, §16.6).
    nats_url: str = "nats://nats:4222"

    # asyncpg DSN; scheme-validated by packages.db.create_pool at pool init.
    database_url: str
