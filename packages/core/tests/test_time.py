"""Test packages.core.time — now_utc shape and clock-injection contract."""

from __future__ import annotations

from datetime import UTC, datetime

from packages.core.time import _testing_set_clock, now_utc

_FIXED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_now_utc_returns_aware_utc_datetime() -> None:
    t = now_utc()
    assert isinstance(t, datetime)
    assert t.tzinfo is UTC


def test_set_clock_redirects_now_utc() -> None:
    _testing_set_clock(lambda: _FIXED)
    assert now_utc() == _FIXED


def test_clock_auto_resets_after_prior_test_set_a_fake() -> None:
    """The autouse `reset_clock` fixture in conftest must have restored the real clock."""
    assert now_utc() != _FIXED
