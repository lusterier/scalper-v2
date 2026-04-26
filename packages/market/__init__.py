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
* :class:`SubscriptionManager` — refcounted facade over
  :class:`BinanceWsClient` per H-014; per-symbol context-manager API
  yielding a :class:`SymbolFeed` async iterator of multiplex frames.
* :class:`OhlcPipeline` — closed-bucket detection + persist + publish
  loop driven off SubscriptionManager feeds (T-104b).
* :class:`OhlcBackfill` — REST-based gap-fill on startup + after every
  WS reconnect (T-105). Wired as the :class:`BinanceWsClient`
  ``on_connect`` callback by the composition root.
* Error hierarchy rooted at :class:`MarketError`
  (a :class:`~packages.core.ScalperError`).

The lower-level :func:`exp_backoff_delays` is also re-exported for
callers that want to drive their own backoff loop with the same
sequence (e.g. T-105 reconnect resync).
"""

from __future__ import annotations

from .backfill import OhlcBackfill
from .backoff import exp_backoff_delays
from .errors import (
    BinanceRestError,
    BinanceWsError,
    MarketError,
    NotConnectedError,
)
from .ohlc import OhlcPipeline
from .rest import BinanceRestClient, OhlcCandle
from .subscription import SubscriptionManager, SymbolFeed
from .ws import BinanceWsClient, ConnectionState

__all__ = [
    "BinanceRestClient",
    "BinanceRestError",
    "BinanceWsClient",
    "BinanceWsError",
    "ConnectionState",
    "MarketError",
    "NotConnectedError",
    "OhlcBackfill",
    "OhlcCandle",
    "OhlcPipeline",
    "SubscriptionManager",
    "SymbolFeed",
    "exp_backoff_delays",
]
