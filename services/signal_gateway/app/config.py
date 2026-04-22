"""Pydantic Settings model for signal-gateway (§5.11).

All env-backed configuration lives in one typed model. Validation happens at
startup: a missing or malformed value fails the lifespan before uvicorn
binds the port, not at first request. ``SecretStr`` fields redact in
``repr(settings)`` and in any downstream log emission that serialises the
model.
"""

from __future__ import annotations

from typing import Literal

# SecretStr appears only as a BaseSettings field type; pydantic-settings
# evaluates that annotation at class-creation time (schema construction),
# so the import is runtime-required, not typing-only.
from pydantic import SecretStr  # noqa: TC002
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["LogLevel", "Settings"]


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Env-sourced configuration for the signal-gateway service.

    Field names are lowercase-with-underscores; env vars are the uppercase
    equivalent (pydantic-settings handles the mapping). ``extra="ignore"``
    tolerates sibling env vars (``POSTGRES_USER``, ``TUNNEL_TOKEN``, …) that
    live in ``.env.example`` but do not belong to this service.
    """

    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "signal-gateway"
    log_level: LogLevel = "INFO"
    http_port: int = 8000

    # Message bus — NATS JetStream (§8, §16.6).
    nats_url: str = "nats://nats:4222"

    # asyncpg DSN; scheme-validated by packages.db.create_pool at pool init.
    database_url: str

    # Shared HMAC-SHA256 secret for webhook auth (§16.3). Loaded in T-015a
    # but unused until the /webhook handler lands in T-015b.
    signal_gateway_hmac_secret: SecretStr
