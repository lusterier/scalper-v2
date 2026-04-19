"""Root of the scalper-v2 exception hierarchy (§5.4).

Service- and package-level error families (e.g., `ExchangeError` and its
children in §11.3) live with their owners and inherit from `ScalperError`,
so callers can `except ScalperError` to catch any project error without
importing modules they don't otherwise depend on.
"""

from __future__ import annotations

__all__ = ["ScalperError"]


class ScalperError(Exception):
    """Root of the scalper-v2 exception hierarchy."""
