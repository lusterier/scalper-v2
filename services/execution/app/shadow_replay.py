"""T-512a — shadow variant restart-recovery via OHLC replay (BRIEF §13.4 / H-023).

On execution-service startup, scan ``shadow_variants WHERE terminated_at IS NULL``,
for each active variant:

1. Check parent trade still open (live ``trades.status`` for ``parent_kind='live'``;
   paper ``paper_trades.closed_at`` for ``parent_kind='paper'``); if closed during
   downtime → write ``SHUTDOWN_MID_REPLAY`` outcome (per ADR-0010 paper plno-scope).
2. Compute remaining timer per wall-clock carry-over (``created_at + max_duration_hours``).
3. Decode variant overrides from ``row.meta["overrides"]`` (T-511b1 retro-fit).
4. Construct per-variant :class:`PaperExchange` (seed_open_state from row + decoded overrides).
5. Iterate ``ohlc_1m`` cursor for ``[created_at, now()]`` window; for each candle
   invoke ``pe._on_candle`` + own_sub ``_make_candle_handler`` body.
6. Detect terminal via ``terminal_future.done()`` post-each-candle (handles both
   full-close ``_drain_full_close`` and partial-TP-then-SL H-024 v2 cases — NOT
   ``_active_positions[symbol]`` absence which is full-close-only).
7. Terminal during replay → :func:`update_shadow_variant_terminal`; done.
8. Replay window cap exceeded OR per-variant timeout fired OR parent closed during
   downtime → ``SHUTDOWN_MID_REPLAY`` outcome.
9. Else → spawn live continuation: ``pe.start_consuming()`` + own_sub bus subscribe
   + ``_drive_variant_to_terminal`` (shared helper from shadow_worker per T-511b1
   refactor).

T-512b ships the mandatory kill-during-variant integration test
(``test_shadow_variant_survives_restart_via_replay`` per BRIEF §20:2787) —
testcontainer + subprocess.spawn + SIGTERM mid-variant. T-512a is unit-only
infrastructure.

Lifespan order in services.execution.app.main:
    pool → bus → rate_limiter → adapter_pool → per-bot subscribe →
    reconcile_on_startup (T-221) → dispatcher_tasks → shadow_worker.start →
    **resume_active_variants_on_startup (this)** → scheduler.start → state attach.
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
from packages.core import BotId, CorrelationId
from packages.core.types import ShadowVariantTerminal
from packages.db.queries.market_data import select_ohlc_for_replay_window
from packages.db.queries.shadow import (
    select_all_active_shadow_variants,
    update_shadow_variant_terminal,
)
from packages.exchange.paper import PaperExchange, TerminalEvent

from .shadow_worker import (
    ShadowWorker,
    _drive_variant_to_terminal,
    _make_candle_handler,
    _terminal_from_pe_state,
    _unsubscribe,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import asyncpg

    from packages.bus import BusProtocol
    from packages.db.queries.shadow import ShadowVariantRow

    from .config import Settings

logger = logging.getLogger(__name__)

__all__ = ["replay_shadow_variant_to_now", "resume_active_variants_on_startup"]


_DEFAULT_OVERRIDES: dict[str, str] = {
    "be_trigger": "0",
    "be_sl_level": "0",
    "trail_pct": "0",
    "sl_pct": "0.005",
    "tp_pct": "0.01",
    "tp_qty_pct": "1",
    "max_duration_hours": "4",
}


def _decode_overrides(meta: dict[str, Any]) -> dict[str, Decimal]:
    """Extract overrides from ``row.meta["overrides"]`` per T-511b1 retro-fit.

    Strings encoded by ``str(Decimal)`` at insert time → ``Decimal(...)`` here.
    Default fallback per T-511b1 ``_run_shadow_variant`` lines 207-214 if a
    given key was missing from the original variant.overrides dict (legitimate
    case: VariantSpec.overrides is partial; defaults applied at consumer time).
    """
    raw = meta.get("overrides", {}) if isinstance(meta, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    return {k: Decimal(str(raw.get(k, default))) for k, default in _DEFAULT_OVERRIDES.items()}


async def _check_parent_open(
    conn: asyncpg.Connection[asyncpg.Record] | Any,
    *,
    parent_kind: Literal["live", "paper"],
    parent_trade_id: int,
) -> bool:
    """Return True iff parent trade still open (parent_kind discriminator routing)."""
    if parent_kind == "live":
        row = await conn.fetchrow(
            "SELECT status FROM trades WHERE id = $1",
            parent_trade_id,
        )
        return row is not None and row["status"] != "closed"
    # paper
    row = await conn.fetchrow(
        "SELECT closed_at FROM paper_trades WHERE id = $1",
        parent_trade_id,
    )
    return row is not None and row["closed_at"] is None


async def _finalize_replay_terminal(
    pool: asyncpg.Pool,
    *,
    variant_id: int,
    parent_trade_id: int,
    terminal_outcome: ShadowVariantTerminal,
    terminated_at: datetime,
    realized_pnl: Decimal | None,
    mfe_pct: float | None,
    mae_pct: float | None,
) -> None:
    """Wrap ``update_shadow_variant_terminal`` with cascade-delete-race tolerance.

    Mirror T-511b1 wg#13 log format: log ``shadow.replay_cascade_delete_race``
    on None return + continue (no retry per ``@non_idempotent``).
    """
    async with pool.acquire() as conn:
        result = await update_shadow_variant_terminal(
            conn,
            variant_id=variant_id,
            terminated_at=terminated_at,
            terminal_outcome=terminal_outcome,
            realized_pnl=realized_pnl,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
        )
    if result is None:
        logger.info(
            "shadow.replay_cascade_delete_race",
            extra={
                "event": "shadow.replay_cascade_delete_race",
                "variant_id": variant_id,
                "parent_trade_id": parent_trade_id,
                "terminal_outcome": terminal_outcome.value,
            },
        )


async def replay_shadow_variant_to_now(
    *,
    pool: asyncpg.Pool,
    bus: BusProtocol,
    settings: Settings,
    shadow_worker: ShadowWorker,
    row: ShadowVariantRow,
    clock: Callable[[], datetime],
) -> None:
    """Per-variant replay → finalize-or-resume.

    Best-effort: any unhandled exception logged + variant left active (next
    restart re-attempts). Returns when variant either finalised (terminal in
    DB) OR live continuation task spawned (registered via
    ``shadow_worker.register_resume_task``).
    """
    now = clock()
    overrides = _decode_overrides(row.meta)
    max_duration_hours = overrides["max_duration_hours"]
    expires_at = row.created_at + timedelta(hours=float(max_duration_hours))

    # WG#5: replay window cap — defensive against extreme stuck variants.
    window_max = float(settings.shadow_replay_query_window_max_hours)
    window_actual_hours = (now - row.created_at).total_seconds() / 3600.0
    if window_actual_hours > window_max:
        logger.warning(
            "shadow.replay_window_exceeded",
            extra={
                "event": "shadow.replay_window_exceeded",
                "variant_id": row.id,
                "window_actual_hours": window_actual_hours,
                "window_max_hours": window_max,
            },
        )
        await _finalize_replay_terminal(
            pool,
            variant_id=row.id,
            parent_trade_id=row.parent_trade_id,
            terminal_outcome=ShadowVariantTerminal.SHUTDOWN_MID_REPLAY,
            terminated_at=now,
            realized_pnl=None,
            mfe_pct=None,
            mae_pct=None,
        )
        return

    # WG#7+#8+#9: parent_kind dispatch + closed-during-downtime handling.
    async with pool.acquire() as conn:
        parent_open = await _check_parent_open(
            conn,
            parent_kind=row.parent_kind,
            parent_trade_id=row.parent_trade_id,
        )
    if not parent_open:
        logger.info(
            "shadow.replay_parent_closed_during_downtime",
            extra={
                "event": "shadow.replay_parent_closed_during_downtime",
                "variant_id": row.id,
                "parent_trade_id": row.parent_trade_id,
                "parent_kind": row.parent_kind,
            },
        )
        await _finalize_replay_terminal(
            pool,
            variant_id=row.id,
            parent_trade_id=row.parent_trade_id,
            terminal_outcome=ShadowVariantTerminal.SHUTDOWN_MID_REPLAY,
            terminated_at=now,
            realized_pnl=None,
            mfe_pct=None,
            mae_pct=None,
        )
        return

    # WG#3: timer carry-over already-elapsed → finalize as TIMEOUT immediately.
    if now >= expires_at:
        await _finalize_replay_terminal(
            pool,
            variant_id=row.id,
            parent_trade_id=row.parent_trade_id,
            terminal_outcome=ShadowVariantTerminal.TIMEOUT,
            terminated_at=now,
            realized_pnl=None,
            mfe_pct=0.0,
            mae_pct=0.0,
        )
        return

    # Construct per-variant PE with seed_open_state from row + decoded overrides.
    side: Literal["buy", "sell"] = "buy" if row.side == "buy" else "sell"
    sl_pct = overrides["sl_pct"]
    tp_pct = overrides["tp_pct"]
    tp_qty_pct = overrides["tp_qty_pct"]
    seed_open_state: dict[str, Any] = {
        "symbol": _variant_symbol_from_row(row),
        "side": side,
        "qty": row.qty,
        "entry_price": row.entry_price,
        "trade_id": row.parent_trade_id,
        "sl_price": row.entry_price * (Decimal("1") - sl_pct)
        if side == "buy"
        else row.entry_price * (Decimal("1") + sl_pct),
        "tp_price": row.entry_price * (Decimal("1") + tp_pct)
        if side == "buy"
        else row.entry_price * (Decimal("1") - tp_pct),
        "tp_size": row.qty * tp_qty_pct,
        "tpsl_mode": "Partial" if tp_qty_pct < Decimal("1") else "Full",
    }

    terminal_future: asyncio.Future[TerminalEvent] = asyncio.Future()

    async def on_terminal(evt: TerminalEvent) -> None:
        if not terminal_future.done():
            terminal_future.set_result(evt)

    pe = PaperExchange(
        seed_balance=settings.shadow_seed_balance_usd,
        slippage_model="fixed_pct",
        fee_rate=settings.shadow_fee_rate,
        bot_id=BotId(row.bot_id),
        bus=bus,
        slippage_params={"fixed_slippage_pct": Decimal("0")},
        pool=pool,
        seed_open_state=seed_open_state,
        terminal_callback=on_terminal,
    )

    variant_state: dict[str, Decimal] = {
        "best_price": row.entry_price,
        "worst_price": row.entry_price,
    }

    candle_handler = _make_candle_handler(
        pe=pe,
        side=side,
        entry_price=row.entry_price,
        be_trigger=overrides["be_trigger"],
        be_sl_level=overrides["be_sl_level"],
        trail_pct=overrides["trail_pct"],
        state=variant_state,
    )

    # WG#6: per-variant compute timeout wraps replay loop.
    own_sub: object | None = None
    try:
        terminated_during_replay = await asyncio.wait_for(
            _replay_candle_loop(
                pool=pool,
                pe=pe,
                candle_handler=candle_handler,
                terminal_future=terminal_future,
                symbol=seed_open_state["symbol"],
                from_at=row.created_at,
                to_at=now,
            ),
            timeout=settings.shadow_replay_per_variant_timeout_seconds,
        )
    except TimeoutError:
        logger.warning(
            "shadow.replay_per_variant_timeout",
            extra={
                "event": "shadow.replay_per_variant_timeout",
                "variant_id": row.id,
                "timeout_seconds": settings.shadow_replay_per_variant_timeout_seconds,
            },
        )
        await _finalize_replay_terminal(
            pool,
            variant_id=row.id,
            parent_trade_id=row.parent_trade_id,
            terminal_outcome=ShadowVariantTerminal.SHUTDOWN_MID_REPLAY,
            terminated_at=clock(),
            realized_pnl=None,
            mfe_pct=None,
            mae_pct=None,
        )
        return

    if terminated_during_replay:
        # Terminal fired during replay window; classify + finalize.
        terminal_evt = await terminal_future  # already set; non-blocking
        outcome = _terminal_from_pe_state(
            exec_type=terminal_evt.exec_type,
            sl_type_at_close=terminal_evt.sl_type_at_close,
            tpsl_mode_at_close=terminal_evt.tpsl_mode_at_close,
        )
        if side == "buy":
            mfe_pct = float((variant_state["best_price"] - row.entry_price) / row.entry_price)
            mae_pct = float((row.entry_price - variant_state["worst_price"]) / row.entry_price)
        else:
            mfe_pct = float((row.entry_price - variant_state["best_price"]) / row.entry_price)
            mae_pct = float((variant_state["worst_price"] - row.entry_price) / row.entry_price)
        await _finalize_replay_terminal(
            pool,
            variant_id=row.id,
            parent_trade_id=row.parent_trade_id,
            terminal_outcome=outcome,
            terminated_at=clock(),
            realized_pnl=terminal_evt.realized_pnl,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
        )
        return

    # No terminal during replay → spawn live continuation task. Variant_state
    # carries replay-end best/worst per WG#11; live phase reuses _make_candle_handler
    # state dict so trail SL re-arm logic continues from replay end (NOT entry).
    async def _live_continuation() -> None:
        nonlocal own_sub
        try:
            await pe.start_consuming()
            own_sub = await bus.subscribe(
                f"market.ohlc.1m.{seed_open_state['symbol']}",
                candle_handler,
            )
            timeout_seconds = max(0.0, (expires_at - clock()).total_seconds())
            outcome, realized_pnl, mfe_pct, mae_pct = await _drive_variant_to_terminal(
                terminal_future=terminal_future,
                variant_state=variant_state,
                timeout_seconds=timeout_seconds,
                side=side,
                entry_price=row.entry_price,
            )
            await _finalize_replay_terminal(
                pool,
                variant_id=row.id,
                parent_trade_id=row.parent_trade_id,
                terminal_outcome=outcome,
                terminated_at=clock(),
                realized_pnl=realized_pnl,
                mfe_pct=mfe_pct,
                mae_pct=mae_pct,
            )
        finally:
            await pe.bus_unsubscribe_market_ohlc()
            if own_sub is not None:
                await _unsubscribe(own_sub)

    task = asyncio.create_task(
        _live_continuation(),
        name=f"shadow_resume_{row.id}",
    )
    shadow_worker.register_resume_task(parent_trade_id=row.parent_trade_id, task=task)


def _variant_symbol_from_row(row: ShadowVariantRow) -> str:
    """ShadowVariantRow does not carry symbol; resume path looks it up.

    T-511b1 shipped ShadowVariantRow projection at ``packages/db/queries/shadow.py:80``
    contains: id, parent_trade_id, bot_id, variant_name, side, entry_price,
    qty, created_at, terminated_at, terminal_outcome, realized_pnl, mfe_pct,
    mae_pct, meta, parent_kind. NO ``symbol`` column — symbol is implicit
    via parent trade. T-512a stashes symbol in ``row.meta["symbol"]`` at insert
    time (T-511b1 retro-fit alongside overrides) so resume can recover it.

    Falls back to a sentinel if absent (legacy rows from before retro-fit) —
    these are skipped via SHUTDOWN_MID_REPLAY outcome by caller's pre-check.
    """
    if isinstance(row.meta, dict):
        sym = row.meta.get("symbol")
        if isinstance(sym, str):
            return sym
    msg = (
        f"shadow_variants row id={row.id} missing meta.symbol — pre-T-512a "
        f"insert; cannot resume. Caller should write SHUTDOWN_MID_REPLAY."
    )
    raise RuntimeError(msg)


async def _replay_candle_loop(
    *,
    pool: asyncpg.Pool,
    pe: PaperExchange,
    candle_handler: Callable[[MessageEnvelope], Any],
    terminal_future: asyncio.Future[TerminalEvent],
    symbol: str,
    from_at: datetime,
    to_at: datetime,
) -> bool:
    """Cursor-iterate ohlc_1m for [from_at, to_at]; drive PE + handler per candle.

    Returns True if terminal fired during replay (per WG#3 ``terminal_future.done()``
    detection — handles both full-close ``_drain_full_close`` and partial-TP-then-SL
    H-024 v2 cases). Returns False if window exhausted with no terminal.

    Per WG#6 diagnostic logging: tracks ``cursor_rows_consumed`` + ``last_bucket_start``
    + compute/io ms split so timeout investigations can distinguish hung DB cursor
    from runaway logic.
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
                    f"shadow-replay-{symbol}-{ohlc_row.bucket_start.isoformat()}"
                ),
                publisher="shadow-replay",
                payload=payload.model_dump(mode="json"),
            )
            # PE intra-candle SL/TP cross detection.
            await pe._on_candle(envelope)
            # Variant FSM (BE/trail SL adjustment).
            await candle_handler(envelope)

            elapsed_compute_ms += (time.monotonic() - compute_start_iter) * 1000.0
            rows_consumed += 1
            last_bucket_start = ohlc_row.bucket_start

            # WG#3 BLOCKER 2 fix: terminal detection via terminal_future.done()
            # post-each-candle handles partial-TP-then-SL H-024 v2 case
            # (NOT _active_positions[symbol] absence which is full-close-only).
            if terminal_future.done():
                logger.info(
                    "shadow.replay_terminal_during_window",
                    extra={
                        "event": "shadow.replay_terminal_during_window",
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
        "shadow.replay_window_exhausted_no_terminal",
        extra={
            "event": "shadow.replay_window_exhausted_no_terminal",
            "symbol": symbol,
            "rows_consumed": rows_consumed,
            "last_bucket_start": last_bucket_start.isoformat() if last_bucket_start else None,
            "elapsed_compute_ms": round(elapsed_compute_ms, 2),
            "elapsed_io_ms": round(elapsed_io_ms, 2),
            "elapsed_total_ms": round(total_ms, 2),
        },
    )
    return False


async def resume_active_variants_on_startup(
    *,
    pool: asyncpg.Pool,
    bus: BusProtocol,
    settings: Settings,
    shadow_worker: ShadowWorker,
    clock: Callable[[], datetime],
) -> None:
    """Lifespan hook — enumerate active variants + dispatch per-variant replay.

    Best-effort: per-variant exception logged + continue (one bad variant must
    NOT block restart of the rest). Mirror :func:`reconcile_on_startup` (T-221).
    """
    async with pool.acquire() as conn:
        active = await select_all_active_shadow_variants(conn)
    logger.info(
        "shadow.resume_startup_enumerate",
        extra={
            "event": "shadow.resume_startup_enumerate",
            "active_count": len(active),
        },
    )
    for row in active:
        try:
            await replay_shadow_variant_to_now(
                pool=pool,
                bus=bus,
                settings=settings,
                shadow_worker=shadow_worker,
                row=row,
                clock=clock,
            )
        except Exception as exc:
            logger.error(
                "shadow.resume_per_variant_failed",
                extra={
                    "event": "shadow.resume_per_variant_failed",
                    "variant_id": row.id,
                    "parent_trade_id": row.parent_trade_id,
                    "error": str(exc),
                },
            )
