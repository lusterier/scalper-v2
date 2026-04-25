"""Exception hierarchy for :mod:`packages.market` (§5.4).

All errors raised by this package inherit from :class:`MarketError`,
which itself inherits from :class:`packages.core.ScalperError`, so
callers can narrow to ``except MarketError`` or broaden to
``except ScalperError`` without importing ``websockets`` or
``httpx``.
"""

from __future__ import annotations

from packages.core import ScalperError

__all__ = [
    "BinanceRestError",
    "BinanceWsError",
    "MarketError",
    "NotConnectedError",
]


class MarketError(ScalperError):
    """Base class for errors raised by :mod:`packages.market`."""


class NotConnectedError(MarketError):
    """Raised when an operation requires a connected WS but none is open."""


class BinanceWsError(MarketError):
    """Raised on Binance WebSocket protocol or transport failure.

    Wraps the underlying :mod:`websockets` exception
    (``ConnectionClosed``, ``ConnectionClosedError``, ``InvalidStatusCode``,
    …) or a JSON-decode error on a received frame as ``__cause__``.
    """


class BinanceRestError(MarketError):
    """Raised when a Binance REST call returns a non-2xx response.

    Wraps the :class:`httpx.HTTPStatusError` (or transport
    :class:`httpx.HTTPError`) as ``__cause__``. Binance's JSON error
    body — when present — is exposed via :attr:`api_code` /
    :attr:`api_message` for log-level triage.
    """

    def __init__(
        self,
        message: str,
        *,
        api_code: int | None = None,
        api_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.api_code = api_code
        self.api_message = api_message
