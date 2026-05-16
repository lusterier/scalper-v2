"""§N4 unit tests for :mod:`services.execution.app.funding_fee_poll` (T-532b).

Mock-based (mirror ``test_equity_snapshot.py``): ``pool.acquire`` ctx
(MagicMock-wrapped so ``call_count`` pins the one-acquire-per-sub-account
divergence from the T-531 per-insert-acquire mirror), ``get_funding_fees_window``
per sub_account, ``insert_funding_fee`` + ``insert_trading_event`` patched
on the module. Covers the T-531 per-bot fan-out, the EXACTLY-1
cumulative-emit (no N-bot double-count), two-tier resilience (incl. the
emit-failure isolation), ``now_fn``-once, empty→skip-both, window boundary.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.exchange.types import FundingFee
from services.execution.app import funding_fee_poll as fp_mod
from services.execution.app.funding_fee_poll import run_funding_fee_poll_tick

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_FIXED_NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
_WINDOW_S = 10800  # 3h — mirror execution_audit_window_seconds
_SETTLED = datetime(2026, 5, 16, 11, 30, 0, tzinfo=UTC)


class _FakeConn:
    pass


def _build_pool() -> MagicMock:
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire() -> AsyncIterator[_FakeConn]:
        yield _FakeConn()

    # MagicMock wrapper → .call_count pins one-acquire-per-sub-account.
    pool.acquire = MagicMock(side_effect=_acquire)
    return pool


def _fee(symbol: str, funding: str) -> FundingFee:
    return FundingFee(symbol=symbol, settled_at=_SETTLED, funding=Decimal(funding))


def _adapter(fees: list[FundingFee] | Exception) -> MagicMock:
    adapter = MagicMock()
    if isinstance(fees, Exception):
        adapter.get_funding_fees_window = AsyncMock(side_effect=fees)
    else:
        adapter.get_funding_fees_window = AsyncMock(return_value=fees)
    return adapter


@pytest.fixture
def patched_inserts(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    ins_fee = AsyncMock(return_value=None)
    ins_evt = AsyncMock(return_value=None)
    monkeypatch.setattr(fp_mod, "insert_funding_fee", ins_fee)
    monkeypatch.setattr(fp_mod, "insert_trading_event", ins_evt)
    return ins_fee, ins_evt


def _run_kwargs(
    *,
    sub_account_to_adapter: dict[str, Any],
    sub_account_to_bot_ids: dict[str, list[str]],
    pool: MagicMock | None = None,
    now_fn: Any = None,
) -> dict[str, Any]:
    return {
        "pool": pool if pool is not None else _build_pool(),
        "sub_account_to_adapter": sub_account_to_adapter,
        "sub_account_to_bot_ids": sub_account_to_bot_ids,
        "window_seconds": _WINDOW_S,
        "bound_logger": MagicMock(),
        "now_fn": now_fn or MagicMock(return_value=_FIXED_NOW),
    }


async def test_fan_out_per_bot_and_exactly_one_emit(
    patched_inserts: tuple[AsyncMock, AsyncMock],
) -> None:
    """L-017 both-sides cardinality: 3 fees x 2 bots → 6 insert_funding_fee
    AND EXACTLY 1 insert_trading_event (summed once from the in-memory list,
    NOT SUM over the 6 fanned rows → no N-bot double-count). Signed SUM
    -0.12 + 0.05 + -0.03 = -0.10 (start=Decimal('0'))."""
    ins_fee, ins_evt = patched_inserts
    fees = [_fee("BTCUSDT", "-0.12"), _fee("ETHUSDT", "0.05"), _fee("BTCUSDT", "-0.03")]
    await run_funding_fee_poll_tick(
        **_run_kwargs(
            sub_account_to_adapter={"sub-a": _adapter(fees)},
            sub_account_to_bot_ids={"sub-a": ["m", "n"]},
        )
    )
    assert ins_fee.await_count == 6  # 3 fees x 2 bots
    bot_ids = {c.kwargs["bot_id"] for c in ins_fee.await_args_list}
    assert bot_ids == {"m", "n"}
    ins_evt.assert_awaited_once()
    assert ins_evt.await_args is not None
    kw = ins_evt.await_args.kwargs
    assert kw["bot_id"] is None  # H-017-clean: sub-account attribution
    assert kw["event_type"] == "funding_settlement_window"
    assert kw["payload"]["cumulative_funding"] == str(Decimal("-0.10"))
    assert kw["payload"]["settlement_count"] == 3
    assert kw["payload"]["sub_account"] == "sub-a"


async def test_window_boundary_passed_to_adapter(
    patched_inserts: tuple[AsyncMock, AsyncMock],
) -> None:
    """now_fn=12:00:00Z, window=10800s → since=09:00:00Z."""
    adapter = _adapter([_fee("BTCUSDT", "1")])
    await run_funding_fee_poll_tick(
        **_run_kwargs(
            sub_account_to_adapter={"sub-a": adapter},
            sub_account_to_bot_ids={"sub-a": ["m"]},
        )
    )
    adapter.get_funding_fees_window.assert_awaited_once_with(
        "sub-a", _FIXED_NOW - timedelta(seconds=_WINDOW_S)
    )


async def test_empty_window_skips_both(
    patched_inserts: tuple[AsyncMock, AsyncMock],
) -> None:
    """fees==[] → 0 insert_funding_fee AND 0 insert_trading_event (no
    empty-emit noise; mirror audit sub-threshold 0-rows-written)."""
    ins_fee, ins_evt = patched_inserts
    pool = _build_pool()
    await run_funding_fee_poll_tick(
        **_run_kwargs(
            sub_account_to_adapter={"sub-a": _adapter([])},
            sub_account_to_bot_ids={"sub-a": ["m"]},
            pool=pool,
        )
    )
    ins_fee.assert_not_awaited()
    ins_evt.assert_not_awaited()
    pool.acquire.assert_not_called()  # empty → no acquire at all


async def test_acquire_once_per_non_empty_sub_account(
    patched_inserts: tuple[AsyncMock, AsyncMock],
) -> None:
    """Bod-A divergence pin: ONE pool.acquire per non-empty sub-account
    (NOT per-insert like the T-531 mirror). 2 non-empty → call_count==2."""
    pool = _build_pool()
    await run_funding_fee_poll_tick(
        **_run_kwargs(
            sub_account_to_adapter={
                "sub-a": _adapter([_fee("BTCUSDT", "1")]),
                "sub-b": _adapter([_fee("ETHUSDT", "2"), _fee("ETHUSDT", "3")]),
            },
            sub_account_to_bot_ids={"sub-a": ["m"], "sub-b": ["n", "o"]},
            pool=pool,
        )
    )
    assert pool.acquire.call_count == 2  # one per non-empty sub-account


async def test_fetch_failure_skips_subaccount_continues(
    patched_inserts: tuple[AsyncMock, AsyncMock],
) -> None:
    ins_fee, ins_evt = patched_inserts
    logger = MagicMock()
    kwargs = _run_kwargs(
        sub_account_to_adapter={
            "bad": _adapter(RuntimeError("bybit down")),
            "good": _adapter([_fee("BTCUSDT", "1.5")]),
        },
        sub_account_to_bot_ids={"bad": ["b1"], "good": ["g1"]},
    )
    kwargs["bound_logger"] = logger
    await run_funding_fee_poll_tick(**kwargs)
    assert ins_fee.await_count == 1  # only "good" → 1 fee x 1 bot
    assert ins_fee.await_args is not None
    assert ins_fee.await_args.kwargs["bot_id"] == "g1"
    ins_evt.assert_awaited_once()  # only "good"
    logged = [c.args[0] for c in logger.error.call_args_list]
    assert "funding_fee_poll.fetch_failed" in logged


async def test_emit_failure_isolates_subaccount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """persist_failed two-tier: insert_trading_event raising for sub-a must
    NOT drop sub-b (Bod-A: no conn.transaction, accepted degradation)."""
    logger = MagicMock()
    monkeypatch.setattr(fp_mod, "insert_funding_fee", AsyncMock(return_value=None))

    async def _emit(_conn: Any, **kw: Any) -> None:
        if kw["payload"]["sub_account"] == "sub-a":
            raise RuntimeError("trading_events down")

    monkeypatch.setattr(fp_mod, "insert_trading_event", _emit)
    kwargs = _run_kwargs(
        sub_account_to_adapter={
            "sub-a": _adapter([_fee("BTCUSDT", "1")]),
            "sub-b": _adapter([_fee("ETHUSDT", "2")]),
        },
        sub_account_to_bot_ids={"sub-a": ["m"], "sub-b": ["n"]},
    )
    kwargs["bound_logger"] = logger
    await run_funding_fee_poll_tick(**kwargs)
    logged = [c.args[0] for c in logger.error.call_args_list]
    assert "funding_fee_poll.persist_failed" in logged
    # sub-b fully processed despite sub-a's emit failure.
    recorded = [c.args[0] for c in logger.info.call_args_list]
    assert "funding_fee_poll.sub_account_recorded" in recorded


async def test_now_fn_called_once_shared(
    patched_inserts: tuple[AsyncMock, AsyncMock],
) -> None:
    """now_fn() once at tick start; shared by window_start + occurred_at."""
    _, ins_evt = patched_inserts
    now_fn = MagicMock(return_value=_FIXED_NOW)
    kwargs = _run_kwargs(
        sub_account_to_adapter={"sub-a": _adapter([_fee("BTCUSDT", "1")])},
        sub_account_to_bot_ids={"sub-a": ["m"]},
        now_fn=now_fn,
    )
    await run_funding_fee_poll_tick(**kwargs)
    assert now_fn.call_count == 1
    assert ins_evt.await_args is not None
    assert ins_evt.await_args.kwargs["occurred_at"] == _FIXED_NOW
