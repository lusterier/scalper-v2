"""Integration tests for :mod:`packages.db.queries.feature_engine` (T-110b).

Runs against a throwaway PostgreSQL + TimescaleDB migrated to head
(includes T-103 ``ohlc_1m`` hypertable + 5 caggs and T-108 ``features``
hypertable). Verifies:

* **insert_feature** round-trips for each of the three value variants.
  ``value_num`` test uses the L-005 sentinel ``3.141592653589793``
  (15-digit double-precision π expansion) with **exact-equality** assert
  — would round in ``real`` (32-bit) storage to ``3.1415927``, so the
  test fails against any future regression that downgrades the column
  away from ``DOUBLE PRECISION``.
* **ON CONFLICT DO UPDATE** — re-insert with refined ``value_num`` for
  the same PK overwrites; assertion is value-level, not row-count, so a
  regression to ``DO NOTHING`` would be caught.
* **Distinct ``source_version`` creates independent rows** — the PK
  includes ``source_version``, so two algorithm versions for the same
  ``(feature_name, symbol, computed_at)`` coexist as separate rows.
* **fetch_warmup_window** — interval ``1m`` reads raw hypertable;
  interval ``15m`` reads the cagg after manual refresh; unknown
  intervals raise ``ValueError`` before any SQL emission; empty result
  and under-fill return short lists without raising.

JSONB codec is registered per-connection in the ``conn`` fixture below
(mirror T-108 ``test_0004_migration:55-62``). Production codec
registration is T-110c's concern (Hand-off section in
``docs/plans/T-110b.md``).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset (see
``conftest.py``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import asyncpg
import pytest

from packages.db.queries.feature_engine import (
    fetch_ohlc_range,
    fetch_warmup_window,
    insert_feature,
    select_latest_feature,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

type _Conn = asyncpg.Connection[asyncpg.Record]


_FEATURE_NAME = "ind.btcusdt.15m.ema_20"
_SYMBOL = "BTCUSDT"
_SOURCE_VERSION = "builtin.ema.v1"


def _computed_at(minute: int = 0) -> datetime:
    return datetime(2026, 4, 26, 12, minute, 0, tzinfo=UTC)


def _bucket(minute: int = 0) -> datetime:
    return datetime(2026, 4, 26, 11, minute, 0, tzinfo=UTC)


@pytest.fixture
async def conn(migrated_db_dsn: str) -> AsyncIterator[_Conn]:
    """asyncpg connection with the JSONB codec registered.

    Codec registration mirrors T-108 ``test_0004_migration`` pattern;
    asyncpg defaults to raw JSON string for ``jsonb`` so without the
    codec, ``insert_feature(value_json=dict(...))`` would raise.
    """
    connection: _Conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        await connection.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )
        yield connection
    finally:
        await connection.close()


# ---------------------------------------------------------------------------
# insert_feature — value variants
# ---------------------------------------------------------------------------


async def test_insert_feature_value_num_round_trip(conn: _Conn) -> None:
    """L-005 storage-type guard — DOUBLE PRECISION preserves 15-digit π expansion.

    Sentinel ``3.141592653589793`` distinguishes ``DOUBLE PRECISION``
    from ``real`` (which would round to ``3.1415927``). The assert
    MUST be exact-equality, not ``pytest.approx`` or ``math.isclose``;
    a tolerance assert defeats the precision-bit probe (passes against
    either column type).
    """
    pi_double = 3.141592653589793
    await insert_feature(
        conn,
        feature_name=_FEATURE_NAME,
        symbol=_SYMBOL,
        computed_at=_computed_at(),
        value_num=pi_double,
        value_bool=None,
        value_json=None,
        source_version=_SOURCE_VERSION,
    )
    row = await conn.fetchrow(
        "SELECT value_num FROM features WHERE feature_name = $1 AND symbol = $2",
        _FEATURE_NAME,
        _SYMBOL,
    )
    assert row is not None
    assert row["value_num"] == pi_double  # exact equality — see docstring


async def test_insert_feature_value_bool_round_trip(conn: _Conn) -> None:
    await insert_feature(
        conn,
        feature_name="ind.btcusdt.15m.cross_above",
        symbol=_SYMBOL,
        computed_at=_computed_at(),
        value_num=None,
        value_bool=True,
        value_json=None,
        source_version="builtin.cross.v1",
    )
    row = await conn.fetchrow(
        "SELECT value_bool, value_num, value_json FROM features WHERE symbol = $1",
        _SYMBOL,
    )
    assert row is not None
    assert row["value_bool"] is True
    assert row["value_num"] is None
    assert row["value_json"] is None


async def test_insert_feature_value_json_round_trip(conn: _Conn) -> None:
    """JSONB codec round-trips a Bollinger-shaped dict unchanged."""
    bollinger = {"upper": 50100.5, "middle": 50000.0, "lower": 49899.5}
    await insert_feature(
        conn,
        feature_name="ind.btcusdt.15m.bollinger_20_2",
        symbol=_SYMBOL,
        computed_at=_computed_at(),
        value_num=None,
        value_bool=None,
        value_json=bollinger,
        source_version="builtin.bollinger.v1",
    )
    row = await conn.fetchrow(
        "SELECT value_json FROM features WHERE symbol = $1",
        _SYMBOL,
    )
    assert row is not None
    assert row["value_json"] == bollinger


# ---------------------------------------------------------------------------
# ON CONFLICT DO UPDATE
# ---------------------------------------------------------------------------


async def test_insert_feature_on_conflict_do_update_overwrites_value_num(
    conn: _Conn,
) -> None:
    """Same PK + refined value writes through (last-write-wins)."""
    computed = _computed_at()
    await insert_feature(
        conn,
        feature_name=_FEATURE_NAME,
        symbol=_SYMBOL,
        computed_at=computed,
        value_num=49000.0,
        value_bool=None,
        value_json=None,
        source_version=_SOURCE_VERSION,
    )
    await insert_feature(
        conn,
        feature_name=_FEATURE_NAME,
        symbol=_SYMBOL,
        computed_at=computed,
        value_num=50500.0,
        value_bool=None,
        value_json=None,
        source_version=_SOURCE_VERSION,
    )
    rows = await conn.fetch(
        "SELECT value_num FROM features WHERE feature_name = $1 AND symbol = $2",
        _FEATURE_NAME,
        _SYMBOL,
    )
    assert len(rows) == 1
    assert rows[0]["value_num"] == 50500.0


async def test_insert_feature_distinct_source_versions_yield_separate_rows(
    conn: _Conn,
) -> None:
    """``source_version`` is part of the PK; v1 and v2 coexist."""
    computed = _computed_at()
    await insert_feature(
        conn,
        feature_name=_FEATURE_NAME,
        symbol=_SYMBOL,
        computed_at=computed,
        value_num=70.0,
        value_bool=None,
        value_json=None,
        source_version="builtin.ema.v1",
    )
    await insert_feature(
        conn,
        feature_name=_FEATURE_NAME,
        symbol=_SYMBOL,
        computed_at=computed,
        value_num=72.0,
        value_bool=None,
        value_json=None,
        source_version="builtin.ema.v2",
    )
    rows = await conn.fetch(
        "SELECT source_version, value_num FROM features "
        "WHERE feature_name = $1 AND symbol = $2 "
        "ORDER BY source_version",
        _FEATURE_NAME,
        _SYMBOL,
    )
    assert len(rows) == 2
    assert rows[0]["source_version"] == "builtin.ema.v1"
    assert rows[0]["value_num"] == 70.0
    assert rows[1]["source_version"] == "builtin.ema.v2"
    assert rows[1]["value_num"] == 72.0


# ---------------------------------------------------------------------------
# fetch_warmup_window — table routing
# ---------------------------------------------------------------------------


async def _seed_ohlc_1m(conn: _Conn, *, n: int, start_minute: int = 0) -> None:
    """Insert ``n`` synthetic 1-minute candles into ``ohlc_1m``."""
    for i in range(n):
        await conn.execute(
            "INSERT INTO ohlc_1m "
            "(symbol, bucket_start, open, high, low, close, volume, source) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, 'binance')",
            _SYMBOL,
            _bucket(start_minute + i),
            Decimal(50000 + i),
            Decimal(50100 + i),
            Decimal(49900 + i),
            Decimal(50050 + i),
            Decimal("1.5"),
            # source pinned in SQL above ($8 wasn't used; keep call site lean)
        )


async def test_fetch_warmup_window_1m_from_raw_hypertable(conn: _Conn) -> None:
    """Interval=1m reads the raw ``ohlc_1m`` hypertable; ASC order, n cap."""
    await _seed_ohlc_1m(conn, n=5)
    rows = await fetch_warmup_window(conn, symbol=_SYMBOL, interval="1m", n=3, source="binance")
    assert len(rows) == 3
    # ASC by bucket_start: oldest 3 of the 5 inserted candles
    assert rows[0][1] == _bucket(2)  # bucket index 2 (minute 11:02)
    assert rows[1][1] == _bucket(3)
    assert rows[2][1] == _bucket(4)
    # Tuple field order: (symbol, bucket_start, open, high, low, close, volume, source)
    assert rows[0][0] == _SYMBOL
    assert rows[0][2] == Decimal(50002)
    assert rows[0][7] == "binance"


async def test_fetch_warmup_window_15m_from_cagg(conn: _Conn) -> None:
    """Interval=15m reads the cagg after manual refresh."""
    # Seed two 15-minute windows of 1m data.
    bucket_a_start = datetime(2026, 4, 26, 10, 0, tzinfo=UTC)
    bucket_b_start = datetime(2026, 4, 26, 10, 15, tzinfo=UTC)
    for window_start in (bucket_a_start, bucket_b_start):
        for i in range(15):
            await conn.execute(
                "INSERT INTO ohlc_1m "
                "(symbol, bucket_start, open, high, low, close, volume, source) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, 'binance')",
                _SYMBOL,
                window_start + timedelta(minutes=i),
                Decimal("50000"),
                Decimal("50100"),
                Decimal("49900"),
                Decimal("50050"),
                Decimal("1"),
            )
    # Manually refresh the 15m cagg so the rows materialize.
    await conn.execute("CALL refresh_continuous_aggregate('ohlc_15m', NULL, NULL)")
    rows = await fetch_warmup_window(conn, symbol=_SYMBOL, interval="15m", n=10, source="binance")
    assert len(rows) == 2
    assert rows[0][1] == bucket_a_start  # ASC order
    assert rows[1][1] == bucket_b_start


async def test_fetch_warmup_window_unknown_interval_raises(conn: _Conn) -> None:
    """Unknown interval raises ``ValueError`` before any SQL emission."""
    with pytest.raises(ValueError, match="unknown interval '3m'"):
        await fetch_warmup_window(conn, symbol=_SYMBOL, interval="3m", n=10, source="binance")


async def test_fetch_warmup_window_empty_returns_empty_list(conn: _Conn) -> None:
    """Empty source table returns ``[]`` without raising."""
    rows = await fetch_warmup_window(conn, symbol=_SYMBOL, interval="1m", n=10, source="binance")
    assert rows == []


async def test_fetch_warmup_window_under_fill_returns_short_list(conn: _Conn) -> None:
    """Under-fill: 3 rows in table, n=10 → returns prefix of 3 rows."""
    await _seed_ohlc_1m(conn, n=3)
    rows = await fetch_warmup_window(conn, symbol=_SYMBOL, interval="1m", n=10, source="binance")
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# fetch_ohlc_range — T-112 backfill query helper
# ---------------------------------------------------------------------------


async def test_fetch_ohlc_range_returns_rows_in_range_ASC(conn: _Conn) -> None:
    """Date range filter [from, to] inclusive; ASC by bucket_start."""
    await _seed_ohlc_1m(conn, n=5)  # buckets at 11:00..11:04
    from_dt = _bucket(1)  # 11:01
    to_dt = _bucket(3)  # 11:03
    rows = await fetch_ohlc_range(
        conn,
        symbol=_SYMBOL,
        interval="1m",
        source="binance",
        from_dt=from_dt,
        to_dt=to_dt,
    )
    assert len(rows) == 3
    # ASC ordering: 11:01, 11:02, 11:03
    assert rows[0][1] == _bucket(1)
    assert rows[1][1] == _bucket(2)
    assert rows[2][1] == _bucket(3)


async def test_fetch_ohlc_range_empty_range_returns_empty_list(conn: _Conn) -> None:
    """Range before any seeded data returns ``[]`` without raising."""
    rows = await fetch_ohlc_range(
        conn,
        symbol=_SYMBOL,
        interval="1m",
        source="binance",
        from_dt=_bucket(0),
        to_dt=_bucket(10),
    )
    assert rows == []


async def test_fetch_ohlc_range_unknown_interval_raises(conn: _Conn) -> None:
    """Unknown interval raises ``ValueError`` before any SQL emission."""
    with pytest.raises(ValueError, match="unknown interval '3m'"):
        await fetch_ohlc_range(
            conn,
            symbol=_SYMBOL,
            interval="3m",
            source="binance",
            from_dt=_bucket(0),
            to_dt=_bucket(10),
        )


# ---------------------------------------------------------------------------
# T-306 — select_latest_feature integration
# ---------------------------------------------------------------------------


async def test_select_latest_feature_returns_most_recent_row(conn: _Conn) -> None:
    """INSERT 3 rows distinct computed_at; select_latest_feature returns latest by computed_at."""
    base = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
    for offset_seconds, value_num in ((0, 100.5), (60, 101.5), (120, 102.5)):
        await insert_feature(
            conn,
            feature_name=_FEATURE_NAME,
            symbol=_SYMBOL,
            computed_at=base + timedelta(seconds=offset_seconds),
            value_num=value_num,
            value_bool=None,
            value_json=None,
            source_version=_SOURCE_VERSION,
        )
    row = await select_latest_feature(conn, feature_name=_FEATURE_NAME, symbol=_SYMBOL)
    assert row is not None
    assert row.value_num == 102.5
    assert row.computed_at == base + timedelta(seconds=120)


async def test_select_latest_feature_returns_none_when_no_row(conn: _Conn) -> None:
    """No matching (feature_name, symbol) → None."""
    row = await select_latest_feature(conn, feature_name="ind.nonexistent.1m.foo", symbol="NOSUCH")
    assert row is None
