"""Liveness and readiness routes for signal-gateway (§9.1, §18.4).

Two endpoints, both owned by the skeleton in T-015a:

* ``GET /health`` — liveness. Returns ``200 {}`` whenever the process can
  handle a request. No dependencies, cannot fail while the service runs.
  Docker healthcheck target per §18.4.

* ``GET /ready`` — readiness. Returns ``200 {"ready": true}`` when the
  :class:`packages.bus.NatsClient` is ``CONNECTED`` **and** an asyncpg
  pool connection acquires inside 1 s; ``503 {"ready": false,
  "reason": <bus|db>}`` otherwise. Distinct from ``/health`` so the
  container stays up (still emitting JSON logs, still exposing
  ``/metrics``) while a downstream dep is transiently unavailable.
"""

from __future__ import annotations

from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from packages.bus import ConnectionState, NatsClient

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
    """Readiness probe — 200 when bus CONNECTED + pool acquires in 1 s."""
    if bus.state is not ConnectionState.CONNECTED:
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
