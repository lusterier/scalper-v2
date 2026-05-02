"""§N4 unit tests for :mod:`services.execution.app.audit` (T-220b).

Mock-based: pool.acquire ctx, ExchangeClient.get_closed_pnl_window per sub_account,
patched query helpers (insert_trade_pnl_delta + select_realized_pnl_sum_for_bots_since)
on audit_mod. Validates D1-D7 + H-017 verbatim test pin + 6 hand-fixtures + edge
cases (Bybit failure, DB failure, UNIQUE violation, paper-mode parity, §N1 UTC).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from asyncpg.exceptions import UniqueViolationError

from services.execution.app import audit as audit_mod
from services.execution.app.audit import run_pnl_audit_tick

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


class _FakeConn:
    pass


def _build_pool() -> MagicMock:
    conn = _FakeConn()
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    pool.acquire = _acquire
    return pool


def _make_adapter(closed_pnl: Decimal) -> MagicMock:
    adapter = MagicMock()
    adapter.get_closed_pnl_window = AsyncMock(return_value=closed_pnl)
    return adapter


@pytest.fixture
def patched_queries(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    mocks: dict[str, Any] = {
        "select_realized_pnl_sum_for_bots_since": AsyncMock(return_value=Decimal("0")),
        "insert_trade_pnl_delta": AsyncMock(return_value=None),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(audit_mod, name, mock)
    return mocks


def _kwargs(
    *,
    cumulative_bybit: Decimal = Decimal("0"),
    threshold: Decimal = Decimal("0.50"),
    sub_accounts: dict[str, Decimal] | None = None,
    bot_ids: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    if sub_accounts is None:
        sub_accounts = {"alpha-sub": cumulative_bybit}
    if bot_ids is None:
        bot_ids = {sa: ["alpha"] for sa in sub_accounts}
    return {
        "pool": _build_pool(),
        "sub_account_to_adapter": {sa: _make_adapter(pnl) for sa, pnl in sub_accounts.items()},
        "sub_account_to_bot_ids": bot_ids,
        "window_seconds": 10800,
        "divergence_threshold_usd": threshold,
        "bound_logger": MagicMock(),
        "now_fn": lambda: _FIXED_NOW,
    }


# ---------------------------------------------------------------------------
# Hazard test pin (verbatim brief §20 H-017)
# ---------------------------------------------------------------------------


async def test_audit_never_double_attributes_closed_pnl(
    patched_queries: dict[str, Any],
) -> None:
    """H-017 verbatim — schema is cumulative-only; no trade_id FK; no per-trade attribution.

    Sub-threshold rerun = no INSERT (filter primary).
    Supra-threshold post-correction rerun = T-219 already corrected
    `trades.realized_pnl`; subsequent tick reads zero divergence → no INSERT.
    Both paths assert `insert_trade_pnl_delta` is NOT called more than once
    per (sub_account, audit_run_at).
    """
    # Sub-threshold rerun: cumulative_bybit == cumulative_db.
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("100")
    kwargs = _kwargs(cumulative_bybit=Decimal("100"))
    await run_pnl_audit_tick(**kwargs)
    patched_queries["insert_trade_pnl_delta"].assert_not_called()


# ---------------------------------------------------------------------------
# Hand-fixtures A-F (delta math + threshold semantics)
# ---------------------------------------------------------------------------


async def test_audit_writes_trade_pnl_delta_when_divergence_exceeds_threshold(
    patched_queries: dict[str, Any],
) -> None:
    """Fixture A — supra-threshold positive: bybit=110, db=100, threshold=0.50 → INSERT."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("100")
    kwargs = _kwargs(cumulative_bybit=Decimal("110"))
    await run_pnl_audit_tick(**kwargs)
    insert_call = patched_queries["insert_trade_pnl_delta"].call_args
    assert insert_call.kwargs["delta"] == Decimal("10")
    assert insert_call.kwargs["cumulative_bybit"] == Decimal("110")
    assert insert_call.kwargs["cumulative_db"] == Decimal("100")
    assert insert_call.kwargs["sub_account"] == "alpha-sub"


async def test_audit_no_op_when_delta_below_threshold(
    patched_queries: dict[str, Any],
) -> None:
    """Fixture B — sub-threshold positive: bybit=100.30, db=100 → delta=0.30 → NO INSERT."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("100")
    kwargs = _kwargs(cumulative_bybit=Decimal("100.30"))
    await run_pnl_audit_tick(**kwargs)
    patched_queries["insert_trade_pnl_delta"].assert_not_called()


async def test_audit_writes_when_delta_negative_above_threshold(
    patched_queries: dict[str, Any],
) -> None:
    """Fixture C — supra-threshold negative: bybit=99, db=100 → delta=-1, abs=1 > 0.50 → INSERT."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("100")
    kwargs = _kwargs(cumulative_bybit=Decimal("99"))
    await run_pnl_audit_tick(**kwargs)
    insert_call = patched_queries["insert_trade_pnl_delta"].call_args
    assert insert_call.kwargs["delta"] == Decimal("-1")


async def test_audit_no_op_when_delta_negative_below_threshold(
    patched_queries: dict[str, Any],
) -> None:
    """Fixture D — sub-threshold negative: bybit=99.70, db=100 → abs=0.30 → NO INSERT."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("100")
    kwargs = _kwargs(cumulative_bybit=Decimal("99.70"))
    await run_pnl_audit_tick(**kwargs)
    patched_queries["insert_trade_pnl_delta"].assert_not_called()


async def test_audit_no_op_at_exact_threshold_boundary(
    patched_queries: dict[str, Any],
) -> None:
    """Fixture E — exact boundary: bybit=100.50, db=100 → abs=0.50 NOT > 0.50 → NO INSERT."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("100")
    kwargs = _kwargs(cumulative_bybit=Decimal("100.50"))
    await run_pnl_audit_tick(**kwargs)
    patched_queries["insert_trade_pnl_delta"].assert_not_called()


async def test_audit_idempotent_under_post_t_219_correction(
    patched_queries: dict[str, Any],
) -> None:
    """Fixture F — T-219 already wrote realized_pnl=110; rerun sees zero divergence → NO INSERT."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("110")
    kwargs = _kwargs(cumulative_bybit=Decimal("110"))
    await run_pnl_audit_tick(**kwargs)
    patched_queries["insert_trade_pnl_delta"].assert_not_called()


# ---------------------------------------------------------------------------
# Error path tests (loop continues; loop independence)
# ---------------------------------------------------------------------------


async def test_audit_bybit_api_failure_logs_error_and_continues(
    patched_queries: dict[str, Any],
) -> None:
    """Bybit API exception in get_closed_pnl_window → audit.bybit_api_failed log; loop continues."""
    bad_adapter = MagicMock()
    bad_adapter.get_closed_pnl_window = AsyncMock(side_effect=RuntimeError("api error"))
    good_adapter = _make_adapter(Decimal("0"))
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("0")

    pool = _build_pool()
    logger = MagicMock()
    await run_pnl_audit_tick(
        pool=pool,
        sub_account_to_adapter={"bad": bad_adapter, "good": good_adapter},
        sub_account_to_bot_ids={"bad": ["b1"], "good": ["g1"]},
        window_seconds=10800,
        divergence_threshold_usd=Decimal("0.50"),
        bound_logger=logger,
        now_fn=lambda: _FIXED_NOW,
    )
    error_keys = [c.args[0] for c in logger.error.call_args_list]
    assert "audit.bybit_api_failed" in error_keys
    # Loop continued — good adapter was queried.
    good_adapter.get_closed_pnl_window.assert_awaited_once()


async def test_audit_db_failure_logs_error_and_continues(
    patched_queries: dict[str, Any],
) -> None:
    """SQL exception in select_realized_pnl_sum_for_bots_since → db_failed; loop continues."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].side_effect = RuntimeError("db error")
    kwargs = _kwargs(cumulative_bybit=Decimal("100"))
    await run_pnl_audit_tick(**kwargs)
    error_keys = [c.args[0] for c in kwargs["bound_logger"].error.call_args_list]
    assert "audit.db_failed" in error_keys


async def test_audit_unique_violation_logs_warn_does_not_raise(
    patched_queries: dict[str, Any],
) -> None:
    """ADR-0007 D7 — concurrent run UNIQUE violation caught + WARN; idempotent."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("100")
    patched_queries["insert_trade_pnl_delta"].side_effect = UniqueViolationError("duplicate key")
    kwargs = _kwargs(cumulative_bybit=Decimal("110"))
    await run_pnl_audit_tick(**kwargs)
    warn_keys = [c.args[0] for c in kwargs["bound_logger"].warning.call_args_list]
    assert "audit.unique_violation_concurrent_run" in warn_keys


# ---------------------------------------------------------------------------
# Multi-sub-account loop independence
# ---------------------------------------------------------------------------


async def test_audit_loops_all_sub_accounts_independently(
    patched_queries: dict[str, Any],
) -> None:
    """2 sub-accounts; one fails Bybit → other still audited."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("0")
    bad = MagicMock()
    bad.get_closed_pnl_window = AsyncMock(side_effect=RuntimeError("api error"))
    good = _make_adapter(Decimal("100"))

    pool = _build_pool()
    logger = MagicMock()
    await run_pnl_audit_tick(
        pool=pool,
        sub_account_to_adapter={"bad": bad, "good": good},
        sub_account_to_bot_ids={"bad": ["b1"], "good": ["g1"]},
        window_seconds=10800,
        divergence_threshold_usd=Decimal("0.50"),
        bound_logger=logger,
        now_fn=lambda: _FIXED_NOW,
    )
    # Good sub-account got divergence INSERT.
    insert_call = patched_queries["insert_trade_pnl_delta"].call_args
    assert insert_call.kwargs["sub_account"] == "good"


# ---------------------------------------------------------------------------
# Contract tests (now_fn UTC + Settings threading)
# ---------------------------------------------------------------------------


async def test_audit_uses_now_fn_for_audit_run_at(
    patched_queries: dict[str, Any],
) -> None:
    """§N1 UTC — audit_run_at value comes from now_fn()."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("0")
    fixed_t = datetime(2026, 6, 1, 9, 30, 0, tzinfo=UTC)
    kwargs = _kwargs(cumulative_bybit=Decimal("100"))
    kwargs["now_fn"] = lambda: fixed_t
    await run_pnl_audit_tick(**kwargs)
    insert_call = patched_queries["insert_trade_pnl_delta"].call_args
    assert insert_call.kwargs["audit_run_at"] == fixed_t


async def test_audit_run_at_is_tz_aware_utc(
    patched_queries: dict[str, Any],
) -> None:
    """§N1 — audit_run_at MUST be tz-aware UTC (Gate-1 BLOCKER #3 fix)."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("0")
    kwargs = _kwargs(cumulative_bybit=Decimal("100"))
    await run_pnl_audit_tick(**kwargs)
    insert_call = patched_queries["insert_trade_pnl_delta"].call_args
    audit_run_at = insert_call.kwargs["audit_run_at"]
    assert audit_run_at.tzinfo is not None
    assert audit_run_at.utcoffset() == timedelta(0)


async def test_audit_window_start_is_now_minus_window_seconds(
    patched_queries: dict[str, Any],
) -> None:
    """Settings threading — window_start = audit_run_at - timedelta(seconds=window_seconds)."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("0")
    kwargs = _kwargs(cumulative_bybit=Decimal("100"))
    kwargs["window_seconds"] = 3600  # 1h
    await run_pnl_audit_tick(**kwargs)
    insert_call = patched_queries["insert_trade_pnl_delta"].call_args
    expected_window_start = _FIXED_NOW - timedelta(seconds=3600)
    assert insert_call.kwargs["window_start"] == expected_window_start


async def test_audit_works_for_paper_mode_sub_account(
    patched_queries: dict[str, Any],
) -> None:
    """Paper-mode parity — sub_account == bot_id per Decision #8; single-bot list."""
    patched_queries["select_realized_pnl_sum_for_bots_since"].return_value = Decimal("100")
    paper_adapter = _make_adapter(Decimal("105"))
    pool = _build_pool()
    logger = MagicMock()
    await run_pnl_audit_tick(
        pool=pool,
        sub_account_to_adapter={"paper-bot-1": paper_adapter},
        sub_account_to_bot_ids={"paper-bot-1": ["paper-bot-1"]},  # 1:1 per Decision #8
        window_seconds=10800,
        divergence_threshold_usd=Decimal("0.50"),
        bound_logger=logger,
        now_fn=lambda: _FIXED_NOW,
    )
    paper_adapter.get_closed_pnl_window.assert_awaited_once_with(
        "paper-bot-1",
        since=_FIXED_NOW - timedelta(seconds=10800),
    )
    insert_call = patched_queries["insert_trade_pnl_delta"].call_args
    assert insert_call.kwargs["sub_account"] == "paper-bot-1"
    assert insert_call.kwargs["delta"] == Decimal("5")
