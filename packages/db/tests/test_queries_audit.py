"""§N4 unit tests for :mod:`packages.db.queries.audit` (T-401a).

Mock-based: ``conn.fetchrow`` returns canned values. Pin the public
contract:

* SQL shape: ``INSERT ... RETURNING id`` with 8 placeholders matching
  migration 0011 column order (occurred_at, actor, action, entity_type,
  entity_id, before_state, after_state, correlation_id).
* JSONB serialisation: dict ``before_state`` / ``after_state`` go
  through :func:`json.dumps` (mirror :func:`packages.db.queries.signal_gateway.insert_signal`
  pattern; default asyncpg codec doesn't auto-convert dicts to jsonb).
* ``None`` for ``before_state`` (create) / ``after_state`` (delete)
  passes through as SQL ``NULL``.
* :func:`packages.core.is_non_idempotent` returns True for the helper
  per §N3 / §5.8 + WG#6.
* Empty ``RETURNING`` row raises :class:`RuntimeError` (defensive).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.core import is_non_idempotent
from packages.db.queries.audit import insert_audit_event

_FIXED_NOW = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


async def test_insert_audit_event_returns_generated_id() -> None:
    """Happy path: helper returns BIGSERIAL ``id`` from RETURNING."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 7})
    result = await insert_audit_event(
        conn,
        occurred_at=_FIXED_NOW,
        actor="lan:127.0.0.1",
        action="symbol_map.create",
        entity_type="symbol_map",
        entity_id="BTCUSDT.P",
        before_state=None,
        after_state={"input_symbol": "BTCUSDT.P", "canonical_symbol": "BTCUSDT"},
        correlation_id="cid-1",
    )
    assert result == 7
    assert isinstance(result, int)


async def test_insert_audit_event_serialises_state_dicts_to_json() -> None:
    """``before_state`` / ``after_state`` dicts pass through ``json.dumps``."""
    conn = MagicMock()
    captured_args: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any, **_kwargs: Any) -> dict[str, int]:
        captured_args.append((sql, *args))
        return {"id": 1}

    conn.fetchrow = _capture
    before = {"canonical_symbol": "OLD"}
    after = {"canonical_symbol": "NEW"}
    await insert_audit_event(
        conn,
        occurred_at=_FIXED_NOW,
        actor="lan:10.0.0.1",
        action="symbol_map.update",
        entity_type="symbol_map",
        entity_id="BTCUSDT.P",
        before_state=before,
        after_state=after,
        correlation_id="cid-update",
    )
    # Args 6/7 (1-indexed: $6/$7) are JSON-serialised dicts.
    sql, *bind_args = captured_args[0]
    assert "INSERT INTO audit_events" in sql
    assert "RETURNING id" in sql
    assert bind_args[0] == _FIXED_NOW
    assert bind_args[1] == "lan:10.0.0.1"
    assert bind_args[2] == "symbol_map.update"
    assert bind_args[3] == "symbol_map"
    assert bind_args[4] == "BTCUSDT.P"
    assert json.loads(bind_args[5]) == before
    assert json.loads(bind_args[6]) == after
    assert bind_args[7] == "cid-update"


async def test_insert_audit_event_passes_none_state_through_as_null() -> None:
    """``before_state=None`` (create) / ``after_state=None`` (delete) → SQL NULL."""
    conn = MagicMock()
    captured_args: list[tuple[Any, ...]] = []

    async def _capture(_sql: str, *args: Any, **_kwargs: Any) -> dict[str, int]:
        captured_args.append(args)
        return {"id": 1}

    conn.fetchrow = _capture
    await insert_audit_event(
        conn,
        occurred_at=_FIXED_NOW,
        actor="system",
        action="test.no_state_change",
        entity_type="test_entity",
        entity_id="id-1",
        before_state=None,
        after_state=None,
        correlation_id=None,
    )
    bind_args = captured_args[0]
    # Both state slots forwarded as None (SQL NULL via $::jsonb cast).
    assert bind_args[5] is None
    assert bind_args[6] is None
    # correlation_id None propagates too.
    assert bind_args[7] is None


def test_insert_audit_event_marker_is_non_idempotent() -> None:
    """``@non_idempotent`` decorator marker pin per §N3 / §5.8 + WG#6."""
    assert is_non_idempotent(insert_audit_event)
    assert insert_audit_event.__non_idempotent__ is True  # type: ignore[attr-defined]


async def test_insert_audit_event_raises_when_returning_row_is_none() -> None:
    """Defensive: empty RETURNING row → RuntimeError (mirror insert_signal pattern)."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    with pytest.raises(RuntimeError, match="produced no row"):
        await insert_audit_event(
            conn,
            occurred_at=_FIXED_NOW,
            actor="system",
            action="t",
            entity_type="t",
            entity_id="id",
            before_state=None,
            after_state=None,
            correlation_id=None,
        )
