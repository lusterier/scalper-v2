"""Pydantic Settings model for market-data-svc (§5.11).

All env-backed configuration lives in one typed model. Validation
happens at startup: a missing or malformed value fails the lifespan
before uvicorn binds the port, not at first request. Mirrors the
signal-gateway settings pattern (T-015a).

``MARKET_DATA_SYMBOLS`` is the F1 stopgap for the active-symbol set
(brief §9.2 line 1454 specifies a ``bots`` JOIN ``bot_configs`` lookup
that requires F3's bot-registry to be populated). Empty default →
empty list → :meth:`OhlcPipeline.start([])` is a no-op and the service
stays healthy + ready until the env var is populated. The split-and-strip
in :attr:`Settings.symbols` rejects empty fragments so ``""`` and
``"BTCUSDT,,ETHUSDT"`` both yield the right list. F1+ entry exists to
swap to the bots+bot_configs query once F3 lands.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["LogLevel", "Settings"]


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Env-sourced configuration for the market-data-svc service.

    Field names are lowercase-with-underscores; env vars are the
    uppercase equivalent (pydantic-settings handles the mapping).
    ``extra="ignore"`` tolerates sibling env vars (``POSTGRES_USER``,
    ``SIGNAL_GATEWAY_HMAC_SECRET``, …) that live in ``.env.example``
    or ``secrets.env`` but do not belong to this service.
    """

    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "market-data-svc"
    log_level: LogLevel = "INFO"
    http_port: int = 8000

    # Message bus — NATS JetStream (§8, §16.6).
    nats_url: str = "nats://nats:4222"

    # asyncpg DSN; scheme-validated by packages.db.create_pool at pool init.
    database_url: str

    # F1 stopgap symbol seed (see module docstring). Comma-separated
    # canonical Bybit-shape symbols. Empty (default) → no consumer
    # tasks spawned; service still healthy + ready.
    market_data_symbols: str = ""

    @property
    def symbols(self) -> list[str]:
        """Parsed ``market_data_symbols`` env var as a stripped, non-empty list.

        Rejects empty fragments so ``""`` → ``[]`` and
        ``"BTCUSDT,,ETHUSDT"`` → ``["BTCUSDT", "ETHUSDT"]``. Empty
        list is the documented no-op input to
        :meth:`OhlcPipeline.start`.
        """
        return [s.strip() for s in self.market_data_symbols.split(",") if s.strip()]
