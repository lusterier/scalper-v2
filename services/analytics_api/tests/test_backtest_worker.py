"""T-509 backtest worker unit tests (6 unit + 1 env-gated integration)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest

from packages.core.types import BacktestStatus
from packages.db.queries.analytics import (
    BacktestRunRow,
    claim_next_backtest_run,
)
from services.analytics_api.app.backtest_worker import run_backtest_worker_loop
from services.analytics_api.app.config import Settings


def _make_pool() -> MagicMock:
    """Mock asyncpg.Pool with conn.fetchrow + transaction context."""
    pool = MagicMock()
    pool.close = AsyncMock()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)  # default: no queued
    conn.execute = AsyncMock(return_value="UPDATE 1")
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _make_settings(*, enabled: bool = True, poll_s: int = 1) -> Settings:
    """Settings with required env vars; poll_interval_s overridden for fast tests."""
    return Settings(
        database_url="postgresql://test",
        nats_url="nats://test",
        backtest_worker_enabled=enabled,
        backtest_worker_poll_interval_s=poll_s,
    )


def _make_run_row() -> BacktestRunRow:
    """Construct a BacktestRunRow stand-in for dispatch tests."""
    return BacktestRunRow(
        id=uuid4(),
        name="test-run",
        bot_id="alpha",
        config_yaml="bot_id: alpha\n",
        config_hash="0" * 64,
        date_range_start=datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC),
        date_range_end=datetime(2026, 4, 1, 13, 0, 0, tzinfo=UTC),
        status=BacktestStatus.RUNNING,
        started_at=datetime.now(UTC),
        finished_at=None,
        summary=None,
        notes=None,
    )


# --- 6 unit tests --------------------------------------------------------------


async def test_claim_next_backtest_run_sql_bind_shape() -> None:
    """SQL has UPDATE backtest_runs + FOR UPDATE SKIP LOCKED + RETURNING; binds $1=started_at."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    started_at = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    await claim_next_backtest_run(conn, started_at=started_at)
    args, _ = conn.fetchrow.call_args
    sql = args[0]
    assert "UPDATE backtest_runs" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "RETURNING" in sql
    assert "ORDER BY created_at" in sql
    assert args[1] == started_at


async def test_worker_loop_dispatches_to_t507b_with_claimed_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock pool returns claim then None; mock t507b dispatch; assert dispatch called."""
    run_row = _make_run_row()
    pool = _make_pool()
    # Simulate first poll returns row, second returns None (then we cancel).
    conn_after_aenter = pool.acquire().__aenter__.return_value
    conn_after_aenter.fetchrow = AsyncMock(side_effect=[run_row, None])

    dispatch_calls: list[BacktestRunRow] = []

    async def _fake_dispatch(rr: BacktestRunRow, _settings: Settings, _logger: object) -> None:
        dispatch_calls.append(rr)

    monkeypatch.setattr(
        "services.analytics_api.app.backtest_worker._dispatch_to_t507b",
        _fake_dispatch,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.backtest_worker.claim_next_backtest_run",
        AsyncMock(side_effect=[run_row, None]),
    )

    settings = _make_settings(poll_s=1)
    logger = MagicMock()
    task = asyncio.create_task(
        run_backtest_worker_loop(pool=pool, settings=settings, logger=logger),
    )
    # Wait briefly for first iteration + dispatch.
    await asyncio.sleep(0.1)
    task.cancel()
    import contextlib

    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert len(dispatch_calls) == 1
    assert dispatch_calls[0].id == run_row.id


async def test_worker_loop_sleeps_when_no_queued_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock claim returns None always; assert asyncio.sleep called with poll_interval_s."""
    monkeypatch.setattr(
        "services.analytics_api.app.backtest_worker.claim_next_backtest_run",
        AsyncMock(return_value=None),
    )
    real_sleep = asyncio.sleep
    sleep_calls: list[float] = []

    async def _capture_sleep(s: float) -> None:
        sleep_calls.append(s)
        await real_sleep(0)  # yield control to allow cancel

    # Patch only inside the worker module's asyncio namespace.
    monkeypatch.setattr(asyncio, "sleep", _capture_sleep)

    settings = _make_settings(poll_s=7)
    logger = MagicMock()
    pool = _make_pool()
    task = asyncio.create_task(
        run_backtest_worker_loop(pool=pool, settings=settings, logger=logger),
    )
    await real_sleep(0.05)  # yield using REAL sleep (avoid mock recursion)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Verify sleep called with poll_interval_s (worker invocation).
    assert sleep_calls, "worker never called asyncio.sleep"
    assert 7 in sleep_calls, f"poll_interval_s=7 not seen; got {sleep_calls}"


async def test_worker_loop_handles_t507b_exception_marks_failed_dict_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-507b raises → worker outer except writes status='failed'.

    Asserts codec_registered=True dict bind per L-011 regression guard.
    """
    run_row = _make_run_row()

    async def _raising_dispatch(*_args: object, **_kwargs: object) -> None:
        msg = "synthetic dispatch failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        "services.analytics_api.app.backtest_worker._dispatch_to_t507b",
        _raising_dispatch,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.backtest_worker.claim_next_backtest_run",
        AsyncMock(side_effect=[run_row, None]),
    )

    completion_calls: list[dict[str, object]] = []

    async def _capture_completion(_conn: object, **kwargs: object) -> None:
        completion_calls.append(kwargs)

    monkeypatch.setattr(
        "services.analytics_api.app.backtest_worker.update_backtest_run_completion",
        _capture_completion,
    )

    settings = _make_settings(poll_s=1)
    logger = MagicMock()
    pool = _make_pool()
    task = asyncio.create_task(
        run_backtest_worker_loop(pool=pool, settings=settings, logger=logger),
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(completion_calls) == 1
    call = completion_calls[0]
    assert call["status"] == BacktestStatus.FAILED
    assert call["codec_registered"] is True
    summary = call["summary"]
    assert isinstance(summary, dict)  # L-011 regression guard: dict, not str-of-json
    assert "error" in summary


async def test_worker_loop_cancelled_propagates_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifespan cancels worker; CancelledError propagates without warnings."""
    monkeypatch.setattr(
        "services.analytics_api.app.backtest_worker.claim_next_backtest_run",
        AsyncMock(return_value=None),
    )
    settings = _make_settings(poll_s=10)
    logger = MagicMock()
    pool = _make_pool()
    task = asyncio.create_task(
        run_backtest_worker_loop(pool=pool, settings=settings, logger=logger),
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_settings_backtest_worker_enabled_default_false() -> None:
    """Settings.backtest_worker_enabled defaults False; operator opt-in via env."""
    settings = Settings(
        database_url="postgresql://test",
        nats_url="nats://test",
    )
    assert settings.backtest_worker_enabled is False
    assert settings.backtest_worker_poll_interval_s == 5  # default per Field


# --- env-gated SKIP LOCKED real-PG integration test -------------------------


_DSN_ENV_VAR = "POSTGRES_TEST_DSN"
_FEATURE_FLAG_ENV = "BACKTEST_INTEGRATION"


def _gate_or_skip() -> str:
    if not os.environ.get(_FEATURE_FLAG_ENV):
        pytest.skip(
            f"{_FEATURE_FLAG_ENV} env not set — backtest worker integration test "
            "is env-gated; export BACKTEST_INTEGRATION=1 to run.",
            allow_module_level=False,
        )
    dsn = os.environ.get(_DSN_ENV_VAR)
    if not dsn:
        pytest.skip(
            f"{_DSN_ENV_VAR} not set — integration requires reachable PostgreSQL.",
            allow_module_level=False,
        )
    return dsn


async def test_claim_next_backtest_run_skip_locked_real_pg() -> None:
    """SKIP LOCKED: 2 concurrent claims against 1 queued row → only 1 receives row."""
    from pathlib import Path as _Path

    base_dsn = _gate_or_skip()
    db_name = f"scalper_v2_t509_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(dsn=base_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin.close()
    parsed = urlparse(base_dsn)
    new_dsn = urlunparse(parsed._replace(path=f"/{db_name}"))
    # Sync I/O at fixture level OK (test-runtime, not production hot path).
    repo_root = _Path(__file__).resolve().parents[3]  # noqa: ASYNC240
    alembic_ini = repo_root / "migrations" / "alembic.ini"
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["uv", "run", "alembic", "-c", str(alembic_ini), "upgrade", "head"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "POSTGRES_URL": new_dsn},
            cwd=repo_root,
        )
        # Seed 1 queued row.
        seed = await asyncpg.connect(dsn=new_dsn)
        try:
            await seed.execute(
                "INSERT INTO bots (bot_id, status, exchange_mode, exchange_source) "
                "VALUES ('alpha', 'active', 'paper', 'tradingview')",
            )
            await seed.execute(
                """INSERT INTO backtest_runs (
                    name, bot_id, config_yaml, config_hash,
                    date_range_start, date_range_end, status, started_at
                ) VALUES (
                    'race-test', 'alpha', 'bot_id: alpha\n', $1,
                    $2, $3, 'queued', $4
                )""",
                "0" * 64,
                datetime(2026, 4, 1, tzinfo=UTC),
                datetime(2026, 4, 2, tzinfo=UTC),
                datetime.now(UTC),
            )
        finally:
            await seed.close()

        # 2 concurrent claims.
        async def _claim_via_new_conn() -> object:
            conn = await asyncpg.connect(dsn=new_dsn)
            try:
                async with conn.transaction():
                    return await claim_next_backtest_run(
                        conn,
                        started_at=datetime.now(UTC),
                    )
            finally:
                await conn.close()

        results = await asyncio.gather(_claim_via_new_conn(), _claim_via_new_conn())
        non_none = [r for r in results if r is not None]
        assert len(non_none) == 1, f"SKIP LOCKED race failed: got {len(non_none)} claims"
    finally:
        admin = await asyncpg.connect(dsn=base_dsn)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        finally:
            await admin.close()
