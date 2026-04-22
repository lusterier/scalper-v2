"""Tests for ``GET /ready`` readiness probe."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import asyncpg

from packages.bus import ConnectionState

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient


def test_ready_when_bus_connected_and_pool_acquires(client: TestClient) -> None:
    """Happy path: bus CONNECTED, pool acquires cleanly → 200 + ready:true."""
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True}


def test_not_ready_when_bus_disconnected(
    client: TestClient,
    mock_bus: MagicMock,
) -> None:
    """Bus not CONNECTED → 503 with reason=bus; pool check is short-circuited."""
    mock_bus.state = ConnectionState.DISCONNECTED
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "bus"}


def test_not_ready_when_pool_acquire_times_out(
    client: TestClient,
    mock_pool: MagicMock,
) -> None:
    """Pool acquire raises TimeoutError → 503 with reason=db."""
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(side_effect=TimeoutError())
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "db"}


def test_not_ready_when_pool_raises_interface_error(
    client: TestClient,
    mock_pool: MagicMock,
) -> None:
    """Pool raises :class:`asyncpg.InterfaceError` → 503 with reason=db."""
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(
        side_effect=asyncpg.InterfaceError("connection closed"),
    )
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "db"}
