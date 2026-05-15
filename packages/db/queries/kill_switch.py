"""Risk kill-switch latch persistence (T-525a1; H-027).

Durable per-bot latch substrate for the daily-loss kill-switch (T-525a2
writer) and, forward-compat, the max-drawdown hard-stop (T-525b). The latch
MUST persist across a strategy-engine restart and be re-evaluated on startup
(H-027) — an in-memory-only latch would reset on restart and silently
re-enable a bot the operator's risk limit had stopped (capital-loss exposure).

T-525a1 ships the persistence + reconcile half. The *writer* (the gate that
trips the latch on a Decimal P&L threshold) is T-525a2.

L-021 SQL-parameter type-cast audit: every ``$N`` in this module is
**column-direct** — ``WHERE bot_id = $1`` (TEXT equality), and the
upsert/clear binds in ``INSERT ... VALUES (...)`` / ``SET col = $N``
assignment positions. L-021 targets *non*-column-direct contexts
(arithmetic, CASE branches, comparison-across-unioned-types, function args);
none exist here, so no ``::type`` cast is needed (asyncpg inference is
unambiguous in column-direct positions). There is no ``closed_at >= $N``
style timestamp comparison parameter in this module — that lives in
T-525a2's ``sum_realized_pnl_since`` where the explicit ``::timestamptz``
cast is applied. §N1: writers pass explicit UTC datetimes; no
``CURRENT_TIMESTAMP``/``NOW()`` literal appears in any SQL string here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime  # noqa: TC003 — runtime annotations on @dataclass slots
from decimal import Decimal  # noqa: TC003 — runtime annotation on @dataclass slot
from typing import TYPE_CHECKING

from packages.core import idempotent

if TYPE_CHECKING:
    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

__all__ = [
    "KillSwitchState",
    "clear_kill_switch",
    "is_stale_daily_latch",
    "select_kill_switch_state",
    "upsert_kill_switch_trip",
]

# trip_reason values whose latch is a *daily* window (UTC-day-auto-clearable).
# 'max_drawdown' (T-525b) is intentionally NOT here — a drawdown latch is a
# hard-stop and must NOT clear on a UTC-day rollover.
_DAILY_TRIP_REASON = "daily_loss_limit"


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    """One row of ``bot_kill_switch_state`` (migration 0018)."""

    bot_id: str
    tripped: bool
    trip_reason: str | None
    tripped_at: datetime | None
    daily_anchor_date: date | None
    cumulative_loss_usd: Decimal | None


def is_stale_daily_latch(state: KillSwitchState, now: datetime) -> bool:
    """Pure predicate: True iff a *daily* latch belongs to a prior UTC day.

    True ⟺ ``state.tripped`` AND ``state.trip_reason == 'daily_loss_limit'``
    AND ``state.daily_anchor_date`` is set AND
    ``state.daily_anchor_date < now(UTC).date()``.

    A ``'max_drawdown'`` latch (T-525b) returns False (hard-stop — not
    cleared by a day rollover). A not-tripped state returns False. ``now``
    is a parameter (no internal ``datetime.now()`` — pure, deterministic,
    UTC-comparable). The caller (reconcile T-525a1 / gate T-525a2) shares
    this single source of the stale-day rule.
    """
    if not state.tripped:
        return False
    if state.trip_reason != _DAILY_TRIP_REASON:
        return False
    if state.daily_anchor_date is None:
        return False
    # ``now`` is a tz-aware UTC datetime (caller passes now_fn() → UTC per §N1);
    # ``now.date()`` is therefore the UTC calendar date.
    return state.daily_anchor_date < now.date()


async def select_kill_switch_state(
    conn: _DbExecutor,
    *,
    bot_id: str,
) -> KillSwitchState | None:
    """Read the latch row for ``bot_id``; ``None`` if no row exists.

    Read-only (no idempotency marker — markers are for external writes only).
    """
    row = await conn.fetchrow(
        "SELECT bot_id, tripped, trip_reason, tripped_at, daily_anchor_date, "
        "cumulative_loss_usd FROM bot_kill_switch_state WHERE bot_id = $1",
        bot_id,
    )
    if row is None:
        return None
    return KillSwitchState(
        bot_id=str(row["bot_id"]),
        tripped=bool(row["tripped"]),
        trip_reason=row["trip_reason"],
        tripped_at=row["tripped_at"],
        daily_anchor_date=row["daily_anchor_date"],
        cumulative_loss_usd=row["cumulative_loss_usd"],
    )


@idempotent
async def upsert_kill_switch_trip(
    conn: _DbExecutor,
    *,
    bot_id: str,
    trip_reason: str,
    tripped_at: datetime,
    daily_anchor_date: date,
    cumulative_loss_usd: Decimal,
) -> None:
    """Latch the kill-switch for ``bot_id`` (INSERT ... ON CONFLICT DO UPDATE).

    @idempotent (§N3): re-applying the same trip is convergent — a duplicate
    NATS-redelivered signal that re-trips an already-tripped latch leaves the
    row in the same logical state (the audit fields refresh to the latest
    observed loss, which is the desired convergent behavior). All ``$N`` are
    column-direct (``VALUES``/``SET`` assignment) — L-021-safe, no cast.
    ``updated_at`` is set to ``tripped_at`` (the trip instant; explicit UTC
    per §N1 — no ``NOW()``).
    """
    await conn.execute(
        "INSERT INTO bot_kill_switch_state "
        "(bot_id, tripped, trip_reason, tripped_at, daily_anchor_date, "
        "cumulative_loss_usd, updated_at) "
        "VALUES ($1, true, $2, $3, $4, $5, $3) "
        "ON CONFLICT (bot_id) DO UPDATE SET "
        "tripped = true, "
        "trip_reason = EXCLUDED.trip_reason, "
        "tripped_at = EXCLUDED.tripped_at, "
        "daily_anchor_date = EXCLUDED.daily_anchor_date, "
        "cumulative_loss_usd = EXCLUDED.cumulative_loss_usd, "
        "updated_at = EXCLUDED.updated_at",
        bot_id,
        trip_reason,
        tripped_at,
        daily_anchor_date,
        cumulative_loss_usd,
    )


@idempotent
async def clear_kill_switch(
    conn: _DbExecutor,
    *,
    bot_id: str,
    updated_at: datetime,
) -> None:
    """Clear the latch for ``bot_id`` (INSERT ... ON CONFLICT DO UPDATE).

    @idempotent (§N3): clearing an already-clear latch is convergent.
    Upsert-shaped so a clear on a bot that never tripped is still a no-op
    (inserts a not-tripped row). ``updated_at`` is an explicit UTC datetime
    (§N1 — no ``NOW()``). All ``$N`` column-direct (L-021-safe).
    """
    await conn.execute(
        "INSERT INTO bot_kill_switch_state "
        "(bot_id, tripped, trip_reason, tripped_at, daily_anchor_date, "
        "cumulative_loss_usd, updated_at) "
        "VALUES ($1, false, NULL, NULL, NULL, NULL, $2) "
        "ON CONFLICT (bot_id) DO UPDATE SET "
        "tripped = false, "
        "trip_reason = NULL, "
        "tripped_at = NULL, "
        "daily_anchor_date = NULL, "
        "cumulative_loss_usd = NULL, "
        "updated_at = EXCLUDED.updated_at",
        bot_id,
        updated_at,
    )
