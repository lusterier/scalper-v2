"""Key-based secret redactor (§16.1).

A structlog processor that walks the event dict and replaces values
whose key name matches a secret-like pattern. Matching is by key name
only — no value heuristics. Key names and patterns are normalized by
lowercasing and replacing ``-`` with ``_`` before substring match, so
``X-API-Key`` and ``api_key`` both match the ``api_key`` pattern.

Recursion is bounded to ``_MAX_DEPTH`` levels (operator-approved cap).
Values deeper than that are passed through untouched — log lines should
not nest that deep, and the cap bounds per-record cost.

The pattern list is module-global and mutable via ``add_redacted_keys``
so services can register their own secret field names at startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from structlog.types import EventDict, WrappedLogger

__all__ = ["add_redacted_keys", "redactor"]


_DEFAULT_REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "api_secret",
        "authorization",
        "bearer",
        "cookie",
        "hmac",
        "password",
        "private_key",
        "secret",
        "token",
    },
)

_redacted_keys: set[str] = set(_DEFAULT_REDACTED_KEYS)

_REDACTED_PLACEHOLDER = "***"
_MAX_DEPTH = 8


def _normalize(name: str) -> str:
    return name.lower().replace("-", "_")


def add_redacted_keys(*names: str) -> None:
    """Extend the redactor pattern list with additional key names.

    Patterns match case-insensitively as substrings against normalized
    keys (lowercased, ``-`` → ``_``). Safe to call multiple times.
    """
    for name in names:
        _redacted_keys.add(_normalize(name))


def _reset_redacted_keys() -> None:
    """Restore the default pattern list. **TEST USE ONLY.**"""
    _redacted_keys.clear()
    _redacted_keys.update(_DEFAULT_REDACTED_KEYS)


def _should_redact(key: str) -> bool:
    norm = _normalize(key)
    return any(pattern in norm for pattern in _redacted_keys)


def _walk_value(value: Any, depth: int) -> Any:
    if depth >= _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return _walk_mapping(value, depth)
    if isinstance(value, list):
        return [_walk_value(item, depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_walk_value(item, depth + 1) for item in value)
    return value


def _walk_mapping(d: EventDict, depth: int) -> EventDict:
    if depth >= _MAX_DEPTH:
        return d
    out: EventDict = {}
    for k, v in d.items():
        if isinstance(k, str) and _should_redact(k):
            out[k] = _REDACTED_PLACEHOLDER
        else:
            out[k] = _walk_value(v, depth + 1)
    return out


def redactor(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """structlog processor: redact secret-like keys at any nesting depth."""
    return _walk_mapping(event_dict, depth=0)
