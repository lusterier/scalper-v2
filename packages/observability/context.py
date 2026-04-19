"""Trace and correlation ID propagation via structlog contextvars (§15.2).

Trace IDs are generated per request/task by the owning service and bound
to the current async context; all log lines emitted by that context
automatically include the ID via structlog's ``merge_contextvars``
processor. Correlation IDs are propagated the same way and link
signal → orders → events for post-hoc debugging.

Services call ``bind_trace(new_trace_id())`` at request entry (or use
``trace_scope()`` as a context manager) and ``bind_correlation(cid)``
when the correlation ID becomes known (typically on webhook ingest).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import structlog

from packages.core import CorrelationId, TraceId

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "bind_correlation",
    "bind_trace",
    "clear_context",
    "get_correlation_id",
    "get_trace_id",
    "new_trace_id",
    "trace_scope",
]


def new_trace_id() -> TraceId:
    """Generate a fresh trace ID (UUID4 hex, 32 chars, no dashes)."""
    return TraceId(uuid.uuid4().hex)


def bind_trace(trace_id: TraceId) -> None:
    """Bind ``trace_id`` to the current async context."""
    structlog.contextvars.bind_contextvars(trace_id=trace_id)


def bind_correlation(correlation_id: CorrelationId) -> None:
    """Bind ``correlation_id`` to the current async context."""
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)


def get_trace_id() -> TraceId | None:
    """Return the trace ID bound to the current context, or ``None``."""
    value = structlog.contextvars.get_contextvars().get("trace_id")
    return TraceId(value) if isinstance(value, str) else None


def get_correlation_id() -> CorrelationId | None:
    """Return the correlation ID bound to the current context, or ``None``."""
    value = structlog.contextvars.get_contextvars().get("correlation_id")
    return CorrelationId(value) if isinstance(value, str) else None


def clear_context() -> None:
    """Clear all structlog contextvar bindings for the current context."""
    structlog.contextvars.clear_contextvars()


@contextmanager
def trace_scope(
    trace_id: TraceId | None = None,
    correlation_id: CorrelationId | None = None,
) -> Iterator[TraceId]:
    """Bind trace (and optional correlation) IDs for the enclosed block.

    Generates a fresh trace ID if none is passed. Restores the prior
    context on exit — bindings present before the ``with`` block are
    preserved, bindings added inside are unwound.

    Typical use in a service entry point::

        with trace_scope() as tid:
            log.info("request_received")
            # ... handle request ...
    """
    tid = trace_id if trace_id is not None else new_trace_id()
    bindings: dict[str, Any] = {"trace_id": tid}
    if correlation_id is not None:
        bindings["correlation_id"] = correlation_id
    with structlog.contextvars.bound_contextvars(**bindings):
        yield tid
