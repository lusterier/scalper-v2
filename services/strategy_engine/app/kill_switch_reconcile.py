"""Startup reconcile of the persistent risk kill-switch latch (T-525a1; H-027).

Best-effort lifespan hook: re-evaluates ``bot_kill_switch_state`` at
strategy-engine startup so a restart does NOT silently reset an active
operator risk-stop (H-027 "persisted across restart AND re-evaluated on
startup"). A daily latch whose ``daily_anchor_date`` precedes the current
UTC date is cleared (a new trading day); a same-UTC-day latch is retained
(the stop survives the restart) and warn-logged for operator visibility;
a ``max_drawdown`` latch (T-525b) is never cleared here (hard-stop).

Mirrors the T-221 ``reconcile_on_startup`` *best-effort non-blocking
lifespan-hook convention* (log + continue, never raise into lifespan) —
NOT its exchange-vs-DB orphan-reconciliation algorithm (structurally
different: this reads one PG row by PK and optionally clears a stale
daily latch). A transient DB hiccup at boot must not crash startup;
the next per-signal gate read (T-525a2) re-evaluates anyway.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncpg

from packages.db.queries.kill_switch import (
    clear_kill_switch,
    is_stale_daily_latch,
    select_kill_switch_state,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from structlog.stdlib import BoundLogger

__all__ = ["reconcile_kill_switch_on_startup"]


async def reconcile_kill_switch_on_startup(
    *,
    pool: asyncpg.Pool,
    bot_id: str,
    now_fn: Callable[[], datetime],
    system_logger: BoundLogger,
) -> None:
    """Re-evaluate the persistent kill-switch latch at startup (H-027).

    Best-effort: any ``asyncpg.PostgresError`` / ``OSError`` / ``TimeoutError``
    is logged to system.log and swallowed — this MUST NOT raise into the
    lifespan (a boot-time DB hiccup must not crash strategy-engine; the
    per-signal gate re-evaluates regardless).
    """
    now = now_fn()
    try:
        async with pool.acquire() as conn:
            state = await select_kill_switch_state(conn, bot_id=bot_id)
            if state is None:
                system_logger.info("kill_switch.reconcile_no_state", bot_id=bot_id)
                return
            if is_stale_daily_latch(state, now):
                await clear_kill_switch(conn, bot_id=bot_id, updated_at=now)
                system_logger.info(
                    "kill_switch.reconcile_cleared_stale_daily_latch",
                    bot_id=bot_id,
                    prior_anchor=(
                        state.daily_anchor_date.isoformat()
                        if state.daily_anchor_date is not None
                        else None
                    ),
                    prior_reason=state.trip_reason,
                )
                return
            if state.tripped:
                # Same-UTC-day (or hard-stop) latch retained across restart —
                # operator-visible WARNING: this bot is stopped (H-027).
                system_logger.warning(
                    "kill_switch.reconcile_latch_retained",
                    bot_id=bot_id,
                    trip_reason=state.trip_reason,
                    daily_anchor=(
                        state.daily_anchor_date.isoformat()
                        if state.daily_anchor_date is not None
                        else None
                    ),
                    tripped_at=(
                        state.tripped_at.isoformat() if state.tripped_at is not None else None
                    ),
                )
                return
            system_logger.info("kill_switch.reconcile_not_tripped", bot_id=bot_id)
    except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
        system_logger.error(
            "kill_switch.reconcile_failed",
            bot_id=bot_id,
            error=str(exc),
        )
