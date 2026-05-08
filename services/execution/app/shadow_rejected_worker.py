"""T-513a — rejected-signal 60-min observation FSM (BRIEF §13.5).

Subscribes to ``shadow.rejected.start.>`` wildcard; per :class:`ShadowRejectedStartPayload`
spawns a 60-min observation task (default; configurable via
``Settings.shadow_rejected_observation_minutes`` per L-001). Observation FSM:

* Subscribe to ``market.ohlc.1m.<symbol>`` for the observation window.
* Per closed candle: track best/worst price (MFE/MAE basis); check threshold
  crosses (TP / SL / BE-trigger). Rules per WG#4 SL-first conservative bias
  + WG#5 BE-trigger sticky flag.
* At terminal cross OR window timeout: ``update_shadow_rejected_terminal``
  with classified outcome + MFE/MAE pcts.

Always-on per BRIEF §13.5 ("Separate from variants"); operational kill-
switch at composition root via ``Settings.shadow_rejected_enabled``.

NO :class:`PaperExchange` per-task — pure observer (rejected signals don't
trade). Lighter than T-511b1 ``ShadowWorker`` per-variant FSM.

T-513b ships restart-recovery via OHLC replay (mirror T-512a
``shadow_replay.py`` pattern but for ``shadow_rejected`` rows).

Lifespan order in :mod:`services.execution.app.main`::

    pool → bus → rate_limiter → adapter_pool → per-bot subscribe →
    reconcile_on_startup (T-221) → dispatcher_tasks → shadow_worker.start
    (T-511b2) → resume_active_variants_on_startup (T-512a) →
    **shadow_rejected_worker.start (this)** → scheduler.start.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from packages.bus import MessageEnvelope  # noqa: TC001 — runtime use in handler dispatch
from packages.bus.payloads import ShadowRejectedStartPayload
from packages.bus.schemas import OhlcCandlePayload
from packages.core.types import ShadowRejectedTerminal
from packages.db.queries.shadow import (
    insert_shadow_rejected,
    update_shadow_rejected_terminal,
)

from .shadow_worker import _unsubscribe

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from packages.bus import BusProtocol

logger = logging.getLogger(__name__)


__all__ = ["ShadowRejectedWorker"]


def _compute_thresholds(
    *,
    side: Literal["buy", "sell"],
    entry: Decimal,
    sl_pct: Decimal,
    tp_pct: Decimal,
    be_trigger: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Compute ``(tp_threshold, sl_threshold, be_threshold)`` for given side.

    Pure function. Mirror live ``shadow_worker.py:39-48`` ``_check_be_trigger``
    delta-form ``(current - entry) / entry >= be_trigger`` — T-513a precomputes
    absolute threshold for hot-path performance (no Decimal division per-candle).

    Math safety: ``entry == 0`` → all thresholds 0 (caller-side guard at
    ``_run_observation`` early-returns NO_TRIGGER before any candle subscribe).
    """
    if side == "buy":
        return (
            entry * (Decimal("1") + tp_pct),
            entry * (Decimal("1") - sl_pct),
            entry * (Decimal("1") + be_trigger),
        )
    return (
        entry * (Decimal("1") - tp_pct),
        entry * (Decimal("1") + sl_pct),
        entry * (Decimal("1") - be_trigger),
    )


def _compute_mfe_mae_pcts(
    *,
    side: Literal["buy", "sell"],
    entry: Decimal,
    best: Decimal,
    worst: Decimal,
) -> tuple[float, float]:
    """MFE = max favorable; MAE = max adverse. Float at DB boundary.

    Decimal arithmetic happens BEFORE float cast (Decimal preservation up to
    ``shadow_rejected.mfe_pct/mae_pct`` DOUBLE PRECISION column boundary per
    migration 0014).

    Defensive: ``entry == 0`` → ``(0.0, 0.0)`` (avoids ``decimal.DivisionByZero``;
    caller-side guard at ``_run_observation`` would already have skipped to
    NO_TRIGGER, but helper is robust standalone).
    """
    if entry == 0:
        return (0.0, 0.0)
    if side == "buy":
        mfe = float((best - entry) / entry)
        mae = float((entry - worst) / entry)
    else:
        mfe = float((entry - best) / entry)
        mae = float((worst - entry) / entry)
    return (mfe, mae)


def _make_observation_candle_handler(
    *,
    obs_state: dict[str, Any],
    terminal_future: asyncio.Future[None],
    side: Literal["buy", "sell"],
    tp_threshold: Decimal,
    sl_threshold: Decimal,
    be_threshold: Decimal,
) -> Callable[[MessageEnvelope], Awaitable[None]]:
    """Closure factory: per-candle observation handler.

    Per-candle terminal classification rules (deterministic; SL-first
    conservative bias for same-candle TP+SL race per WG#4):

    1. **SL cross checked FIRST** (long: ``low <= sl_threshold``; short:
       ``high >= sl_threshold``) → outcome=WOULD_SL; set terminal_future.
    2. **TP cross checked SECOND** (long: ``high >= tp_threshold``; short:
       ``low <= tp_threshold``) → outcome=WOULD_TP; set terminal_future.
    3. **BE-trigger cross checked THIRD** (favorable price reached
       be_threshold but no TP/SL) → set ``obs_state["be_triggered"] = True``;
       continue observation. Sticky flag persists for window duration.
    4. Update best/worst price for MFE/MAE basis (per WG#5 inequality `>=`
       mirrors shadow_worker.py:39-48 delta-form convention).

    Short-circuit: ``if not candle.is_closed or terminal_future.done()`` —
    skip mid-bar updates + skip post-terminal candles delivered before
    unsubscribe completes.
    """

    async def on_candle(envelope: MessageEnvelope) -> None:
        candle = OhlcCandlePayload.model_validate(envelope.payload)
        if not candle.is_closed or terminal_future.done():
            return

        # Update MFE/MAE basis (BEFORE terminal classification — captures the
        # most-favorable/adverse price even when the same candle terminates).
        if side == "buy":
            if candle.high > obs_state["best_price"]:
                obs_state["best_price"] = candle.high
            if candle.low < obs_state["worst_price"]:
                obs_state["worst_price"] = candle.low
        else:
            if candle.low < obs_state["best_price"]:
                obs_state["best_price"] = candle.low
            if candle.high > obs_state["worst_price"]:
                obs_state["worst_price"] = candle.high

        # Terminal classification (SL-first conservative bias per WG#4).
        if side == "buy":
            if candle.low <= sl_threshold:
                obs_state["outcome"] = ShadowRejectedTerminal.WOULD_SL
                terminal_future.set_result(None)
                return
            if candle.high >= tp_threshold:
                obs_state["outcome"] = ShadowRejectedTerminal.WOULD_TP
                terminal_future.set_result(None)
                return
            if candle.high >= be_threshold:
                obs_state["be_triggered"] = True
        else:
            if candle.high >= sl_threshold:
                obs_state["outcome"] = ShadowRejectedTerminal.WOULD_SL
                terminal_future.set_result(None)
                return
            if candle.low <= tp_threshold:
                obs_state["outcome"] = ShadowRejectedTerminal.WOULD_TP
                terminal_future.set_result(None)
                return
            if candle.low <= be_threshold:
                obs_state["be_triggered"] = True

    return on_candle


class ShadowRejectedWorker:
    """Subscribes ``shadow.rejected.start.>``; spawns 60-min observation per signal.

    Each task subscribes to ``market.ohlc.1m.<symbol>`` for the window;
    tracks MFE/MAE per closed candle; classifies terminal per BRIEF §13.5
    4-outcome enum (``would_tp`` / ``would_sl`` / ``would_be`` / ``no_trigger``).
    NO :class:`PaperExchange` per-task — pure observer (rejected signals
    don't trade).

    Composition: instantiate at execution-service lifespan AFTER
    ``shadow_worker.start()`` (T-511b2) + AFTER ``resume_active_variants_on_startup``
    (T-512a). Settings-gated at construction site
    (``if settings.shadow_rejected_enabled:``); worker doesn't gate internally.
    """

    def __init__(
        self,
        *,
        bus: BusProtocol,
        pool: Any,
        observation_minutes: int = 60,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._bus = bus
        self._pool = pool
        self._observation_minutes = observation_minutes
        self._clock = clock
        # Keyed by shadow_rejected.id (1:1 ID-to-task mapping; differs from
        # T-511b2 ShadowWorker._active_tasks which is dict[int, list[Task]]
        # keyed by parent_trade_id for 1:N variants-per-trade mapping).
        self._active_tasks: dict[int, asyncio.Task[None]] = {}
        self._start_sub: object | None = None

    async def start(self) -> None:
        """Subscribe to ``shadow.rejected.start.>`` wildcard. Idempotent.

        L-002 exempt: consumer-side wildcard subscription (``.>`` subjects)
        is concrete usage, NOT f-string interpolation. Helpers
        (``subject_for_shadow_rejected_start``) are required only for
        producer-side per-bot subject construction.
        """
        if self._start_sub is None:
            self._start_sub = await self._bus.subscribe(
                "shadow.rejected.start.>", self._on_rejected_start_envelope
            )

    async def stop(self) -> None:
        """Unsubscribe + cancel in-flight observation tasks. Idempotent.

        Each cancelled task's H-016 ``try/finally`` finalizer handles its own
        bus_unsubscribe + registry cleanup. Cancel-only (no await); duplicate
        stop calls fire ``.cancel()`` on already-cancelled tasks (no-op).
        """
        if self._start_sub is not None:
            await _unsubscribe(self._start_sub)
            self._start_sub = None
        for task in list(self._active_tasks.values()):
            if not task.done():
                task.cancel()
        self._active_tasks.clear()

    def register_resume_task(
        self,
        *,
        rejected_id: int,
        task: asyncio.Task[None],
    ) -> None:
        """T-513b1 — register a restart-recovery resume task into ``_active_tasks``.

        Public surface for ``services.execution.app.shadow_rejected_replay`` so
        ``stop()`` cancels resume tasks identically to live-spawn tasks.
        Encapsulates the ``_active_tasks`` registry layout — callers must NOT
        access the dict directly. Mirror :meth:`ShadowWorker.register_resume_task`
        (T-512a) but 1:1 keying (``dict[int, Task]``) since rejected obs are
        1:1 ID-to-task vs variants 1:N parent_trade_id-to-tasks.
        """
        self._active_tasks[rejected_id] = task

    async def _on_rejected_start_envelope(self, envelope: MessageEnvelope) -> None:
        """NATS handler: parse :class:`ShadowRejectedStartPayload` → dispatch."""
        payload = ShadowRejectedStartPayload.model_validate(envelope.payload)
        await self._on_rejected_start(payload)

    async def _on_rejected_start(self, payload: ShadowRejectedStartPayload) -> None:
        """Insert shadow_rejected row + spawn 60-min observation task."""
        # T-510b shipped insert_shadow_rejected (`@non_idempotent`); meta JSONB
        # stash overrides for T-513b restart-recovery decode (Decimal→str via
        # _serialize_meta + _to_jsonable per L-013 convention).
        async with self._pool.acquire() as conn:
            row = await insert_shadow_rejected(
                conn,
                signal_id=payload.signal_id,
                bot_id=payload.bot_id,
                symbol=payload.symbol,
                would_side="buy" if payload.action == "LONG" else "sell",
                created_at=self._clock(),
                meta={
                    "virtual_entry_price": str(payload.virtual_entry_price),
                    "sl_pct": str(payload.sl_pct),
                    "tp_pct": str(payload.tp_pct),
                    "be_trigger": str(payload.be_trigger),
                    "be_sl_level": str(payload.be_sl_level),
                },
            )
        rejected_id = row.id
        task = asyncio.create_task(
            self._run_observation(payload=payload, rejected_id=rejected_id),
            name=f"shadow_rejected_{rejected_id}",
        )
        self._active_tasks[rejected_id] = task

    async def _run_observation(
        self,
        *,
        payload: ShadowRejectedStartPayload,
        rejected_id: int,
    ) -> None:
        """Per-rejected-signal observation FSM. H-016 finalizer in finally.

        Defensive entry==0 early-return: if virtual_entry_price is 0 (KV miss /
        cold-start / PG transient error → ``_resolve_virtual_entry`` Decimal('0')
        fallback), classify NO_TRIGGER immediately and skip subscribe + 60-min
        wait. Without this guard, ``_compute_thresholds(0)`` returns all-zero
        thresholds → every candle close>0 incorrectly fires WOULD_TP.
        """
        own_sub: object | None = None
        try:
            entry = payload.virtual_entry_price
            side: Literal["buy", "sell"] = "buy" if payload.action == "LONG" else "sell"

            # T-513a entry==0 defensive early-return (per pass-2 CONCERN fix).
            if entry == 0:
                async with self._pool.acquire() as conn:
                    await update_shadow_rejected_terminal(
                        conn,
                        rejected_id=rejected_id,
                        terminated_at=self._clock(),
                        terminal_outcome=ShadowRejectedTerminal.NO_TRIGGER,
                        mfe_pct=0.0,
                        mae_pct=0.0,
                    )
                logger.info(
                    "shadow.rejected_observation_skipped_zero_entry",
                    extra={
                        "event": "shadow.rejected_observation_skipped_zero_entry",
                        "rejected_id": rejected_id,
                        "signal_id": payload.signal_id,
                    },
                )
                return

            tp_threshold, sl_threshold, be_threshold = _compute_thresholds(
                side=side,
                entry=entry,
                sl_pct=payload.sl_pct,
                tp_pct=payload.tp_pct,
                be_trigger=payload.be_trigger,
            )
            obs_state: dict[str, Any] = {
                "best_price": entry,
                "worst_price": entry,
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
            own_sub = await self._bus.subscribe(f"market.ohlc.1m.{payload.symbol}", handler)

            timeout_seconds = float(self._observation_minutes * 60)
            outcome: ShadowRejectedTerminal
            try:
                await asyncio.wait_for(terminal_future, timeout=timeout_seconds)
                outcome = obs_state["outcome"]
            except TimeoutError:
                # Window elapsed — classify based on BE-trigger sticky flag.
                outcome = (
                    ShadowRejectedTerminal.WOULD_BE
                    if obs_state["be_triggered"]
                    else ShadowRejectedTerminal.NO_TRIGGER
                )

            mfe_pct, mae_pct = _compute_mfe_mae_pcts(
                side=side,
                entry=entry,
                best=obs_state["best_price"],
                worst=obs_state["worst_price"],
            )

            async with self._pool.acquire() as conn:
                result = await update_shadow_rejected_terminal(
                    conn,
                    rejected_id=rejected_id,
                    terminated_at=self._clock(),
                    terminal_outcome=outcome,
                    mfe_pct=mfe_pct,
                    mae_pct=mae_pct,
                )
            if result is None:
                logger.info(
                    "shadow.rejected_update_terminal_row_missing",
                    extra={
                        "event": "shadow.rejected_update_terminal_row_missing",
                        "rejected_id": rejected_id,
                        "terminal_outcome": outcome.value,
                    },
                )
        finally:
            # H-016 unconditional finalizer per BRIEF §20 policy. Mirror
            # T-511b1 shadow_worker.py:331-340 try/finally pattern.
            if own_sub is not None:
                await _unsubscribe(own_sub)
            self._active_tasks.pop(rejected_id, None)
