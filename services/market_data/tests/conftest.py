"""Shared fixtures for market-data-svc unit tests.

Lets tests exercise the real :func:`create_app` factory and its
lifespan path without touching real Postgres / NATS / Binance:

* :func:`packages.db.create_pool` is monkey-patched at the
  ``services.market_data.app.main`` boundary to return a mock pool.
* :class:`packages.bus.NatsClient` patched similarly.
* :class:`packages.market.BinanceWsClient` patched so ``ws.run()`` is
  a tame no-op coroutine that hangs on a never-set
  :class:`asyncio.Event` until the lifespan's ``ws.close()`` cancels
  it on shutdown — mirrors the production loop's "wait until close"
  shape without the actual socket.

The lifespan then runs end-to-end against the mocks, attaching them
to ``app.state`` exactly as it would in production. Tests mutate
``mock_bus.state`` / ``mock_ws.state`` / pool-acquire behaviour to
simulate outages for the ``/ready`` coverage matrix.

No testcontainers, no real I/O — the integration test for the full
live composition (mock-WS server feeding closed-bucket frames) is
deferred to T-F1+ per the design proposal.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from packages.bus import ConnectionState as BusConnectionState
from packages.market import ConnectionState as WsConnectionState
from services.market_data.app.config import Settings
from services.market_data.app.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings populated with values safe for in-process tests."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
    monkeypatch.setenv("NATS_URL", "nats://test-nats:4222")
    monkeypatch.delenv("MARKET_DATA_SYMBOLS", raising=False)
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
def mock_pool() -> MagicMock:
    """asyncpg.Pool stand-in.

    ``pool.close()`` is async, so it's an :class:`AsyncMock`.
    ``pool.acquire`` is synchronous (returns an async context manager).
    Tests override ``pool.acquire.return_value.__aenter__`` to simulate
    pool timeouts or :class:`asyncpg.InterfaceError` /
    :class:`asyncpg.PostgresError`.
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
    return bus


@pytest.fixture
def mock_ws() -> MagicMock:
    """BinanceWsClient stand-in.

    ``run()`` returns a coroutine that hangs on a never-set
    :class:`asyncio.Event` so the lifespan-spawned ``ws_task`` looks
    "running"; ``close()`` sets the event so the task exits cleanly,
    matching the production "ws.close → loop exits via state=CLOSED"
    contract from T-101b. Defaults to ``CONNECTED``; tests mutate
    ``.state`` for ``/ready reason="ws"`` coverage.
    """
    ws = MagicMock()
    ws.state = WsConnectionState.CONNECTED
    closed = asyncio.Event()

    async def _run() -> None:
        await closed.wait()

    async def _close() -> None:
        closed.set()

    async def _add_stream(_stream: str) -> None:
        return None

    async def _remove_stream(_stream: str) -> None:
        return None

    ws.run = _run
    ws.close = _close
    ws.add_stream = _add_stream
    ws.remove_stream = _remove_stream
    ws.streams = frozenset()
    return ws


@pytest.fixture
def app_with_mocks(
    settings: Settings,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_ws: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """Build the real app with create_pool / NatsClient / BinanceWsClient patched."""
    monkeypatch.setattr(
        "services.market_data.app.main.create_pool",
        AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(
        "services.market_data.app.main.NatsClient",
        MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(
        "services.market_data.app.main.BinanceWsClient",
        MagicMock(return_value=mock_ws),
    )
    return create_app(settings=settings)


@pytest.fixture
def client(app_with_mocks: FastAPI) -> Iterator[TestClient]:
    """TestClient that runs the lifespan on entry, teardown on exit."""
    with TestClient(app_with_mocks) as c:
        yield c
