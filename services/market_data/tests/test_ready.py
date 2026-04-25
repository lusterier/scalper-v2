"""Tests for ``GET /ready`` readiness probe.

Three-check matrix: bus + db + ws. Reason precedence is bus → db → ws
(cheapest first; first failing reason wins). Each test mutates one
mock state into a non-CONNECTED / failing state and asserts the
corresponding 503 + ``reason`` string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import asyncpg

from packages.bus import ConnectionState as BusConnectionState
from packages.market import ConnectionState as WsConnectionState

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient


def test_ready_when_all_three_healthy(client: TestClient) -> None:
    """Happy path: bus CONNECTED, pool acquires, ws CONNECTED → 200."""
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True}


def test_not_ready_when_bus_disconnected(
    client: TestClient,
    mock_bus: MagicMock,
) -> None:
    """Bus not CONNECTED → 503 reason=bus; pool + ws checks short-circuited."""
    mock_bus.state = BusConnectionState.DISCONNECTED
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "bus"}


def test_not_ready_when_pool_acquire_times_out(
    client: TestClient,
    mock_pool: MagicMock,
) -> None:
    """Pool acquire raises TimeoutError → 503 reason=db; ws check short-circuited."""
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


def test_not_ready_when_ws_reconnecting(
    client: TestClient,
    mock_ws: MagicMock,
) -> None:
    """WS in RECONNECTING → 503 reason=ws (deliberate F1 contract; F1+ may add grace period)."""
    mock_ws.state = WsConnectionState.RECONNECTING
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "ws"}


def test_not_ready_when_ws_disconnected(
    client: TestClient,
    mock_ws: MagicMock,
) -> None:
    """WS DISCONNECTED (initial pre-connect or post-close) → 503 reason=ws."""
    mock_ws.state = WsConnectionState.DISCONNECTED
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "ws"}


def test_not_ready_when_ws_closed(
    client: TestClient,
    mock_ws: MagicMock,
) -> None:
    """WS CLOSED (post-shutdown handler shouldn't ever fire here, but cover) → 503 reason=ws."""
    mock_ws.state = WsConnectionState.CLOSED
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "ws"}


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


def test_reason_precedence_db_before_ws(
    client: TestClient,
    mock_pool: MagicMock,
    mock_ws: MagicMock,
) -> None:
    """Both pool and ws are sad → db wins (db check runs before ws check)."""
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(side_effect=TimeoutError())
    mock_ws.state = WsConnectionState.DISCONNECTED
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "db"}
