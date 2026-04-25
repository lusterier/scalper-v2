"""OHLC pipeline (§9.2, §8.2, §8.4).

:class:`OhlcPipeline` drives the closed-bucket detection, persistence,
and NATS publish path for the Binance kline_1m streams of every
configured symbol. The class is composed by the market-data-svc
composition root (T-100, future) — it owns no I/O resources directly;
:class:`~packages.market.SubscriptionManager`,
:class:`asyncpg.Pool`, :class:`~packages.bus.NatsClient`, and the
per-symbol task supervision are all DI'd via the constructor.

Lifecycle:

* :meth:`start(symbols)` — spawn one consumer task per symbol, each
  doing ``async with subscription_mgr.subscribe(symbol) as feed:
  async for frame in feed: await self._handle(symbol, frame)``.
* :meth:`stop()` — cancel all tasks, await termination with timeout,
  exit cleanly. Per-task SubscriptionManager release on context exit
  decrements refcounts; if no other consumer holds the symbol, the
  underlying WS streams are torn down (T-102 H-014).

Per-frame classification (``data.k.x`` boolean per Binance kline spec
— the canonical closed-bucket flag, true on the terminal frame of a
minute bucket and false on every prior intra-minute update):

* ``x=True``  → closed bucket. Persist via INSERT ... ON CONFLICT
  DO UPDATE (last-write-wins so T-105 backfill via REST
  ``/api/v3/klines`` can repair WS-stored drift), then publish
  ``market.ohlc.1m.<symbol>`` with ``is_closed=true``.
* ``x=False`` → in-progress. Publish only (UI live tail per §9.2
  line 1462). **No DB write.**
* ``x`` missing or non-bool, non-kline event, or otherwise malformed
  → log ``ohlc_pipeline_malformed_frame``, drop.

Ordering: **publish-after-persist** for closed candles. A NATS publish
failure after a successful DB write logs ``ohlc_pipeline_publish_error``
and drops the frame. **Downstream-consumer contract**: T-110
feature-engine and any other consumer of ``market.ohlc.>`` must treat
the DB as canonical source-of-truth and the NATS stream as a live tap.
Concretely, T-110 reads warmup history from the ``ohlc_*`` continuous
aggregates (T-103) at startup and only relies on the bus for
low-latency notification of new closes; if a publish drops, the row
is still in DB and the next service restart re-warms cleanly. The
opposite order (publish-before-persist) would let a phantom candle
leak onto the bus that does not exist in DB — backfill cannot repair
because the canonical source-of-truth is the DB itself.

Stateless closed-detection: no per-symbol ``last_closed`` dict, no
in-memory dedup. Idempotency rides on the DB PK
``(symbol, bucket_start, source)`` plus the deterministic UUID5
``Nats-Msg-Id`` derived from ``(symbol, bucket_start)`` for closed
publishes (:func:`packages.bus.schemas.message_id_for_closed_candle`).
A future reader inclined to "optimize" by adding a per-symbol cache
should NOT — restart-safe, no warmup required, no race with
concurrent task handlers, and T-105 backfill is the recovery path for
any frames missed during a disconnect.

Per-symbol task error semantics:

* Handler raises → log ``ohlc_pipeline_handler_error`` with symbol +
  exc, continue with the next frame. The task itself stays alive.
  ``except Exception`` (NOT bare ``except``) so
  :class:`asyncio.CancelledError` propagates and :meth:`stop` can
  exit promptly.
* Unrecoverable error inside the consume loop (SymbolFeed itself
  errors — should not happen given the T-102 contract) → task dies,
  log; no auto-restart in F1 (operator restarts the service).

Symbol set source-of-truth: callers pass ``symbols: list[str]`` to
:meth:`start`; the pipeline does NOT query DB for the active-symbol
JOIN. That query lives with the composition root (T-100 lifespan,
future) — keeps the pipeline pure-class testable and separates "what
symbols to follow" from "how to consume frames".
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from packages.bus import MessageEnvelope
from packages.bus.schemas import OhlcCandlePayload, message_id_for_closed_candle
from packages.core import CorrelationId
from packages.db.queries.market_data import insert_ohlc_1m

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import NatsClient

    from .subscription import SubscriptionManager


__all__ = ["OhlcPipeline"]


_PUBLISHER = "market-data-svc"
_SOURCE = "binance"
_INTERVAL = "1m"
_SUBJECT_PREFIX = f"market.ohlc.{_INTERVAL}"
_DEFAULT_STOP_TIMEOUT_SECONDS = 5.0


class OhlcPipeline:
    """Per-symbol kline_1m consumer with persist + publish (§9.2)."""

    def __init__(
        self,
        *,
        subscription_mgr: SubscriptionManager,
        pool: asyncpg.Pool[asyncpg.Record],
        bus: NatsClient,
        logger: BoundLogger,
        stop_timeout_seconds: float = _DEFAULT_STOP_TIMEOUT_SECONDS,
    ) -> None:
        self._subscription_mgr = subscription_mgr
        self._pool = pool
        self._bus = bus
        self._logger = logger
        self._stop_timeout = stop_timeout_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._started = False

    async def start(self, symbols: list[str]) -> None:
        """Spawn one consumer task per symbol; idempotent on second call.

        Symbols are taken at face value (canonical Bybit shape per
        :class:`SubscriptionManager`'s contract); no DB query for the
        active set — composition root owns that lookup.
        """
        if self._started:
            return
        self._started = True
        for symbol in symbols:
            self._tasks[symbol] = asyncio.create_task(
                self._consume_symbol(symbol),
                name=f"ohlc_pipeline:{symbol}",
            )
        self._logger.info("ohlc_pipeline_started", symbols=list(symbols))

    async def stop(self) -> None:
        """Cancel all consumer tasks and await termination within timeout.

        Each task's :class:`asyncio.CancelledError` propagates through
        the SubscriptionManager's ``async with`` block, releasing the
        per-symbol refcount. If the timeout fires (a task wedged in a
        non-cancellable await), the gather is abandoned — the tasks
        will continue running until the event loop shuts down. Logged
        but not raised.
        """
        if not self._started:
            return
        self._started = False
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            try:
                async with asyncio.timeout(self._stop_timeout):
                    await asyncio.gather(*self._tasks.values(), return_exceptions=True)
            except TimeoutError:
                self._logger.warning(
                    "ohlc_pipeline_stop_timeout",
                    timeout_seconds=self._stop_timeout,
                    pending=[s for s, t in self._tasks.items() if not t.done()],
                )
        self._tasks.clear()
        self._logger.info("ohlc_pipeline_stopped")

    async def _consume_symbol(self, symbol: str) -> None:
        async with self._subscription_mgr.subscribe(symbol) as feed:
            async for frame in feed:
                # `except Exception` (NOT bare `except`) keeps the task
                # alive across handler errors but lets CancelledError
                # propagate through to the `async with` exit so stop()
                # can shut the consumer down promptly.
                try:
                    await self._handle(symbol, frame)
                except Exception as exc:
                    self._logger.error(
                        "ohlc_pipeline_handler_error",
                        symbol=symbol,
                        error=str(exc),
                    )

    async def _handle(self, symbol: str, frame: dict[str, Any]) -> None:
        """Classify ``frame``; persist+publish on close, publish-only on in-progress.

        Stateless on purpose: no per-symbol last_closed cache. Dedup is
        carried by the DB PK ``(symbol, bucket_start, source)`` and the
        deterministic ``Nats-Msg-Id`` from
        :func:`message_id_for_closed_candle`. A future reader should NOT
        add an in-memory cache here — it would be restart-unsafe and
        race-prone, and T-105 backfill is the recovery path for any
        frames missed during a disconnect.
        """
        data = frame.get("data")
        if not isinstance(data, dict) or data.get("e") != "kline":
            return
        kline = data.get("k")
        if not isinstance(kline, dict):
            self._logger.warning("ohlc_pipeline_malformed_frame", symbol=symbol)
            return
        is_closed = kline.get("x")
        if not isinstance(is_closed, bool):
            self._logger.warning("ohlc_pipeline_malformed_frame", symbol=symbol)
            return
        candle = self._parse(symbol, kline, is_closed=is_closed)
        if is_closed:
            await self._persist_then_publish(candle)
        else:
            await self._publish_in_progress(candle)

    @staticmethod
    def _parse(
        symbol: str,
        kline: dict[str, Any],
        *,
        is_closed: bool,
    ) -> OhlcCandlePayload:
        """Map a Binance kline subobject to :class:`OhlcCandlePayload`.

        ``kline["t"]`` is Binance's millisecond epoch (int). OHLC and
        volume values are decimal-formatted strings; wrapping in
        :class:`Decimal` via ``str()`` defends against the rare mock
        that hands back a float and matches the T-101a REST parser
        pattern.
        """
        return OhlcCandlePayload(
            symbol=symbol,
            bucket_start=datetime.fromtimestamp(int(kline["t"]) / 1000.0, tz=UTC),
            open=Decimal(str(kline["o"])),
            high=Decimal(str(kline["h"])),
            low=Decimal(str(kline["l"])),
            close=Decimal(str(kline["c"])),
            volume=Decimal(str(kline["v"])),
            is_closed=is_closed,
        )

    async def _persist_then_publish(self, candle: OhlcCandlePayload) -> None:
        """Closed-candle path: persist first, publish on success.

        On persist failure, no publish is attempted (publish-after-persist
        contract). On publish failure, the DB row stands and downstream
        recovers via DB read at next warmup.
        """
        try:
            async with self._pool.acquire() as conn:
                await insert_ohlc_1m(
                    conn,
                    symbol=candle.symbol,
                    bucket_start=candle.bucket_start,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                    source=_SOURCE,
                )
        except Exception as exc:
            self._logger.error(
                "ohlc_pipeline_persist_error",
                symbol=candle.symbol,
                bucket_start=candle.bucket_start.isoformat(),
                error=str(exc),
            )
            return
        envelope = self._build_envelope(
            candle,
            message_id=message_id_for_closed_candle(candle.symbol, candle.bucket_start),
        )
        await self._publish(envelope, candle, is_closed=True)

    async def _publish_in_progress(self, candle: OhlcCandlePayload) -> None:
        """In-progress path: publish only, no DB write, fresh uuid4 envelope."""
        envelope = self._build_envelope(candle, message_id=None)
        await self._publish(envelope, candle, is_closed=False)

    async def _publish(
        self,
        envelope: MessageEnvelope,
        candle: OhlcCandlePayload,
        *,
        is_closed: bool,
    ) -> None:
        try:
            await self._bus.publish(self._subject(candle.symbol), envelope)
        except Exception as exc:
            self._logger.error(
                "ohlc_pipeline_publish_error",
                symbol=candle.symbol,
                bucket_start=candle.bucket_start.isoformat(),
                is_closed=is_closed,
                error=str(exc),
            )

    @staticmethod
    def _subject(symbol: str) -> str:
        return f"{_SUBJECT_PREFIX}.{symbol}"

    def _build_envelope(
        self,
        candle: OhlcCandlePayload,
        *,
        message_id: UUID | None,
    ) -> MessageEnvelope:
        """Construct the §8.3 envelope around a candle payload.

        ``message_id=None`` lets :class:`MessageEnvelope` mint a fresh
        :func:`uuid.uuid4` (used for in-progress publishes — every tick
        is its own message). For closed candles, callers pass the
        deterministic UUID5 from
        :func:`message_id_for_closed_candle` so JetStream's
        ``duplicate_window`` (when configured on MARKET_OHLC; see
        TASKS.md F1+) dedups re-published closed candles server-side.
        """
        payload = candle.model_dump(mode="json")
        correlation_id = CorrelationId(
            f"ohlc:{candle.symbol}:{candle.bucket_start.isoformat()}",
        )
        if message_id is None:
            return MessageEnvelope(
                correlation_id=correlation_id,
                publisher=_PUBLISHER,
                payload=payload,
            )
        return MessageEnvelope(
            message_id=message_id,
            correlation_id=correlation_id,
            publisher=_PUBLISHER,
            payload=payload,
        )
