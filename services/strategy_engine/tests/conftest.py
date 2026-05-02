"""Shared fixtures for strategy-engine unit tests.

Lets tests exercise the real :func:`create_app` factory and its
lifespan path without touching real Postgres / NATS / on-disk YAML:

* :func:`packages.db.create_pool` is monkey-patched at the
  ``services.strategy_engine.app.main`` boundary to return a mock pool.
* :class:`packages.bus.NatsClient` patched similarly.
* :func:`packages.scoring.load_bot_config` and
  :func:`packages.scoring.registry.load_plugin_registry` patched so the
  lifespan does not require fixture YAML on disk.

The lifespan then runs end-to-end against the mocks, attaching pool /
bus / plugin_registry / bot_config / resolver to ``app.state`` exactly
as it would in production. Tests mutate ``mock_bus.state`` /
pool-acquire behaviour to simulate outages for the ``/ready`` coverage
matrix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from packages.bus import ConnectionState as BusConnectionState
from services.strategy_engine.app.config import Settings
from services.strategy_engine.app.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings populated with values safe for in-process tests."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
    monkeypatch.setenv("NATS_URL", "nats://test-nats:4222")
    monkeypatch.setenv("BOT_ID", "test_bot")
    monkeypatch.setenv("BOT_CONFIG_DIR", "/app/configs/bots")
    monkeypatch.setenv("PLUGIN_REGISTRY_PATH", "/app/configs/plugin_registry.yaml")
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
def mock_pool() -> MagicMock:
    """asyncpg.Pool stand-in.

    ``pool.close()`` is async, so it's an :class:`AsyncMock`.
    ``pool.acquire`` is synchronous (returns an async context manager).
    """
    pool = MagicMock()
    pool.close = AsyncMock()

    fake_conn = MagicMock()
    fake_conn.fetch = AsyncMock(return_value=[])
    fake_conn.fetchrow = AsyncMock(return_value=None)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool


@pytest.fixture
def mock_bus() -> MagicMock:
    """NatsClient stand-in. Defaults to ``CONNECTED``; tests mutate ``.state``."""
    bus = MagicMock()
    bus.state = BusConnectionState.CONNECTED
    bus.connect = AsyncMock()
    bus.close = AsyncMock()
    bus.publish = AsyncMock()
    bus.subscribe = AsyncMock()
    return bus


@pytest.fixture
def mock_plugin_registry() -> dict[tuple[str, str], type]:
    """Empty plugin registry — no plugin conditions in test bot config."""
    return {}


@pytest.fixture
def mock_bot_config() -> MagicMock:
    """Stand-in BotConfig with one mock rule for rules_count assertions."""
    bot_config = MagicMock()
    bot_config.bot_id = "test_bot"
    bot_config.scoring = MagicMock()
    bot_config.scoring.rules = [MagicMock()]
    return bot_config


@pytest.fixture
def app_with_mocks(
    settings: Settings,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_plugin_registry: dict[tuple[str, str], type],
    mock_bot_config: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """Build the real app with create_pool / NatsClient / scoring loaders patched."""
    monkeypatch.setattr(
        "services.strategy_engine.app.main.create_pool",
        AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.main.NatsClient",
        MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.main.load_plugin_registry",
        MagicMock(return_value=mock_plugin_registry),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.main.load_bot_config",
        MagicMock(return_value=mock_bot_config),
    )
    return create_app(settings=settings)


@pytest.fixture
def client(app_with_mocks: FastAPI) -> Iterator[TestClient]:
    """TestClient that runs the lifespan on entry, teardown on exit."""
    with TestClient(app_with_mocks) as c:
        yield c
