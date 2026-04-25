"""Binance market-data primitives (§9.2, §3.1).

Public surface:

* :class:`BinanceRestClient` — async wrapper around ``httpx`` for the
  Binance REST endpoints market-data-svc consumes (kline backfill).
* :class:`OhlcCandle` — closed-bucket dataclass returned by
  :meth:`BinanceRestClient.get_klines`; aligned with the §7.2
  ``ohlc_1m`` schema (Decimal prices/volume).
* :class:`BinanceWsClient` — async wrapper around ``websockets`` with
  the §9.2 reconnect contract (H-007 exp backoff 1s→60s, full jitter,
  long-disconnect signal).
* :class:`ConnectionState` — lifecycle enum for readiness probes.
* Error hierarchy rooted at :class:`MarketError`
  (a :class:`~packages.core.ScalperError`).

The lower-level :func:`exp_backoff_delays` is also re-exported for
callers that want to drive their own backoff loop with the same
sequence (e.g. T-105 reconnect resync).
"""

from __future__ import annotations

from .backoff import exp_backoff_delays
from .errors import (
    BinanceRestError,
    BinanceWsError,
    MarketError,
    NotConnectedError,
)
from .rest import BinanceRestClient, OhlcCandle
from .ws import BinanceWsClient, ConnectionState

__all__ = [
    "BinanceRestClient",
    "BinanceRestError",
    "BinanceWsClient",
    "BinanceWsError",
    "ConnectionState",
    "MarketError",
    "NotConnectedError",
    "OhlcCandle",
    "exp_backoff_delays",
]
