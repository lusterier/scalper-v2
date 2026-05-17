"""Pre-scoring opposite-side guard (T-542, H-005) per BRIEF §20 + ADR-0016.

Resolves §20 **H-005 — Opposite-side signals**: a live position is open
LONG BTCUSDT and a SHORT signal arrives (v1 blocked it; v2 deferred until
now). Implemented as a per-bot pre-scoring **consumer silent-skip gate**
(ADR-0016 — NOT the BRIEF-design-of-record `opposite_side_open` scoring
condition; the operator chose the gate at the T-542 plan-stage because all
seven shipped F5 pre-scoring guards are consumer gates and the scoring
condition would need nonexistent position-state context-plumbing).

Mirror the T-526 cooldown gate exactly: derive state from the open-positions
table on every signal arrival; no persistent gate state, no bus subscription,
no restart reconciliation. When the gate returns ``active=True`` the consumer
logs ``signal_blocked_opposite_side``, increments the Prom counter, and
returns BEFORE step 3c — same silent-skip class as ``signal_blocked_cooldown``
(post-3b gate class HAS a counter, unlike the 3a/3b/source-filter class).

## Knob semantics

``RiskSection.block_opposite_side`` (:class:`packages.scoring.RiskSection`) —
``bool``, **default ``True`` (blocked)** per BRIEF §20 "per-bot enable/disable,
default blocked". ``False`` short-circuits the gate BEFORE the DB SELECT (no
per-signal DB hit when the guard is opted out) — mirror the cooldown
disabled-knob short-circuit. A directional block has no magnitude, so this is
a bool, not the ``0``-disabled int convention of the other risk knobs.

## Opposite predicate

``signal_side`` is the consumer's ``_ACTION_TO_SIDE[signal.action]``
(``LONG→buy`` / ``SHORT→sell``; ``CLOSE`` never reaches the gate — the
consumer's CLOSE-action block returns earlier). Block iff an open position
row exists for ``(bot_id, symbol)`` AND its ``side`` differs from
``signal_side`` (genuine opposite). Same side = pyramid/add (allow); no open
row = allow (flat).

## Live vs paper dispatch

``exchange_mode`` (from :attr:`BotConfig.exchange.mode` at handler factory
closure-capture) selects the source table — ``position_state`` for live /
testnet, ``paper_position_state`` for paper. Each bot is one mode by config.
Mirror the cooldown ``trades`` / ``paper_trades`` dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from packages.db.queries.execution import select_open_position_side

if TYPE_CHECKING:
    import asyncpg

    from packages.core import BotId
    from packages.scoring import RiskSection

__all__ = ["OppositeSideDecision", "check_opposite_side"]


@dataclass(frozen=True, slots=True)
class OppositeSideDecision:
    """Pre-scoring gate verdict for a single signal.

    Fields:
        active: True if the signal must be skipped (an opposite-side position
            is open for this ``(bot_id, symbol)``).
        reason: ``"opposite_side_open"`` when active; ``None`` when inactive.
        open_side: the open position's side (``"buy"`` / ``"sell"``) when a
            position exists; ``None`` when flat.
        signal_side: the incoming signal's mapped side (``"buy"`` / ``"sell"``)
            — echoed back for the consumer's structured log.
    """

    active: bool
    reason: str | None
    open_side: str | None
    signal_side: str | None


async def check_opposite_side(
    *,
    pool: asyncpg.Pool,
    bot_id: BotId,
    exchange_mode: Literal["live", "testnet", "paper"],
    symbol: str,
    signal_side: Literal["buy", "sell"],
    risk_config: RiskSection,
) -> OppositeSideDecision:
    """Per-signal opposite-side verdict; derived state from the open-positions table.

    Returns ``active=False`` immediately when ``block_opposite_side`` is
    ``False`` (no DB hit — disabled short-circuit, mirror cooldown). Otherwise
    reads the open position ``side`` for ``(bot_id, symbol)`` from the table
    selected by ``exchange_mode`` (``position_state`` / ``paper_position_state``)
    and blocks iff a row exists with a side opposite ``signal_side``.
    """
    if not risk_config.block_opposite_side:
        return OppositeSideDecision(
            active=False,
            reason=None,
            open_side=None,
            signal_side=signal_side,
        )

    table_name: Literal["position_state", "paper_position_state"] = (
        "position_state" if exchange_mode in ("live", "testnet") else "paper_position_state"
    )
    async with pool.acquire() as conn:
        open_side = await select_open_position_side(
            conn,
            bot_id=str(bot_id),
            symbol=symbol,
            table_name=table_name,
        )

    if open_side is not None and open_side != signal_side:
        return OppositeSideDecision(
            active=True,
            reason="opposite_side_open",
            open_side=open_side,
            signal_side=signal_side,
        )
    return OppositeSideDecision(
        active=False,
        reason=None,
        open_side=open_side,
        signal_side=signal_side,
    )
