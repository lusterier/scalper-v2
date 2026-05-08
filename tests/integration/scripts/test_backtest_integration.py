"""T-507b env-gated full-fidelity integration test (1 test per OQ-B=B).

Requires POSTGRES_TEST_DSN + BACKTEST_INTEGRATION=1; skipped at collection
time otherwise. Runs against a throwaway PostgreSQL DB seeded with:
  1. bots row (target bot)
  2. bot_configs row (active version pointing at minimal YAML)
  3. ohlc_1m candles for the symbol + window (used by FeatureResolver
     DB-fallback path per OQ-5=A; T-507a ReplayBus.kv_get returns None)
  4. features rows for the rule's feature ref (passthrough mode requires
     no features but full-fidelity per OQ-B=B exercises real DB-resolver path)
  5. signals row (ingestion_status='validated') in the window

Test invokes the actual CLI `main()` end-to-end + asserts run completes.
This catches BLOCKERs that mock-only tests miss (real ExecutionDispatcher
signature compat, real reconcile flow, real SQL bind compatibility).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import textwrap
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg
import pytest

from scripts.backtest import main

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_DSN_ENV = "POSTGRES_TEST_DSN"
_FEATURE_FLAG_ENV = "BACKTEST_INTEGRATION"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"


def _gate_or_skip() -> str:
    if not os.environ.get(_FEATURE_FLAG_ENV):
        pytest.skip(
            f"{_FEATURE_FLAG_ENV} env not set — backtest CLI integration test "
            "is env-gated; export BACKTEST_INTEGRATION=1 to run.",
            allow_module_level=True,
        )
    dsn = os.environ.get(_DSN_ENV)
    if not dsn:
        pytest.skip(
            f"{_DSN_ENV} env not set — integration requires reachable PostgreSQL.",
            allow_module_level=True,
        )
    return dsn


@pytest.fixture
async def integration_db_dsn() -> AsyncIterator[str]:
    """Throwaway DB + alembic upgrade head + yield DSN."""
    base_dsn = _gate_or_skip()
    db_name = f"scalper_v2_backtest_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(dsn=base_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin.close()
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(base_dsn)
    new_dsn = urlunparse(parsed._replace(path=f"/{db_name}"))
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), "upgrade", "head"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "POSTGRES_URL": new_dsn},
            cwd=_REPO_ROOT,
        )
        # Drop cagg auto-refresh policy (mirror paper conftest).
        conn = await asyncpg.connect(dsn=new_dsn)
        try:
            await conn.execute("SELECT remove_continuous_aggregate_policy('ohlc_15m')")
        finally:
            await conn.close()
        yield new_dsn
    finally:
        admin = await asyncpg.connect(dsn=base_dsn)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        finally:
            await admin.close()


_MINIMAL_BOT_CONFIG = textwrap.dedent("""\
    bot_id: alpha
    version: 1
    trading:
      universe: [BTCUSDT]
    exchange:
      mode: paper
      source: tradingview
    signals:
      ttl_seconds: 3600
    execution:
      qty: '0.01'
      leverage: 1
      sl_pct: '0.005'
      tp_pct: '0.010'
      tp_qty_pct: '1.0'
      be_trigger: '0'
      be_sl_level: '0'
      trail_pct: '0'
      fee_rate: '0.0006'
    scoring:
      mode: passthrough
      trigger_threshold: '0.5'
      rules: []
""")


@pytest.fixture
async def seeded_db(integration_db_dsn: str) -> AsyncIterator[tuple[str, Path]]:
    """Seed bots + bot_configs + ohlc_1m + signals; write YAML to tmp; yield (dsn, path)."""
    conn = await asyncpg.connect(dsn=integration_db_dsn)
    try:
        # 1. bots row.
        await conn.execute(
            "INSERT INTO bots (bot_id, status, exchange_mode, exchange_source) "
            "VALUES ($1, 'active', 'paper', 'tradingview')",
            "alpha",
        )
        # 2. bot_configs row (referenced by run audit; not actively read by CLI which
        #    uses --config-path).
        config_hash = "0" * 64
        await conn.execute(
            "INSERT INTO bot_configs (bot_id, version, config_yaml, config_hash) "
            "VALUES ($1, $2, $3, $4)",
            "alpha",
            1,
            _MINIMAL_BOT_CONFIG,
            config_hash,
        )
        # 3. ohlc_1m candles — 2 candles within window.
        from_at = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
        candles = [
            (from_at, "BTCUSDT", "65000", "65100", "64900", "65050", "100"),
            (from_at + timedelta(minutes=1), "BTCUSDT", "65050", "65150", "64950", "65100", "120"),
        ]
        for ts, sym, o, h, lo, c, v in candles:
            await conn.execute(
                "INSERT INTO ohlc_1m"
                " (bucket_start, symbol, open, high, low, close, volume, source)"
                " VALUES ($1, $2, $3::numeric, $4::numeric, $5::numeric,"
                " $6::numeric, $7::numeric, 'binance')",
                ts,
                sym,
                o,
                h,
                lo,
                c,
                v,
            )
        # 4. signals row — 1 LONG signal between candles.
        signal_ts = from_at + timedelta(seconds=30)
        await conn.execute(
            """INSERT INTO signals (
                received_at, schema_version, source, idempotency_key,
                symbol, original_symbol, action, payload, ingestion_status,
                correlation_id, bot_id
            ) VALUES (
                $1, '1.0', 'tradingview', $2, 'BTCUSDT', 'BTCUSDT',
                'LONG', '{"price":"65020"}'::jsonb, 'validated', $3, 'alpha'
            )""",
            signal_ts,
            f"key-{uuid.uuid4()}",
            str(uuid.uuid4()),
        )
        # Write YAML config to tmp file (sync I/O acceptable in test fixture
        # setup; integration test runs serially, no event-loop blocking risk).
        config_path = Path(f"/tmp/backtest-cfg-{uuid.uuid4().hex[:8]}.yaml")
        config_path.write_text(_MINIMAL_BOT_CONFIG)  # noqa: ASYNC240
        yield integration_db_dsn, config_path
        config_path.unlink(missing_ok=True)  # noqa: ASYNC240
    finally:
        await conn.close()


async def test_backtest_full_replay_2_candles_1_signal(
    seeded_db: tuple[str, Path],
) -> None:
    """End-to-end: CLI main() against seeded DB; assert run completes; summary persisted."""
    dsn, config_path = seeded_db
    args = argparse.Namespace(
        bot="alpha",
        from_at=datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC),
        to_at=datetime(2026, 4, 1, 12, 5, 0, tzinfo=UTC),
        config_path=config_path,
        overrides=[],
        pace="max",
        source="binance",
        plugin_registry_path=None,
        db_url=dsn,
        name=None,
        notes=None,
    )
    exit_code = await main(args)
    assert exit_code == 0

    # Assert backtest_runs row exists with status='completed' + summary persisted.
    conn = await asyncpg.connect(dsn=dsn)
    try:
        row = await conn.fetchrow("SELECT status, summary FROM backtest_runs WHERE bot_id='alpha'")
        assert row is not None
        assert row["status"] == "completed"
        # summary is JSONB; asyncpg without codec returns str; parse.
        import json as json_mod

        summary = (
            json_mod.loads(row["summary"]) if isinstance(row["summary"], str) else row["summary"]
        )
        assert "total_trades" in summary
        assert "wr" in summary
        assert "pnl" in summary
        assert "pf" in summary
        assert "mdd" in summary
    finally:
        await conn.close()
