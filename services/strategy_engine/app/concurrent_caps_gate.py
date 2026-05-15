"""Pre-scoring concurrent-trades caps gate (T-524) per BRIEF §9.4 + ADR-0011.

Sibling of the T-526 cooldown gate (:mod:`.cooldown_gate`). Both are pre-scoring
gates called from :mod:`.consumer` between BRIEF §9.4 step 3b (symbol filter)
and step 3c (signal_id resolve); the caps gate runs AFTER the cooldown gate
(T-524 OQ-5 default A). When the gate returns ``blocked=True`` the consumer
logs ``signal_blocked_caps`` + increments a Prom counter + returns BEFORE
scoring_evaluations / orders.requests / signals.rejected — identical silent-skip
pattern to T-526 (T-524 OQ-2=A).

State derived from ``trades`` / ``paper_trades`` on every signal arrival via
:func:`packages.db.queries.trades.count_open_trades` — no orders.events
subscribe, no in-memory counter, no restart reconcile (T-524 OQ-1=A;
consistent with T-526). Derive-from-trades is *required* for the global cap:
a per-bot strategy-engine process cannot observe cross-bot ``orders.events``,
so an in-memory counter could never see other bots' open positions.

## Knob semantics (RiskSection — shared with T-526 cooldown)

* ``max_open_trades_per_bot`` — cap on this bot's own open positions.
* ``max_open_trades_global`` — cap on ALL open positions in this bot's
  exchange-mode realm (paper bot → ``paper_trades`` across all bots; live /
  testnet bot → ``trades`` across all bots; T-524 OQ-3=A).

**Disabled-knob convention**: a knob = ``0`` disables that cap. Both = ``0``
short-circuits the gate BEFORE any DB query (no per-signal DB hit when the
feature is unused). If only one knob is ``0``, only its query is skipped
(the per-bot count query is issued only when ``max_open_trades_per_bot > 0``;
the global count query only when ``max_open_trades_global > 0``).

**Block predicate**: ``current_open_count >= cap`` (NOT ``>``). Rationale:
``max_open_trades_per_bot=3`` means "the bot may hold at most 3 concurrently";
accepting a new signal when 3 are already open would create a 4th → violate.
So block when ``count >= cap``. A cap lowered after positions opened
(``count > cap``) still blocks (the ``>=`` covers it).

**Precedence**: the per-bot cap is checked before the global cap. When both
would block, the per-bot reason is reported (deterministic precedence).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from packages.db.queries.trades import count_open_trades

if TYPE_CHECKING:
    import asyncpg

    from packages.core import BotId
    from packages.scoring import RiskSection

__all__ = ["CapsDecision", "check_concurrent_caps"]


@dataclass(frozen=True, slots=True)
class CapsDecision:
    """Pre-scoring caps-gate verdict for a single signal.

    Fields:
        blocked: True if the signal must be skipped per a tripped cap.
        reason: ``"max_open_trades_per_bot"`` / ``"max_open_trades_global"``
            when blocked; ``None`` when not blocked.
        current_count: the open-position count that tripped the binding cap
            (per-bot count if per-bot cap bound; global count if global cap
            bound); ``None`` when not blocked.
        cap_limit: the binding cap's configured value; ``None`` when not
            blocked.
    """

    blocked: bool
    reason: str | None
    current_count: int | None
    cap_limit: int | None


_NOT_BLOCKED: CapsDecision = CapsDecision(
    blocked=False,
    reason=None,
    current_count=None,
    cap_limit=None,
)


async def check_concurrent_caps(
    *,
    pool: asyncpg.Pool,
    bot_id: BotId,
    exchange_mode: Literal["live", "testnet", "paper"],
    risk_config: RiskSection,
) -> CapsDecision:
    """Per-signal concurrent-trades cap verdict; derived state.

    Returns :data:`_NOT_BLOCKED` immediately when both caps are disabled
    (``0``) — no DB hit. Otherwise counts open positions in the table
    selected by ``exchange_mode`` (``trades`` for live/testnet,
    ``paper_trades`` for paper) and applies the per-bot cap first, then the
    global cap (deterministic precedence). The per-bot query is issued only
    when ``max_open_trades_per_bot > 0``; the global query only when
    ``max_open_trades_global > 0`` (a disabled cap costs no DB round-trip).
    """
    per_bot_cap = risk_config.max_open_trades_per_bot
    global_cap = risk_config.max_open_trades_global
    if per_bot_cap <= 0 and global_cap <= 0:
        return _NOT_BLOCKED

    table_name: Literal["trades", "paper_trades"] = (
        "trades" if exchange_mode in ("live", "testnet") else "paper_trades"
    )

    async with pool.acquire() as conn:
        if per_bot_cap > 0:
            per_bot_count = await count_open_trades(
                conn,
                table_name=table_name,
                bot_id=str(bot_id),
            )
            if per_bot_count >= per_bot_cap:
                return CapsDecision(
                    blocked=True,
                    reason="max_open_trades_per_bot",
                    current_count=per_bot_count,
                    cap_limit=per_bot_cap,
                )

        if global_cap > 0:
            global_count = await count_open_trades(
                conn,
                table_name=table_name,
                bot_id=None,
            )
            if global_count >= global_cap:
                return CapsDecision(
                    blocked=True,
                    reason="max_open_trades_global",
                    current_count=global_count,
                    cap_limit=global_cap,
                )

    return _NOT_BLOCKED
