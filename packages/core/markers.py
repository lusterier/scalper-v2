"""Idempotency markers and the registry that backs CI enforcement (§5.8).

`@idempotent` and `@non_idempotent` set a public attribute on the
decorated function (`__idempotent__` / `__non_idempotent__`) and add an
entry to a module-level set keyed by the function's fully-qualified
name (`module.qualname`). Keying by qualname rather than the function
object means re-imports during test collection don't multiply entries.

The CI helper `assert_all_methods_labeled(cls)` walks `cls`'s own
public methods (skipping private `_*`, dunder, and inherited) and
raises `UnlabeledMethodError` if any are unlabeled. Used to enforce
hazard H-003: every external side-effect method on `ExchangeClient`
implementations must declare its retry semantics.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from .errors import ScalperError

__all__ = [
    "UnlabeledMethodError",
    "assert_all_methods_labeled",
    "idempotent",
    "is_idempotent",
    "is_non_idempotent",
    "non_idempotent",
]


_IDEMPOTENT: set[str] = set()
_NON_IDEMPOTENT: set[str] = set()


def _key(func: Callable[..., Any]) -> str:
    return f"{func.__module__}.{func.__qualname__}"


class UnlabeledMethodError(ScalperError):
    """Raised by `assert_all_methods_labeled` when a public method lacks a marker."""


def idempotent[F: Callable[..., Any]](func: F) -> F:
    """Mark a function as idempotent (safe to retry).

    See §5.8 / §N3. CI verifies every `ExchangeClient` method is labeled.
    """
    func.__idempotent__ = True  # type: ignore[attr-defined]
    _IDEMPOTENT.add(_key(func))
    return func


def non_idempotent[F: Callable[..., Any]](func: F) -> F:
    """Mark a function as non-idempotent (no retry on timeout).

    See §5.8 / §N3 / hazard H-003.
    """
    func.__non_idempotent__ = True  # type: ignore[attr-defined]
    _NON_IDEMPOTENT.add(_key(func))
    return func


def is_idempotent(func: Callable[..., Any]) -> bool:
    return _key(func) in _IDEMPOTENT


def is_non_idempotent(func: Callable[..., Any]) -> bool:
    return _key(func) in _NON_IDEMPOTENT


def assert_all_methods_labeled(cls: type) -> None:
    """Raise `UnlabeledMethodError` if any public method on `cls` is unlabeled.

    Walks methods defined on `cls` itself (not inherited); skips dunder
    and any name starting with `_`. `@staticmethod` and `@classmethod`
    are unwrapped to their underlying function before lookup.
    """
    unlabeled: list[str] = []
    for name, _member in inspect.getmembers(cls):
        if name.startswith("_"):
            continue
        if name not in cls.__dict__:
            continue
        raw = cls.__dict__[name]
        func = raw.__func__ if isinstance(raw, (classmethod, staticmethod)) else raw
        if not callable(func):
            continue
        if is_idempotent(func) or is_non_idempotent(func):
            continue
        unlabeled.append(f"{cls.__qualname__}.{name}")
    if unlabeled:
        raise UnlabeledMethodError(f"unlabeled methods: {', '.join(unlabeled)}")
