"""UTC time helper (§5.12 / §N1).

`now_utc()` is the only approved way to obtain the current time in
production code. Direct use of `datetime.now()`, `datetime.utcnow()`,
or `time.time()` for wall-clock semantics is banned by the brief; ruff
`DTZ` rules enforce the datetime calls.

The `_testing_*` helpers exist so test code can install a fake clock
without coupling production paths to dependency injection. The leading
underscore plus `_testing_` segment is the loud signal: production code
MUST NOT call them. The pytest fixture in `packages/core/tests/conftest.py`
auto-resets the clock between tests so swap-then-forget can't leak.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["now_utc"]


def _real_now_utc() -> datetime:
    return datetime.now(tz=UTC)


_now_fn: Callable[[], datetime] = _real_now_utc


def now_utc() -> datetime:
    """Return the current UTC datetime (timezone-aware, `tzinfo=UTC`)."""
    return _now_fn()


def _testing_set_clock(fn: Callable[[], datetime]) -> None:
    """Install a fake clock. **TEST USE ONLY.**

    Production code must call `now_utc()` directly. Tests should rely on
    the `_reset_clock` autouse fixture in `conftest.py` to clean up.
    """
    global _now_fn
    _now_fn = fn


def _testing_reset_clock() -> None:
    """Restore the real wall clock. **TEST USE ONLY.**"""
    global _now_fn
    _now_fn = _real_now_utc
