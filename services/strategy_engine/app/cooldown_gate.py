"""Pre-scoring cooldown gate (T-526) per BRIEF §9.4 + ADR-0011.

Derives cooldown state from the bot's closed-trades table on every signal
arrival; no persistent gate state, no message-bus subscriptions, no restart
reconciliation. Mirror BRIEF §9.4 pre-scoring skip pattern: when the gate
returns ``active=True``, the consumer logs ``signal_blocked_cooldown`` and
returns BEFORE step 3c signal_id resolve — exactly like ``signal_expired``
(step 3a) / ``signal_outside_universe`` (step 3b).

## Knob semantics

Three independent ``RiskSection`` knobs (:class:`packages.scoring.RiskSection`):

1. ``cooldown_after_loss_minutes`` — single-loss cooldown. ``last_loss_at +
   timedelta(minutes=N)``. Every new loss extends naturally (new ``last_loss_at``).
2. ``cooldown_after_streak_n_losses`` — streak count threshold.
3. ``cooldown_after_streak_n_losses_minutes`` — streak cooldown duration.

**Disabled-knob convention**: any one of ``streak_n`` / ``streak_minutes`` /
``loss_minutes`` = ``0`` disables that knob; both single-loss + streak knobs
= ``0`` short-circuits the gate BEFORE the DB SELECT (no per-signal DB hit
when feature unused). Per OQ-2=A (operator session 2026-05-15): loss = strict
``realized_pnl < 0``; zero or positive resets streak counter.

## Combined cooldown

When both knobs are active and binding, effective ``cooldown_until = max(
loss_until, streak_until)`` per OQ-4=A. Reject reason names the binding
knob (``cooldown_after_loss`` / ``cooldown_after_streak`` /
``cooldown_after_loss_and_streak``).

## Live vs paper dispatch

``exchange_mode`` (taken from :attr:`BotConfig.exchange.mode` at handler
factory closure-capture) selects the source table — ``trades`` for live /
testnet, ``paper_trades`` for paper. Each bot is one mode by config.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from packages.db.queries.trades import select_recent_closed_trades

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    import asyncpg

    from packages.core import BotId
    from packages.scoring import RiskSection

__all__ = ["CooldownDecision", "check_cooldown"]


@dataclass(frozen=True, slots=True)
class CooldownDecision:
    """Pre-scoring gate verdict for a single signal.

    Fields:
        active: True if signal must be skipped per cooldown rules.
        reason: ``"cooldown_after_loss"`` / ``"cooldown_after_streak"`` /
            ``"cooldown_after_loss_and_streak"`` when active; ``None`` when
            inactive.
        cooldown_until: tz-aware UTC datetime when the binding cooldown
            expires; ``None`` when inactive.
        streak_count: count of consecutive losses ending at the most recent
            closed trade (capped at the DB ``LIMIT`` row count). ``0`` when
            inactive or when there are no closed trades.
        last_loss_at: ``closed_at`` of the most recent loss in the lookback
            window; ``None`` when no losses exist within the window.
    """

    active: bool
    reason: str | None
    cooldown_until: datetime | None
    streak_count: int
    last_loss_at: datetime | None


_INACTIVE: CooldownDecision = CooldownDecision(
    active=False,
    reason=None,
    cooldown_until=None,
    streak_count=0,
    last_loss_at=None,
)


def _compute_limit(risk_config: RiskSection) -> int:
    """Smallest LIMIT covering both knobs; ``0`` short-circuits.

    Loss knob needs LIMIT 1 (only ``last_loss_at`` matters). Streak knob
    needs LIMIT ``streak_n_losses`` (walk top-N rows). Combined: pick max.
    Returns ``0`` when both knobs disabled — caller short-circuits before
    SELECT.
    """
    loss_enabled = risk_config.cooldown_after_loss_minutes > 0
    streak_enabled = (
        risk_config.cooldown_after_streak_n_losses > 0
        and risk_config.cooldown_after_streak_n_losses_minutes > 0
    )
    if not loss_enabled and not streak_enabled:
        return 0
    return max(
        1 if loss_enabled else 0,
        risk_config.cooldown_after_streak_n_losses if streak_enabled else 0,
    )


def _walk_streak(
    rows: list[tuple[Decimal, datetime]],
) -> tuple[int, datetime | None]:
    """Walk top-N rows newest-first; stop at first non-loss.

    Returns ``(streak_count, last_loss_at)``. ``last_loss_at`` is the
    ``closed_at`` of the newest row IF it is a loss; ``None`` otherwise
    (i.e., most-recent trade was a win → no fresh loss, neither cooldown
    binds).
    """
    streak_count = 0
    last_loss_at: datetime | None = None
    for pnl, closed_at in rows:
        if pnl < 0:
            streak_count += 1
            if last_loss_at is None:
                last_loss_at = closed_at
        else:
            break
    return streak_count, last_loss_at


def _build_decision(
    *,
    now: datetime,
    risk_config: RiskSection,
    streak_count: int,
    last_loss_at: datetime | None,
) -> CooldownDecision:
    """Combine knob deadlines into a single :class:`CooldownDecision`.

    Per OQ-4=A: ``effective_cooldown_until = max(loss_until, streak_until)``;
    reason names the binding knob(s).
    """
    if last_loss_at is None:
        return _INACTIVE

    loss_minutes = risk_config.cooldown_after_loss_minutes
    streak_n = risk_config.cooldown_after_streak_n_losses
    streak_minutes = risk_config.cooldown_after_streak_n_losses_minutes

    loss_until: datetime | None = None
    if loss_minutes > 0:
        candidate = last_loss_at + timedelta(minutes=loss_minutes)
        if candidate > now:
            loss_until = candidate

    streak_until: datetime | None = None
    if streak_n > 0 and streak_minutes > 0 and streak_count >= streak_n:
        candidate = last_loss_at + timedelta(minutes=streak_minutes)
        if candidate > now:
            streak_until = candidate

    if loss_until is not None and streak_until is not None:
        cooldown_until = max(loss_until, streak_until)
        reason: Literal[
            "cooldown_after_loss",
            "cooldown_after_streak",
            "cooldown_after_loss_and_streak",
        ] = "cooldown_after_loss_and_streak"
    elif loss_until is not None:
        cooldown_until = loss_until
        reason = "cooldown_after_loss"
    elif streak_until is not None:
        cooldown_until = streak_until
        reason = "cooldown_after_streak"
    else:
        return CooldownDecision(
            active=False,
            reason=None,
            cooldown_until=None,
            streak_count=streak_count,
            last_loss_at=last_loss_at,
        )

    return CooldownDecision(
        active=True,
        reason=reason,
        cooldown_until=cooldown_until,
        streak_count=streak_count,
        last_loss_at=last_loss_at,
    )


async def check_cooldown(
    *,
    pool: asyncpg.Pool,
    bot_id: BotId,
    exchange_mode: Literal["live", "testnet", "paper"],
    now: datetime,
    risk_config: RiskSection,
) -> CooldownDecision:
    """Per-signal cooldown verdict; derived state from closed trades.

    Returns :data:`_INACTIVE` (active=False) immediately when both single-loss
    and streak knobs are disabled (no DB hit). Otherwise reads top-N closed
    trades for ``bot_id`` from the table selected by ``exchange_mode``
    (``trades`` / ``paper_trades``), walks the newest-first streak, and
    combines per-knob deadlines.

    ``now`` is the consumer's single-snapshot UTC datetime (WG#1 reuse) —
    same value compared against loss/streak deadlines AND against future
    ``received_at_lower_bound`` in step 3c.
    """
    limit = _compute_limit(risk_config)
    if limit == 0:
        return _INACTIVE

    table_name: Literal["trades", "paper_trades"] = (
        "trades" if exchange_mode in ("live", "testnet") else "paper_trades"
    )
    async with pool.acquire() as conn:
        rows = await select_recent_closed_trades(
            conn,
            bot_id=str(bot_id),
            table_name=table_name,
            limit=limit,
        )

    streak_count, last_loss_at = _walk_streak([(r.realized_pnl, r.closed_at) for r in rows])
    return _build_decision(
        now=now,
        risk_config=risk_config,
        streak_count=streak_count,
        last_loss_at=last_loss_at,
    )
