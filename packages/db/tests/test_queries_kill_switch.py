"""§N4 unit tests for :mod:`packages.db.queries.kill_switch` (T-525a1).

Mock-based. Pins:

* `KillSwitchState` row narrowing (select hit → typed; miss → None).
* WG#3: `is_idempotent(upsert_kill_switch_trip)` AND
  `is_idempotent(clear_kill_switch)` are True (explicit).
* WG#2: SQL strings are column-direct only — NO `::` cast literal, NO
  `NOW()`/`CURRENT_TIMESTAMP`/`current_date` literal; upsert/clear are
  `ON CONFLICT (bot_id) DO UPDATE`.
* WG#5: `is_stale_daily_latch` 4-row truth table (incl. `max_drawdown` →
  False; pure — no internal datetime.now()).
* `$N` binds positional (L-008 mock-level).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.core import is_idempotent
from packages.db.queries.kill_switch import (
    KillSwitchState,
    clear_kill_switch,
    is_stale_daily_latch,
    select_kill_switch_state,
    upsert_kill_switch_trip,
)

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 5, 16, 9, 0, 0, tzinfo=UTC)


# --- WG#3 idempotency markers ------------------------------------------------


def test_upsert_and_clear_are_idempotent_marked() -> None:
    assert is_idempotent(upsert_kill_switch_trip) is True
    assert is_idempotent(clear_kill_switch) is True


def test_select_is_not_idempotent_marked() -> None:
    """Read-only — markers are for external writes only (§N3)."""
    assert is_idempotent(select_kill_switch_state) is False


# --- WG#5 is_stale_daily_latch truth table ----------------------------------


def _state(*, tripped: bool, reason: str | None, anchor: date | None) -> KillSwitchState:
    return KillSwitchState(
        bot_id="alpha",
        tripped=tripped,
        trip_reason=reason,
        tripped_at=_NOW if tripped else None,
        daily_anchor_date=anchor,
        cumulative_loss_usd=Decimal("-105.0000") if tripped else None,
    )


def test_stale_latch_not_tripped_false() -> None:
    s = _state(tripped=False, reason=None, anchor=None)
    assert is_stale_daily_latch(s, _NOW) is False


def test_stale_latch_daily_prior_day_true() -> None:
    s = _state(tripped=True, reason="daily_loss_limit", anchor=date(2026, 5, 15))
    assert is_stale_daily_latch(s, _NOW) is True  # anchor 05-15 < now 05-16


def test_stale_latch_daily_same_day_false() -> None:
    s = _state(tripped=True, reason="daily_loss_limit", anchor=date(2026, 5, 16))
    assert is_stale_daily_latch(s, _NOW) is False  # same UTC day → retain


def test_stale_latch_max_drawdown_reason_false() -> None:
    """Drawdown latch is a T-525b hard-stop — NOT cleared by UTC-day rollover."""
    s = _state(tripped=True, reason="max_drawdown", anchor=date(2026, 5, 15))
    assert is_stale_daily_latch(s, _NOW) is False


def test_stale_latch_is_pure_no_internal_now() -> None:
    """Predicate result depends solely on the passed `now` (deterministic)."""
    s = _state(tripped=True, reason="daily_loss_limit", anchor=date(2026, 5, 16))
    # Same state, a `now` on the anchor day → not stale; next day → stale.
    assert is_stale_daily_latch(s, datetime(2026, 5, 16, 23, 59, tzinfo=UTC)) is False
    assert is_stale_daily_latch(s, datetime(2026, 5, 17, 0, 1, tzinfo=UTC)) is True


# --- select narrowing --------------------------------------------------------


async def test_select_hit_returns_typed_state() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "bot_id": "alpha",
            "tripped": True,
            "trip_reason": "daily_loss_limit",
            "tripped_at": _NOW,
            "daily_anchor_date": date(2026, 5, 16),
            "cumulative_loss_usd": Decimal("-105.0000"),
        }
    )
    st = await select_kill_switch_state(conn, bot_id="alpha")
    assert isinstance(st, KillSwitchState)
    assert st.tripped is True
    assert st.trip_reason == "daily_loss_limit"
    assert st.cumulative_loss_usd == Decimal("-105.0000")


async def test_select_miss_returns_none() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    assert await select_kill_switch_state(conn, bot_id="ghost") is None


# --- WG#2 SQL-shape pins -----------------------------------------------------


async def _capture_execute(conn: MagicMock) -> list[tuple[Any, ...]]:
    captured: list[tuple[Any, ...]] = []

    async def _exec(sql: str, *args: Any) -> str:
        captured.append((sql, *args))
        return "INSERT 0 1"

    conn.execute = _exec
    return captured


async def test_upsert_sql_is_on_conflict_column_direct_no_cast_no_now() -> None:
    conn = MagicMock()
    captured = await _capture_execute(conn)
    await upsert_kill_switch_trip(
        conn,
        bot_id="alpha",
        trip_reason="daily_loss_limit",
        tripped_at=_NOW,
        daily_anchor_date=date(2026, 5, 16),
        cumulative_loss_usd=Decimal("-105.0000"),
    )
    sql = captured[0][0]
    assert "INSERT INTO bot_kill_switch_state" in sql
    assert "ON CONFLICT (bot_id) DO UPDATE" in sql
    # WG#2: column-direct only — no cast literal, no SQL time function.
    assert "::" not in sql
    low = sql.lower()
    assert "now()" not in low
    assert "current_timestamp" not in low
    assert "current_date" not in low
    # $N binds positional: $1 bot_id, $2 reason, $3 tripped_at, $4 anchor, $5 loss.
    assert captured[0][1:] == (
        "alpha",
        "daily_loss_limit",
        _NOW,
        date(2026, 5, 16),
        Decimal("-105.0000"),
    )


async def test_clear_sql_is_on_conflict_resets_columns_no_now() -> None:
    conn = MagicMock()
    captured = await _capture_execute(conn)
    await clear_kill_switch(conn, bot_id="alpha", updated_at=_NOW)
    sql = captured[0][0]
    assert "INSERT INTO bot_kill_switch_state" in sql
    assert "ON CONFLICT (bot_id) DO UPDATE" in sql
    assert "tripped = false" in sql
    assert "::" not in sql
    low = sql.lower()
    assert "now()" not in low
    assert "current_timestamp" not in low
    assert captured[0][1:] == ("alpha", _NOW)
