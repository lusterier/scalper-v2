"""Binance market-data primitives (§9.2, §3.1).

Public surface (T-101a slice — REST + scaffold):

* :class:`BinanceRestClient` — async wrapper around ``httpx`` for the
  Binance REST endpoints market-data-svc consumes (kline backfill).
* :class:`OhlcCandle` — closed-bucket dataclass returned by
  :meth:`BinanceRestClient.get_klines`; aligned with the §7.2
  ``ohlc_1m`` schema (Decimal prices/volume).
* Error hierarchy rooted at :class:`MarketError`
  (a :class:`~packages.core.ScalperError`). :class:`BinanceWsError`
  is defined here so T-101b can extend the taxonomy without touching
  ``errors.py`` again.

The lower-level :func:`exp_backoff_delays` is also re-exported for
callers that want to drive their own backoff loop with the same
sequence (e.g. T-105 reconnect resync, T-101b WS reconnect).

T-101b will land :class:`BinanceWsClient` + :class:`ConnectionState`
on a follow-up branch.
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

__all__ = [
    "BinanceRestClient",
    "BinanceRestError",
    "BinanceWsError",
    "MarketError",
    "NotConnectedError",
    "OhlcCandle",
    "exp_backoff_delays",
]
