"""OHLC backfill via Binance REST ``/api/v3/klines`` (§9.2 lines 1464-1471).

:class:`OhlcBackfill` plugs the gap between what's already in
``ohlc_1m`` and the current minute by paging the Binance REST
``/api/v3/klines`` endpoint. It runs at two moments in the
market-data-svc lifecycle:

* **Startup** — the composition root (T-100 lifespan) wires it as the
  ``BinanceWsClient`` ``on_connect`` callback, so the very first
  successful WS connect triggers a gap-fill before relying purely on
  live frames.
* **After every reconnect** — same callback fires; any minutes missed
  during the disconnect window get repaired.

Idempotency rides on the existing ``insert_ohlc_1m`` write
(:mod:`packages.db.queries.market_data`), which uses
``ON CONFLICT (symbol, bucket_start, source) DO UPDATE``. Backfill
adds no new external write, so no new ``@idempotent`` /
``@non_idempotent`` decision is introduced (§N3).

Per-symbol seriality with a small inter-symbol pause (50 ms by default)
keeps the Binance public REST endpoint comfortably below the documented
weight limits for our sub-10-bot scale, and avoids hammering the API
when many symbols all reconnect at once.

Per-symbol error isolation: REST or DB failure on one symbol logs and
moves to the next — one bad symbol must not freeze the rest of the
universe. ``except Exception`` (NOT bare ``except``) so
:class:`asyncio.CancelledError` still propagates up the lifespan when
the service shuts down mid-run.

The end-time floor is one minute before ``now`` rounded down to the
minute boundary; this excludes the in-progress current minute, which
the WS path will deliver as the ``x=true`` closed-bucket frame at the
next tick. Without this floor the REST endpoint would return a
"closed" candle for the current minute that has not actually closed
yet, polluting ``ohlc_1m`` with under-reported OHLC.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from packages.core import now_utc
from packages.db.queries.market_data import (
    fetch_latest_ohlc_bucket,
    insert_ohlc_1m,
)

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg
    from structlog.stdlib import BoundLogger

    from .rest import BinanceRestClient, OhlcCandle


__all__ = ["OhlcBackfill"]


_SOURCE = "binance"
_INTERVAL = "1m"
_REST_PAGE_LIMIT = 1000
_DEFAULT_INITIAL_HOURS = 24
_DEFAULT_PER_SYMBOL_PAUSE_SECONDS = 0.05


class OhlcBackfill:
    """Coordinates per-symbol gap-fill from Binance REST into ``ohlc_1m``."""

    def __init__(
        self,
        *,
        rest: BinanceRestClient,
        pool: asyncpg.Pool[asyncpg.Record],
        logger: BoundLogger,
        initial_hours: int = _DEFAULT_INITIAL_HOURS,
        per_symbol_pause_seconds: float = _DEFAULT_PER_SYMBOL_PAUSE_SECONDS,
    ) -> None:
        self._rest = rest
        self._pool = pool
        self._logger = logger
        self._initial_hours = initial_hours
        self._per_symbol_pause = per_symbol_pause_seconds

    async def run_for_symbols(self, symbols: list[str]) -> None:
        """Backfill each symbol sequentially, pausing briefly between them.

        Empty ``symbols`` is a no-op (no Binance call, no log noise).
        Any per-symbol error is caught and logged so the loop can move
        on; :class:`asyncio.CancelledError` is intentionally NOT caught
        — service shutdown must propagate.
        """
        if not symbols:
            return
        total = 0
        for index, symbol in enumerate(symbols):
            try:
                total += await self._backfill_symbol(symbol)
            except Exception as exc:
                self._logger.error(
                    "ohlc_backfill_symbol_error",
                    symbol=symbol,
                    error=str(exc),
                )
            if index < len(symbols) - 1 and self._per_symbol_pause > 0:
                await asyncio.sleep(self._per_symbol_pause)
        self._logger.info(
            "ohlc_backfill_completed",
            symbols=list(symbols),
            total_candles=total,
        )

    async def _backfill_symbol(self, symbol: str) -> int:
        """Fetch + persist all missing 1m candles for ``symbol``.

        Returns the number of candles persisted (sum across pages).
        """
        end_time = self._end_time()
        start_time = await self._resolve_start_time(symbol, end_time=end_time)
        if start_time >= end_time:
            self._logger.info(
                "ohlc_backfill_no_gap",
                symbol=symbol,
                last_seen=start_time.isoformat(),
            )
            return 0

        self._logger.info(
            "ohlc_backfill_started",
            symbol=symbol,
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )
        persisted = 0
        cursor = start_time
        while cursor < end_time:
            candles = await self._rest.get_klines(
                symbol,
                _INTERVAL,
                start_time=cursor,
                end_time=end_time,
                limit=_REST_PAGE_LIMIT,
            )
            if not candles:
                break
            await self._persist_page(candles)
            persisted += len(candles)
            self._logger.info(
                "ohlc_backfill_progress",
                symbol=symbol,
                count=len(candles),
                last_bucket=candles[-1].bucket_start.isoformat(),
            )
            cursor = candles[-1].bucket_start + timedelta(minutes=1)
        return persisted

    async def _resolve_start_time(self, symbol: str, *, end_time: datetime) -> datetime:
        """Return the start-time cursor for the page-loop.

        ``last_bucket + 1m`` when the symbol has prior rows; otherwise
        ``end_time - initial_hours`` (cold-start default per the
        operator's 24h baseline). The ``end_time`` upper bound is passed
        in so callers cannot accidentally diverge between resolution
        and the loop.
        """
        async with self._pool.acquire() as conn:
            last_bucket = await fetch_latest_ohlc_bucket(
                conn,
                symbol=symbol,
                source=_SOURCE,
            )
        if last_bucket is None:
            return end_time - timedelta(hours=self._initial_hours)
        return last_bucket + timedelta(minutes=1)

    async def _persist_page(self, candles: list[OhlcCandle]) -> None:
        """Persist one REST page to ``ohlc_1m`` via ``insert_ohlc_1m``.

        Single ``pool.acquire()`` for the whole page — fewer connection
        round-trips than per-row acquire, and at 1000 rows / page this
        is well within asyncpg's per-connection statement budget.
        """
        async with self._pool.acquire() as conn:
            for candle in candles:
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

    @staticmethod
    def _end_time() -> datetime:
        """Floor: one minute before ``now``, rounded down to the minute boundary.

        Excludes the in-progress current minute (REST would return an
        un-closed candle for it). The WS path delivers the in-progress
        minute as a closed-bucket frame at the next tick.
        """
        floor = now_utc().replace(second=0, microsecond=0)
        return floor - timedelta(minutes=1)
