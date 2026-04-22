"""Shared fixtures for signal-gateway unit tests.

The fixtures here let tests exercise the real :func:`create_app` factory
and its lifespan path without touching a real Postgres or NATS server:

* :func:`packages.db.create_pool` is monkey-patched at the
  ``services.signal_gateway.app.main`` boundary to return the mock pool.
* :class:`packages.bus.NatsClient` is monkey-patched similarly.

The lifespan then runs end-to-end against the mocks, attaching them to
``app.state`` exactly as it would in production. Tests mutate
``mock_bus.state`` or the pool-acquire behaviour to simulate outages for
``/ready`` coverage.

No testcontainers, no real I/O — those arrive with T-015b (full
integration test for ``/webhook``) and T-016 (CI-full wiring).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from packages.bus import ConnectionState
from services.signal_gateway.app.config import Settings
from services.signal_gateway.app.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings populated with values safe for in-process tests."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", "unit-test-secret-padded-32chars!")
    monkeypatch.setenv("NATS_URL", "nats://test-nats:4222")
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
def mock_pool() -> MagicMock:
    """asyncpg.Pool stand-in.

    ``pool.close()`` is async, so it's an :class:`AsyncMock`. ``pool.acquire``
    is synchronous (returns an async context manager). Tests override
    ``pool.acquire.return_value.__aenter__`` to simulate pool timeouts or
    :class:`asyncpg.InterfaceError` / :class:`asyncpg.PostgresError`.
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
    bus.state = ConnectionState.CONNECTED
    bus.connect = AsyncMock()
    bus.close = AsyncMock()
    return bus


@pytest.fixture
def app_with_mocks(
    settings: Settings,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """Build the real app, with ``create_pool`` / ``NatsClient`` patched to mocks."""
    monkeypatch.setattr(
        "services.signal_gateway.app.main.create_pool",
        AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(
        "services.signal_gateway.app.main.NatsClient",
        MagicMock(return_value=mock_bus),
    )
    return create_app(settings=settings)


@pytest.fixture
def client(app_with_mocks: FastAPI) -> Iterator[TestClient]:
    """TestClient that runs the lifespan on entry, teardown on exit."""
    with TestClient(app_with_mocks) as c:
        yield c
