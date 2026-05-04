"""Liveness and readiness routes for alerting-svc (T-409, §18.4).

Two endpoints:

* ``GET /health`` — liveness. Returns ``200 {}`` whenever the process can
  handle a request. Mirror of analytics-api / strategy-engine.

* ``GET /ready`` — readiness. Returns ``200 {"ready": true}`` when the NATS
  bus is ``CONNECTED`` (alerting-svc has no DB; only NATS + Telegram are
  external deps). Reason precedence: ``bus`` is the only failure mode in
  F4. Distinct from ``/health`` so the container stays up while NATS is
  transiently unavailable (still emits JSON logs + ``/metrics``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from packages.bus import ConnectionState as BusConnectionState
from packages.bus import NatsClient

from .deps import get_bus

__all__ = ["router"]


router = APIRouter(tags=["health"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health() -> dict[str, str]:
    """Liveness probe — always 200 while the process is alive."""
    return {}


@router.get("/ready")
async def ready(
    bus: Annotated[NatsClient, Depends(get_bus)],
) -> JSONResponse:
    """Readiness probe — 200 when bus is CONNECTED."""
    if bus.state is not BusConnectionState.CONNECTED:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"ready": False, "reason": "bus"},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"ready": True},
    )
