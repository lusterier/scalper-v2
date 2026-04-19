"""Tests for trace/correlation ID propagation via structlog contextvars."""

from __future__ import annotations

import asyncio
import re

from packages.core import CorrelationId, TraceId
from packages.observability.context import (
    bind_correlation,
    bind_trace,
    clear_context,
    get_correlation_id,
    get_trace_id,
    new_trace_id,
    trace_scope,
)

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def test_new_trace_id_is_32_hex_chars() -> None:
    tid = new_trace_id()
    assert _HEX32.fullmatch(tid) is not None


def test_new_trace_id_returns_unique_values() -> None:
    assert new_trace_id() != new_trace_id()


def test_bind_and_get_trace_id() -> None:
    assert get_trace_id() is None
    bind_trace(TraceId("t-abc"))
    assert get_trace_id() == "t-abc"


def test_bind_and_get_correlation_id() -> None:
    assert get_correlation_id() is None
    bind_correlation(CorrelationId("cid-123"))
    assert get_correlation_id() == "cid-123"


def test_clear_context_removes_all_bindings() -> None:
    bind_trace(TraceId("t"))
    bind_correlation(CorrelationId("c"))
    clear_context()
    assert get_trace_id() is None
    assert get_correlation_id() is None


def test_trace_scope_generates_id_when_absent() -> None:
    with trace_scope() as tid:
        assert _HEX32.fullmatch(tid) is not None
        assert get_trace_id() == tid


def test_trace_scope_uses_supplied_id() -> None:
    with trace_scope(TraceId("fixed")) as tid:
        assert tid == "fixed"
        assert get_trace_id() == "fixed"


def test_trace_scope_restores_prior_trace_id_on_exit() -> None:
    bind_trace(TraceId("outer"))
    with trace_scope(TraceId("inner")):
        assert get_trace_id() == "inner"
    assert get_trace_id() == "outer"


def test_trace_scope_optionally_binds_correlation() -> None:
    with trace_scope(TraceId("t"), CorrelationId("c")):
        assert get_trace_id() == "t"
        assert get_correlation_id() == "c"
    assert get_trace_id() is None
    assert get_correlation_id() is None


def test_trace_scope_preserves_outer_correlation_when_none_passed() -> None:
    bind_correlation(CorrelationId("outer-cid"))
    with trace_scope(TraceId("t")):
        assert get_correlation_id() == "outer-cid"


async def test_trace_scope_isolates_across_asyncio_tasks() -> None:
    async def child() -> str | None:
        with trace_scope(TraceId("child-tid")):
            return get_trace_id()

    with trace_scope(TraceId("parent-tid")):
        result = await asyncio.create_task(child())
        assert result == "child-tid"
        assert get_trace_id() == "parent-tid"
