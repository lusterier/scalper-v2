"""Binance REST client (§9.2 backfill, §3.1 httpx).

T-101 ships the single endpoint that F1 actually consumes:

* :meth:`BinanceRestClient.get_klines` — historical kline (OHLC)
  fetch, used by T-105 (market-data-svc backfill on startup +
  reconnect-resync).

`get_exchange_info` and other endpoints are deliberately **not**
shipped here per §0.8 — no concrete F1 consumer. Brief §9.2 cites
`bots`+`bot_configs` (DB join) as the active-symbol source-of-truth,
not Binance's exchange info. Add when a caller needs them.

Error mapping: 4xx/5xx HTTP responses raise :class:`BinanceRestError`
with the Binance JSON ``{code, msg}`` parsed into
:attr:`api_code`/:attr:`api_message` when present. Transport-level
failures (DNS, connection refused, timeout) raise the same error
class, ``api_code=None``, with the underlying :exc:`httpx.HTTPError`
chained as ``__cause__``.

Lifecycle: instantiate, use, ``await client.close()``. Also usable
as ``async with BinanceRestClient(...) as c: ...`` — the context
manager closes the underlying httpx pool on exit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Self, cast

import httpx

from .errors import BinanceRestError

if TYPE_CHECKING:
    from types import TracebackType


__all__ = ["BinanceRestClient", "OhlcCandle"]


_DEFAULT_BASE_URL = "https://api.binance.com"
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
_DEFAULT_READ_TIMEOUT_SECONDS = 10.0
_DEFAULT_KLINES_LIMIT = 500
_KLINES_PATH = "/api/v3/klines"


@dataclass(frozen=True, slots=True)
class OhlcCandle:
    """A single closed OHLC bucket — the shape persisted to ``ohlc_1m``.

    Prices and volume are :class:`~decimal.Decimal` to preserve the
    full precision of Binance's string-encoded numerics; the §7.2
    ``ohlc_1m`` schema uses ``NUMERIC(30, 12)``, and asyncpg yields
    Decimal for that column type — keeping the wire/DB types aligned
    avoids float-rounding drift across the persistence boundary.
    """

    symbol: str
    bucket_start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str = "binance"


class BinanceRestClient:
    """Async wrapper around ``httpx.AsyncClient`` for Binance REST."""

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout: float = _DEFAULT_READ_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=read_timeout,
                pool=read_timeout,
            ),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = _DEFAULT_KLINES_LIMIT,
    ) -> list[OhlcCandle]:
        """Fetch historical klines for ``symbol`` at ``interval``.

        ``start_time`` / ``end_time`` are inclusive endpoints; when both
        omitted Binance returns the most recent ``limit`` candles. Times
        are interpreted as UTC and serialized as Binance-style millisecond
        epochs.
        """
        params: dict[str, str | int] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time is not None:
            params["startTime"] = _to_millis(start_time)
        if end_time is not None:
            params["endTime"] = _to_millis(end_time)

        try:
            response = await self._client.get(_KLINES_PATH, params=params)
        except httpx.HTTPError as exc:
            raise BinanceRestError(
                f"Binance REST transport error on {_KLINES_PATH}: {exc}",
            ) from exc

        if response.status_code >= 400:
            raise _api_error(response)

        return [_parse_kline_row(symbol, row) for row in response.json()]


def _to_millis(when: datetime) -> int:
    """Serialize a UTC ``datetime`` to Binance's millisecond epoch."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return int(when.timestamp() * 1000)


def _parse_kline_row(symbol: str, row: list[object]) -> OhlcCandle:
    """Map one element of Binance's kline-array response to :class:`OhlcCandle`.

    Binance's kline format is a 12-element heterogeneous array
    (timestamps as int millis, prices/volumes as decimal-formatted
    strings). T-101 only consumes the first six fields — open time,
    open, high, low, close, volume — which are exactly what `ohlc_1m`
    persists per §7.2.
    """
    open_time_ms = cast("int", row[0])
    return OhlcCandle(
        symbol=symbol,
        bucket_start=datetime.fromtimestamp(open_time_ms / 1000.0, tz=UTC),
        open=Decimal(str(row[1])),
        high=Decimal(str(row[2])),
        low=Decimal(str(row[3])),
        close=Decimal(str(row[4])),
        volume=Decimal(str(row[5])),
    )


def _api_error(response: httpx.Response) -> BinanceRestError:
    """Build a :class:`BinanceRestError` from a non-2xx Binance response.

    Tries to parse the JSON ``{code, msg}`` body that Binance returns for
    documented errors; falls back to a status-only message when the
    body is not JSON or doesn't carry the expected fields.
    """
    api_code: int | None = None
    api_message: str | None = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        raw_code = body.get("code")
        if isinstance(raw_code, int):
            api_code = raw_code
        raw_msg = body.get("msg")
        if isinstance(raw_msg, str):
            api_message = raw_msg

    summary = (
        f"Binance REST {response.request.method} {response.request.url.path} "
        f"-> {response.status_code}"
    )
    if api_code is not None or api_message is not None:
        summary = f"{summary} (code={api_code}, msg={api_message!r})"

    return BinanceRestError(summary, api_code=api_code, api_message=api_message)
