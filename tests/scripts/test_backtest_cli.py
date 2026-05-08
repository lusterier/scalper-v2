"""T-507b backtest CLI unit tests (12 named tests).

Hand-computed §A-§E summary fixtures cross-check `_compute_summary` math.
Composition smoke uses fake source + mock pool to avoid DB dependency.
Integration test (env-gated) lives in test_backtest_integration.py.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from scripts.backtest import (
    _apply_overrides,
    _build_parser,
    _compute_summary,
    _parse_utc_datetime,
)

# --- Argparse + UTC validation (3 tests) -----------------------------------


def test_argparse_required_flags_no_longer_argparse_level() -> None:
    """T-508: argparse no longer enforces required=True on run-mode flags.

    Validation moved to cli_main (covered by
    `test_argparse_run_mode_missing_required_flags_rejected`).
    Empty args parse successfully at argparse level.
    """
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.bot is None
    assert args.compare is None


def test_argparse_from_to_must_be_utc() -> None:
    """--from with naive datetime → SystemExit(2) with stderr message."""
    with pytest.raises(SystemExit) as exc_info:
        _parse_utc_datetime("from", "2026-04-01T00:00:00")  # no tzinfo
    assert exc_info.value.code == 2


def test_argparse_overrides_apply_to_raw_yaml_dict() -> None:
    """--override mutates raw YAML dict per dotted path."""
    yaml_dict = {"scoring": {"max_signal_age_seconds": 60, "rules": [{"threshold": 0.5}]}}
    result = _apply_overrides(yaml_dict, ["scoring.max_signal_age_seconds=120"])
    assert result["scoring"]["max_signal_age_seconds"] == 120

    yaml_dict2 = {"scoring": {"rules": [{"threshold": 0.5}]}}
    result2 = _apply_overrides(yaml_dict2, ["scoring.rules.0.threshold=0.7"])
    assert result2["scoring"]["rules"][0]["threshold"] == 0.7


def test_argparse_overrides_invalid_path_raises() -> None:
    """--override path that doesn't exist on YAML raises ValueError."""
    yaml_dict = {"scoring": {"max_signal_age_seconds": 60}}
    with pytest.raises(ValueError, match="does not exist on YAML"):
        _apply_overrides(yaml_dict, ["scoring.nonexistent.field=42"])


# --- Summary stats math — hand-computed §A-§E (5 tests) -------------------


def _mock_conn_returning_pnls(pnls: list[Decimal]) -> MagicMock:
    """Build a mock conn whose `fetch` returns rows shaped like backtest_trades."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"realized_pnl": p} for p in pnls])
    return conn


async def test_compute_summary_4_trades() -> None:
    """§A: pnls = [10, -5, 20, -3] → total=4 / wr=0.5 / pnl=22 / pf=3.75 / mdd=5."""
    conn = _mock_conn_returning_pnls(
        [Decimal("10.00"), Decimal("-5.00"), Decimal("20.00"), Decimal("-3.00")]
    )
    summary = await _compute_summary(conn, run_id=uuid4())
    assert summary == {
        "total_trades": 4,
        "wr": 0.5,
        "pnl": "22.00",
        "pf": 3.75,
        "mdd": "5.00",
    }


async def test_compute_summary_all_losses_pf_none() -> None:
    """§B: pnls = [-2, -3, -1] → wr=0 / pnl=-6 / pf=None (no wins per ADR-0008) / mdd=6."""
    conn = _mock_conn_returning_pnls([Decimal("-2.00"), Decimal("-3.00"), Decimal("-1.00")])
    summary = await _compute_summary(conn, run_id=uuid4())
    assert summary["total_trades"] == 3
    assert summary["wr"] == 0.0
    assert summary["pnl"] == "-6.00"
    assert summary["pf"] is None  # ADR-0008
    assert summary["mdd"] == "6.00"


async def test_compute_summary_all_wins_pf_none() -> None:
    """§C: pnls = [5, 3, 7] → wr=1.0 / pf=None (no losses per ADR-0008) / mdd=0."""
    conn = _mock_conn_returning_pnls([Decimal("5.00"), Decimal("3.00"), Decimal("7.00")])
    summary = await _compute_summary(conn, run_id=uuid4())
    assert summary["total_trades"] == 3
    assert summary["wr"] == 1.0
    assert summary["pnl"] == "15.00"
    assert summary["pf"] is None  # ADR-0008
    assert summary["mdd"] == "0"


async def test_compute_summary_empty_run() -> None:
    """§D: no trades → safe defaults (None / 0)."""
    conn = _mock_conn_returning_pnls([])
    summary = await _compute_summary(conn, run_id=uuid4())
    assert summary == {
        "total_trades": 0,
        "wr": None,
        "pnl": "0",
        "pf": None,
        "mdd": "0",
    }


async def test_compute_summary_decimal_precision() -> None:
    """§E: 12 fractional digits preserved (no float coercion on pnl)."""
    conn = _mock_conn_returning_pnls([Decimal("0.123456789012"), Decimal("-0.111111111111")])
    summary = await _compute_summary(conn, run_id=uuid4())
    assert summary["total_trades"] == 2
    # pnl is sum (Decimal precision preserved); 0.123456789012 - 0.111111111111 = 0.012345677901
    assert summary["pnl"] == "0.012345677901"


# --- copy_paper_trades_to_backtest helper (2 tests) -----------------------


async def test_copy_paper_trades_filters_by_bot_id_only() -> None:
    """SQL binds $1=run_id + $2=bot_id; WHERE clause has bot_id + status='closed' only."""
    from packages.db.queries.analytics import copy_paper_trades_to_backtest

    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 5")
    run_id = uuid4()
    count = await copy_paper_trades_to_backtest(
        conn,
        run_id=run_id,
        bot_id="alpha",
    )
    assert count == 5
    args, _kwargs = conn.execute.call_args
    sql = args[0]
    assert "WHERE bot_id = $2" in sql
    assert "AND status = 'closed'" in sql
    assert "BETWEEN" not in sql  # OQ-D=C: no time-window filter
    assert args[1] == run_id
    assert args[2] == "alpha"


async def test_copy_paper_trades_returns_zero_on_empty_match() -> None:
    """`INSERT 0 0` command tag → returns 0."""
    from packages.db.queries.analytics import copy_paper_trades_to_backtest

    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 0")
    count = await copy_paper_trades_to_backtest(conn, run_id=uuid4(), bot_id="alpha")
    assert count == 0


# --- update_backtest_run_to_running (1 test, BLOCKER #1 fix regression guard) -


async def test_update_backtest_run_to_running_binds_started_at_param() -> None:
    """§N1: SQL must NOT use NOW(); started_at bound as Python datetime."""
    from packages.db.queries.analytics import update_backtest_run_to_running

    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    run_id = uuid4()
    started_at = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    await update_backtest_run_to_running(conn, run_id=run_id, started_at=started_at)
    args, _kwargs = conn.execute.call_args
    sql = args[0]
    assert "NOW()" not in sql  # §N1: no SQL clock
    assert "CURRENT_TIMESTAMP" not in sql
    assert "started_at=$1" in sql
    assert args[1] == started_at
    assert args[2] == run_id


# --- update_backtest_run_completion L-013 convention (1 test) -------------


async def test_update_backtest_run_completion_uses_text_mode_jsonb() -> None:
    """L-013: summary serialised via json.dumps(_to_jsonable(...)) text-mode + $N::jsonb."""
    from packages.core.types import BacktestStatus
    from packages.db.queries.analytics import update_backtest_run_completion

    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    run_id = uuid4()
    summary = {
        "total_trades": 4,
        "wr": 0.5,
        "pnl": "22.00",
        "pf": 3.75,
        "mdd": "5.00",
    }
    await update_backtest_run_completion(
        conn,
        run_id=run_id,
        status=BacktestStatus.COMPLETED,
        summary=summary,
        finished_at=datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC),
    )
    args, _kwargs = conn.execute.call_args
    sql = args[0]
    assert "summary=$2::jsonb" in sql
    # Bind value MUST be str (text-mode), not dict (codec-mode).
    assert isinstance(args[2], str)
    # Must be valid JSON.
    import json as json_mod

    parsed = json_mod.loads(args[2])
    assert parsed["total_trades"] == 4


# --- T-508 --compare mode (8 tests) ----------------------------------------

from scripts.backtest import (  # noqa: E402
    _format_aggregate_diff,
    _format_per_trade_diff,
)

# Argparse / cli_main dispatch (4 tests including WG#1 mutex hard-fail)


def test_argparse_compare_requires_2_uuids() -> None:
    """--compare with 1 arg → SystemExit(2) (argparse nargs=2 violation)."""
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--compare", "abc"])
    assert exc_info.value.code == 2


def test_argparse_compare_same_uuid_rejected_by_cli_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--compare A A → cli_main returns 2 with stderr message."""
    from scripts.backtest import cli_main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backtest.py",
            "--compare",
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000001",
        ],
    )
    assert cli_main() == 2


def test_argparse_compare_with_run_mode_flags_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#1: --compare + --bot → cli_main returns 2 with mutex stderr."""
    from scripts.backtest import cli_main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backtest.py",
            "--compare",
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            "--bot",
            "alpha",
        ],
    )
    assert cli_main() == 2


def test_argparse_run_mode_missing_required_flags_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run mode without --bot → cli_main returns 2 (custom stderr per T-508 cli_main)."""
    from scripts.backtest import cli_main

    monkeypatch.setattr(sys, "argv", ["backtest.py"])
    assert cli_main() == 2


# Aggregate diff math (2 tests; hand-computed §A-§B fixtures + WG#4 fixture pin)


def test_format_aggregate_diff_4_trades() -> None:
    """§A: 5 metric deltas; verify exact stdout content per WG#4 fixture pin."""
    summary_a = {"total_trades": 4, "wr": 0.5, "pnl": "22.00", "pf": 3.75, "mdd": "5.00"}
    summary_b = {"total_trades": 4, "wr": 0.75, "pnl": "45.00", "pf": 9.0, "mdd": "2.00"}
    output = _format_aggregate_diff(summary_a, summary_b)
    lines = output.splitlines()
    assert lines[0] == "Aggregate metrics:"
    # Per-row exact-string assertions (WG#4 lock).
    assert "total_trades" in lines[3]
    assert "0" in lines[3].split()[-1]  # delta=0 (4-4)
    assert "wr" in lines[4]
    assert "+0.2500" in lines[4]
    assert "pnl" in lines[5]
    assert "+23.00" in lines[5]
    assert "pf" in lines[6]
    assert "+5.2500" in lines[6]
    assert "mdd" in lines[7]
    assert "-3.00" in lines[7]


def test_format_aggregate_diff_pf_none_displays_na() -> None:
    """§B: PF=None per ADR-0008 → '—' value cell + 'n/a' delta cell (WG#4 fixture pin)."""
    summary_a = {"total_trades": 4, "wr": 0.5, "pnl": "22.00", "pf": 3.75, "mdd": "5.00"}
    summary_b = {"total_trades": 4, "wr": 0.5, "pnl": "22.00", "pf": None, "mdd": "5.00"}
    output = _format_aggregate_diff(summary_a, summary_b)
    lines = output.splitlines()
    pf_line = next(line for line in lines if line.strip().startswith("pf"))
    assert "—" in pf_line  # B's PF=None → em-dash value cell
    assert "n/a" in pf_line  # delta column 'n/a' per ADR-0008


# Per-trade diff (3 tests; §C/§D fixtures + WG#3 no-common-signals message)


def test_format_per_trade_diff_3_diverging_signals() -> None:
    """§C: 4 trades, 3 differ → 3 rows + row count summary."""
    from packages.db.queries.analytics import DivergingTradeRow

    rows = [
        DivergingTradeRow(101, "sl", Decimal("-5.00"), "tp", Decimal("10.00")),
        DivergingTradeRow(102, "tp", Decimal("10.00"), "tp", Decimal("15.00")),
        DivergingTradeRow(104, "sl", Decimal("-3.00"), "tp", Decimal("20.00")),
    ]
    output = _format_per_trade_diff(rows, common_count=4)
    assert "3 of 4 common signals diverged" in output
    assert "101" in output
    assert "102" in output
    assert "104" in output
    assert "103" not in output  # 103 matches; not in diverging list


def test_format_per_trade_diff_empty_returns_no_diverging_message() -> None:
    """§D: M>0 + N=0 → 'No diverging trades found.' message."""
    output = _format_per_trade_diff([], common_count=4)
    assert output == "No diverging trades found.\n"


def test_format_per_trade_diff_no_common_signals_returns_na_message() -> None:
    """WG#3: M=0 → 'No common signals' message (NOT misleading 'N of 0 diverged')."""
    output = _format_per_trade_diff([], common_count=0)
    assert "No common signals between run_A and run_B" in output
    assert "per-trade diff not applicable" in output


# select_diverging_trades_for_compare SQL bind shape (1 test)


# --- T-509 main() backwards-compat after run_id refactor (2 tests) -----------


async def test_main_default_run_id_creates_fresh_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-509 backwards-compat: args without run_id (existing CLI) creates row.

    Verifies T-507b CLI invocation path UNCHANGED post-T-509 main() refactor.
    """
    import scripts.backtest as backtest_mod

    insert_calls: list[object] = []

    async def _capture_insert(*_args: object, **kwargs: object) -> object:
        insert_calls.append(kwargs)
        # Return minimal object with .id attribute
        return MagicMock(id=uuid4())

    monkeypatch.setattr(backtest_mod, "insert_backtest_run", _capture_insert)
    monkeypatch.setattr(backtest_mod, "update_backtest_run_to_running", AsyncMock())
    monkeypatch.setattr(backtest_mod, "update_backtest_run_completion", AsyncMock())
    monkeypatch.setattr(backtest_mod, "copy_paper_trades_to_backtest", AsyncMock(return_value=0))
    monkeypatch.setattr(
        backtest_mod,
        "_compute_summary",
        AsyncMock(return_value={"total_trades": 0, "wr": None, "pnl": "0", "pf": None, "mdd": "0"}),
    )
    # Stub out heavy composition (load config + bus + paper).
    monkeypatch.setattr(
        backtest_mod,
        "_load_bot_config_with_overrides",
        lambda **_: (
            "yaml",
            "hash",
            MagicMock(
                symbols=["BTCUSDT"],
                execution=MagicMock(fee_rate=Decimal("0.0006")),
                signals=MagicMock(ttl_seconds=120),
            ),
        ),
    )
    monkeypatch.setattr(
        backtest_mod,
        "create_pool",
        AsyncMock(return_value=_make_pool_for_main()),
    )
    monkeypatch.setattr(
        backtest_mod,
        "ReplayBus",
        lambda: MagicMock(
            subscribe=AsyncMock(),
            publish=AsyncMock(),
            run_until_empty=AsyncMock(),
            close=AsyncMock(),
        ),
    )
    monkeypatch.setattr(backtest_mod, "HistoricalOHLCSource", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(backtest_mod, "HistoricalSignalSource", lambda *a, **kw: _FakeAsyncIter([]))
    monkeypatch.setattr(
        backtest_mod, "PaperExchange", lambda **kw: MagicMock(run_replay=AsyncMock())
    )
    monkeypatch.setattr(backtest_mod, "FeatureResolver", lambda **kw: MagicMock())
    monkeypatch.setattr(backtest_mod, "make_signal_handler", lambda **kw: AsyncMock())
    monkeypatch.setattr(backtest_mod, "make_per_bot_handler", lambda **kw: AsyncMock())
    monkeypatch.setattr(backtest_mod, "ExecutionDispatcher", lambda **kw: MagicMock())
    monkeypatch.setattr(backtest_mod, "run_dispatcher_for_bot", AsyncMock())
    monkeypatch.setattr(
        backtest_mod,
        "ExecutionSettings",
        lambda: MagicMock(
            execution_orders_dedup_capacity=10000,
            execution_fill_price_retry_attempts=3,
            execution_fill_price_retry_backoff_s=0.1,
            position_poll_interval_s=1.0,
            position_poll_stale_ticks=5,
            dispatch_dedup_capacity=10000,
        ),
    )

    args = argparse.Namespace(
        bot="alpha",
        from_at=datetime(2026, 4, 1, tzinfo=UTC),
        to_at=datetime(2026, 4, 2, tzinfo=UTC),
        config_path=Path("/tmp/test.yaml"),
        overrides=[],
        pace="max",
        source="binance",
        plugin_registry_path=None,
        db_url="postgresql://test",
        name=None,
        notes=None,
        # NO run_id attr — existing CLI behavior
    )
    rc = await backtest_mod.main(args)
    assert rc == 0
    assert len(insert_calls) == 1, "CLI path MUST call insert_backtest_run exactly once"


async def test_main_with_external_run_id_skips_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-509: args.run_id (worker path) → skip insert_backtest_run."""
    import scripts.backtest as backtest_mod

    insert_calls: list[object] = []

    async def _capture_insert(*_args: object, **kwargs: object) -> object:
        insert_calls.append(kwargs)
        return MagicMock(id=uuid4())

    monkeypatch.setattr(backtest_mod, "insert_backtest_run", _capture_insert)
    monkeypatch.setattr(backtest_mod, "update_backtest_run_to_running", AsyncMock())
    monkeypatch.setattr(backtest_mod, "update_backtest_run_completion", AsyncMock())
    monkeypatch.setattr(backtest_mod, "copy_paper_trades_to_backtest", AsyncMock(return_value=0))
    monkeypatch.setattr(
        backtest_mod,
        "_compute_summary",
        AsyncMock(return_value={"total_trades": 0, "wr": None, "pnl": "0", "pf": None, "mdd": "0"}),
    )
    monkeypatch.setattr(
        backtest_mod,
        "_load_bot_config_with_overrides",
        lambda **_: (
            "yaml",
            "hash",
            MagicMock(
                symbols=["BTCUSDT"],
                execution=MagicMock(fee_rate=Decimal("0.0006")),
                signals=MagicMock(ttl_seconds=120),
            ),
        ),
    )
    monkeypatch.setattr(
        backtest_mod,
        "create_pool",
        AsyncMock(return_value=_make_pool_for_main()),
    )
    monkeypatch.setattr(
        backtest_mod,
        "ReplayBus",
        lambda: MagicMock(
            subscribe=AsyncMock(),
            publish=AsyncMock(),
            run_until_empty=AsyncMock(),
            close=AsyncMock(),
        ),
    )
    monkeypatch.setattr(backtest_mod, "HistoricalOHLCSource", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(backtest_mod, "HistoricalSignalSource", lambda *a, **kw: _FakeAsyncIter([]))
    monkeypatch.setattr(
        backtest_mod, "PaperExchange", lambda **kw: MagicMock(run_replay=AsyncMock())
    )
    monkeypatch.setattr(backtest_mod, "FeatureResolver", lambda **kw: MagicMock())
    monkeypatch.setattr(backtest_mod, "make_signal_handler", lambda **kw: AsyncMock())
    monkeypatch.setattr(backtest_mod, "make_per_bot_handler", lambda **kw: AsyncMock())
    monkeypatch.setattr(backtest_mod, "ExecutionDispatcher", lambda **kw: MagicMock())
    monkeypatch.setattr(backtest_mod, "run_dispatcher_for_bot", AsyncMock())
    monkeypatch.setattr(
        backtest_mod,
        "ExecutionSettings",
        lambda: MagicMock(
            execution_orders_dedup_capacity=10000,
            execution_fill_price_retry_attempts=3,
            execution_fill_price_retry_backoff_s=0.1,
            position_poll_interval_s=1.0,
            position_poll_stale_ticks=5,
            dispatch_dedup_capacity=10000,
        ),
    )

    external_run_id = uuid4()
    args = argparse.Namespace(
        bot="alpha",
        from_at=datetime(2026, 4, 1, tzinfo=UTC),
        to_at=datetime(2026, 4, 2, tzinfo=UTC),
        config_path=Path("/tmp/test.yaml"),
        overrides=[],
        pace="max",
        source="binance",
        plugin_registry_path=None,
        db_url="postgresql://test",
        name=None,
        notes=None,
        run_id=external_run_id,  # T-509 worker path
    )
    rc = await backtest_mod.main(args)
    assert rc == 0
    assert insert_calls == [], "Worker path MUST NOT call insert_backtest_run"


def _make_pool_for_main() -> MagicMock:
    """Minimal pool mock for main() backwards-compat tests."""
    pool = MagicMock()
    pool.close = AsyncMock()
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    tx_cm = MagicMock()
    tx_cm.__aenter__ = AsyncMock(return_value=conn)
    tx_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx_cm)
    pool.acquire = MagicMock(return_value=cm)
    return pool


class _FakeAsyncIter:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def __aiter__(self) -> object:
        async def _gen() -> object:
            for item in self._items:
                yield item

        return _gen()


# --- T-509 codec_registered regression guard (1 test) -----------------------


async def test_update_backtest_run_completion_codec_registered_dict_bind() -> None:
    """L-013 BLOCKER #1 fix: codec_registered=True passes dict (not str) to asyncpg."""
    from packages.core.types import BacktestStatus
    from packages.db.queries.analytics import update_backtest_run_completion

    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    summary = {"total_trades": 4, "wr": 0.5}
    await update_backtest_run_completion(
        conn,
        run_id=uuid4(),
        status=BacktestStatus.FAILED,
        summary=summary,
        finished_at=datetime(2026, 4, 1, tzinfo=UTC),
        codec_registered=True,
    )
    args, _ = conn.execute.call_args
    # bind value MUST be dict (codec mode), NOT str (text mode)
    assert isinstance(args[2], dict), f"codec_registered=True MUST bind dict, got {type(args[2])}"


async def test_select_diverging_trades_sql_bind_shape() -> None:
    """SQL has IS DISTINCT FROM + ORDER BY signal_id; binds $1=run_a + $2=run_b."""
    from packages.db.queries.analytics import select_diverging_trades_for_compare

    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    run_a, run_b = uuid4(), uuid4()
    await select_diverging_trades_for_compare(conn, run_a=run_a, run_b=run_b)
    args, _kwargs = conn.fetch.call_args
    sql = args[0]
    assert "INNER JOIN backtest_trades b ON a.signal_id = b.signal_id" in sql
    assert "IS DISTINCT FROM" in sql
    assert "ORDER BY a.signal_id" in sql
    assert args[1] == run_a
    assert args[2] == run_b
