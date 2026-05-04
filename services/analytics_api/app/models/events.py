"""SSE event-type whitelist + query-param parser for ``/events/stream`` (T-408).

5 event types per BRIEF §9.6:1633-1638 + OQ-1=A:

* ``positions`` — open-position lifecycle (close events + SL move indicators)
* ``signals`` — validated inbound signals
* ``trades`` — trade lifecycle (placed + filled)
* ``scoring`` — rejected signals across all bots (shadow tracking)
* ``alerts`` — alert-worthy events (alerting-svc consumer; T-409)

Unknown types in ``?types=`` → 422 (per WG#8 exact error string).
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["EventType", "parse_types"]


class EventType(StrEnum):
    """SSE event type whitelist (T-408).

    UI subscribes via comma-separated ``?types=`` query param. Each type maps
    to one or more NATS subjects (server-side mapping in
    :mod:`services.analytics_api.app.sse`). F4 ships 5 types; F5+ may extend.
    """

    POSITIONS = "positions"
    SIGNALS = "signals"
    TRADES = "trades"
    SCORING = "scoring"
    ALERTS = "alerts"


_ALLOWED_TYPES_STR = ", ".join(t.value for t in EventType)


def parse_types(raw: str) -> frozenset[EventType]:
    """Parse comma-separated ``?types=`` query value → frozenset[EventType].

    Empty / whitespace-only → ValueError (caller maps to 422 per WG#8).
    Unknown type token → ValueError with detail enumerating allowed types.
    Duplicate tokens silently dedup via frozenset semantics (Edge case #3).
    """
    stripped = raw.strip()
    if not stripped:
        raise ValueError("types query param is required and non-empty")
    parsed: set[EventType] = set()
    for token in stripped.split(","):
        t = token.strip()
        if not t:
            continue
        try:
            parsed.add(EventType(t))
        except ValueError as exc:
            raise ValueError(f"unknown event type {t!r}; allowed: {_ALLOWED_TYPES_STR}") from exc
    if not parsed:
        raise ValueError("types query param is required and non-empty")
    return frozenset(parsed)
