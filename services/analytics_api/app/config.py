"""Pydantic Settings model for analytics-api (§5.11, §9.6).

All env-backed configuration lives in one typed model. Validation
happens at startup: a missing or malformed value fails the lifespan
before uvicorn binds the port, not at first request. Mirrors the
strategy-engine pattern (T-309), itself a mirror of execution-service
(T-214) / feature-engine (T-109) / market-data-svc (T-100) /
signal-gateway (T-015a).

T-400 ships the skeleton with the standard service-identity + DSN set
only. analytics-api is a single-instance singleton per BRIEF §2.2:234
(unlike per-bot strategy-engine), so there is no ``bot_id`` field; no
``bot_config_dir`` / ``plugin_registry_path`` either, since analytics-
api reads from PG only and does not load YAML at startup.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["LogLevel", "Settings"]


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Env-sourced configuration for the analytics-api service.

    Field names are lowercase-with-underscores; env vars are the
    uppercase equivalent (pydantic-settings handles the mapping).
    ``extra="ignore"`` tolerates sibling env vars (``BOT_ID``,
    ``MARKET_DATA_SYMBOLS``, ``SIGNAL_GATEWAY_HMAC_SECRET``,
    ``BOT_<ID>_BYBIT_API_KEY``, …) that live in ``.env.example`` /
    ``secrets.env`` shared cross-service compose env_file but do not
    belong to this singleton.
    """

    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "analytics-api"
    log_level: LogLevel = "INFO"
    http_port: int = 8000

    # Message bus — NATS JetStream (§8, §16.6). T-408 SSE will subscribe
    # to NATS streams (signals.validated, orders.events.*, …) and fan-out
    # to dashboard clients; T-400 connects + monitors readiness only.
    nats_url: str = "nats://nats:4222"

    # asyncpg DSN; scheme-validated by packages.db.create_pool at pool init.
    database_url: str

    # T-408 SSE tuning (per §N9 + L-001 — operational knobs, not mathematically
    # fixed). Defaults chosen for F4 single-operator LAN scale; tunable without
    # redeploy via env vars (SSE_HEARTBEAT_INTERVAL_S etc.) for incident-time
    # adjustments.
    sse_heartbeat_interval_s: int = Field(
        default=15,
        ge=1,
        le=300,
        description="Send `: heartbeat\\n\\n` SSE comment after this many idle seconds (OQ-4=A).",
    )
    sse_client_queue_maxsize: int = Field(
        default=1000,
        ge=10,
        le=100_000,
        description="Per-client asyncio.Queue maxsize before drop-oldest fires (OQ-3=A).",
    )
    sse_max_connections: int = Field(
        default=50,
        ge=1,
        le=10_000,
        description="Global cap on concurrent SSE clients (OQ-7=A; F4 single-operator scale).",
    )
    sse_overflow_log_interval_s: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Rate-limit `sse_client_buffer_overflow` warnings to 1/N seconds per client.",
    )

    # T-509 backtest worker — operator opt-in via env BACKTEST_WORKER_ENABLED=true.
    backtest_worker_enabled: bool = Field(
        default=False,
        description=(
            "Enable backtest worker in analytics-api lifespan "
            "(env BACKTEST_WORKER_ENABLED=true to opt in)."
        ),
    )
    backtest_worker_poll_interval_s: int = Field(
        default=5,
        ge=1,
        le=300,
        description="Interval (seconds) between backtest queue polls when no queued runs.",
    )
