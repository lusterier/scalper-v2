"""Pre-scoring daily-loss kill-switch gate (T-525a2) per BRIEF §9.4 + ADR-0011 + H-027.

Third sibling of the T-526 cooldown gate + T-524 caps gate. Pre-scoring gate
called from :mod:`.consumer` between BRIEF §9.4 step 3b (symbol filter) and
step 3c (signal_id resolve); runs AFTER the T-524 caps gate (chain
cooldown → caps → loss-limit). When ``blocked=True`` the consumer logs
``signal_blocked_loss_limit`` + increments a Prom counter + returns BEFORE
scoring_evaluations / orders.requests / signals.rejected — identical
silent-skip pattern to T-526/T-524 (carried T-526 OQ-3=A / T-524 OQ-2=A).

This gate is the *writer + enforcement* half of the H-027 daily-loss
kill-switch; T-525a1 shipped the persistent latch substrate
(:mod:`packages.db.queries.kill_switch`) + the startup reconcile. The running
P&L sum is still stateless derive-from-trades
(:func:`packages.db.queries.trades.sum_realized_pnl_since`); only the *latch*
(tripped flag + UTC-day anchor + audit) is persistent (H-027).

## Semantics

* **Disabled**: ``daily_loss_limit_usd <= 0`` → not blocked, BEFORE any DB
  (short-circuit; mirror T-524/T-526; a disabled feature ignores even a
  lingering latch).
* **Sticky latch (reason-agnostic, operator OQ 2026-05-15)**: a non-stale
  tripped latch → blocked regardless of ``trip_reason`` (a kill-switch is a
  kill-switch — block whether it tripped on ``daily_loss_limit`` or future
  T-525b ``max_drawdown``). The today-P&L recompute is SKIPPED (a recovering
  intra-day win must NOT un-trip the kill-switch). ``reason`` = the latch's
  ``trip_reason`` (the true binding reason).
* **Day rollover (long-running process, no restart)**: a stale prior-UTC-day
  daily latch is cleared in the gate (mirrors
  :func:`reconcile_kill_switch_on_startup`, since reconcile only runs at
  startup) then control falls through to recompute the new day.
* **Trip**: today's cumulative realized P&L ``total <= -daily_loss_limit_usd``
  (Decimal; boundary trips — the limit is the max tolerable cumulative loss,
  reaching it exactly trips, mirror T-524 ``>=`` / T-526 ``>=``) → latch via
  :func:`packages.db.queries.kill_switch.upsert_kill_switch_trip`
  (``reason='daily_loss_limit'``) → blocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, Literal

from packages.db.queries.kill_switch import (
    clear_kill_switch,
    is_stale_daily_latch,
    select_kill_switch_state,
    upsert_kill_switch_trip,
)
from packages.db.queries.trades import sum_realized_pnl_since

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    import asyncpg

    from packages.core import BotId
    from packages.scoring import RiskSection

__all__ = ["LossLimitDecision", "check_daily_loss_limit"]


@dataclass(frozen=True, slots=True)
class LossLimitDecision:
    """Pre-scoring daily-loss-gate verdict for a single signal.

    Fields:
        blocked: True if the signal must be skipped (latch tripped/active).
        reason: the binding latch reason — ``'daily_loss_limit'`` on a fresh
            trip; ``state.trip_reason`` for a pre-existing latch (e.g.
            ``'max_drawdown'`` once T-525b ships); ``None`` when not blocked.
        cumulative_loss_usd: the realized-P&L sum that tripped (fresh trip) or
            the latch's stored ``cumulative_loss_usd`` (pre-existing latch);
            ``None`` when not blocked.
        limit_usd: the configured ``daily_loss_limit_usd``; ``None`` when not
            blocked.
    """

    blocked: bool
    reason: str | None
    cumulative_loss_usd: Decimal | None
    limit_usd: Decimal | None


_NOT_BLOCKED: LossLimitDecision = LossLimitDecision(
    blocked=False,
    reason=None,
    cumulative_loss_usd=None,
    limit_usd=None,
)


async def check_daily_loss_limit(
    *,
    pool: asyncpg.Pool,
    bot_id: BotId,
    exchange_mode: Literal["live", "testnet", "paper", "demo"],
    now: datetime,
    risk_config: RiskSection,
) -> LossLimitDecision:
    """Per-signal daily-loss kill-switch verdict (lazy trip; persistent latch).

    9-step linear flow (see module docstring + ``docs/plans/T-525a2.md``
    Hand verification). Returns :data:`_NOT_BLOCKED` immediately when the
    feature is disabled (no DB hit). The latch read/write goes through the
    T-525a1 ``@idempotent`` helpers; a concurrent double-trip is convergent.
    """
    limit = risk_config.daily_loss_limit_usd
    if limit <= 0:
        return _NOT_BLOCKED

    table_name: Literal["trades", "paper_trades"] = (
        "trades" if exchange_mode in ("live", "testnet", "demo") else "paper_trades"
    )

    async with pool.acquire() as conn:
        state = await select_kill_switch_state(conn, bot_id=str(bot_id))
        if state is not None and state.tripped:
            if is_stale_daily_latch(state, now):
                # New UTC trading day for a long-running process (reconcile
                # only runs at startup) — clear the stale daily latch, then
                # fall through to recompute today's P&L.
                await clear_kill_switch(conn, bot_id=str(bot_id), updated_at=now)
            else:
                # Non-stale tripped latch → blocked, reason-agnostic
                # (operator OQ 2026-05-15). Recompute SKIPPED: a recovering
                # intra-day win must NOT un-trip the kill-switch (sticky).
                return LossLimitDecision(
                    blocked=True,
                    reason=state.trip_reason,
                    cumulative_loss_usd=state.cumulative_loss_usd,
                    limit_usd=limit,
                )

        since = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        total = await sum_realized_pnl_since(
            conn,
            table_name=table_name,
            bot_id=str(bot_id),
            since=since,
        )
        if total <= -limit:
            await upsert_kill_switch_trip(
                conn,
                bot_id=str(bot_id),
                trip_reason="daily_loss_limit",
                tripped_at=now,
                daily_anchor_date=now.date(),
                cumulative_loss_usd=total,
            )
            return LossLimitDecision(
                blocked=True,
                reason="daily_loss_limit",
                cumulative_loss_usd=total,
                limit_usd=limit,
            )

    return _NOT_BLOCKED
