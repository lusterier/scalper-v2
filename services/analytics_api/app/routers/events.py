"""``/events/stream`` SSE multiplexed endpoint (T-408, BRIEF §9.6:1633-1638).

Single endpoint exposing Server-Sent Events stream for the dashboard UI.
Per-connection ephemeral NATS subscriptions (OQ-2=A) — connection close
drains subscriptions via :meth:`SSEMultiplexer.unregister_client` in the
generator's ``finally`` block.

Wire format per BRIEF §9.6:1638: each event is ``data: {"type": "...",
"payload": {...}}\\n\\n``. Heartbeat is SSE comment line ``: heartbeat\\n\\n``
emitted every ``settings.sse_heartbeat_interval_s`` (default 15s) when no
data event has flowed.

WG references (T-408 plan-reviewer APPROVE pass-2):

* WG#7 — test #6 docstring split: this generator only consumes
  ``handle.queue``; envelope→event mapping is owned by
  :func:`services.analytics_api.app.sse._envelope_to_sse_event`.
* WG#8 — exact 422 error strings per :func:`parse_types`.
* WG#10 — response headers ``text/event-stream`` + ``X-Accel-Buffering: no``
  + ``Cache-Control: no-cache``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ..deps import get_sse_multiplexer
from ..models.events import parse_types
from ..sse import SSEConnectionLimitError, SSEMultiplexer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = ["router"]


router = APIRouter(tags=["events"])


@router.get("/events/stream")
async def stream_events(
    request: Request,
    multiplexer: Annotated[SSEMultiplexer, Depends(get_sse_multiplexer)],
    types: Annotated[
        str,
        Query(
            description=(
                "Comma-separated event types: positions, signals, trades, scoring, alerts."
            ),
        ),
    ] = "",
) -> StreamingResponse:
    """Multiplexed SSE stream of NATS events.

    422 on empty / unknown ``?types=``. 503 on max connections reached.
    """
    try:
        parsed_types = parse_types(types)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    try:
        handle = await multiplexer.register_client(parsed_types)
    except SSEConnectionLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(f"max SSE connections reached ({multiplexer.max_connections}); retry later"),
        ) from exc

    heartbeat_interval = multiplexer.heartbeat_interval_s

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            sorted_types = ",".join(sorted(t.value for t in parsed_types))
            yield f": connected types={sorted_types}\n\n".encode()
            heartbeat_at = time.monotonic() + heartbeat_interval
            while True:
                # is_disconnected() is best-effort per Edge case #5 — finally
                # block is the load-bearing cleanup mechanism (broken pipe on
                # yield to closed connection raises, finally runs cleanup).
                if await request.is_disconnected():
                    break
                timeout = max(0.0, heartbeat_at - time.monotonic())
                try:
                    event = await asyncio.wait_for(handle.queue.get(), timeout=timeout)
                except TimeoutError:
                    yield b": heartbeat\n\n"
                    heartbeat_at = time.monotonic() + heartbeat_interval
                    continue
                if event is None:
                    # Sentinel from unregister_client / shutdown — break loop.
                    break
                payload_json = json.dumps(event)
                yield f"data: {payload_json}\n\n".encode()
                heartbeat_at = time.monotonic() + heartbeat_interval
        finally:
            # Idempotent — multiplexer.shutdown may have already run unregister.
            with contextlib.suppress(Exception):
                await multiplexer.unregister_client(handle)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
