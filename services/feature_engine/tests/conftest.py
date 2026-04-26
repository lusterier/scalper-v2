"""Shared fixtures for feature-engine unit tests.

Lets tests exercise the real :func:`create_app` factory and its
lifespan path without touching real Postgres / NATS:

* :func:`packages.db.create_pool` is monkey-patched at the
  ``services.feature_engine.app.main`` boundary to return a mock pool.
* :class:`packages.bus.NatsClient` patched similarly.
* :func:`build_features` defaults to returning an empty registry so
  the lifespan exercises acquire_handles → warmup_load → start_consuming
  as no-ops. Tests that need a populated registry override the
  ``build_features`` patch with their own fixture.

The lifespan then runs end-to-end against the mocks, attaching pool /
bus / buffer_registry / pipeline to ``app.state`` exactly as it would
in production. Tests mutate ``mock_bus.state`` / pool-acquire behaviour
to simulate outages for the ``/ready`` coverage matrix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from packages.bus import ConnectionState as BusConnectionState
from services.feature_engine.app.config import Settings
from services.feature_engine.app.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings populated with values safe for in-process tests."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
    monkeypatch.setenv("NATS_URL", "nats://test-nats:4222")
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
def mock_pool() -> MagicMock:
    """asyncpg.Pool stand-in.

    ``pool.close()`` is async, so it's an :class:`AsyncMock`.
    ``pool.acquire`` is synchronous (returns an async context manager).
    """
    pool = MagicMock()
    pool.close = AsyncMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
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
    bus.kv_put = AsyncMock(return_value=1)
    return bus


@pytest.fixture
def app_with_mocks(
    settings: Settings,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """Build the real app with create_pool / NatsClient / build_features patched.

    ``build_features`` returns empty mapping by default so the T-110d
    lifespan goes acquire (no-op) → warmup (no-op) → start_consuming
    (no-op). Tests that need a populated registry override the
    ``build_features`` patch via their own monkeypatch.setattr call
    BEFORE calling this fixture (or build their own factory).
    """
    monkeypatch.setattr(
        "services.feature_engine.app.main.create_pool",
        AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(
        "services.feature_engine.app.main.NatsClient",
        MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(
        "services.feature_engine.app.main.build_features",
        lambda: {},
    )
    return create_app(settings=settings)


@pytest.fixture
def client(app_with_mocks: FastAPI) -> Iterator[TestClient]:
    """TestClient that runs the lifespan on entry, teardown on exit."""
    with TestClient(app_with_mocks) as c:
        yield c
