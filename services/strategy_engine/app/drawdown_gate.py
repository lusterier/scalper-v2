"""Pre-scoring max-drawdown hard-stop gate (T-525b) per BRIEF §9.4 + ADR-0011 + H-027.

The 4th sibling pre-scoring gate (after T-526 cooldown + T-524 caps + T-525a2
loss-limit). Called from :mod:`.consumer` between BRIEF §9.4 step 3b (symbol
filter) and step 3c (signal_id resolve); runs AFTER the T-525a2 loss-limit
gate (chain cooldown → caps → loss-limit → drawdown). When ``blocked=True``
the consumer logs ``signal_blocked_drawdown`` + increments a Prom counter +
returns BEFORE scoring_evaluations / orders.requests / signals.rejected —
identical silent-skip pattern to the sibling gates.

This gate is the **max-drawdown writer** half of the H-027 kill-switch
(daily-loss writer = T-525a2; persistence substrate + reconcile + the
``max_drawdown``→never-stale forward-compat = T-525a1, all shipped). It tracks
the bot's LIFETIME cumulative realized P&L and trips a **hard-stop** latch
when the give-back from the all-time profit peak reaches
``max_drawdown_pct``. Unlike the daily-loss latch, a ``max_drawdown`` latch is
NEVER auto-cleared at UTC midnight (``is_stale_daily_latch`` returns ``False``
for ``trip_reason='max_drawdown'`` — shipped T-525a1).

## Semantics (operator OQ 2026-05-15)

* **Disabled**: ``max_drawdown_pct <= 0`` → not blocked, BEFORE any DB
  (short-circuit; mirror siblings).
* **Sticky latch (reason-agnostic)**: a non-stale tripped latch → blocked
  regardless of ``trip_reason``; the peak/current recompute is SKIPPED (a
  kill-switch must not re-evaluate). ``reason`` = the latch's ``trip_reason``.
* **peak > 0 guard (OQ-A)**: ``drawdown_pct = (peak - current) / peak`` is
  computed ONLY when ``peak > 0``. ``peak ≤ 0`` (never net-profitable) →
  NOT tripped — you cannot give back profit you never earned; the pure-loss
  case is the T-525a2 daily-loss gate's domain. The guard also makes the
  division total (no ``/0``, no ``/negative``).
* **Lifetime window (OQ-B)**: peak/current are over ALL the bot's closed
  trades (no time window) — a max-drawdown hard-stop protects against
  sustained equity erosion, not intraday.
* **Trip**: ``drawdown_pct >= max_drawdown_pct`` (boundary trips — the limit
  is the max tolerable give-back; reaching it exactly trips, mirror T-524/
  T-526 ``>=`` / T-525a2 ``<=``) → latch via
  :func:`packages.db.queries.kill_switch.upsert_kill_switch_trip`
  (``reason='max_drawdown'``). ``daily_anchor_date`` is written for schema
  consistency but is **semantically inert** for ``max_drawdown``
  (``is_stale_daily_latch`` ignores it for this reason — hard-stop by
  construction; shipped T-525a1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from packages.db.queries.kill_switch import (
    clear_kill_switch,
    is_stale_daily_latch,
    select_kill_switch_state,
    upsert_kill_switch_trip,
)
from packages.db.queries.trades import select_pnl_peak_and_current

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    import asyncpg

    from packages.core import BotId
    from packages.scoring import RiskSection

__all__ = ["DrawdownDecision", "check_max_drawdown"]


@dataclass(frozen=True, slots=True)
class DrawdownDecision:
    """Pre-scoring max-drawdown-gate verdict for a single signal.

    Fields:
        blocked: True if the signal must be skipped (latch tripped/active).
        reason: ``'max_drawdown'`` on a fresh trip; ``state.trip_reason`` for a
            pre-existing latch (reason-agnostic — e.g. ``'daily_loss_limit'``
            cross-block); ``None`` when not blocked.
        drawdown_pct: the computed give-back ratio that tripped (fresh trip);
            ``None`` for a pre-existing-latch block (recompute skipped) or when
            not blocked.
        limit_pct: the configured ``max_drawdown_pct``; ``None`` when not
            blocked.
    """

    blocked: bool
    reason: str | None
    drawdown_pct: Decimal | None
    limit_pct: Decimal | None


_NOT_BLOCKED: DrawdownDecision = DrawdownDecision(
    blocked=False,
    reason=None,
    drawdown_pct=None,
    limit_pct=None,
)


async def check_max_drawdown(
    *,
    pool: asyncpg.Pool,
    bot_id: BotId,
    exchange_mode: Literal["live", "testnet", "paper", "demo"],
    now: datetime,
    risk_config: RiskSection,
) -> DrawdownDecision:
    """Per-signal max-drawdown hard-stop verdict (lazy trip; persistent latch).

    9-step linear flow (see module docstring + ``docs/plans/T-525b.md`` Hand
    verification). Returns :data:`_NOT_BLOCKED` immediately when disabled (no
    DB hit). The latch read/write goes through the T-525a1 ``@idempotent``
    helpers; a concurrent double-trip is convergent.
    """
    limit = risk_config.max_drawdown_pct
    if limit <= 0:
        return _NOT_BLOCKED

    table_name: Literal["trades", "paper_trades"] = (
        "trades" if exchange_mode in ("live", "testnet", "demo") else "paper_trades"
    )

    async with pool.acquire() as conn:
        state = await select_kill_switch_state(conn, bot_id=str(bot_id))
        if state is not None and state.tripped:
            if is_stale_daily_latch(state, now):
                # Only a stale *daily* latch reaches here (max_drawdown is
                # never stale — hard-stop). The T-525a2 loss-limit gate, which
                # runs BEFORE this gate, already stale-cleared it this signal;
                # the symmetric clear keeps the rule single-sourced + safe.
                await clear_kill_switch(conn, bot_id=str(bot_id), updated_at=now)
            else:
                # Non-stale tripped latch → blocked, reason-agnostic. Recompute
                # SKIPPED: a kill-switch must not re-evaluate (sticky).
                return DrawdownDecision(
                    blocked=True,
                    reason=state.trip_reason,
                    drawdown_pct=None,
                    limit_pct=limit,
                )

        peak, current = await select_pnl_peak_and_current(
            conn,
            table_name=table_name,
            bot_id=str(bot_id),
        )
        # OQ-A peak>0 guard — STRICTLY precedes the division (return-early;
        # division unreachable when peak <= 0). Cannot give back profit never
        # earned; pure-loss is the T-525a2 daily-loss gate's domain.
        if peak <= 0:
            return _NOT_BLOCKED

        drawdown_pct = (peak - current) / peak
        if drawdown_pct >= limit:
            await upsert_kill_switch_trip(
                conn,
                bot_id=str(bot_id),
                trip_reason="max_drawdown",
                tripped_at=now,
                daily_anchor_date=now.date(),
                cumulative_loss_usd=current,
            )
            return DrawdownDecision(
                blocked=True,
                reason="max_drawdown",
                drawdown_pct=drawdown_pct,
                limit_pct=limit,
            )

    return _NOT_BLOCKED
