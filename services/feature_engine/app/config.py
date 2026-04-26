"""Pydantic Settings model for feature-engine (§5.11).

All env-backed configuration lives in one typed model. Validation
happens at startup: a missing or malformed value fails the lifespan
before uvicorn binds the port, not at first request. Mirrors the
market-data-svc settings pattern (T-100), itself a mirror of the
signal-gateway pattern (T-015a).

T-109 ships pool + bus only — no env-stopgap for the feature registry.
T-111 lands ``configs/features/indicators.yaml``; T-110 wires its
loader into the lifespan. Until then, the service stays healthy +
ready emitting only default Prometheus collectors.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["LogLevel", "Settings"]


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Env-sourced configuration for the feature-engine service.

    Field names are lowercase-with-underscores; env vars are the
    uppercase equivalent (pydantic-settings handles the mapping).
    ``extra="ignore"`` tolerates sibling env vars (``POSTGRES_USER``,
    ``MARKET_DATA_SYMBOLS``, ``SIGNAL_GATEWAY_HMAC_SECRET``, …) that
    live in ``.env.example`` or ``secrets.env`` but do not belong to
    this service.
    """

    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "feature-engine"
    log_level: LogLevel = "INFO"
    http_port: int = 8000

    # Message bus — NATS JetStream (§8, §16.6).
    nats_url: str = "nats://nats:4222"

    # asyncpg DSN; scheme-validated by packages.db.create_pool at pool init.
    database_url: str
