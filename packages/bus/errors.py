"""Exception hierarchy for :mod:`packages.bus` (§5.4).

All errors raised by this package inherit from :class:`BusError`,
which itself inherits from :class:`packages.core.ScalperError`, so
callers can narrow to ``except BusError`` or broaden to
``except ScalperError`` without importing ``nats``.
"""

from __future__ import annotations

from packages.core import ScalperError

__all__ = ["BusError", "NotConnectedError"]


class BusError(ScalperError):
    """Base class for errors raised by :mod:`packages.bus`."""


class NotConnectedError(BusError):
    """Raised when a publish/subscribe is attempted before :meth:`connect`."""
