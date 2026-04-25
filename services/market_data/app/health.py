"""Liveness and readiness routes for market-data-svc (§9.2, §18.4).

Two endpoints, both owned by the T-100 skeleton:

* ``GET /health`` — liveness. Returns ``200 {}`` whenever the process
  can handle a request. No dependencies, cannot fail while the service
  runs. Docker healthcheck target per §18.4. Mirrors signal-gateway.

* ``GET /ready`` — readiness. Returns ``200 {"ready": true}`` when
  **all three** of (a) :class:`packages.bus.NatsClient` is
  ``CONNECTED``, (b) an asyncpg pool connection acquires inside 1 s,
  and (c) :class:`packages.market.BinanceWsClient` is ``CONNECTED``.
  Otherwise ``503 {"ready": false, "reason": <bus|db|ws>}``. Reason
  precedence is bus → db → ws (cheapest check first; first failing
  reason wins). Distinct from ``/health`` so the container stays up
  (still emitting JSON logs, still exposing ``/metrics``) while a
  downstream dep is transiently unavailable.

  The ``ws`` check is new vs signal-gateway (which has no Binance WS).
  Returning ``503 reason="ws"`` during ``RECONNECTING`` / ``CONNECTING``
  is the deliberate F1 contract: operator visibility into outages
  beats grace-period heuristics that mask real Binance downtime.
  Future grace-period semantics (RECONNECTING < 30 s = still ready)
  are queued as a TASKS.md F1+ entry to avoid LB / k8s restart loops
  during transient Binance outages.

Both ``packages.bus`` and ``packages.market`` export an enum named
``ConnectionState``; aliased on import (``BusConnectionState`` /
``WsConnectionState``) to disambiguate at the call sites below.
"""

from __future__ import annotations

from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from packages.bus import ConnectionState as BusConnectionState
from packages.bus import NatsClient
from packages.market import BinanceWsClient
from packages.market import ConnectionState as WsConnectionState

from .deps import get_bus, get_pool, get_ws

__all__ = ["router"]


_POOL_ACQUIRE_TIMEOUT_SECONDS = 1.0


router = APIRouter(tags=["health"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health() -> dict[str, str]:
    """Liveness probe — always 200 while the process is alive."""
    return {}


@router.get("/ready")
async def ready(
    bus: Annotated[NatsClient, Depends(get_bus)],
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    ws: Annotated[BinanceWsClient, Depends(get_ws)],
) -> JSONResponse:
    """Readiness probe — 200 when bus + db + ws all healthy."""
    if bus.state is not BusConnectionState.CONNECTED:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"ready": False, "reason": "bus"},
        )
    try:
        async with pool.acquire(timeout=_POOL_ACQUIRE_TIMEOUT_SECONDS):
            pass
    except (TimeoutError, asyncpg.InterfaceError, asyncpg.PostgresError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"ready": False, "reason": "db"},
        )
    if ws.state is not WsConnectionState.CONNECTED:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"ready": False, "reason": "ws"},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"ready": True},
    )
