"""§9.3 line 1525 backfill determinism property test (T-112).

Env-gated (``POSTGRES_TEST_DSN``) integration test. Runs the backfill
CLI as a subprocess (black-box; mirrors how operator invokes it),
takes a snapshot of the ``features`` table, runs again, takes a
second snapshot, and asserts byte-for-byte equality across all 7
columns ordered by ``(feature_name, symbol, computed_at, source_version)``.

Per Concern #4 resolution in ``docs/plans/T-112.md``: explicit
``ORDER BY`` (hypertable row order is non-deterministic across
compression boundaries; ORDER BY is load-bearing) + tuple equality
(no tolerance — Decimal→float at wire seam is deterministic).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import asyncpg

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKFILL_SCRIPT = _REPO_ROOT / "scripts" / "backfill_features.py"


async def _seed_ohlc_15m(conn: asyncpg.Connection[asyncpg.Record], n: int) -> None:
    """Seed n 15m candles into ohlc_1m + refresh ohlc_15m cagg.

    Generates n*15 1m candles spanning n full 15-minute buckets, then
    triggers manual cagg refresh so the 15m cagg holds n materialised rows.
    """
    base = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
    for bucket_idx in range(n):
        bucket_start = base + timedelta(minutes=15 * bucket_idx)
        for minute_offset in range(15):
            ts = bucket_start + timedelta(minutes=minute_offset)
            await conn.execute(
                "INSERT INTO ohlc_1m "
                "(symbol, bucket_start, open, high, low, close, volume, source) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, 'binance')",
                "BTCUSDT",
                ts,
                Decimal("50000") + Decimal(bucket_idx),
                Decimal("50100") + Decimal(bucket_idx),
                Decimal("49900") + Decimal(bucket_idx),
                Decimal("50050") + Decimal(bucket_idx),
                Decimal("1.5"),
            )
    await conn.execute("CALL refresh_continuous_aggregate('ohlc_15m', NULL, NULL)")


async def test_backfill_idempotency_running_twice_yields_identical_rows(
    migrated_db_dsn: str,
) -> None:
    """§9.3 line 1525: byte-identical features rows across two runs.

    Per CONCERN #4 resolution: SELECT 7 columns ORDER BY PK; len()
    equality + per-row tuple equality (exact, no tolerance).
    """
    # 1. Seed 30 BTCUSDT 15m candles (warmup_candles=20 for ema_20 → 11 inserts).
    setup_conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        await setup_conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )
        await _seed_ohlc_15m(setup_conn, n=30)
    finally:
        await setup_conn.close()

    cli_args = [
        sys.executable,
        str(_BACKFILL_SCRIPT),
        "--feature",
        "ind.btcusdt.15m.ema_20",
        "--from",
        "2026-04-01T00:00:00+00:00",
        "--to",
        "2026-04-01T23:59:59+00:00",
        "--source",
        "binance",
        "--database-url",
        migrated_db_dsn,
    ]

    # 2. Run backfill once.
    await asyncio.to_thread(subprocess.run, cli_args, check=True, capture_output=True, text=True)

    # 3. Snapshot features table (explicit 7 columns + ORDER BY per Concern #4).
    snap_conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        await snap_conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )
        snap_a = await snap_conn.fetch(
            "SELECT feature_name, symbol, computed_at, value_num, value_bool, "
            "value_json, source_version FROM features "
            "ORDER BY feature_name, symbol, computed_at, source_version"
        )

        # 4. Run backfill again.
        await asyncio.to_thread(
            subprocess.run, cli_args, check=True, capture_output=True, text=True
        )

        # 5. Second snapshot.
        snap_b = await snap_conn.fetch(
            "SELECT feature_name, symbol, computed_at, value_num, value_bool, "
            "value_json, source_version FROM features "
            "ORDER BY feature_name, symbol, computed_at, source_version"
        )

        # 6. Tuple-equality assertion across all 7 columns (no tolerance).
        assert len(snap_a) == len(snap_b) > 0
        for row_a, row_b in zip(snap_a, snap_b, strict=True):
            assert tuple(row_a) == tuple(row_b)
    finally:
        await snap_conn.close()
