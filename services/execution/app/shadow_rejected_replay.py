"""T-513b1 — rejected-signal observation restart-recovery via OHLC replay (BRIEF §13.5 / §20 H-023).

On execution-service startup, scan ``shadow_rejected WHERE terminated_at IS NULL``,
for each active observation:

1. Decode meta from ``row.meta`` JSONB (T-513a shipped: ``virtual_entry_price``,
   ``sl_pct``, ``tp_pct``, ``be_trigger``, ``be_sl_level`` — all str-encoded
   Decimal at insert time).
2. WG#7: replay window cap pre-check (``shadow_rejected_replay_query_window_max_hours``);
   if exceeded → finalize ``SHUTDOWN_MID_REPLAY`` + warn-log + return PRE OHLC cursor open.
3. WG#8: timer carry-over pre-check (``now >= expires_at``); if elapsed →
   finalize ``NO_TRIGGER`` + return.
4. WG#9: ``virtual_entry == 0`` defensive early-return — finalize ``NO_TRIGGER``
   without subscribe (T-513a precedent); replay must NOT iterate OHLC with
   entry == 0 (false ``WOULD_TP`` fires on every candle.high ≥ 0).
5. Compute thresholds via T-513a :func:`_compute_thresholds` (imported).
6. Construct obs_state dict (best=entry, worst=entry, be_triggered=False, outcome=None).
7. Construct candle handler via T-513a :func:`_make_observation_candle_handler` (imported).
8. WG#10: per-task compute timeout wraps replay loop
   (``shadow_rejected_replay_per_observation_timeout_seconds``).
9. Iterate ohlc_1m cursor for [created_at, now()] window; per-candle invoke
   handler. After each candle: check ``terminal_future.done()``.
10. Terminal during replay → finalize with classified outcome + computed MFE/MAE
    via T-513a :func:`_compute_mfe_mae_pcts`.
11. Per-task compute timeout fired → finalize ``SHUTDOWN_MID_REPLAY``.
12. Else (window exhausted no terminal) → spawn live continuation task:

    * subscribe ``market.ohlc.1m.<symbol>`` with same handler factory.
    * ``asyncio.wait_for(terminal_future, remaining_timeout)``.
    * on terminal → finalize classified outcome + MFE/MAE.
    * on TimeoutError → finalize ``WOULD_BE`` (if be_triggered sticky) or ``NO_TRIGGER``.
    * try/finally: bus_unsubscribe.
    * Register the live continuation task via
      :meth:`ShadowRejectedWorker.register_resume_task`.

T-513b2 ships the mandatory kill-during-observation integration test
(``test_rejected_signal_shadow_survives_restart_via_replay`` per BRIEF §20:2790) —
mirror T-512b pattern. T-513b1 is unit-only infrastructure.

**BE-trigger restart deficiency (acknowledged limitation per OQ-5 baked)**:
T-513a ``obs_state["be_triggered"]`` is in-memory only (not persisted). On
restart, replay starts with ``be_triggered=False`` and re-iterates all OHLC
candles from ``created_at`` — the sticky flag is re-asserted by the same
candle that triggered it pre-restart (per BRIEF §13.7 OHLC replay determinism).
Edge case: BE crossed but neither SL nor TP fired AND signal-window expired
during replay timer carry-over check (step 3) — replay returns ``NO_TRIGGER``
default (cannot recover sticky). For 60-min observation default this only
affects observations created >60min before restart, which step 3 handles by
finalizing immediately. Future task may add ``meta["be_triggered"]``
persistence if production false-classifications surface.

Lifespan order in :mod:`services.execution.app.main`::

    pool → bus → rate_limiter → adapter_pool → per-bot subscribe →
    reconcile_on_startup (T-221) → dispatcher_tasks → shadow_worker.start
    (T-511b2) → resume_active_variants_on_startup (T-512a) →
    shadow_rejected_worker.start (T-513a) →
    **resume_active_observations_on_startup (this)** → scheduler.start.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from packages.bus import MessageEnvelope
from packages.bus.schemas import OhlcCandlePayload
from packages.core import CorrelationId
from packages.core.types import ShadowRejectedTerminal
from packages.db.queries.market_data import select_ohlc_for_replay_window
from packages.db.queries.shadow import (
    select_all_active_shadow_rejected,
    update_shadow_rejected_terminal,
)

from .shadow_rejected_worker import (
    ShadowRejectedWorker,
    _compute_mfe_mae_pcts,
    _compute_thresholds,
    _make_observation_candle_handler,
)
from .shadow_worker import _unsubscribe

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import asyncpg

    from packages.bus import BusProtocol
    from packages.db.queries.shadow import ShadowRejectedRow

    from .config import Settings

logger = logging.getLogger(__name__)

__all__ = ["replay_rejected_observation_to_now", "resume_active_observations_on_startup"]


_DEFAULT_META: dict[str, str] = {
    "virtual_entry_price": "0",
    "sl_pct": "0.005",
    "tp_pct": "0.01",
    "be_trigger": "0",
    "be_sl_level": "0",
}


def _decode_meta(meta: dict[str, Any]) -> dict[str, Decimal]:
    """Extract Decimal-typed thresholds from ``row.meta`` per T-513a serialization.

    Strings encoded by ``str(Decimal)`` at insert time → ``Decimal(...)`` here.
    Default fallback per T-513a if a given key was missing from the original
    payload (legacy rows from before T-513a meta extension).
    """
    raw = meta if isinstance(meta, dict) else {}
    return {k: Decimal(str(raw.get(k, default))) for k, default in _DEFAULT_META.items()}


async def _finalize_replay_terminal(
    pool: asyncpg.Pool,
    *,
    rejected_id: int,
    terminal_outcome: ShadowRejectedTerminal,
    terminated_at: datetime,
    mfe_pct: float | None,
    mae_pct: float | None,
) -> None:
    """Wrap ``update_shadow_rejected_terminal`` with cascade-delete-race tolerance.

    Mirror T-512a + T-510b convention: log ``shadow.rejected_replay_cascade_delete_race``
    on ``None`` return + continue (no retry per ``@non_idempotent``).
    """
    async with pool.acquire() as conn:
        result = await update_shadow_rejected_terminal(
            conn,
            rejected_id=rejected_id,
            terminated_at=terminated_at,
            terminal_outcome=terminal_outcome,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
        )
    if result is None:
        logger.info(
            "shadow.rejected_replay_cascade_delete_race",
            extra={
                "event": "shadow.rejected_replay_cascade_delete_race",
                "rejected_id": rejected_id,
                "terminal_outcome": terminal_outcome.value,
            },
        )


async def replay_rejected_observation_to_now(
    *,
    pool: asyncpg.Pool,
    bus: BusProtocol,
    settings: Settings,
    shadow_rejected_worker: ShadowRejectedWorker,
    row: ShadowRejectedRow,
    observation_minutes: int,
    clock: Callable[[], datetime],
) -> None:
    """Per-observation replay → finalize-or-resume.

    Best-effort: any unhandled exception logged + observation left active
    (next restart re-attempts). Returns when observation either finalised
    (terminal in DB) OR live continuation task spawned (registered via
    :meth:`ShadowRejectedWorker.register_resume_task`).
    """
    now = clock()
    meta = _decode_meta(row.meta)
    virtual_entry = meta["virtual_entry_price"]
    sl_pct = meta["sl_pct"]
    tp_pct = meta["tp_pct"]
    be_trigger = meta["be_trigger"]
    expires_at = row.created_at + timedelta(minutes=observation_minutes)

    # WG#7: replay window cap — defensive against extreme stuck observations.
    window_max = float(settings.shadow_rejected_replay_query_window_max_hours)
    window_actual_hours = (now - row.created_at).total_seconds() / 3600.0
    if window_actual_hours > window_max:
        logger.warning(
            "shadow.rejected_replay_window_exceeded",
            extra={
                "event": "shadow.rejected_replay_window_exceeded",
                "rejected_id": row.id,
                "window_actual_hours": window_actual_hours,
                "window_max_hours": window_max,
            },
        )
        await _finalize_replay_terminal(
            pool,
            rejected_id=row.id,
            terminal_outcome=ShadowRejectedTerminal.SHUTDOWN_MID_REPLAY,
            terminated_at=now,
            mfe_pct=None,
            mae_pct=None,
        )
        return

    # WG#8: timer carry-over — already-elapsed → finalize NO_TRIGGER immediately.
    # Cannot recover be_triggered sticky flag across restart (T-513a in-memory
    # only); plan §"BE-trigger restart deficiency" rationale.
    if now >= expires_at:
        await _finalize_replay_terminal(
            pool,
            rejected_id=row.id,
            terminal_outcome=ShadowRejectedTerminal.NO_TRIGGER,
            terminated_at=now,
            mfe_pct=0.0,
            mae_pct=0.0,
        )
        return

    # WG#9: virtual_entry == 0 defensive early-return (T-513a precedent).
    # Without this guard, _compute_thresholds(0) returns all-zero thresholds →
    # every candle close > 0 incorrectly fires WOULD_TP.
    if virtual_entry == 0:
        await _finalize_replay_terminal(
            pool,
            rejected_id=row.id,
            terminal_outcome=ShadowRejectedTerminal.NO_TRIGGER,
            terminated_at=now,
            mfe_pct=0.0,
            mae_pct=0.0,
        )
        return

    side: Literal["buy", "sell"] = "buy" if row.would_side == "buy" else "sell"
    tp_threshold, sl_threshold, be_threshold = _compute_thresholds(
        side=side,
        entry=virtual_entry,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        be_trigger=be_trigger,
    )

    obs_state: dict[str, Any] = {
        "best_price": virtual_entry,
        "worst_price": virtual_entry,
        "be_triggered": False,
        "outcome": None,
    }
    terminal_future: asyncio.Future[None] = asyncio.Future()

    handler = _make_observation_candle_handler(
        obs_state=obs_state,
        terminal_future=terminal_future,
        side=side,
        tp_threshold=tp_threshold,
        sl_threshold=sl_threshold,
        be_threshold=be_threshold,
    )

    # WG#10: per-task compute timeout wraps replay loop.
    own_sub: object | None = None
    try:
        terminated_during_replay = await asyncio.wait_for(
            _replay_observation_candle_loop(
                pool=pool,
                handler=handler,
                terminal_future=terminal_future,
                symbol=row.symbol,
                from_at=row.created_at,
                to_at=now,
            ),
            timeout=settings.shadow_rejected_replay_per_observation_timeout_seconds,
        )
    except TimeoutError:
        logger.warning(
            "shadow.rejected_replay_per_observation_timeout",
            extra={
                "event": "shadow.rejected_replay_per_observation_timeout",
                "rejected_id": row.id,
                "timeout_seconds": (
                    settings.shadow_rejected_replay_per_observation_timeout_seconds
                ),
            },
        )
        await _finalize_replay_terminal(
            pool,
            rejected_id=row.id,
            terminal_outcome=ShadowRejectedTerminal.SHUTDOWN_MID_REPLAY,
            terminated_at=clock(),
            mfe_pct=None,
            mae_pct=None,
        )
        return

    if terminated_during_replay:
        # Terminal fired during replay window; classify + finalize.
        outcome: ShadowRejectedTerminal = obs_state["outcome"]
        mfe_pct, mae_pct = _compute_mfe_mae_pcts(
            side=side,
            entry=virtual_entry,
            best=obs_state["best_price"],
            worst=obs_state["worst_price"],
        )
        await _finalize_replay_terminal(
            pool,
            rejected_id=row.id,
            terminal_outcome=outcome,
            terminated_at=clock(),
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
        )
        return

    # WG#12: no terminal during replay → spawn live continuation task.
    # obs_state carries replay-end best/worst/be_triggered per BE-trigger
    # restart determinism (live phase reuses _make_observation_candle_handler
    # state dict so sticky flag continues from replay end).
    async def _live_continuation() -> None:
        nonlocal own_sub
        try:
            own_sub = await bus.subscribe(f"market.ohlc.1m.{row.symbol}", handler)
            timeout_seconds = max(0.0, (expires_at - clock()).total_seconds())
            outcome_local: ShadowRejectedTerminal
            try:
                await asyncio.wait_for(terminal_future, timeout=timeout_seconds)
                outcome_local = obs_state["outcome"]
            except TimeoutError:
                # Window elapsed — classify per BE-trigger sticky flag.
                outcome_local = (
                    ShadowRejectedTerminal.WOULD_BE
                    if obs_state["be_triggered"]
                    else ShadowRejectedTerminal.NO_TRIGGER
                )
            mfe_pct_local, mae_pct_local = _compute_mfe_mae_pcts(
                side=side,
                entry=virtual_entry,
                best=obs_state["best_price"],
                worst=obs_state["worst_price"],
            )
            await _finalize_replay_terminal(
                pool,
                rejected_id=row.id,
                terminal_outcome=outcome_local,
                terminated_at=clock(),
                mfe_pct=mfe_pct_local,
                mae_pct=mae_pct_local,
            )
        finally:
            if own_sub is not None:
                await _unsubscribe(own_sub)

    task = asyncio.create_task(
        _live_continuation(),
        name=f"shadow_rejected_resume_{row.id}",
    )
    shadow_rejected_worker.register_resume_task(rejected_id=row.id, task=task)


async def _replay_observation_candle_loop(
    *,
    pool: asyncpg.Pool,
    handler: Callable[[MessageEnvelope], Any],
    terminal_future: asyncio.Future[None],
    symbol: str,
    from_at: datetime,
    to_at: datetime,
) -> bool:
    """Cursor-iterate ohlc_1m for [from_at, to_at]; drive handler per candle.

    Returns True if terminal fired during replay (terminal_future.done() check
    post-each-candle). Returns False if window exhausted with no terminal.

    Per WG#11 diagnostic logging: tracks ``rows_consumed`` + ``last_bucket_start``
    + compute/io ms split so timeout investigations can distinguish hung DB
    cursor from runaway logic.
    """
    rows_consumed = 0
    last_bucket_start: datetime | None = None
    compute_start = time.monotonic()
    elapsed_compute_ms = 0.0
    elapsed_io_ms = 0.0

    async with pool.acquire() as conn:
        io_start = time.monotonic()
        async for ohlc_row in select_ohlc_for_replay_window(
            conn,
            symbol=symbol,
            from_at=from_at,
            to_at=to_at,
        ):
            elapsed_io_ms += (time.monotonic() - io_start) * 1000.0
            compute_start_iter = time.monotonic()

            payload = OhlcCandlePayload(
                symbol=symbol,
                bucket_start=ohlc_row.bucket_start,
                open=ohlc_row.open,
                high=ohlc_row.high,
                low=ohlc_row.low,
                close=ohlc_row.close,
                volume=ohlc_row.volume,
                is_closed=True,
            )
            envelope = MessageEnvelope(
                correlation_id=CorrelationId(
                    f"shadow-rejected-replay-{symbol}-{ohlc_row.bucket_start.isoformat()}"
                ),
                publisher="shadow-rejected-replay",
                payload=payload.model_dump(mode="json"),
            )
            await handler(envelope)

            elapsed_compute_ms += (time.monotonic() - compute_start_iter) * 1000.0
            rows_consumed += 1
            last_bucket_start = ohlc_row.bucket_start

            if terminal_future.done():
                logger.info(
                    "shadow.rejected_replay_terminal_during_window",
                    extra={
                        "event": "shadow.rejected_replay_terminal_during_window",
                        "symbol": symbol,
                        "rows_consumed": rows_consumed,
                        "last_bucket_start": last_bucket_start.isoformat(),
                        "elapsed_compute_ms": round(elapsed_compute_ms, 2),
                        "elapsed_io_ms": round(elapsed_io_ms, 2),
                    },
                )
                return True

            io_start = time.monotonic()

    total_ms = (time.monotonic() - compute_start) * 1000.0
    logger.info(
        "shadow.rejected_replay_window_exhausted_no_terminal",
        extra={
            "event": "shadow.rejected_replay_window_exhausted_no_terminal",
            "symbol": symbol,
            "rows_consumed": rows_consumed,
            "last_bucket_start": last_bucket_start.isoformat() if last_bucket_start else None,
            "elapsed_compute_ms": round(elapsed_compute_ms, 2),
            "elapsed_io_ms": round(elapsed_io_ms, 2),
            "elapsed_total_ms": round(total_ms, 2),
        },
    )
    return False


async def resume_active_observations_on_startup(
    *,
    pool: asyncpg.Pool,
    bus: BusProtocol,
    settings: Settings,
    shadow_rejected_worker: ShadowRejectedWorker,
    clock: Callable[[], datetime],
) -> None:
    """Lifespan hook — enumerate active observations + dispatch per-observation replay.

    Best-effort: per-observation exception logged + continue (one bad
    observation must NOT block restart of the rest). Mirror T-512a
    :func:`resume_active_variants_on_startup` + T-221 reconcile pattern.
    """
    async with pool.acquire() as conn:
        active = await select_all_active_shadow_rejected(conn)
    logger.info(
        "shadow.rejected_resume_startup_enumerate",
        extra={
            "event": "shadow.rejected_resume_startup_enumerate",
            "active_count": len(active),
        },
    )
    for row in active:
        try:
            await replay_rejected_observation_to_now(
                pool=pool,
                bus=bus,
                settings=settings,
                shadow_rejected_worker=shadow_rejected_worker,
                row=row,
                observation_minutes=settings.shadow_rejected_observation_minutes,
                clock=clock,
            )
        except Exception as exc:
            logger.error(
                "shadow.rejected_resume_per_observation_failed",
                extra={
                    "event": "shadow.rejected_resume_per_observation_failed",
                    "rejected_id": row.id,
                    "error": str(exc),
                },
            )
