"""Pydantic Settings model for signal-gateway (§5.11).

All env-backed configuration lives in one typed model. Validation happens at
startup: a missing or malformed value fails the lifespan before uvicorn
binds the port, not at first request. ``SecretStr`` fields redact in
``repr(settings)`` and in any downstream log emission that serialises the
model.
"""

from __future__ import annotations

from typing import Annotated, Literal

# SecretStr + Field are runtime-required — pydantic-settings evaluates
# the Annotated[SecretStr, Field(min_length=32)] annotation at class
# creation for schema construction. Ruff's TC002 heuristic picks this
# up via the Field(...) runtime call in the metadata position.
from pydantic import Field, SecretStr
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

    # Shared HMAC-SHA256 secret for webhook auth (§16.3). min_length=32 is
    # the HMAC-SHA256 strength floor (256 bits) — an empty or short secret
    # would let hmac.compare_digest fail-open on the T-015b2 /webhook
    # handler, so we reject at startup. Validator runs at Settings()
    # construction; a short value fails the lifespan before port bind.
    signal_gateway_hmac_secret: Annotated[SecretStr, Field(min_length=32)]
