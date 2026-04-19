"""structlog JSON logging + stdlib bridge (§5.7, §15.1, §3.1).

Services call :func:`configure` once at startup. After that:

* Acquire a structlog logger with :func:`get_logger`, which binds the
  ``service`` and ``log_stream`` fields required by §15.1.
* Stdlib ``logging.getLogger()`` records (httpx, uvicorn, FastAPI, etc.)
  are routed through the same processor chain via
  :func:`configure_stdlib_logging`, so every line on stdout is JSON.

Every record carries: ``timestamp`` (ISO-8601 UTC with ``+00:00``),
``level`` (uppercase), ``service``, ``log_stream``, ``event``, plus any
``trace_id``/``correlation_id`` bound on the current context.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any, Literal

import structlog

from packages.core import now_utc

from .redact import redactor

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger
    from structlog.types import EventDict, Processor, WrappedLogger

__all__ = [
    "LogStream",
    "configure",
    "configure_stdlib_logging",
    "get_logger",
]


LogStream = Literal["trading", "audit", "system"]


_LEVEL_ALIAS = {"warn": "warning", "exception": "error"}


def _add_timestamp(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Stamp the record with ``now_utc()`` as ISO-8601 with ``+00:00`` (§5.12)."""
    event_dict["timestamp"] = now_utc().isoformat()
    return event_dict


def _add_log_level_upper(
    _logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Set ``level`` to the UPPERCASE level name, matching §15.1."""
    name = _LEVEL_ALIAS.get(method_name, method_name)
    event_dict["level"] = name.upper()
    return event_dict


def _pre_chain() -> list[Processor]:
    """Processors applied before the final render, for both structlog and stdlib records."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.ExtraAdder(),
        _add_log_level_upper,
        _add_timestamp,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def _render_chain() -> list[Processor]:
    """Processors that transform and render the final event dict."""
    return [
        redactor,
        structlog.processors.JSONRenderer(sort_keys=True),
    ]


def configure_stdlib_logging(
    *,
    level: int | str = logging.INFO,
    stream: Any = None,
) -> None:
    """Route stdlib :mod:`logging` records through the JSON processor chain.

    Installs a single :class:`logging.StreamHandler` on the root logger
    using :class:`structlog.stdlib.ProcessorFormatter`, then clears any
    pre-existing handlers to prevent double-emission. Called implicitly
    by :func:`configure`; exposed separately for callers that want the
    stdlib bridge without touching structlog's own configuration.
    """
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_pre_chain(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *_render_chain(),
        ],
    )
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)


def configure(
    *,
    level: int | str = logging.INFO,
    stream: Any = None,
) -> None:
    """Configure structlog + stdlib logging for JSON-Lines stdout output.

    Idempotent: existing root-logger handlers are removed and replaced,
    and ``structlog.configure`` resets any prior configuration. Call
    once at service startup before acquiring any logger.
    """
    configure_stdlib_logging(level=level, stream=stream)
    structlog.configure(
        processors=[
            *_pre_chain(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_logger(service: str, log_stream: LogStream) -> BoundLogger:
    """Return a structlog logger with ``service`` and ``log_stream`` pre-bound.

    Services typically acquire one logger per stream at startup::

        trading = get_logger("execution", "trading")
        system = get_logger("execution", "system")
    """
    return structlog.stdlib.get_logger().bind(service=service, log_stream=log_stream)
