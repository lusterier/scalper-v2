"""Integration tests for :func:`packages.db.queries.market_data.insert_ohlc_1m` (T-104a).

Runs against a throwaway PostgreSQL + TimescaleDB migrated to head
(includes T-103 ``ohlc_1m`` hypertable + 5 caggs). Verifies:

* **Single-row INSERT** — happy path: a closed candle lands as the
  expected row with Decimal precision preserved through the
  ``NUMERIC(30, 12)`` round-trip.
* **ON CONFLICT DO UPDATE** — re-insert with a different OHLC for the
  same ``(symbol, bucket_start, source)`` PK overwrites the prior
  values (last-write-wins, the contract that lets T-105 backfill
  repair WS-stored drift). Assertion is value-level, not just
  row-count, so a regression to ``DO NOTHING`` would be caught.
* **Distinct PK tuples coexist** — different symbol, different
  bucket_start, and different source each create independent rows;
  only the full PK triggers ON CONFLICT.
* **Decimal precision survives** — values at the NUMERIC(30, 12)
  precision floor (12 fractional digits) round-trip without drift.

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset (see
``conftest.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import asyncpg
import pytest

from packages.db.queries.market_data import insert_ohlc_1m

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

type _Conn = asyncpg.Connection[asyncpg.Record]


def _bucket(minute: int = 0) -> datetime:
    return datetime(2026, 4, 25, 12, minute, 0, tzinfo=UTC)


@pytest.fixture
async def conn(migrated_db_dsn: str) -> AsyncIterator[_Conn]:
    connection: _Conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        yield connection
    finally:
        await connection.close()


async def _fetch_row(
    conn: _Conn,
    symbol: str,
    bucket_start: datetime,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT * FROM ohlc_1m WHERE symbol = $1 AND bucket_start = $2 AND source = 'binance'",
        symbol,
        bucket_start,
    )


# ---------------------------------------------------------------------------
# Single-row INSERT
# ---------------------------------------------------------------------------


async def test_insert_single_row_persists_with_precision(conn: _Conn) -> None:
    bucket = _bucket()
    await insert_ohlc_1m(
        conn,
        symbol="BTCUSDT",
        bucket_start=bucket,
        open=Decimal("50000.123456789012"),
        high=Decimal("50100.000000000001"),
        low=Decimal("49950.999999999999"),
        close=Decimal("50050.555555555555"),
        volume=Decimal("123.456789012345"),
        source="binance",
    )
    row = await _fetch_row(conn, "BTCUSDT", bucket)
    assert row is not None
    assert row["symbol"] == "BTCUSDT"
    assert row["bucket_start"] == bucket
    assert row["open"] == Decimal("50000.123456789012")
    assert row["high"] == Decimal("50100.000000000001")
    assert row["low"] == Decimal("49950.999999999999")
    assert row["close"] == Decimal("50050.555555555555")
    assert row["volume"] == Decimal("123.456789012345")
    assert row["source"] == "binance"


# ---------------------------------------------------------------------------
# ON CONFLICT DO UPDATE — last-write-wins
# ---------------------------------------------------------------------------


async def test_on_conflict_overwrites_with_new_values(conn: _Conn) -> None:
    """Second insert for same PK must overwrite OHLC + volume (T-105 repair contract)."""
    bucket = _bucket()
    await insert_ohlc_1m(
        conn,
        symbol="BTCUSDT",
        bucket_start=bucket,
        open=Decimal("50000.0"),
        high=Decimal("50100.0"),
        low=Decimal("49900.0"),
        close=Decimal("50050.0"),
        volume=Decimal("100.0"),
        source="binance",
    )
    await insert_ohlc_1m(
        conn,
        symbol="BTCUSDT",
        bucket_start=bucket,
        open=Decimal("50001.0"),
        high=Decimal("50200.0"),
        low=Decimal("49800.0"),
        close=Decimal("50100.0"),
        volume=Decimal("200.0"),
        source="binance",
    )
    row = await _fetch_row(conn, "BTCUSDT", bucket)
    assert row is not None
    assert row["open"] == Decimal("50001.0")
    assert row["high"] == Decimal("50200.0")
    assert row["low"] == Decimal("49800.0")
    assert row["close"] == Decimal("50100.0")
    assert row["volume"] == Decimal("200.0")

    count = await conn.fetchval(
        "SELECT count(*) FROM ohlc_1m WHERE symbol = 'BTCUSDT' AND bucket_start = $1",
        bucket,
    )
    assert count == 1


async def test_on_conflict_with_identical_values_is_noop_in_intent(conn: _Conn) -> None:
    """Re-inserting identical values yields the same row — typical Binance re-emit case."""
    bucket = _bucket()
    fields: dict[str, object] = {
        "open": Decimal("50000.0"),
        "high": Decimal("50100.0"),
        "low": Decimal("49900.0"),
        "close": Decimal("50050.0"),
        "volume": Decimal("100.0"),
    }
    for _ in range(3):
        await insert_ohlc_1m(
            conn,
            symbol="BTCUSDT",
            bucket_start=bucket,
            source="binance",
            **fields,  # type: ignore[arg-type]
        )
    row = await _fetch_row(conn, "BTCUSDT", bucket)
    assert row is not None
    assert row["open"] == fields["open"]
    count = await conn.fetchval(
        "SELECT count(*) FROM ohlc_1m WHERE symbol = 'BTCUSDT' AND bucket_start = $1",
        bucket,
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Distinct PK tuples coexist
# ---------------------------------------------------------------------------


async def test_distinct_symbol_creates_independent_row(conn: _Conn) -> None:
    bucket = _bucket()
    common: dict[str, object] = {
        "bucket_start": bucket,
        "open": Decimal("1.0"),
        "high": Decimal("2.0"),
        "low": Decimal("0.5"),
        "close": Decimal("1.5"),
        "volume": Decimal("10.0"),
        "source": "binance",
    }
    await insert_ohlc_1m(conn, symbol="BTCUSDT", **common)  # type: ignore[arg-type]
    await insert_ohlc_1m(conn, symbol="ETHUSDT", **common)  # type: ignore[arg-type]
    count = await conn.fetchval(
        "SELECT count(*) FROM ohlc_1m WHERE bucket_start = $1",
        bucket,
    )
    assert count == 2


async def test_distinct_bucket_start_creates_independent_row(conn: _Conn) -> None:
    common: dict[str, object] = {
        "symbol": "BTCUSDT",
        "open": Decimal("1.0"),
        "high": Decimal("2.0"),
        "low": Decimal("0.5"),
        "close": Decimal("1.5"),
        "volume": Decimal("10.0"),
        "source": "binance",
    }
    await insert_ohlc_1m(conn, bucket_start=_bucket(0), **common)  # type: ignore[arg-type]
    await insert_ohlc_1m(conn, bucket_start=_bucket(1), **common)  # type: ignore[arg-type]
    count = await conn.fetchval(
        "SELECT count(*) FROM ohlc_1m WHERE symbol = 'BTCUSDT'",
    )
    assert count == 2


async def test_distinct_source_creates_independent_row(conn: _Conn) -> None:
    bucket = _bucket()
    common: dict[str, object] = {
        "symbol": "BTCUSDT",
        "bucket_start": bucket,
        "open": Decimal("1.0"),
        "high": Decimal("2.0"),
        "low": Decimal("0.5"),
        "close": Decimal("1.5"),
        "volume": Decimal("10.0"),
    }
    await insert_ohlc_1m(conn, source="binance", **common)  # type: ignore[arg-type]
    await insert_ohlc_1m(conn, source="bybit", **common)  # type: ignore[arg-type]
    count = await conn.fetchval(
        "SELECT count(*) FROM ohlc_1m WHERE symbol = 'BTCUSDT' AND bucket_start = $1",
        bucket,
    )
    assert count == 2
