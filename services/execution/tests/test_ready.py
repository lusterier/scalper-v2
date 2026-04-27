"""Tests for ``GET /ready`` readiness probe.

Two-check matrix: bus + db. Reason precedence is bus → db (cheapest
first; first failing reason wins). Each test mutates one mock state
into a non-CONNECTED / failing state and asserts the corresponding
503 + ``reason`` string.

T-214 ships ``bus`` + ``db`` reasons only. T-209 will add a third
``exchange_ws`` reason when the private Bybit WS connection lands —
private-WS readiness is a load-bearing health signal because order-
event dispatch (T-218) depends on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import asyncpg

from packages.bus import ConnectionState as BusConnectionState

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient


def test_ready_when_bus_and_db_healthy(client: TestClient) -> None:
    """Happy path: bus CONNECTED, pool acquires → 200."""
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True}


def test_not_ready_when_bus_disconnected(
    client: TestClient,
    mock_bus: MagicMock,
) -> None:
    """Bus not CONNECTED → 503 reason=bus; pool check short-circuited."""
    mock_bus.state = BusConnectionState.DISCONNECTED
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "bus"}


def test_not_ready_when_pool_acquire_times_out(
    client: TestClient,
    mock_pool: MagicMock,
) -> None:
    """Pool acquire raises TimeoutError → 503 reason=db."""
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(side_effect=TimeoutError())
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "db"}


def test_not_ready_when_pool_raises_interface_error(
    client: TestClient,
    mock_pool: MagicMock,
) -> None:
    """Pool raises :class:`asyncpg.InterfaceError` → 503 reason=db."""
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(
        side_effect=asyncpg.InterfaceError("connection closed"),
    )
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "db"}


def test_reason_precedence_bus_before_db(
    client: TestClient,
    mock_bus: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Both bus and pool are sad simultaneously → bus wins (cheaper check first)."""
    mock_bus.state = BusConnectionState.DISCONNECTED
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(side_effect=TimeoutError())
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "bus"}
