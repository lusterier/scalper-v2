"""Tests for JSON logging configuration and stdlib bridge."""

from __future__ import annotations

import io
import json
import logging
import re

from packages.core import CorrelationId, TraceId
from packages.observability.context import trace_scope
from packages.observability.logging import (
    configure,
    configure_stdlib_logging,
    get_logger,
)

_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")


def _first_line(stream: io.StringIO) -> dict[str, object]:
    text = stream.getvalue().strip()
    first = text.splitlines()[0]
    parsed = json.loads(first)
    assert isinstance(parsed, dict)
    return parsed


def test_log_emits_required_fields() -> None:
    stream = io.StringIO()
    configure(stream=stream)
    log = get_logger("execution", "trading")
    with trace_scope(TraceId("t1"), CorrelationId("c1")):
        log.info("order_placed", symbol="BTCUSDT", qty=0.01)
    rec = _first_line(stream)
    assert rec["event"] == "order_placed"
    assert rec["service"] == "execution"
    assert rec["log_stream"] == "trading"
    assert rec["level"] == "INFO"
    assert rec["trace_id"] == "t1"
    assert rec["correlation_id"] == "c1"
    assert rec["symbol"] == "BTCUSDT"
    assert rec["qty"] == 0.01
    assert isinstance(rec["timestamp"], str)
    assert _ISO_UTC.fullmatch(rec["timestamp"]) is not None


def test_message_field_preserved_alongside_event() -> None:
    stream = io.StringIO()
    configure(stream=stream)
    log = get_logger("exec", "trading")
    log.info("order_placed", message="placed 0.01 BTC at market", symbol="BTCUSDT")
    rec = _first_line(stream)
    assert rec["event"] == "order_placed"
    assert rec["message"] == "placed 0.01 BTC at market"


def test_redactor_applied_to_kwargs() -> None:
    stream = io.StringIO()
    configure(stream=stream)
    log = get_logger("exec", "system")
    log.info("auth_checked", api_key="s3cret", user_id="u1")
    rec = _first_line(stream)
    assert rec["api_key"] == "***"
    assert rec["user_id"] == "u1"


def test_level_is_uppercase() -> None:
    stream = io.StringIO()
    configure(stream=stream)
    log = get_logger("s", "system")
    log.warning("deprecation_noted")
    rec = _first_line(stream)
    assert rec["level"] == "WARNING"


def test_stdlib_bridge_routes_through_processor_chain() -> None:
    stream = io.StringIO()
    configure(stream=stream)
    logging.getLogger("httpx").info("request_sent")
    rec = _first_line(stream)
    assert rec["level"] == "INFO"
    assert rec["event"] == "request_sent"
    assert _ISO_UTC.fullmatch(str(rec["timestamp"])) is not None


def test_stdlib_bridge_redacts_extra_fields() -> None:
    stream = io.StringIO()
    configure(stream=stream)
    logging.getLogger("httpx").info("auth_header_set", extra={"authorization": "Bearer abc"})
    rec = _first_line(stream)
    assert rec["authorization"] == "***"


def test_configure_stdlib_logging_alone_produces_json() -> None:
    stream = io.StringIO()
    configure_stdlib_logging(stream=stream)
    logging.getLogger("uvicorn").info("server_started", extra={"port": 8000})
    rec = _first_line(stream)
    assert rec["event"] == "server_started"
    assert rec["port"] == 8000


def test_configure_replaces_existing_handlers() -> None:
    root = logging.getLogger()
    sentinel = logging.StreamHandler(io.StringIO())
    root.addHandler(sentinel)
    stream = io.StringIO()
    configure(stream=stream)
    assert sentinel not in root.handlers
    assert len(root.handlers) == 1


def test_exception_info_rendered_in_json() -> None:
    stream = io.StringIO()
    configure(stream=stream)
    log = get_logger("s", "system")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("caught_failure")
    rec = _first_line(stream)
    assert rec["event"] == "caught_failure"
    assert rec["level"] == "ERROR"
    assert "ValueError: boom" in str(rec["exception"])
