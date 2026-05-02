"""§9.5:1585-1592 PositionLifecycle state monitor (T-217a).

Per-trade ``asyncio.Task`` spawned post-T-216b2 emit boundary. Each tick:

1. Sleeps ``settings.position_poll_interval_s`` seconds.
2. Reads latest price from NATS KV bucket ``latest_price`` keyed on symbol.
3. Reads current ``position_state`` row (PK ``(bot_id, symbol)``); if None →
   exit (T-219 close DELETE'd the row — graceful self-cancel).
4. Computes MFE / MAE / best_price / running_pnl per side.
5. UPDATEs ``position_state`` via PK-keyed helper (H-018-symmetric).

T-217a does NOT do SL adjustments (BE trigger / trail update); T-217b owns those.

**Stale-price handling**: if KV read returns None / empty / undecodable bytes
for ``settings.position_poll_stale_ticks`` consecutive ticks, log WARN
``execution.lifecycle_price_stale_pause`` once and continue (does NOT exit).
Stale count resets to 0 on first successful read. T-221 reconciliation
owns recovery from prolonged staleness.

**Cancellation**: :class:`asyncio.CancelledError` propagates cleanly without
log noise (graceful shutdown). Lifespan reverse-shutdown cancels
position_lifecycle_tasks BEFORE dispatcher_tasks so monitor stops UPDATE'ing
position_state — column-disjoint UPDATEs are MVCC-safe regardless, but the
ordering keeps shutdown's audit log monotonic.

**Forward-pointer (T-217b)**: BE trigger + trail SL adjustment logic will
extend :func:`run_position_monitor_for_trade` body post-tick metrics update.
T-217b reads :func:`select_trade_fsm_params` per tick + invokes
:func:`ExchangeClient.set_trading_stop` on transitions.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from packages.db.queries.execution import (
    select_position_state,
    update_position_state_monitor_tick,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import NatsClient
    from packages.core import BotId


__all__ = ["run_position_monitor_for_trade"]


# TODO(T-F2+): producer side wires kv_put in OhlcPipeline._publish; until then
# position_state.{best_price,mfe_price,mae_price,running_pnl} stay at server
# defaults (NULL/0). Every monitor will hit stale-tick WARN within
# ``stale_ticks_threshold * poll_interval_s`` seconds of spawn until producer
# ships. Operator-actionable runtime gap, not a code defect.
_LATEST_PRICE_BUCKET = "latest_price"


async def run_position_monitor_for_trade(
    *,
    bot_id: BotId,
    symbol: str,
    trade_id: int,
    side: str,
    entry_price: Decimal,
    qty: Decimal,
    pool: asyncpg.Pool,
    bus: NatsClient,
    bound_logger: BoundLogger,
    poll_interval_s: float,
    stale_ticks_threshold: int,
    now_fn: Callable[[], datetime],
) -> None:
    """T-217a per-trade state-tracking monitor body.

    Lifecycle: spawn → loop ticks until ``position_state`` is DELETE'd or
    task is cancelled. See module docstring for stale-price + cancellation
    semantics.
    """
    side_factor = Decimal("1") if side == "buy" else Decimal("-1")
    stale_count = 0
    while True:
        await asyncio.sleep(poll_interval_s)

        latest = await bus.kv_get(_LATEST_PRICE_BUCKET, symbol)
        if latest is None:
            stale_count += 1
            if stale_count == stale_ticks_threshold:
                bound_logger.warning(
                    "execution.lifecycle_price_stale_pause",
                    bot_id=bot_id,
                    trade_id=trade_id,
                    symbol=symbol,
                    stale_ticks=stale_count,
                )
            continue

        price_bytes, _revision = latest
        try:
            current_price = Decimal(price_bytes.decode("utf-8"))
        except (InvalidOperation, ValueError, UnicodeDecodeError) as exc:
            bound_logger.warning(
                "execution.lifecycle_price_decode_error",
                bot_id=bot_id,
                trade_id=trade_id,
                symbol=symbol,
                error=str(exc),
            )
            stale_count += 1
            continue
        stale_count = 0

        async with pool.acquire() as conn:
            ps = await select_position_state(conn, bot_id=bot_id, symbol=symbol)
            if ps is None:
                bound_logger.info(
                    "execution.lifecycle_exit_position_closed",
                    bot_id=bot_id,
                    trade_id=trade_id,
                    symbol=symbol,
                )
                return

            running_pnl = (current_price - entry_price) * qty * side_factor
            new_best = _update_best_price(side, current_price, ps_best=ps.best_price)
            new_mfe, new_mae = _update_mfe_mae(
                side,
                current_price,
                ps_mfe=ps.mfe_price,
                ps_mae=ps.mae_price,
            )

            await update_position_state_monitor_tick(
                conn,
                bot_id=bot_id,
                symbol=symbol,
                best_price=new_best,
                mfe_price=new_mfe,
                mae_price=new_mae,
                running_pnl=running_pnl,
                updated_at=now_fn(),
            )


def _update_best_price(
    side: str,
    current_price: Decimal,
    *,
    ps_best: Decimal | None,
) -> Decimal:
    """Track most-favorable price observed since entry.

    Long → highest price seen. Short → lowest price seen. Initial seed = current.
    """
    if ps_best is None:
        return current_price
    if side == "buy":
        return current_price if current_price > ps_best else ps_best
    return current_price if current_price < ps_best else ps_best


def _update_mfe_mae(
    side: str,
    current_price: Decimal,
    *,
    ps_mfe: Decimal | None,
    ps_mae: Decimal | None,
) -> tuple[Decimal, Decimal]:
    """MFE = maximum favorable excursion; MAE = maximum adverse excursion.

    Long: MFE = highest price; MAE = lowest price.
    Short: MFE = lowest price; MAE = highest price.
    Initial (None, None) → seed both with ``current_price``.
    """
    new_mfe = ps_mfe if ps_mfe is not None else current_price
    new_mae = ps_mae if ps_mae is not None else current_price
    if side == "buy":
        if current_price > new_mfe:
            new_mfe = current_price
        if current_price < new_mae:
            new_mae = current_price
    else:
        if current_price < new_mfe:
            new_mfe = current_price
        if current_price > new_mae:
            new_mae = current_price
    return new_mfe, new_mae
