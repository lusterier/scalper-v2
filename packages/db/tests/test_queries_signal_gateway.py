"""§N4 unit tests for :mod:`packages.db.queries.signal_gateway` T-310a additions.

Mock-based: ``conn.fetchrow`` returns canned values. Integration coverage
for the composite-index lookup behaviour lives in
``tests/integration/queries/test_signal_gateway.py`` (testcontainer-gated)
per L-008 active control for non-trivial SQL with hypertable chunk pruning.

T-015b2 covered ``fetch_symbol_mapping`` + ``insert_signal`` ad-hoc; T-310a
adds the first dedicated mock-test file for the module, scoped to the new
``select_signal_id_by_idempotency_key`` helper.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from packages.core import is_idempotent
from packages.db.queries.signal_gateway import select_signal_id_by_idempotency_key

_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
_LOWER_BOUND = _FIXED_NOW - timedelta(seconds=600)


async def test_select_signal_id_by_idempotency_key_returns_int_on_match() -> None:
    """Happy path: matching row returns ``signals.id`` as int."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 42})
    result = await select_signal_id_by_idempotency_key(
        conn,
        idempotency_key="key-1",
        received_at_lower_bound=_LOWER_BOUND,
    )
    assert result == 42
    assert isinstance(result, int)


async def test_select_signal_id_by_idempotency_key_returns_none_on_no_match() -> None:
    """No row → ``None`` (not exception); strategy-engine handles None as "not found"."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    result = await select_signal_id_by_idempotency_key(
        conn,
        idempotency_key="missing-key",
        received_at_lower_bound=_LOWER_BOUND,
    )
    assert result is None


async def test_select_signal_id_uses_received_at_lower_bound_in_where_clause() -> None:
    """WG#3 + L-008: SQL MUST contain `received_at >= $2` for Timescale chunk pruning."""
    conn = MagicMock()
    captured_args: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any, **_kwargs: Any) -> dict[str, int] | None:
        captured_args.append((sql, *args))
        return {"id": 1}

    conn.fetchrow = _capture
    await select_signal_id_by_idempotency_key(
        conn,
        idempotency_key="k",
        received_at_lower_bound=_LOWER_BOUND,
    )
    sql = captured_args[0][0]
    # Pin the literal Timescale chunk-pruning predicate.
    assert "received_at >= $2" in sql
    assert "idempotency_key = $1" in sql
    # Pin the top-K idiom (defensive against multiple matches in window).
    assert "ORDER BY received_at DESC" in sql
    assert "LIMIT 1" in sql
    # Bind values forwarded.
    assert captured_args[0][1] == "k"
    assert captured_args[0][2] == _LOWER_BOUND


def test_select_signal_id_by_idempotency_key_marker_is_idempotent() -> None:
    """`@idempotent` decorator pin per §11.2 retry matrix (read-only SELECT)."""
    assert is_idempotent(select_signal_id_by_idempotency_key)
