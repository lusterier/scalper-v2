"""Observability primitives: JSON logging, trace context, metrics (§15, §5.7).

Services call :func:`configure` once at startup, acquire a logger per
log stream via :func:`get_logger`, and bind a trace ID (and optionally
a correlation ID) at every request/task entry via :func:`trace_scope`
or :func:`bind_trace`. Standard metric definitions live with the
services that own them (§15.3); this package ships only wiring
(:func:`make_registry`, :func:`make_metrics_asgi_app`) plus the
secret redactor used by the logging processor chain.
"""

from __future__ import annotations

from .context import (
    bind_correlation,
    bind_trace,
    clear_context,
    get_correlation_id,
    get_trace_id,
    new_trace_id,
    trace_scope,
)
from .logging import LogStream, configure, configure_stdlib_logging, get_logger
from .metrics import make_metrics_asgi_app, make_registry
from .redact import add_redacted_keys

__all__ = [
    "LogStream",
    "add_redacted_keys",
    "bind_correlation",
    "bind_trace",
    "clear_context",
    "configure",
    "configure_stdlib_logging",
    "get_correlation_id",
    "get_logger",
    "get_trace_id",
    "make_metrics_asgi_app",
    "make_registry",
    "new_trace_id",
    "trace_scope",
]
