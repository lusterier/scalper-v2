"""§N4 unit tests for :mod:`packages.db.queries.audit` (T-401a).

Mock-based: ``conn.fetchrow`` returns canned values. Pin the public
contract:

* SQL shape: ``INSERT ... RETURNING id`` with 8 placeholders matching
  migration 0011 column order (occurred_at, actor, action, entity_type,
  entity_id, before_state, after_state, correlation_id).
* JSONB serialisation: dict ``before_state`` / ``after_state`` are
  passed directly to asyncpg as Python dicts — the registered JSONB
  codec handles ``json.dumps`` once. UUID / datetime / Decimal in the
  state dict are pre-converted to strings by :func:`_to_jsonable`
  (so the codec encoder never sees a non-JSON-native type).
* ``None`` for ``before_state`` (create) / ``after_state`` (delete)
  passes through as SQL ``NULL``.
* :func:`packages.core.is_non_idempotent` returns True for the helper
  per §N3 / §5.8 + WG#6.
* Empty ``RETURNING`` row raises :class:`RuntimeError` (defensive).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

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
    # Args 6/7 (1-indexed: $6/$7) are dicts passed directly to asyncpg —
    # registered JSONB codec serialises once. Pre-fix this used to be
    # ``json.dumps(before/after)`` which double-encoded under the codec.
    sql, *bind_args = captured_args[0]
    assert "INSERT INTO audit_events" in sql
    assert "RETURNING id" in sql
    assert bind_args[0] == _FIXED_NOW
    assert bind_args[1] == "lan:10.0.0.1"
    assert bind_args[2] == "symbol_map.update"
    assert bind_args[3] == "symbol_map"
    assert bind_args[4] == "BTCUSDT.P"
    assert bind_args[5] == before
    assert bind_args[6] == after
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


async def test_insert_audit_event_pre_converts_uuid_datetime_decimal_for_jsonb_codec() -> None:
    """Regression (F4 E1 smoke double-encode fix): UUID/datetime/Decimal pre-stringified in dict.

    Dataclass row projections (BacktestRunRow / BotConfigRow / …) carry
    ``UUID`` ids + ``TIMESTAMPTZ`` datetimes + ``NUMERIC`` Decimals.
    The helper now passes a Python dict (with these types
    pre-converted to strings via :func:`_to_jsonable`) directly to
    asyncpg; the registered JSONB codec serialises once.

    Pre-fix path was ``json.dumps(state, default=str)`` which under
    analytics-api's registered codec
    (``conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads)``)
    encoded the resulting string a SECOND time, storing a JSON string
    scalar (escaped ``"{\\"id\\":1,...}"``) instead of a JSONB object.
    Surfaced live during F4 E1 smoke when ``POST /api/backtests/`` 500-d
    initially (UUID encode fail) then read-back of bot_config.apply
    audit row returned ``after_state: null`` (Pydantic dict-coercion
    fail on the string scalar).
    """
    conn = MagicMock()
    captured_args: list[tuple[Any, ...]] = []

    async def _capture(_sql: str, *args: Any, **_kwargs: Any) -> dict[str, int]:
        captured_args.append(args)
        return {"id": 1}

    conn.fetchrow = _capture
    run_id = UUID("00000000-0000-0000-0000-000000000001")
    started_at = datetime(2026, 5, 7, 14, 13, 10, tzinfo=UTC)
    after_state: dict[str, Any] = {
        "id": run_id,
        "started_at": started_at,
        "qty": Decimal("0.001"),
        "name": "smoke E1 trigger",
        "nested": {"sub_id": run_id, "sub_qty": Decimal("1.5")},
    }
    await insert_audit_event(
        conn,
        occurred_at=_FIXED_NOW,
        actor="lan:192.168.100.100",
        action="backtest.queued",
        entity_type="backtest_run",
        entity_id=str(run_id),
        before_state=None,
        after_state=after_state,
        correlation_id=None,
    )
    after_arg = captured_args[0][6]
    # Passed as dict (NOT pre-serialised string) → codec serialises once.
    assert isinstance(after_arg, dict)
    assert after_arg["id"] == "00000000-0000-0000-0000-000000000001"
    assert after_arg["started_at"].startswith("2026-05-07")
    assert after_arg["started_at"].endswith("+00:00")  # §N1 explicit offset preserved
    assert after_arg["qty"] == "0.001"  # §5.3 Decimal precision preserved as str
    assert after_arg["name"] == "smoke E1 trigger"
    # Nested dict / Decimal also recursively converted.
    assert after_arg["nested"]["sub_id"] == "00000000-0000-0000-0000-000000000001"
    assert after_arg["nested"]["sub_qty"] == "1.5"
    # Final guarantee: asyncpg's JSONB codec encoder (json.dumps) must
    # succeed on the converted dict — emulate the codec call here.
    import json as _json

    assert _json.dumps(after_arg)  # no TypeError
