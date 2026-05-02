"""Integration tests for :mod:`packages.db.queries.signal_gateway` (T-310a).

Runs against a throwaway PostgreSQL + TimescaleDB migrated to head
(includes migration 0002 ``signals`` hypertable + composite UNIQUE
``signals_idempotency (idempotency_key, received_at)`` index).

T-310a-scoped: verifies ``select_signal_id_by_idempotency_key`` returns
the inserted ``signals.id`` for a matching ``idempotency_key`` within
the ``received_at_lower_bound`` window, and ``None`` for a row outside
the window. Per L-008 active control DB-level pin for non-trivial SQL
with composite-index range scan + Timescale chunk pruning.

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset (per
``conftest.py`` shared with sibling integration test files).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import asyncpg
import pytest

from packages.db.queries.signal_gateway import (
    insert_signal,
    select_signal_id_by_idempotency_key,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

type _Conn = asyncpg.Connection[asyncpg.Record]


_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
_LOWER_BOUND = _FIXED_NOW - timedelta(seconds=600)


@pytest.fixture
async def conn(migrated_db_dsn: str) -> AsyncIterator[_Conn]:
    """asyncpg connection against the throwaway migrated DB."""
    connection: _Conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        yield connection
    finally:
        await connection.close()


async def test_select_signal_id_returns_inserted_id_within_lower_bound(conn: _Conn) -> None:
    """INSERT one row inside window → lookup by key returns its id."""
    received_at = _FIXED_NOW - timedelta(seconds=30)
    inserted_id = await insert_signal(
        conn,
        received_at=received_at,
        schema_version="1.0",
        source="test",
        idempotency_key="key-A",
        symbol="BTCUSDT",
        original_symbol=None,
        action="LONG",
        payload={},
        ingestion_status="validated",
        correlation_id="corr-A",
    )
    result = await select_signal_id_by_idempotency_key(
        conn,
        idempotency_key="key-A",
        received_at_lower_bound=_LOWER_BOUND,
    )
    assert result == inserted_id


async def test_select_signal_id_returns_none_for_row_outside_window(conn: _Conn) -> None:
    """Row inserted with received_at older than lower_bound → not returned."""
    far_past = _FIXED_NOW - timedelta(hours=2)
    await insert_signal(
        conn,
        received_at=far_past,
        schema_version="1.0",
        source="test",
        idempotency_key="key-B",
        symbol="BTCUSDT",
        original_symbol=None,
        action="LONG",
        payload={},
        ingestion_status="validated",
        correlation_id="corr-B",
    )
    result = await select_signal_id_by_idempotency_key(
        conn,
        idempotency_key="key-B",
        received_at_lower_bound=_LOWER_BOUND,
    )
    assert result is None
