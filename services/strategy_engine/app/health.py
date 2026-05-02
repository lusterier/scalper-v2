"""Liveness and readiness routes for strategy-engine (§9.4, §18.4).

Two endpoints, both owned by the T-309 skeleton:

* ``GET /health`` — liveness. Returns ``200 {}`` whenever the process
  can handle a request. No dependencies, cannot fail while the service
  runs. Docker healthcheck target per §18.4. Mirrors execution-service
  / feature-engine / market-data-svc / signal-gateway verbatim.

* ``GET /ready`` — readiness. Returns ``200 {"ready": true}`` when
  **both** of (a) :class:`packages.bus.NatsClient` is ``CONNECTED`` and
  (b) an asyncpg pool connection acquires inside 1 s. Otherwise
  ``503 {"ready": false, "reason": <bus|db>}``. Reason precedence is
  bus → db (cheapest check first; first failing reason wins). The
  ``reason="db"`` key matches T-100/T-109/T-214 verbatim so monitoring
  alert rules work uniformly across services. Distinct from ``/health``
  so the container stays up (still emitting JSON logs, still exposing
  ``/metrics``) while a downstream dep is transiently unavailable.

T-309 ships ``bus`` + ``db`` reasons only. Bot config + plugin registry
load at lifespan startup; missing/invalid → service crash, by which
point ``/ready`` is unreachable. No third readiness reason needed.
"""

from __future__ import annotations

from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from packages.bus import ConnectionState as BusConnectionState
from packages.bus import NatsClient

from .deps import get_bus, get_pool

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
) -> JSONResponse:
    """Readiness probe — 200 when bus + db both healthy."""
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
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"ready": True},
    )
