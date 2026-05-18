"""§12.1 PaperExchange — implements ExchangeClient Protocol (T-201).

T-211 shipped class scaffolding (10 ExchangeClient methods raising
NotImplementedError). T-213a extends with fill semantics + slippage +
fee + market-order compute + SL/TP cross detection — pure computation;
NO DB writes, NO event emission. T-213b will land paper_* persistence
+ execution emission body on top.

T-213a partial-body methods (compute logic; raise NotImplementedError
forward-pointer to T-213b for persistence):

* :meth:`place_market_order` — compute fill_price + slippage + fee.
* :meth:`set_trading_stop` — store sl_price/tp_price/tpsl_mode in
  per-instance active-positions in-memory dict (H-013 propagation).

T-213a internal methods (full body):

* :meth:`start_consuming` — NATS subscribe to ``market.ohlc.1m.>``.
* :meth:`_on_candle` — last-price cache update + SL/TP cross detection.
* :meth:`_check_sl_tp_crosses` — pessimistic SL-first per Q4-A.
* :meth:`_compute_slippage` — dispatch to slippage module per model.

T-213a full-stub methods (T-211 unchanged):

* ``set_leverage``, ``cancel_order``, ``get_positions``,
  ``get_fill_price``, ``get_closed_pnl_cumulative`` — DB reads/writes
  owned by T-213b.
* ``stream_executions``, ``stream_positions`` — async iterator owned
  by T-213b.

T-506 adds replay-mode wiring per BRIEF §12.2:1957 *"PaperExchange: as
in 12.1, but wired to HistoricalOHLCSource for tick prices."*:

* Constructor gains ``mode: Literal["live","replay"]`` discriminator
  + ``historical_source: HistoricalOHLCSource | None`` injected dep.
  Live mode (default) is backwards-compatible — every existing call
  site at ``services/execution/app/pool.py`` plus tests works unchanged.
* :meth:`run_replay` — replay-only entry point; consumes
  ``historical_source`` to exhaustion; expands each :class:`OHLCRow`
  via T-505 ``generate_intra_candle_path`` into 3 sequential intra-
  candle segments; per segment runs :meth:`_check_sl_tp_crosses_replay`
  + drains pending fills via existing T-213b ``_drain_sl_tp_fill``
  pipeline.
* :meth:`_process_replay_candle` — populates BOTH ``_last_price`` and
  ``_last_candle`` caches per live-mode parity. Synthesised
  ``OhlcCandlePayload`` for ``_last_candle`` cache hardcodes
  ``source='binance'`` regardless of ``OHLCRow.source`` because
  ``_compute_slippage`` and ``slippage.*`` never read
  ``candle.source`` — verified by grep at plan-time. Lie is contained
  at cache boundary.
* :meth:`_check_sl_tp_crosses_replay` — semantic mirror of
  :meth:`_check_sl_tp_crosses` taking bare ``(symbol, low, high)`` so
  no synthetic ``OhlcCandlePayload`` lies leak into the SL/TP-detect
  path. Live :meth:`_check_sl_tp_crosses` body is byte-for-byte
  unchanged to preserve existing fill-semantics test suite (status.md
  flagged drift risk on live tests as T-506's primary risk).

T-506 replay mode supersedes T-213a Q4-A pessimistic SL-first
simplification per BRIEF §12.2:1961-1963 — bullish candles correctly
fire TP first, bearish correctly fire SL first, because intra-candle
segments are narrower than the full candle range. Live mode keeps
T-213a Q4-A pessimism unchanged (explicit non-goal per §0.8 minimum-
scope discipline).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import (  # noqa: TC003 — Awaitable used in runtime ctor signature
    Awaitable,
    Callable,
)
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 — runtime annotation on frozen dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Literal

from packages.bus import MessageEnvelope
from packages.bus.payloads import TradeClosedPayload, subject_for_trade_closed
from packages.bus.schemas import OhlcCandlePayload
from packages.core import CorrelationId, idempotent, non_idempotent, now_utc
from packages.exchange.errors import OrderRejected
from packages.exchange.types import (
    AccountBalance,
    ExecutionEvent,
    FundingFee,
    InstrumentInfo,
    OrderPlaceResult,
    Position,
    PositionEvent,
)

from . import fees, persistence, slippage
from .intra_candle import generate_intra_candle_path

# T-529 / H-036: paper-side instrument fixture per OQ-3 (recommended).
# Canonical Bybit values for common test symbols. Maintenance — add new
# symbol → update this dict. Mirror live get_instrument_info contract.
_PAPER_INSTRUMENT_FIXTURES: Final[dict[str, InstrumentInfo]] = {
    "BTCUSDT": InstrumentInfo(
        symbol="BTCUSDT",
        qty_step=Decimal("0.001"),
        min_order_qty=Decimal("0.001"),
        min_notional_usd=Decimal("5"),
        tick_size=Decimal("0.10"),
    ),
    "ETHUSDT": InstrumentInfo(
        symbol="ETHUSDT",
        qty_step=Decimal("0.01"),
        min_order_qty=Decimal("0.01"),
        min_notional_usd=Decimal("5"),
        tick_size=Decimal("0.01"),
    ),
    "SOLUSDT": InstrumentInfo(
        symbol="SOLUSDT",
        qty_step=Decimal("0.1"),
        min_order_qty=Decimal("0.1"),
        min_notional_usd=Decimal("5"),
        tick_size=Decimal("0.010"),
    ),
}

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    import asyncpg

    from packages.bus import BusProtocol
    from packages.core import BotId
    from packages.core.replay_clock import ReplayClock

    from .historical_ohlc_source import HistoricalOHLCSource, OHLCRow


logger = logging.getLogger(__name__)

__all__ = ["PaperExchange", "PendingSLTPFill", "SlippageModel"]


SlippageModel = Literal["fixed_pct", "proportional_to_qty", "half_spread"]

# Allow-list defends against typos in bots.config (T-215 reads slippage_model
# from YAML; pydantic Literal validation catches at config-load time, but the
# constructor allow-list is the second-line guard at adapter-pool composition
# per L-002 / T-210 W#2 precedent).
_SLIPPAGE_MODELS: frozenset[str] = frozenset({"fixed_pct", "proportional_to_qty", "half_spread"})

# Decision #11 (BLOCKER 2): per-model required-keys allow-list for
# slippage_params validation. Defends against pydantic-bypass at T-215
# surfacing as opaque KeyError at first market-order placement.
_REQUIRED_KEYS_PER_MODEL: dict[str, frozenset[str]] = {
    "fixed_pct": frozenset({"fixed_slippage_pct"}),
    "proportional_to_qty": frozenset({"qty_slippage_coeff"}),
    "half_spread": frozenset({"half_spread_factor"}),
}


def _stub_message(method: str) -> str:
    """Forward-pointer message for the T-213b owner task (T-213a partial-body).

    Used only by T-211 full-stub methods (set_leverage, cancel_order,
    get_positions, get_fill_price, get_closed_pnl_cumulative,
    stream_executions, stream_positions) which T-213a leaves untouched.
    """
    return (
        f"PaperExchange.{method} body lands at T-213 "
        f"(fill semantics + paper_* persistence + execution emission)"
    )


@dataclass(frozen=True, slots=True)
class TerminalEvent:
    """T-511b1: PaperExchange position-terminal callback payload.

    Fired by ``_drain_full_close`` AFTER persistence commits + post-emit, with
    snapshot captured BEFORE in-memory position removal. Live mode passes no
    callback (default ``None``) → no-op. Shadow worker (T-511b1) registers a
    per-variant callback to drive ``_terminal_from_pe_state`` outcome
    classification per ADR-0005 v2 H-024 derivation chain (sl_type derived
    from PE-snapshot, NOT exchange-orderlink).
    """

    symbol: str
    exec_type: Literal["sl", "tp"]
    sl_type_at_close: Literal["protective", "be", "trail"]
    tpsl_mode_at_close: Literal["Full", "Partial"]
    realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class PendingSLTPFill:
    """Decision #12: SL/TP cross-detection event enqueued by T-213a.

    T-213a populates this dataclass on every SL/TP cross detected via
    ``_check_sl_tp_crosses``. T-213b drains the queue → writes
    ``paper_executions`` + emits :class:`packages.exchange.ExecutionEvent`.

    H-013 invariant (Decision #14): ``tpsl_mode`` is propagated from
    ``set_trading_stop`` registration through to this event without
    any ``"Full"`` default baking.
    """

    symbol: str
    side: Literal["buy", "sell"]
    qty: Decimal
    trigger_price: Decimal
    triggered_at: datetime
    kind: Literal["sl", "tp"]
    tpsl_mode: Literal["Full", "Partial"]


class PaperExchange:
    """§12.1 paper-trading exchange simulator with T-213a fill semantics.

    Construction parameters:

    * ``seed_balance: Decimal`` — initial paper-account balance.
    * ``slippage_model: SlippageModel`` — one of ``"fixed_pct"``,
      ``"proportional_to_qty"``, ``"half_spread"`` per §12.1.
    * ``fee_rate: Decimal`` — per-trade fee rate.
    * ``bot_id: BotId`` — identity for paper_* writes (T-213b).
    * ``bus: BusProtocol`` — pub/sub bus (NatsClient or ReplayBus);
      subscribes ``market.ohlc.1m.>`` in live mode.
    * ``slippage_params: dict[str, Decimal]`` — per-model coefficient
      keyed by parameter name (validated via
      :data:`_REQUIRED_KEYS_PER_MODEL` at construction).
    * ``now_fn: Callable[[], datetime] = now_utc`` — DI'd clock for
      test-deterministic ``triggered_at`` capture.
    """

    def __init__(
        self,
        *,
        seed_balance: Decimal,
        slippage_model: SlippageModel,
        fee_rate: Decimal,
        bot_id: BotId,
        bus: BusProtocol,
        slippage_params: dict[str, Decimal],
        now_fn: Callable[[], datetime] = now_utc,
        pool: asyncpg.Pool,
        event_queue_maxsize: int = 1000,
        mode: Literal["live", "replay"] = "live",
        historical_source: HistoricalOHLCSource | None = None,
        replay_clock: ReplayClock | None = None,
        seed_open_state: dict[str, Any] | None = None,
        terminal_callback: Callable[[TerminalEvent], Awaitable[None]] | None = None,
        emit_parent_lifecycle: bool = False,
    ) -> None:
        if slippage_model not in _SLIPPAGE_MODELS:
            raise ValueError(
                f"slippage_model must be one of {sorted(_SLIPPAGE_MODELS)}, got {slippage_model!r}"
            )
        required = _REQUIRED_KEYS_PER_MODEL[slippage_model]
        if set(slippage_params) != required:
            raise ValueError(
                f"slippage_params for {slippage_model!r} must have keys "
                f"{sorted(required)}, got {sorted(slippage_params)}"
            )
        if mode == "replay" and historical_source is None:
            raise ValueError(
                "PaperExchange(mode='replay') requires historical_source: HistoricalOHLCSource"
            )
        if mode == "live" and historical_source is not None:
            raise ValueError(
                "PaperExchange(mode='live') must not pass historical_source "
                "(use mode='replay' to enable historical replay)"
            )
        self._seed_balance = seed_balance
        self._slippage_model: SlippageModel = slippage_model
        self._fee_rate = fee_rate
        self._bot_id = bot_id
        self._bus = bus
        self._slippage_params = slippage_params
        self._now_fn = now_fn
        self._pool = pool
        self._mode: Literal["live", "replay"] = mode
        self._historical_source = historical_source
        # T-507b OQ-D=C: ReplayClock advanced per OHLC bucket so all components
        # (paper SL/TP triggered_at, signal handler, dispatcher) see consistent
        # replay-virtual now_fn. Live mode: stays None → no-op (no advancement).
        self._replay_clock = replay_clock
        # T-511a: optional seed_open_state for shadow-mode lifecycle (variant
        # forks parent's already-open trade; needs caches pre-populated BEFORE
        # first market.ohlc.1m candle arrives, else place_market_order races on
        # empty _last_price). Live + replay modes default None → no-op.
        self._seed_open_state = seed_open_state
        # T-511a: NATS Subscription handle from start_consuming(); enables
        # bus_unsubscribe_market_ohlc() teardown for H-016 ergonomic precondition.
        self._market_ohlc_subscription: object | None = None
        # T-511b1: optional terminal callback fired by _drain_full_close post-
        # persist + post-emit; shadow worker registers per-variant callback
        # for outcome classification. Live mode default None → no-op.
        self._terminal_callback = terminal_callback
        # T-511b2 / ADR-0010: when True, _persist_close publishes
        # TradeClosedPayload to ``trade.closed.<bot_id>`` (paper-side H-016
        # cancel hook signal). Default False — variant PE in
        # ``shadow_worker._run_shadow_variant`` MUST stay False (variant
        # closing would loop back to ShadowWorker._on_parent_close cancel-
        # ling itself). Primary bot PE in ``execution.app.pool`` wires True.
        self._emit_parent_lifecycle = emit_parent_lifecycle
        # Per-instance state (§N6: not module-level globals).
        self._last_price: dict[str, Decimal] = {}
        self._last_candle: dict[str, OhlcCandlePayload] = {}
        # Active SL/TP registrations from set_trading_stop (T-213a partial body);
        # T-213c will hydrate from paper_positions table on restart.
        self._active_positions: dict[str, dict[str, Any]] = {}
        # SL/TP cross detection queue; T-213b drains.
        self._pending_sl_tp_fills: list[PendingSLTPFill] = []
        # Decision #11 — bounded async queues for stream_executions /
        # stream_positions; backpressure blocks writer at queue.put when full.
        self._execution_queue: asyncio.Queue[ExecutionEvent] = asyncio.Queue(
            maxsize=event_queue_maxsize
        )
        self._position_queue: asyncio.Queue[PositionEvent] = asyncio.Queue(
            maxsize=event_queue_maxsize
        )

    async def start_consuming(self) -> None:
        """T-213c live-mode entry: hydrate ``_active_positions`` BEFORE NATS subscribe.

        OQ-6 default A — hydrate at the lifecycle entry point so dict
        state reflects DB before the first candle arrives. OQ-7 default
        A — query failure propagates; composition root crashes.
        Decision #16: subscribe to ``market.ohlc.1m.>`` for SL/TP monitor.

        T-506: live-only entry point. ``mode='replay'`` callers must
        invoke :meth:`run_replay` instead.
        """
        if self._mode != "live":
            raise RuntimeError(
                f"start_consuming() requires mode='live', got mode={self._mode!r}; "
                "use run_replay() for mode='replay'"
            )
        await self._hydrate_active_positions()
        # T-511a: pre-populate caches from seed_open_state if shadow-mode lifecycle.
        if self._seed_open_state is not None:
            self._apply_seed_open_state()
        # T-511a: store Subscription handle for bus_unsubscribe_market_ohlc().
        self._market_ohlc_subscription = await self._bus.subscribe(
            "market.ohlc.1m.>",
            self._on_candle,
        )

    def _apply_seed_open_state(self) -> None:
        """T-511a: pre-populate caches from seed_open_state for shadow-mode lifecycle.

        Eliminates the place_market_order race where _last_price[symbol] is None
        until first market.ohlc.1m candle arrives (typical 0..60s window between
        start_consuming() and first candle tick).

        Synthesised _last_candle uses entry_price for all 4 OHLCV fields (degenerate
        candle representing the entry instant); _compute_slippage reads only
        candle.high/.low which equal entry_price → zero slippage on synthetic
        candle (correct for shadow seeding — variant inherits parent's already-
        applied slippage in entry_price). Mirrors T-506 BLOCKER #1 fix at
        adapter.py:418-427 — schema lie contained at cache boundary
        (source='binance' Literal default; _compute_slippage never reads
        candle.source — verified by grep at T-506 plan-time).
        """
        s = self._seed_open_state
        if s is None:
            return
        symbol = s["symbol"]
        self._active_positions[symbol] = {
            "trade_id": int(s["trade_id"]),
            "side": s["side"],
            "qty": Decimal(str(s["qty"])),
            "entry_price": Decimal(str(s["entry_price"])),
            "entry_fee": Decimal(str(s.get("entry_fee", "0"))),
            "fees_paid": Decimal(str(s.get("fees_paid", "0"))),
            "sl_price": (Decimal(str(s["sl_price"])) if s.get("sl_price") is not None else None),
            "tp_price": (Decimal(str(s["tp_price"])) if s.get("tp_price") is not None else None),
            "tp_size": Decimal(str(s.get("tp_size", s["qty"]))),
            "tpsl_mode": s.get("tpsl_mode", "Full"),
            "tp_hit": bool(s.get("tp_hit", False)),
            "sl_type": s.get("sl_type", "protective"),
        }
        entry = Decimal(str(s["entry_price"]))
        self._last_price[symbol] = entry
        self._last_candle[symbol] = OhlcCandlePayload(
            symbol=symbol,
            bucket_start=s.get("opened_at", self._now_fn()),
            open=entry,
            high=entry,
            low=entry,
            close=entry,
            volume=Decimal("0"),
            is_closed=True,
        )

    async def bus_unsubscribe_market_ohlc(self) -> None:
        """T-511a: idempotent unsubscribe from market.ohlc.1m.> subject.

        Required by T-511b shadow worker per-variant teardown post-terminal so
        orphan _on_candle handlers don't keep firing after variant completes
        (H-016 hazard ergonomic precondition).

        Implementation depends on subscription type:
        - NATS Subscription (live mode): call subscription.unsubscribe() (async)
        - ReplaySubscription (T-502): set subscription.active = False

        Idempotent: second call no-op.
        """
        sub = self._market_ohlc_subscription
        if sub is None:
            return
        self._market_ohlc_subscription = None
        if hasattr(sub, "unsubscribe") and callable(sub.unsubscribe):
            await sub.unsubscribe()
        elif hasattr(sub, "active"):
            sub.active = False

    async def _hydrate_active_positions(self) -> None:
        """Hydrate ``_active_positions`` dict from paper_positions JOIN.

        Decision #5 — dict's ``qty`` field reads ``paper_positions.remaining_qty``
        so SL/TP fill-trigger qty after restart matches actual open size
        (consistency with mid-life mutation at adapter.py drain paths).
        OQ-2 default B — ``tpsl_mode='Full'`` and ``tp_size=None``;
        operator must re-issue ``set_trading_stop`` after restart on any
        partial-TP'd position to restore Partial mode (WARN-level log
        below surfaces the requirement at fail-loud observability).
        """
        async with self._pool.acquire() as conn:
            rows = await persistence.select_paper_positions_for_hydrate(
                conn,
                bot_id=str(self._bot_id),
            )
        partial_tp_count = 0
        for row in rows:
            if row["tp_hit"]:
                partial_tp_count += 1
            self._active_positions[row["symbol"]] = {
                "trade_id": int(row["trade_id"]),
                "side": row["side"],
                "qty": Decimal(row["remaining_qty"]),  # Decision #5 — current open qty.
                "entry_price": Decimal(row["entry_price"]),
                "entry_fee": Decimal(row["entry_fee"]),  # OQ-1 default A.
                "fees_paid": Decimal(row["fees_paid"]),
                "sl_price": (Decimal(row["sl_price"]) if row["sl_price"] is not None else None),
                "tp_price": (Decimal(row["tp_price"]) if row["tp_price"] is not None else None),
                "tp_size": None,  # OQ-2 default B.
                "tpsl_mode": "Full",  # OQ-2 default B.
                "tp_hit": bool(row["tp_hit"]),
            }
        logger.info(
            "paper_exchange.hydrate_complete",
            extra={
                "bot_id": str(self._bot_id),
                "symbols_hydrated": len(rows),
                "partial_tp_positions": partial_tp_count,
            },
        )
        if partial_tp_count > 0:
            logger.warning(
                "paper_exchange.hydrate_partial_tp_positions_require_set_trading_stop",
                extra={
                    "bot_id": str(self._bot_id),
                    "partial_tp_positions": partial_tp_count,
                    "required_action": (
                        "re-issue set_trading_stop with tpsl_mode='Partial' on each "
                        "partial-TP'd symbol to restore Partial mode after restart"
                    ),
                },
            )

    async def _on_candle(self, envelope: MessageEnvelope) -> None:
        """Update last-price cache + check SL/TP crosses for this symbol.

        T-213a enqueues PendingSLTPFill on cross detection; T-213b drains
        the queue post-enqueue → persists synthetic paper_orders + paper_executions
        + closes/updates paper_trades + paper_positions + emits events.
        """
        candle = OhlcCandlePayload.model_validate(envelope.payload)
        if not candle.is_closed:
            return
        self._last_candle[candle.symbol] = candle
        self._last_price[candle.symbol] = candle.close
        await self._check_sl_tp_crosses(candle)
        # T-213b drain — process every enqueued fill in FIFO order.
        while self._pending_sl_tp_fills:
            fill = self._pending_sl_tp_fills.pop(0)
            await self._drain_sl_tp_fill(fill)

    async def _check_sl_tp_crosses(self, candle: OhlcCandlePayload) -> None:
        """Pessimistic SL-first per Q4-A F2 simplification.

        For each active position on this symbol, check if SL or TP price
        falls within candle's [low, high] range. If both cross in the
        same candle, SL fills first (worst-case for paper P&L; F5
        backtest harness §12.2 will replay intra-candle path).
        """
        position = self._active_positions.get(candle.symbol)
        if position is None:
            return
        side = position.get("side")
        qty = position.get("qty")
        sl_price: Decimal | None = position.get("sl_price")
        tp_price: Decimal | None = position.get("tp_price")
        tpsl_mode: Literal["Full", "Partial"] = position["tpsl_mode"]
        # Position not fully initialised (set_trading_stop ran but
        # place_market_order side+qty not yet set at T-213a state).
        if side is None or qty is None:
            return
        if sl_price is not None and candle.low <= sl_price <= candle.high:
            self._pending_sl_tp_fills.append(
                PendingSLTPFill(
                    symbol=candle.symbol,
                    side=side,
                    qty=qty,
                    trigger_price=sl_price,
                    triggered_at=self._now_fn(),
                    kind="sl",
                    tpsl_mode=tpsl_mode,
                )
            )
            return  # SL-first; TP discarded for this candle.
        if tp_price is not None and candle.low <= tp_price <= candle.high:
            tp_qty = position.get("tp_size") if tpsl_mode == "Partial" else qty
            if tp_qty is None:
                raise ValueError(
                    f"Partial TP without tp_size for {candle.symbol}; "
                    f"set_trading_stop must supply tp_size when tpsl_mode='Partial'"
                )
            self._pending_sl_tp_fills.append(
                PendingSLTPFill(
                    symbol=candle.symbol,
                    side=side,
                    qty=tp_qty,
                    trigger_price=tp_price,
                    triggered_at=self._now_fn(),
                    kind="tp",
                    tpsl_mode=tpsl_mode,
                )
            )

    async def run_replay(self) -> None:
        """T-506 replay-mode entry point: consume historical_source to exhaustion.

        Iterates ``self._historical_source`` (T-503 HistoricalOHLCSource)
        and feeds each :class:`OHLCRow` into :meth:`_process_replay_candle`,
        which expands the candle into 3 intra-candle segments via T-505
        ``generate_intra_candle_path`` and drives ``_check_sl_tp_crosses_replay``
        + ``_drain_sl_tp_fill`` per segment. Returns when source exhausted.

        Per OQ-3=A: NO ``paper_positions`` hydrate, NO DB position-state
        seed during replay. ``_drain_sl_tp_fill`` continues to write to
        live ``paper_*`` tables in replay mode; T-507 CLI runs against
        dev DB; production replay sandbox out of scope per §0.8.
        """
        if self._mode != "replay":
            raise RuntimeError(f"run_replay() requires mode='replay', got mode={self._mode!r}")
        assert self._historical_source is not None  # ctor validation guaranteed
        async for ohlc in self._historical_source:
            await self._process_replay_candle(ohlc)

    async def _process_replay_candle(self, ohlc: OHLCRow) -> None:
        """Expand a real candle into 3 intra-candle segments + drive fill checks.

        Mirrors live-mode :meth:`_on_candle` minus the NATS-envelope
        decode + ``is_closed`` short-circuit (replay candles are always
        closed by construction). Populates BOTH ``_last_price`` and
        ``_last_candle`` caches per live-mode parity — the latter is
        read by :meth:`_compute_slippage` via :meth:`place_market_order`,
        so omission would ``KeyError`` on the first signal-driven order.
        """
        # T-507b OQ-D=C: advance replay clock to candle bucket BEFORE fill
        # detection so paper SL/TP triggered_at + downstream handlers see
        # historical replay-virtual now() instead of wall-clock today.
        if self._replay_clock is not None:
            self._replay_clock.set(ohlc.bucket_start)
        self._last_price[ohlc.symbol] = ohlc.close
        # T-506 BLOCKER fix: cache the FULL real candle for _compute_slippage.
        # Synthesised OhlcCandlePayload hardcodes source="binance" (Literal
        # default) regardless of OHLCRow.source — _compute_slippage (defined
        # at L503; reads candle.high/.low at L519-520) is the only consumer
        # of self._last_candle, and the slippage module functions take direct
        # (high, low, factor) args. No downstream consumer reads candle.source.
        # Schema lie is contained at the cache boundary. (Alternative B
        # refactor _compute_slippage signature — rejected because live-mode
        # test churn violates status.md drift-warning. Alternative C parallel
        # _last_segment_range dict — rejected as additional state + mode-branch.)
        self._last_candle[ohlc.symbol] = OhlcCandlePayload(
            symbol=ohlc.symbol,
            bucket_start=ohlc.bucket_start,
            open=ohlc.open,
            high=ohlc.high,
            low=ohlc.low,
            close=ohlc.close,
            volume=ohlc.volume,
            is_closed=True,
        )
        o, p1, p2, c = generate_intra_candle_path(
            open=ohlc.open, high=ohlc.high, low=ohlc.low, close=ohlc.close
        )
        for seg_open, seg_close in ((o, p1), (p1, p2), (p2, c)):
            seg_low = min(seg_open, seg_close)
            seg_high = max(seg_open, seg_close)
            await self._check_sl_tp_crosses_replay(symbol=ohlc.symbol, low=seg_low, high=seg_high)
            while self._pending_sl_tp_fills:
                fill = self._pending_sl_tp_fills.pop(0)
                await self._drain_sl_tp_fill(fill)

    async def _check_sl_tp_crosses_replay(
        self, *, symbol: str, low: Decimal, high: Decimal
    ) -> None:
        """Replay-mode SL/TP cross detection accepting bare ``(symbol, low, high)``.

        Semantically identical to :meth:`_check_sl_tp_crosses`'s SL-first-
        within-range branch (T-213a Q4-A) — the intra-candle ordering
        correction comes from :meth:`_process_replay_candle` splitting
        the real candle into 3 narrower segments BEFORE this method runs,
        so each call here sees a smaller ``[low, high]`` range that
        contains AT MOST one of ``{SL, TP}`` for typical bullish/bearish
        cases (TradingView "Replay" semantics per BRIEF §12.2:1961-1963).

        Body mirrors :meth:`_check_sl_tp_crosses` lines 331-373 with
        ``candle.symbol`` → ``symbol``, ``candle.low`` → ``low``,
        ``candle.high`` → ``high``. No new logic. Duplication is
        intentional per §0.8 minimum-scope to keep
        :meth:`_check_sl_tp_crosses`'s ``OhlcCandlePayload`` signature
        unchanged for live-mode test backwards-compat.
        """
        position = self._active_positions.get(symbol)
        if position is None:
            return
        side = position.get("side")
        qty = position.get("qty")
        sl_price: Decimal | None = position.get("sl_price")
        tp_price: Decimal | None = position.get("tp_price")
        tpsl_mode: Literal["Full", "Partial"] = position["tpsl_mode"]
        if side is None or qty is None:
            return
        if sl_price is not None and low <= sl_price <= high:
            self._pending_sl_tp_fills.append(
                PendingSLTPFill(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    trigger_price=sl_price,
                    triggered_at=self._now_fn(),
                    kind="sl",
                    tpsl_mode=tpsl_mode,
                )
            )
            return  # SL-first within segment.
        if tp_price is not None and low <= tp_price <= high:
            tp_qty = position.get("tp_size") if tpsl_mode == "Partial" else qty
            if tp_qty is None:
                raise ValueError(
                    f"Partial TP without tp_size for {symbol}; "
                    f"set_trading_stop must supply tp_size when tpsl_mode='Partial'"
                )
            self._pending_sl_tp_fills.append(
                PendingSLTPFill(
                    symbol=symbol,
                    side=side,
                    qty=tp_qty,
                    trigger_price=tp_price,
                    triggered_at=self._now_fn(),
                    kind="tp",
                    tpsl_mode=tpsl_mode,
                )
            )

    def _compute_slippage(
        self, *, price: Decimal, qty: Decimal, candle: OhlcCandlePayload
    ) -> Decimal:
        """Dispatch to per-model slippage function with bot-config params."""
        if self._slippage_model == "fixed_pct":
            return slippage.fixed_pct(
                price=price,
                fixed_slippage_pct=self._slippage_params["fixed_slippage_pct"],
            )
        if self._slippage_model == "proportional_to_qty":
            return slippage.proportional_to_qty(
                price=price,
                qty=qty,
                qty_slippage_coeff=self._slippage_params["qty_slippage_coeff"],
            )
        return slippage.half_spread(
            high=candle.high,
            low=candle.low,
            half_spread_factor=self._slippage_params["half_spread_factor"],
        )

    @idempotent
    async def set_leverage(self, symbol: str, leverage: int) -> None:
        """Paper has no leverage concept — no-op (Decision #13).

        Live BybitV5Adapter caches leverage per symbol; paper omits the
        margin model entirely in F2 (no leverage column on
        paper_positions; no margin engine). Returning None silently
        preserves the ``ExchangeClient`` Protocol surface.
        """
        return

    @non_idempotent
    async def place_market_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: Decimal,
        reduce_only: bool = False,
    ) -> OrderPlaceResult:
        """T-213b: compute fill price + slippage + fee → persist + emit.

        Hand verification §C in docs/plans/T-213.md (fill price);
        Hand verification §E in docs/plans/T-213b.md (realized_pnl on close).
        """
        last_price = self._last_price.get(symbol)
        if last_price is None:
            raise ValueError(
                f"No last-observed price for {symbol}; "
                f"subscribe to market.ohlc.1m.{symbol} via start_consuming() first"
            )
        last_candle = self._last_candle[symbol]
        slippage_amount = self._compute_slippage(price=last_price, qty=qty, candle=last_candle)
        fill_price = last_price + slippage_amount if side == "buy" else last_price - slippage_amount
        fee = fees.compute_fee(qty=qty, fill_price=fill_price, fee_rate=self._fee_rate)
        placed_at = self._now_fn()
        exchange_order_id = f"paper-{uuid.uuid4()}"
        exchange_exec_id = f"paper-exec-{uuid.uuid4()}"
        correlation_id = f"paper-corr-{uuid.uuid4()}"

        paper_trade_id: int | None = None
        if reduce_only:
            await self._persist_close(
                symbol=symbol,
                side=side,
                qty=qty,
                fill_price=fill_price,
                fee=fee,
                placed_at=placed_at,
                exchange_order_id=exchange_order_id,
                exchange_exec_id=exchange_exec_id,
                correlation_id=correlation_id,
            )
        else:
            paper_trade_id = await self._persist_open(
                symbol=symbol,
                side=side,
                qty=qty,
                fill_price=fill_price,
                fee=fee,
                placed_at=placed_at,
                exchange_order_id=exchange_order_id,
                exchange_exec_id=exchange_exec_id,
                correlation_id=correlation_id,
            )

        return OrderPlaceResult(
            exchange_order_id=exchange_order_id,
            placed_at=placed_at,
            paper_trade_id=paper_trade_id,
        )

    async def _persist_open(
        self,
        *,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: Decimal,
        fill_price: Decimal,
        fee: Decimal,
        placed_at: datetime,
        exchange_order_id: str,
        exchange_exec_id: str,
        correlation_id: str,
    ) -> int:
        """Decision #7 OPEN flow: single-tx INSERT chain across paper_* tables.

        Returns ``paper_trades.id`` (BIGSERIAL) so callers (T-511b2 / ADR-0010)
        can populate ``OrderPlaceResult.paper_trade_id`` for paper-aware
        shadow runtime parent_trade_id sourcing.
        """
        existing = self._active_positions.get(symbol)
        if existing is not None and existing.get("side") is not None:
            raise OrderRejected("position_already_open")
        notional_usd = (qty * fill_price).quantize(Decimal("0.0001"))
        async with self._pool.acquire() as conn, conn.transaction():
            order_id = await persistence.insert_paper_order(
                conn,
                bot_id=str(self._bot_id),
                correlation_id=correlation_id,
                exchange_order_id=exchange_order_id,
                symbol=symbol,
                side=side,
                order_type="market",
                qty=qty,
                price=fill_price,
                status="filled",
                requested_at=placed_at,
                idempotent_flag=False,
            )
            trade_id = await persistence.insert_paper_trade(
                conn,
                bot_id=str(self._bot_id),
                open_order_id=order_id,
                symbol=symbol,
                side=side,
                entry_price=fill_price,
                qty=qty,
                notional_usd=notional_usd,
                fees_paid=fee,
                opened_at=placed_at,
            )
            await persistence.insert_paper_execution(
                conn,
                exchange_exec_id=exchange_exec_id,
                order_id=order_id,
                trade_id=trade_id,
                bot_id=str(self._bot_id),
                symbol=symbol,
                side=side,
                price=fill_price,
                qty=qty,
                fee=fee,
                exec_type="open",
                executed_at=placed_at,
            )
            await persistence.insert_paper_position(
                conn,
                bot_id=str(self._bot_id),
                symbol=symbol,
                trade_id=trade_id,
                side=side,
                entry_price=fill_price,
                qty=qty,
                remaining_qty=qty,
                updated_at=placed_at,
            )
        # Mutate in-memory state to reflect new position (Decision #16 invariant).
        # entry_fee preserved separately from fees_paid: fees_paid accumulates
        # (entry_fee + partial-TP fees) for persistence; entry_fee is the
        # OQ-3-default-A "fees_open" passed to _compute_realized_pnl at FULL
        # close time, so partial-TP fees aren't double-subtracted there.
        self._active_positions[symbol] = {
            "trade_id": trade_id,
            "side": side,
            "qty": qty,
            "entry_price": fill_price,
            "entry_fee": fee,
            "fees_paid": fee,
            "sl_price": None,
            "tp_price": None,
            "tp_size": None,
            "tpsl_mode": "Full",
            "tp_hit": False,
        }
        # Decision #2 persist-then-emit: commit done; emit now.
        await self._execution_queue.put(
            ExecutionEvent(
                exchange_exec_id=exchange_exec_id,
                exchange_order_id=exchange_order_id,
                symbol=symbol,
                side=side,
                price=fill_price,
                qty=qty,
                fee=fee,
                executed_at=placed_at,
            )
        )
        await self._position_queue.put(
            PositionEvent(
                symbol=symbol,
                side=side,
                size=qty,
                entry_price=fill_price,
                leverage=None,
                unrealized_pnl=None,
                occurred_at=placed_at,
            )
        )
        return trade_id

    async def _persist_close(
        self,
        *,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: Decimal,
        fill_price: Decimal,
        fee: Decimal,
        placed_at: datetime,
        exchange_order_id: str,
        exchange_exec_id: str,
        correlation_id: str,
    ) -> None:
        """Decision #7 CLOSE flow (reduce_only=True).

        UPDATE paper_trades + DELETE paper_positions.
        """
        existing = self._active_positions.get(symbol)
        if existing is None or existing.get("side") is None:
            raise OrderRejected("no_position_to_close")
        trade_id = existing["trade_id"]
        entry_price = existing["entry_price"]
        position_side = existing["side"]
        remaining_qty = existing["qty"]
        prior_fees = existing["fees_paid"]
        # OQ-3 default A: at full close, entry_fee absorbs into realized_pnl.
        # Partial-TP fees (already on position["realized_pnl"]) must NOT be
        # subtracted again here.
        entry_fee = existing["entry_fee"]
        realized_pnl = self._compute_realized_pnl(
            side=position_side,
            entry_price=entry_price,
            exit_price=fill_price,
            qty=remaining_qty,
            fees_open=entry_fee,
            fees_close=fee,
        )
        # Add prior partial-TP pnl (already net of TP fees per Decision #9).
        realized_pnl_total = realized_pnl + existing.get("realized_pnl", Decimal("0.0000"))
        async with self._pool.acquire() as conn, conn.transaction():
            close_order_id = await persistence.insert_paper_order(
                conn,
                bot_id=str(self._bot_id),
                correlation_id=correlation_id,
                exchange_order_id=exchange_order_id,
                symbol=symbol,
                side=side,
                order_type="market",
                qty=qty,
                price=fill_price,
                status="filled",
                requested_at=placed_at,
                idempotent_flag=False,
            )
            await persistence.insert_paper_execution(
                conn,
                exchange_exec_id=exchange_exec_id,
                order_id=close_order_id,
                trade_id=trade_id,
                bot_id=str(self._bot_id),
                symbol=symbol,
                side=side,
                price=fill_price,
                qty=qty,
                fee=fee,
                exec_type="close",
                executed_at=placed_at,
            )
            await persistence.close_paper_trade(
                conn,
                trade_id=trade_id,
                exit_price=fill_price,
                realized_pnl=realized_pnl_total,
                fees_paid=prior_fees + fee,
                closed_at=placed_at,
                close_reason="manual",
                close_order_id=close_order_id,
            )
            await persistence.delete_paper_position(
                conn,
                bot_id=str(self._bot_id),
                symbol=symbol,
            )
        del self._active_positions[symbol]
        await self._execution_queue.put(
            ExecutionEvent(
                exchange_exec_id=exchange_exec_id,
                exchange_order_id=exchange_order_id,
                symbol=symbol,
                side=side,
                price=fill_price,
                qty=qty,
                fee=fee,
                executed_at=placed_at,
            )
        )
        await self._position_queue.put(
            PositionEvent(
                symbol=symbol,
                side=None,
                size=Decimal("0"),
                entry_price=None,
                leverage=None,
                unrealized_pnl=None,
                occurred_at=placed_at,
            )
        )
        # T-511b2 / ADR-0010: paper-side H-016 cancel hook signal. Variant PE
        # (shadow_worker._run_shadow_variant) keeps default emit_parent_lifecycle=False
        # so variant terminal does NOT emit (would loop back to ShadowWorker
        # cancelling itself). Primary bot PE in pool.py:198 wires True.
        if self._emit_parent_lifecycle:
            try:
                payload = TradeClosedPayload(
                    parent_trade_id=trade_id,
                    parent_kind="paper",
                    bot_id=str(self._bot_id),
                    closed_at=placed_at,
                )
                envelope = MessageEnvelope(
                    correlation_id=CorrelationId(correlation_id),
                    publisher="paper-exchange",
                    payload=payload.model_dump(mode="json"),
                )
                await self._bus.publish(
                    subject_for_trade_closed(str(self._bot_id)),
                    envelope,
                )
            except Exception as exc:
                logger.error(
                    "paper.trade_closed_publish_failed",
                    extra={
                        "event": "paper.trade_closed_publish_failed",
                        "bot_id": str(self._bot_id),
                        "parent_trade_id": trade_id,
                        "error": str(exc),
                    },
                )

    @staticmethod
    def _compute_realized_pnl(
        *,
        side: Literal["buy", "sell"],
        entry_price: Decimal,
        exit_price: Decimal,
        qty: Decimal,
        fees_open: Decimal,
        fees_close: Decimal,
    ) -> Decimal:
        """Decision #8 realized_pnl formula. Hand verification §E.1.

        Long:  (exit - entry) * qty - fees_open - fees_close.
        Short: (entry - exit) * qty - fees_open - fees_close.
        """
        if side == "buy":
            gross = (exit_price - entry_price) * qty
        else:
            gross = (entry_price - exit_price) * qty
        return (gross - fees_open - fees_close).quantize(Decimal("0.0001"))

    async def _drain_sl_tp_fill(self, fill: PendingSLTPFill) -> None:
        """Decision #5/#9 drain dispatcher: partial TP vs full close."""
        if fill.kind == "tp" and fill.tpsl_mode == "Partial":
            await self._drain_partial_tp(fill)
        else:
            await self._drain_full_close(fill)

    async def _drain_partial_tp(self, fill: PendingSLTPFill) -> None:
        """Decision #9 partial TP: paper_trades stays OPEN with reduced qty.

        OQ-3 default A: entry fee reserved for full close (TP fee only).
        """
        position = self._active_positions[fill.symbol]
        ctx = self._build_drain_context(fill, position)
        partial_pnl = self._compute_realized_pnl(
            side=ctx["position_side"],
            entry_price=ctx["entry_price"],
            exit_price=fill.trigger_price,
            qty=fill.qty,
            fees_open=Decimal("0"),
            fees_close=ctx["fee"],
        )
        new_qty = position["qty"] - fill.qty
        new_fees_paid = ctx["prior_fees"] + ctx["fee"]
        new_pnl_total = position.get("realized_pnl", Decimal("0.0000")) + partial_pnl
        async with self._pool.acquire() as conn, conn.transaction():
            order_id = await self._insert_synthetic_close_order(conn, fill, ctx)
            await self._insert_close_execution(conn, fill, ctx, order_id)
            await persistence.update_paper_trade_partial(
                conn,
                trade_id=ctx["trade_id"],
                new_qty=new_qty,
                new_fees_paid=new_fees_paid,
                new_realized_pnl=new_pnl_total,
            )
            await persistence.update_paper_position_partial(
                conn,
                bot_id=str(self._bot_id),
                symbol=fill.symbol,
                new_remaining_qty=new_qty,
                tp_hit=True,
                updated_at=fill.triggered_at,
            )
        position["qty"] = new_qty
        position["fees_paid"] = new_fees_paid
        position["realized_pnl"] = new_pnl_total
        position["tp_hit"] = True
        # T-511a: ADR-0005 v2 invariant — partial_tp promotes sl_type from
        # 'protective' (or 'be') to 'trail'. Mirror execution-service dispatcher.py:213-215.
        position["sl_type"] = "trail"
        await self._emit_close_events(
            fill,
            ctx,
            remaining_size=new_qty,
            side_after=ctx["position_side"],
            entry_price_after=ctx["entry_price"],
        )

    async def _drain_full_close(self, fill: PendingSLTPFill) -> None:
        """Decision #9 full close (SL or Full TP): close paper_trades + delete paper_positions.

        OQ-3 default A: entry_fee absorbed at full close; partial-TP fees already
        netted into ``position["realized_pnl"]`` per Decision #9 — do NOT
        subtract them again here via ``prior_fees``.
        """
        position = self._active_positions[fill.symbol]
        ctx = self._build_drain_context(fill, position)
        realized_pnl = self._compute_realized_pnl(
            side=ctx["position_side"],
            entry_price=ctx["entry_price"],
            exit_price=fill.trigger_price,
            qty=fill.qty,
            fees_open=position["entry_fee"],
            fees_close=ctx["fee"],
        )
        realized_pnl_total = realized_pnl + position.get("realized_pnl", Decimal("0.0000"))
        async with self._pool.acquire() as conn, conn.transaction():
            order_id = await self._insert_synthetic_close_order(conn, fill, ctx)
            await self._insert_close_execution(conn, fill, ctx, order_id)
            await persistence.close_paper_trade(
                conn,
                trade_id=ctx["trade_id"],
                exit_price=fill.trigger_price,
                realized_pnl=realized_pnl_total,
                fees_paid=ctx["prior_fees"] + ctx["fee"],
                closed_at=fill.triggered_at,
                close_reason=fill.kind,
                close_order_id=order_id,
            )
            await persistence.delete_paper_position(
                conn, bot_id=str(self._bot_id), symbol=fill.symbol
            )
        # T-511b1: snapshot BEFORE del so terminal_callback gets sl_type +
        # tpsl_mode from PE state at close moment (ADR-0005 v2 H-024 derivation).
        # Defensive .get() defaults: legacy code paths that bypass set_trading_stop
        # (T-211/T-213a tests using direct dict manipulation) lack sl_type/tpsl_mode
        # — default 'protective' / 'Full' aligns with 3-state vocab + Decision #14.
        terminal_snapshot = TerminalEvent(
            symbol=fill.symbol,
            exec_type=fill.kind,
            sl_type_at_close=position.get("sl_type", "protective"),
            tpsl_mode_at_close=position.get("tpsl_mode", "Full"),
            realized_pnl=realized_pnl_total,
        )
        del self._active_positions[fill.symbol]
        await self._emit_close_events(
            fill, ctx, remaining_size=Decimal("0"), side_after=None, entry_price_after=None
        )
        if self._terminal_callback is not None:
            # T-511b1: fire AFTER persist + emit so DB state is consistent
            # before downstream consumers (shadow worker) react.
            await self._terminal_callback(terminal_snapshot)

    def _build_drain_context(
        self, fill: PendingSLTPFill, position: dict[str, Any]
    ) -> dict[str, Any]:
        """Derive shared drain inputs (fee, ids, counter-side) for partial+full paths."""
        return {
            "trade_id": position["trade_id"],
            "entry_price": position["entry_price"],
            "position_side": position["side"],
            "prior_fees": position["fees_paid"],
            "fee": fees.compute_fee(
                qty=fill.qty, fill_price=fill.trigger_price, fee_rate=self._fee_rate
            ),
            "close_side": "sell" if position["side"] == "buy" else "buy",
            "exchange_order_id": f"paper-{uuid.uuid4()}",
            "exchange_exec_id": f"paper-exec-{uuid.uuid4()}",
            "correlation_id": f"paper-corr-{uuid.uuid4()}",
        }

    async def _insert_synthetic_close_order(
        self, conn: Any, fill: PendingSLTPFill, ctx: dict[str, Any]
    ) -> int:
        """Decision #5: synthetic paper_orders SL/TP row (idempotent=True)."""
        return await persistence.insert_paper_order(
            conn,
            bot_id=str(self._bot_id),
            correlation_id=ctx["correlation_id"],
            exchange_order_id=ctx["exchange_order_id"],
            symbol=fill.symbol,
            side=ctx["close_side"],
            order_type=fill.kind,
            qty=fill.qty,
            price=fill.trigger_price,
            status="filled",
            requested_at=fill.triggered_at,
            idempotent_flag=True,
        )

    async def _insert_close_execution(
        self, conn: Any, fill: PendingSLTPFill, ctx: dict[str, Any], order_id: int
    ) -> None:
        """Insert paper_executions row for SL/TP fill (FK → synthetic order_id)."""
        await persistence.insert_paper_execution(
            conn,
            exchange_exec_id=ctx["exchange_exec_id"],
            order_id=order_id,
            trade_id=ctx["trade_id"],
            bot_id=str(self._bot_id),
            symbol=fill.symbol,
            side=ctx["close_side"],
            price=fill.trigger_price,
            qty=fill.qty,
            fee=ctx["fee"],
            exec_type=fill.kind,
            executed_at=fill.triggered_at,
        )

    async def _emit_close_events(
        self,
        fill: PendingSLTPFill,
        ctx: dict[str, Any],
        *,
        remaining_size: Decimal,
        side_after: Literal["buy", "sell"] | None,
        entry_price_after: Decimal | None,
    ) -> None:
        """Decision #2 persist-then-emit: ExecutionEvent + PositionEvent post-commit."""
        await self._execution_queue.put(
            ExecutionEvent(
                exchange_exec_id=ctx["exchange_exec_id"],
                exchange_order_id=ctx["exchange_order_id"],
                symbol=fill.symbol,
                side=ctx["close_side"],
                price=fill.trigger_price,
                qty=fill.qty,
                fee=ctx["fee"],
                executed_at=fill.triggered_at,
            )
        )
        await self._position_queue.put(
            PositionEvent(
                symbol=fill.symbol,
                side=side_after,
                size=remaining_size,
                entry_price=entry_price_after,
                leverage=None,
                unrealized_pnl=None,
                occurred_at=fill.triggered_at,
            )
        )

    @idempotent
    async def set_trading_stop(
        self,
        symbol: str,
        tpsl_mode: Literal["Full", "Partial"],
        sl_price: Decimal | None = None,
        tp_price: Decimal | None = None,
        tp_size: Decimal | None = None,
        *,
        sl_type: Literal["protective", "be", "trail"] | None = None,
    ) -> None:
        """T-213b: persist sl_price + tp_price to paper_positions; tpsl_mode + tp_size in dict.

        Decision #15 / BLOCKER 1 schema parity: paper_positions has only
        sl_price + tp_price columns (mirror live position_state per §3.1
        line 268). tpsl_mode + tp_size live in ``_active_positions`` dict —
        T-213a-populated; in-memory only.

        H-013 invariant (Decision #14): ``tpsl_mode`` propagated to dict
        without ``'Full'`` default baking; ``_check_sl_tp_crosses`` reads
        from dict when constructing :class:`PendingSLTPFill`.

        T-511b1: optional ``sl_type`` keyword-only kwarg for shadow-worker
        BE-trigger path. Accepts ``'protective'`` or ``'be'``; rejects
        ``'trail'`` with ``ValueError`` because partial_tp → 'trail' must
        flow exclusively through :meth:`_drain_partial_tp` per ADR-0005 v2
        H-024 derivation chain (writing 'trail' externally would corrupt
        terminal classification at next exec_type derivation point).
        """
        if sl_type == "trail":
            raise ValueError("sl_type='trail' may only be set by _drain_partial_tp per ADR-0005 v2")
        existing = self._active_positions.get(symbol, {})
        existing["sl_price"] = sl_price
        existing["tp_price"] = tp_price
        existing["tp_size"] = tp_size
        existing["tpsl_mode"] = tpsl_mode
        if sl_type is not None:
            # T-511b1: explicit sl_type kwarg overrides setdefault behavior;
            # used by shadow worker BE-trigger path (sl_type='be').
            existing["sl_type"] = sl_type
        else:
            # T-511a: initialize sl_type='protective' if missing; preserves existing
            # value (e.g., 'trail' set by partial_tp drain or 'be' set by future
            # lifecycle BE-trigger). 3-state vocab per ADR-0005 v2 + execution-service.
            existing.setdefault("sl_type", "protective")
        self._active_positions[symbol] = existing
        async with self._pool.acquire() as conn:
            await persistence.update_paper_position_sl_tp(
                conn,
                bot_id=str(self._bot_id),
                symbol=symbol,
                sl_price=sl_price,
                tp_price=tp_price,
                updated_at=self._now_fn(),
            )

    @idempotent
    async def cancel_order(self, symbol: str, order_id: str) -> None:
        """Decision #14: UPDATE paper_orders SET status='cancelled' WHERE id + bot_id."""
        async with self._pool.acquire() as conn:
            await persistence.update_paper_order_cancelled(
                conn,
                order_id=int(order_id),
                bot_id=str(self._bot_id),
            )

    @idempotent
    async def get_positions(
        self,
        symbol: str | None = None,
    ) -> list[Position]:
        """OQ-3 default A: empty list for no rows. OQ-4 default A: size = remaining_qty."""
        async with self._pool.acquire() as conn:
            rows = await persistence.select_paper_positions(
                conn,
                bot_id=str(self._bot_id),
                symbol=symbol,
            )
        return [
            Position(
                symbol=row["symbol"],
                side=row["side"],
                size=Decimal(row["remaining_qty"]),
                entry_price=Decimal(row["entry_price"]),
                leverage=None,
                unrealized_pnl=None,
                # T-534a / OQ-4=A: paper SL is PaperExchange-internal
                # synthetic state with no exchange-side "dropped SL"
                # failure mode, so the T-534b watchdog skips paper bots
                # entirely (mirror T-218c paper-skip + ADR-0010 paper/
                # live distinction). sl_price is therefore always None
                # for paper — never read by the (live-only) watchdog.
                sl_price=None,
            )
            for row in rows
        ]

    @idempotent
    async def get_fill_price(
        self,
        symbol: str,
        order_id: str,
    ) -> Decimal | None:
        """T-538 / H-035: VWAP across paper_executions; None if no match.

        Migrated from `select_paper_execution_price_by_order_id` (LIMIT 1)
        to `select_paper_execution_vwap_by_order_id` (SUM/NULLIF aggregate)
        for parity with bybit_v5 live VWAP semantics. Audit Item 4 close.
        """
        async with self._pool.acquire() as conn:
            return await persistence.select_paper_execution_vwap_by_order_id(
                conn,
                exchange_order_id=order_id,
            )

    @idempotent
    async def get_instrument_info(self, symbol: str) -> InstrumentInfo:
        """T-529 / H-036 — paper parity: hardcoded fixture for known test symbols.

        Mirror live behavior for delisted/typo'd symbols (unknown → OrderRejected).
        Maintenance: add new symbol → update :data:`_PAPER_INSTRUMENT_FIXTURES`
        constant at module level.
        """
        info = _PAPER_INSTRUMENT_FIXTURES.get(symbol)
        if info is None:
            msg = f"paper instrument fixture not configured for symbol: {symbol}"
            raise OrderRejected(msg)
        return info

    @idempotent
    async def get_closed_pnl_cumulative(self, sub_account: str) -> Decimal:
        """OQ-5 default A: exact str equality validation. Decision #8: NULL → Decimal('0')."""
        if sub_account != str(self._bot_id):
            raise ValueError(
                f"sub_account mismatch: got {sub_account!r}, expected {str(self._bot_id)!r}"
            )
        async with self._pool.acquire() as conn:
            return await persistence.sum_paper_trades_realized_pnl(
                conn,
                bot_id=str(self._bot_id),
            )

    @idempotent
    async def get_closed_pnl_window(self, sub_account: str, since: datetime) -> Decimal:
        """T-220a — time-windowed companion. Decision #8 sub_account==bot_id contract."""
        if sub_account != str(self._bot_id):
            raise ValueError(
                f"sub_account mismatch: got {sub_account!r}, expected {str(self._bot_id)!r}"
            )
        async with self._pool.acquire() as conn:
            return await persistence.sum_paper_trades_realized_pnl_since(
                conn,
                bot_id=str(self._bot_id),
                since=since,
            )

    @idempotent
    async def get_funding_fees_window(self, sub_account: str, since: datetime) -> list[FundingFee]:
        """T-532a — paper has NO perpetual-funding model → always ``[]``
        (documented limitation, mirror the T-530 ``get_account_balance``
        paper-limitation / T-534a paper ``sl_price=None`` posture — NOT a
        TODO). Decision #8 sub_account==bot_id contract still enforced for
        parity with :meth:`get_closed_pnl_window`."""
        if sub_account != str(self._bot_id):
            raise ValueError(
                f"sub_account mismatch: got {sub_account!r}, expected {str(self._bot_id)!r}"
            )
        return []

    @idempotent
    async def get_account_balance(self, sub_account: str) -> AccountBalance:
        """T-530 — paper account snapshot derived from ``paper_trades``.

        ``wallet_balance = seed_balance + Σ realized_pnl`` (reuses the shipped
        :func:`persistence.sum_paper_trades_realized_pnl`; NULL → ``Decimal('0')``
        per its Decision #8). ``unrealized_pnl = Decimal('0')`` — paper has no
        live mark price in-memory (NO mark-to-market in T-530 scope; documented
        :class:`AccountBalance` limitation). With ``unrealized_pnl == 0`` and no
        paper margin-lockup model, the other three totals alias
        ``wallet_balance``. Decision #8 ``sub_account == str(bot_id)`` contract
        (verbatim mirror :meth:`get_closed_pnl_cumulative`).
        """
        if sub_account != str(self._bot_id):
            raise ValueError(
                f"sub_account mismatch: got {sub_account!r}, expected {str(self._bot_id)!r}"
            )
        async with self._pool.acquire() as conn:
            realized = await persistence.sum_paper_trades_realized_pnl(
                conn,
                bot_id=str(self._bot_id),
            )
        wallet_balance = self._seed_balance + realized
        return AccountBalance(
            wallet_balance=wallet_balance,
            available_balance=wallet_balance,
            total_equity=wallet_balance,
            margin_balance=wallet_balance,
            unrealized_pnl=Decimal("0"),
        )

    @idempotent
    async def get_mark_price(self, symbol: str) -> Decimal:
        """T-527b1 — paper reference price = last observed OHLC close.

        Returns ``_last_price[symbol]`` — the SAME source
        :meth:`place_market_order` uses to simulate fills (populated by
        :meth:`_on_candle` live / :meth:`_process_replay_candle` replay), so
        sizing (T-527b2) and the simulated fill price stay consistent and
        backtest/replay-deterministic. Public-market parity: NO sub-account
        validation (contrast :meth:`get_account_balance`), no DB. Unknown /
        not-yet-observed symbol → :class:`OrderRejected` (mirror
        :meth:`get_instrument_info`).
        """
        price = self._last_price.get(symbol)
        if price is None:
            msg = f"no last price observed for {symbol}"
            raise OrderRejected(msg)
        return price

    def stream_executions(self) -> AsyncIterator[ExecutionEvent]:
        """Decision #12: ``def`` (NOT ``async def``) per T-201 OQ-1.

        Returns an :class:`AsyncIterator` yielding from
        ``self._execution_queue``. Caller idiom::

            async for event in client.stream_executions(): ...

        Decision #12 caveat — single-consumer assumption: shared queue
        round-robins across simultaneous consumers (NOT broadcast). T-218
        dispatcher wires single consumer per bot per §9.5.
        """
        return self._iter_execution_events()

    async def _iter_execution_events(self) -> AsyncIterator[ExecutionEvent]:
        while True:
            yield await self._execution_queue.get()

    def stream_positions(self) -> AsyncIterator[PositionEvent]:
        """Symmetric to :meth:`stream_executions` for position events."""
        return self._iter_position_events()

    async def _iter_position_events(self) -> AsyncIterator[PositionEvent]:
        while True:
            yield await self._position_queue.get()

    async def close(self) -> None:
        """No-op at skeleton — lifecycle method must work without business logic.

        T-213 may extend with paper-state flush; the skeleton no-op is
        forward-compatible.
        """
