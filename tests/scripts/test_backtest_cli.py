"""T-507b backtest CLI unit tests (12 named tests).

Hand-computed §A-§E summary fixtures cross-check `_compute_summary` math.
Composition smoke uses fake source + mock pool to avoid DB dependency.
Integration test (env-gated) lives in test_backtest_integration.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
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


def test_argparse_required_flags_missing() -> None:
    """Missing any of --bot/--from/--to/--config-path → SystemExit(2)."""
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([])
    assert exc_info.value.code == 2


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
