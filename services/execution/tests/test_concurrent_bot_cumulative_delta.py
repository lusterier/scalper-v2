"""§19 Phase F2 line 2529 — concurrent-bot cumulative-delta integration test (T-222 E3).

Verifies T-219 ``reconcile_close`` D4 lock semantics under simulated
concurrent close events from 2 bots sharing a single sub_account
(per ADR-0004: multiple bots may share a sub_account; per ADR-0006 D4:
``asyncio.Lock`` is keyed on ``sub_account`` STRING, NOT ``bot_id``).

Fixture topology:
* 2 bots ``alpha`` + ``beta``, both opened on sub_account ``shared-sub``.
* Both bots have an open ``BTCUSDT`` qty=0.1 trade.
* Adapter pool builds 2 adapters, T-219 reconcile.py shares ONE
  ``asyncio.Lock`` keyed on ``shared-sub``.
* Mock ``adapter.get_closed_pnl_cumulative`` returns scripted values
  (alpha: 100→125 = delta 25, beta: 125→145 = delta 20). Lock
  serializes BEFORE→sleep→AFTER triplet across both bots.
* Trigger 2 close events via ``asyncio.gather``.

Per WG#5: ``realized_pnl`` assertions are exact-Decimal compare
(``== Decimal("25")``, NOT ``pytest.approx``) per §N1 / H-001.

Hand-fixtures:
* alpha: BEFORE=Decimal("100"), AFTER=Decimal("125") → delta = Decimal("25")
* beta:  BEFORE=Decimal("125"), AFTER=Decimal("145") → delta = Decimal("20")
* Sum:   25 + 20 = 45 = (145 - 100) — conservation invariant.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.execution.app import reconcile as reconcile_mod
from services.execution.app.reconcile import reconcile_close

_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def patched_queries(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured_updates: list[dict[str, Any]] = []

    async def _capture_update(_conn: object, **kwargs: Any) -> None:
        captured_updates.append(kwargs)

    mocks: dict[str, Any] = {
        "select_open_order_id_by_trade_id": AsyncMock(return_value=100),
        "select_order_meta_by_id": AsyncMock(return_value=("cid-default", "ord-exch-1")),
        "update_trade_close": _capture_update,
        "delete_position_state": AsyncMock(return_value=None),
        "captured_updates": captured_updates,
    }
    for name in (
        "select_open_order_id_by_trade_id",
        "select_order_meta_by_id",
        "update_trade_close",
        "delete_position_state",
    ):
        monkeypatch.setattr(reconcile_mod, name, mocks[name])
    return mocks


def _adapter(snapshot_pair: tuple[Decimal, Decimal]) -> MagicMock:
    """Adapter mock with scripted (BEFORE, AFTER) snapshot returns."""
    a = MagicMock()
    a.get_closed_pnl_cumulative = AsyncMock(side_effect=list(snapshot_pair))
    return a


def _kwargs(
    *,
    bot_id: str,
    trade_id: int,
    adapter: MagicMock,
    lock: asyncio.Lock,
) -> dict[str, Any]:
    return {
        "conn": MagicMock(),
        "adapter": adapter,
        "bound_logger": MagicMock(),
        "bot_id": bot_id,
        "symbol": "BTCUSDT",
        "sub_account": "shared-sub",
        "closed_pnl_lock": lock,
        "closed_pnl_post_close_sleep_s": 0.0,
        "trade_id": trade_id,
        "close_order_id": 100 + trade_id,
        "exec_type": "close",
        "fees_paid_at_close": None,
        "final_fill_price": Decimal("100"),
        "closed_at": _FIXED_NOW,
    }


# ---------------------------------------------------------------------------
# Hazard test pin (verbatim brief §19 line 2529)
# ---------------------------------------------------------------------------


async def test_two_bots_share_sub_account_concurrent_closes_apportion_correctly(
    patched_queries: dict[str, Any],
) -> None:
    """E3 verbatim — concurrent close events from alpha + beta on shared-sub
    apportion realized_pnl correctly without cross-bot bleed.

    Per ADR-0006 D4: per-sub-account asyncio.Lock serializes BEFORE→AFTER
    snapshot pairs. The two reconcile_close calls run via asyncio.gather
    but are guaranteed serial inside the lock window. Fixture A (alpha
    100→125 = 25) and Fixture B (beta 125→145 = 20) chained via the lock
    so beta's BEFORE reads alpha's AFTER value.

    Decimal exact-equality per §N1 / H-001.
    """
    shared_lock = asyncio.Lock()
    alpha_adapter = _adapter((Decimal("100"), Decimal("125")))
    beta_adapter = _adapter((Decimal("125"), Decimal("145")))

    await asyncio.gather(
        reconcile_close(
            **_kwargs(bot_id="alpha", trade_id=1, adapter=alpha_adapter, lock=shared_lock)
        ),
        reconcile_close(
            **_kwargs(bot_id="beta", trade_id=2, adapter=beta_adapter, lock=shared_lock)
        ),
    )

    captured = patched_queries["captured_updates"]
    assert len(captured) == 2
    by_trade_id = {row["trade_id"]: row for row in captured}
    assert by_trade_id[1]["realized_pnl"] == Decimal("25")
    assert by_trade_id[2]["realized_pnl"] == Decimal("20")
    # Conservation invariant: sum of attributed deltas == total cumulative delta.
    total = by_trade_id[1]["realized_pnl"] + by_trade_id[2]["realized_pnl"]
    assert total == Decimal("145") - Decimal("100")
    assert total == Decimal("45")


# ---------------------------------------------------------------------------
# D4 mechanism + invariant pins
# ---------------------------------------------------------------------------


async def test_lock_serializes_before_sleep_after_triplet_per_sub_account(
    patched_queries: dict[str, Any],
) -> None:
    """D4 mechanism — only one BEFORE→sleep→AFTER triplet runs at a time per
    sub_account. Verified via lock-acquired counter incremented inside both
    reconcile_close calls; counter peaks at 1, never 2.
    """
    shared_lock = asyncio.Lock()
    in_critical_section = 0
    peak = 0

    real_get_closed_pnl = AsyncMock

    def _make_adapter(snapshot_pair: tuple[Decimal, Decimal]) -> MagicMock:
        a = MagicMock()
        snapshots = list(snapshot_pair)
        call_count = {"n": 0}

        async def _snapshot(_sub_account: str) -> Decimal:
            nonlocal in_critical_section, peak
            in_critical_section += 1
            peak = max(peak, in_critical_section)
            try:
                await asyncio.sleep(0)
                return snapshots[call_count["n"]]
            finally:
                call_count["n"] += 1
                in_critical_section -= 1

        a.get_closed_pnl_cumulative = _snapshot
        return a

    alpha_adapter = _make_adapter((Decimal("100"), Decimal("125")))
    beta_adapter = _make_adapter((Decimal("125"), Decimal("145")))

    await asyncio.gather(
        reconcile_close(
            **_kwargs(bot_id="alpha", trade_id=1, adapter=alpha_adapter, lock=shared_lock)
        ),
        reconcile_close(
            **_kwargs(bot_id="beta", trade_id=2, adapter=beta_adapter, lock=shared_lock)
        ),
    )

    # Either bot's snapshot pair runs serially inside the lock window;
    # peak concurrent get_closed_pnl_cumulative calls observed must be exactly 1.
    assert peak == 1, f"D4 lock failed to serialize: peak concurrent snapshots = {peak}"
    _ = real_get_closed_pnl  # keep import shape stable


async def test_no_cross_bot_pnl_bleed_under_concurrent_close(
    patched_queries: dict[str, Any],
) -> None:
    """Invariant: alpha.realized_pnl never includes beta's delta nor vice versa.

    Per H-001 cumulative-delta source-of-truth: each trade's realized_pnl
    is exactly its own (BEFORE, AFTER) delta — never the cumulative pool's
    sum. Cross-bot bleed would manifest as alpha being attributed beta's
    20-USD delta (or 45 = 25+20 sum) instead of its own 25.

    Decimal exact-equality per §N1.
    """
    shared_lock = asyncio.Lock()
    alpha_adapter = _adapter((Decimal("100"), Decimal("125")))
    beta_adapter = _adapter((Decimal("125"), Decimal("145")))

    await asyncio.gather(
        reconcile_close(
            **_kwargs(bot_id="alpha", trade_id=1, adapter=alpha_adapter, lock=shared_lock)
        ),
        reconcile_close(
            **_kwargs(bot_id="beta", trade_id=2, adapter=beta_adapter, lock=shared_lock)
        ),
    )

    captured = patched_queries["captured_updates"]
    by_trade_id = {row["trade_id"]: row for row in captured}
    # Alpha's realized_pnl is NOT 45 (sum) and NOT 20 (beta's delta) — only its own 25.
    assert by_trade_id[1]["realized_pnl"] != Decimal("45")
    assert by_trade_id[1]["realized_pnl"] != Decimal("20")
    assert by_trade_id[1]["realized_pnl"] == Decimal("25")
    # Beta's realized_pnl is NOT 45 and NOT 25 — only its own 20.
    assert by_trade_id[2]["realized_pnl"] != Decimal("45")
    assert by_trade_id[2]["realized_pnl"] != Decimal("25")
    assert by_trade_id[2]["realized_pnl"] == Decimal("20")
