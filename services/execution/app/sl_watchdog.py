"""ADR-0011 SL/TP-verification cluster + H-028 — periodic SL watchdog (T-534b2).

APScheduler-driven tick per ADR-0007 D1-D7 (3rd execution-service scheduled
job — sibling to the T-220b P&L audit + T-531 equity-snapshot jobs; shares
the lifespan-owned ``AsyncIOScheduler``). Mid-session naked-position
protection: a position open on the exchange whose stop-loss has vanished
(operator removed it, a ``set_trading_stop`` silently failed, or Bybit
dropped it) is unbounded-loss exposure.

Per tick (OQ-1=A — per-bot loop mirroring ``restart.py:66-93``; the wiring
mirrors T-531 but the data model forces per-bot: the counter is keyed
``(bot_id, symbol)``, :func:`emergency_close_tracked_position` takes a
per-bot ``ps_row``, paper-skip is per-bot):

1. Live/testnet bots only — paper skipped (``paper_bot_ids``; synthetic
   paper SL has no exchange-drop mode — H-031 precedent / OQ-4=A).
2. One ``select_position_states_for_bots`` round-trip (conn released
   BEFORE per-bot exchange I/O).
3. Per bot, ``adapter.get_positions()``; match by symbol against the DB
   ``position_state`` rows (``restart.py`` shape).
4. A matched pair (exchange ``size>0`` AND a DB ``ps_row``) whose
   ``Position.sl_price`` is absent (``None`` or ``<= 0`` — T-534a decode
   already collapses Bybit blank/``"0"``/non-positive → ``None``; the
   ``<= 0`` arm is a defensive backstop) advances an in-memory
   ``(bot_id, symbol)`` consecutive counter. On the Nth consecutive
   confirmed-missing tick (``missing_threshold_ticks``, default 3, §N9)
   it calls :func:`emergency_close_tracked_position` (T-534b1) and clears
   the entry.

H-028 false-positive guard (the hazard's raison d'être) — the counter
advances ONLY on a *successful* ``get_positions`` returning a matched +
SL-missing observation:

- transient ``(NetworkTimeout | RateLimitError | AuthError)`` →
  ``sl_watchdog.get_positions_transient`` warn + skip the bot this tick;
- any other exception → ``sl_watchdog.get_positions_failed`` error + skip
  the bot this tick (never raise into the scheduled job — other bots
  still verified, next tick still fires).

Counter state machine (OQ-2=A ∧ OQ-3=A composed — baked, not a new
decision): a ``get_positions`` failure is *no observation* → the bot is
NOT added to ``bots_observed``; its counters are neither incremented nor
reset nor pruned (a streak survives a transient blip). Any *observed*
ineligibility resets+prunes: SL restored → inline ``pop``; position
flat/absent or orphan-exchange or post-emergency-close → dropped by the
end-of-tick prune, which targets ONLY keys whose bot was observed this
tick and was not re-confirmed eligible (``k[0] in bots_observed and k
not in seen_eligible``). Orphan-exchange (no DB ``ps_row``) and orphan-DB
(no exchange position) are defer-logged only — reconciliation is T-221's
domain, never the watchdog's (OQ-B=A). The counter is process-memory; a
restart resets it (a missing SL is re-observable within N ticks — durable
risk-latch persistence is H-027's domain, not this).

Gate-4: zero arithmetic — only Decimal/int comparisons + an int counter
increment; no ``float()``, no silent Decimal→float. The reduce-only
flatten qty is ``ps_row.remaining_qty`` pass-through inside the T-534b1
helper (already math-validator VERIFIED).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from packages.db.queries.execution import select_position_states_for_bots
from packages.exchange.errors import AuthError, NetworkTimeout, RateLimitError

from .placement_persist import emergency_close_tracked_position

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import BusProtocol
    from packages.core import BotId
    from packages.db.queries.execution import PositionStateRow
    from packages.exchange.protocols import ExchangeClient


__all__ = ["run_sl_watchdog_tick"]


async def run_sl_watchdog_tick(
    *,
    pool: asyncpg.Pool,
    adapters: dict[BotId, ExchangeClient],
    paper_bot_ids: frozenset[BotId],
    bus: BusProtocol,
    sl_miss_counters: dict[tuple[str, str], int],
    missing_threshold_ticks: int,
    bound_logger: BoundLogger,
    now_fn: Callable[[], datetime],
) -> None:
    """One SL-watchdog tick — verify every tracked open position still has an
    exchange-side stop-loss; emergency-close after N consecutive misses.
    """
    tick_at = now_fn()
    bound_logger.info("sl_watchdog.tick_start", tick_at=tick_at.isoformat())

    # OQ-4=A — live/testnet only; paper bots skipped (H-031 precedent).
    # `sorted(adapters)` is deterministic (BotId is str-backed); iterating
    # the typed keys avoids `# type: ignore[index]` (contrast restart.py:75).
    live_bot_ids = [b for b in sorted(adapters) if b not in paper_bot_ids]
    if not live_bot_ids:
        bound_logger.info("sl_watchdog.no_live_bots_no_op")
        return

    async with pool.acquire() as conn:
        ps_rows = await select_position_states_for_bots(
            conn,
            [str(b) for b in live_bot_ids],
        )
    ps_by_bot: dict[str, dict[str, PositionStateRow]] = {}
    for row in ps_rows:
        ps_by_bot.setdefault(row.bot_id, {})[row.symbol] = row

    bots_observed: set[str] = set()
    seen_eligible: set[tuple[str, str]] = set()

    for bot_id in live_bot_ids:
        adapter = adapters[bot_id]
        bot_key = str(bot_id)
        try:
            positions = await adapter.get_positions()
        except (NetworkTimeout, RateLimitError, AuthError) as exc:
            # H-028 — transient = NO observation; streak preserved
            # (NOT added to bots_observed → counters untouched by prune).
            bound_logger.warning(
                "sl_watchdog.get_positions_transient",
                bot_id=bot_key,
                error=str(exc),
            )
            continue
        except Exception as exc:
            # H-028 — never raise into the job, never increment on
            # uncertainty; other bots still verified, next tick still fires.
            bound_logger.error(
                "sl_watchdog.get_positions_failed",
                bot_id=bot_key,
                error=str(exc),
            )
            continue
        bots_observed.add(bot_key)

        ex_by_symbol = {p.symbol: p for p in positions if p.size > 0}
        db_rows = ps_by_bot.get(bot_key, {})

        for symbol, pos in ex_by_symbol.items():
            ps_row = db_rows.get(symbol)
            if ps_row is None:
                # Orphan-exchange: position on Bybit, no DB ps_row. T-221
                # reconcile territory — the watchdog defer-logs, never
                # reconciles (OQ-B=A). Not eligible → not in seen_eligible
                # → any stale counter for it is pruned below.
                bound_logger.info(
                    "sl_watchdog.untracked_exchange_position_skipped",
                    bot_id=bot_key,
                    symbol=symbol,
                )
                continue

            sl_missing = pos.sl_price is None or pos.sl_price <= Decimal("0")
            if not sl_missing:
                # OQ-2=A — observed SL-present resets the streak (inline
                # pop; deliberately NOT added to seen_eligible so the
                # end-of-tick prune is an idempotent backstop).
                sl_miss_counters.pop((bot_key, symbol), None)
                continue

            key = (bot_key, symbol)
            seen_eligible.add(key)
            count = sl_miss_counters.get(key, 0) + 1
            sl_miss_counters[key] = count
            bound_logger.warning(
                "sl_watchdog.sl_missing_detected",
                bot_id=bot_key,
                symbol=symbol,
                consecutive=count,
                threshold=missing_threshold_ticks,
            )
            if count >= missing_threshold_ticks:
                bound_logger.warning(
                    "sl_watchdog.emergency_close_triggered",
                    bot_id=bot_key,
                    symbol=symbol,
                    consecutive=count,
                )
                # T-534b1 helper is graceful-on-failure (every path
                # log+return, never raises) → the tick continues
                # regardless; post-fire clear per OQ-2=A.
                await emergency_close_tracked_position(
                    adapter=adapter,
                    bus=bus,
                    pool=pool,
                    bound_logger=bound_logger,
                    bot_id=bot_id,
                    ps_row=ps_row,
                    now_fn=now_fn,
                )
                sl_miss_counters.pop(key, None)

    # OQ-2=A ∧ OQ-3=A — prune ONLY observed-ineligible keys (SL-restored
    # already popped inline; absent/flat/orphan-ex handled here). Keys for
    # bots NOT observed this tick (errored) are preserved untouched.
    stale = [k for k in sl_miss_counters if k[0] in bots_observed and k not in seen_eligible]
    for k in stale:
        del sl_miss_counters[k]
