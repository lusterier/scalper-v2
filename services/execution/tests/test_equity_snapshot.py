"""§N4 unit tests for :mod:`services.execution.app.equity_snapshot` (T-531).

Mock-based (mirror ``test_audit.py``): pool.acquire ctx, ``get_account_balance``
per sub_account, ``insert_equity_snapshot`` patched on the module, real
``build_execution_metrics`` + ``CollectorRegistry`` so gauge values are
asserted via ``registry.get_sample_value``. Covers OQ-4=A per-bot fan-out,
two-tier resilience, ``now_fn``-once, and the Gate-4 gauge boundary.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from prometheus_client import CollectorRegistry

from packages.exchange.types import AccountBalance
from services.execution.app import equity_snapshot as eq_mod
from services.execution.app.equity_snapshot import run_equity_snapshot_tick
from services.execution.app.metrics import build_execution_metrics

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_FIXED_NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


class _FakeConn:
    pass


def _build_pool() -> MagicMock:
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire() -> AsyncIterator[_FakeConn]:
        yield _FakeConn()

    pool.acquire = _acquire
    return pool


def _bal(**over: Decimal) -> AccountBalance:
    base = {
        "wallet_balance": Decimal("10250.5000"),
        "available_balance": Decimal("10250.5000"),
        "total_equity": Decimal("10250.5000"),
        "margin_balance": Decimal("10250.5000"),
        "unrealized_pnl": Decimal("0"),
    }
    base.update(over)
    return AccountBalance(**base)


def _adapter(bal: AccountBalance | Exception) -> MagicMock:
    adapter = MagicMock()
    if isinstance(bal, Exception):
        adapter.get_account_balance = AsyncMock(side_effect=bal)
    else:
        adapter.get_account_balance = AsyncMock(return_value=bal)
    return adapter


@pytest.fixture
def patched_insert(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(eq_mod, "insert_equity_snapshot", mock)
    return mock


def _run_kwargs(
    *,
    sub_account_to_adapter: dict[str, Any],
    sub_account_to_bot_ids: dict[str, list[str]],
    registry: CollectorRegistry,
    now_fn: Any = None,
) -> dict[str, Any]:
    return {
        "pool": _build_pool(),
        "sub_account_to_adapter": sub_account_to_adapter,
        "sub_account_to_bot_ids": sub_account_to_bot_ids,
        "metrics": build_execution_metrics(registry),
        "bound_logger": MagicMock(),
        "now_fn": now_fn or MagicMock(return_value=_FIXED_NOW),
    }


async def test_paper_single_bot_records_row_and_gauge(patched_insert: AsyncMock) -> None:
    registry = CollectorRegistry()
    await run_equity_snapshot_tick(
        **_run_kwargs(
            sub_account_to_adapter={"alpha": _adapter(_bal())},
            sub_account_to_bot_ids={"alpha": ["alpha"]},
            registry=registry,
        )
    )
    patched_insert.assert_awaited_once()
    assert patched_insert.await_args is not None
    kw = patched_insert.await_args.kwargs
    assert kw["bot_id"] == "alpha"
    assert kw["snapshot_at"] == _FIXED_NOW
    assert kw["total_equity"] == Decimal("10250.5000")
    assert kw["unrealized_pnl"] == Decimal("0")
    assert registry.get_sample_value("virtual_balance", {"bot_id": "alpha"}) == 10250.5


async def test_shared_subaccount_fans_out_per_bot_one_fetch(
    patched_insert: AsyncMock,
) -> None:
    """OQ-4=A: 1 get_account_balance fetch → N per-bot rows + gauges, same ts."""
    registry = CollectorRegistry()
    adapter = _adapter(_bal(total_equity=Decimal("125000.1234")))
    await run_equity_snapshot_tick(
        **_run_kwargs(
            sub_account_to_adapter={"subA": adapter},
            sub_account_to_bot_ids={"subA": ["alpha", "beta"]},
            registry=registry,
        )
    )
    adapter.get_account_balance.assert_awaited_once_with("subA")
    assert patched_insert.await_count == 2
    bots = {c.kwargs["bot_id"] for c in patched_insert.await_args_list}
    assert bots == {"alpha", "beta"}
    snaps = {c.kwargs["snapshot_at"] for c in patched_insert.await_args_list}
    assert snaps == {_FIXED_NOW}
    assert registry.get_sample_value("virtual_balance", {"bot_id": "alpha"}) == 125000.1234
    assert registry.get_sample_value("virtual_balance", {"bot_id": "beta"}) == 125000.1234


async def test_fetch_failure_skips_subaccount_continues(
    patched_insert: AsyncMock,
) -> None:
    registry = CollectorRegistry()
    logger = MagicMock()
    kwargs = _run_kwargs(
        sub_account_to_adapter={
            "bad": _adapter(RuntimeError("bybit down")),
            "good": _adapter(_bal()),
        },
        sub_account_to_bot_ids={"bad": ["b1"], "good": ["g1"]},
        registry=registry,
    )
    kwargs["bound_logger"] = logger
    await run_equity_snapshot_tick(**kwargs)
    # Only the good sub-account's bot persisted.
    assert patched_insert.await_count == 1
    assert patched_insert.await_args is not None
    assert patched_insert.await_args.kwargs["bot_id"] == "g1"
    logged = [c.args[0] for c in logger.error.call_args_list]
    assert "equity_snapshot.fetch_failed" in logged


async def test_persist_failure_skips_bot_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One bot's INSERT raising must not drop the sibling bot (audit.db_failed analog)."""
    registry = CollectorRegistry()
    logger = MagicMock()

    async def _insert(_conn: Any, **kw: Any) -> None:
        if kw["bot_id"] == "alpha":
            raise RuntimeError("db down")

    monkeypatch.setattr(eq_mod, "insert_equity_snapshot", _insert)
    kwargs = _run_kwargs(
        sub_account_to_adapter={"subA": _adapter(_bal(total_equity=Decimal("777.0000")))},
        sub_account_to_bot_ids={"subA": ["alpha", "beta"]},
        registry=registry,
    )
    kwargs["bound_logger"] = logger
    await run_equity_snapshot_tick(**kwargs)
    logged = [c.args[0] for c in logger.error.call_args_list]
    assert "equity_snapshot.persist_failed" in logged
    # beta still got its gauge despite alpha's persist failure.
    assert registry.get_sample_value("virtual_balance", {"bot_id": "beta"}) == 777.0
    assert registry.get_sample_value("virtual_balance", {"bot_id": "alpha"}) is None


async def test_now_fn_called_once_shared_across_rows(patched_insert: AsyncMock) -> None:
    registry = CollectorRegistry()
    now_fn = MagicMock(return_value=_FIXED_NOW)
    kwargs = _run_kwargs(
        sub_account_to_adapter={"subA": _adapter(_bal())},
        sub_account_to_bot_ids={"subA": ["a", "b", "c"]},
        registry=registry,
        now_fn=now_fn,
    )
    await run_equity_snapshot_tick(**kwargs)
    assert now_fn.call_count == 1
    assert all(c.kwargs["snapshot_at"] == _FIXED_NOW for c in patched_insert.await_args_list)


async def test_gauge_set_from_pre_persist_total_equity_float(
    patched_insert: AsyncMock,
) -> None:
    """Gate-4 boundary (b): gauge = float(pre-persist Decimal), full precision."""
    registry = CollectorRegistry()
    await run_equity_snapshot_tick(
        **_run_kwargs(
            sub_account_to_adapter={
                "alpha": _adapter(_bal(total_equity=Decimal("125000.12345678")))
            },
            sub_account_to_bot_ids={"alpha": ["alpha"]},
            registry=registry,
        )
    )
    assert registry.get_sample_value("virtual_balance", {"bot_id": "alpha"}) == float(
        Decimal("125000.12345678")
    )


async def test_negative_unrealized_passed_through(patched_insert: AsyncMock) -> None:
    registry = CollectorRegistry()
    await run_equity_snapshot_tick(
        **_run_kwargs(
            sub_account_to_adapter={"alpha": _adapter(_bal(unrealized_pnl=Decimal("-125.2500")))},
            sub_account_to_bot_ids={"alpha": ["alpha"]},
            registry=registry,
        )
    )
    assert patched_insert.await_args is not None
    assert patched_insert.await_args.kwargs["unrealized_pnl"] == Decimal("-125.2500")
