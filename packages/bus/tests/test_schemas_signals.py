"""§N4 unit tests for :mod:`packages.bus.schemas.signals` (T-310a SignalRejected scope).

T-015b1 SignalValidated has dedicated coverage in
``services/signal_gateway/tests/test_models.py``; this module covers
``SignalRejected`` (T-310a) and the ``subject_for_signals_rejected``
helper newly shipped for §9.4:1541 shadow tracking.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from packages.bus.schemas import (
    SignalRejected,
    subject_for_signals_rejected,
)


def _utc_now() -> datetime:
    return datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def test_signal_rejected_round_trip() -> None:
    """Happy path: all fields populated; model_dump → model_validate round-trip exact."""
    payload = SignalRejected(
        bot_id="alpha",
        signal_id=42,
        idempotency_key="key-1",
        symbol="BTCUSDT",
        action="LONG",
        reason="score_below_threshold",
        total_score=0.5,
        threshold=1.0,
        rejected_at=_utc_now(),
    )
    dumped = payload.model_dump(mode="json")
    reconstructed = SignalRejected.model_validate(dumped)
    assert reconstructed == payload
    assert reconstructed.decision == "reject"
    assert reconstructed.schema_version == "1.0"


def test_signal_rejected_rejects_naive_datetime() -> None:
    """Naive (no tzinfo) ``rejected_at`` raises ValidationError."""
    naive = datetime(2026, 5, 2, 12, 0, 0)  # noqa: DTZ001 — intentional for naive-datetime rejection test
    with pytest.raises(ValidationError, match=r"timezone-aware"):
        SignalRejected(
            bot_id="alpha",
            signal_id=1,
            idempotency_key="k",
            symbol="BTCUSDT",
            action="LONG",
            reason="r",
            total_score=0.0,
            threshold=1.0,
            rejected_at=naive,
        )


def test_signal_rejected_rejects_non_utc_offset() -> None:
    """+05:00 offset (non-UTC) raises ValidationError."""
    plus5 = datetime(2026, 5, 2, 17, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    with pytest.raises(ValidationError, match=r"UTC"):
        SignalRejected(
            bot_id="alpha",
            signal_id=1,
            idempotency_key="k",
            symbol="BTCUSDT",
            action="LONG",
            reason="r",
            total_score=0.0,
            threshold=1.0,
            rejected_at=plus5,
        )


def test_signal_rejected_serializes_utc_iso_with_explicit_offset() -> None:
    """`rejected_at.isoformat()` ends with `+00:00` per §5.12."""
    payload = SignalRejected(
        bot_id="alpha",
        signal_id=1,
        idempotency_key="k",
        symbol="BTCUSDT",
        action="LONG",
        reason="r",
        total_score=0.0,
        threshold=1.0,
        rejected_at=_utc_now(),
    )
    dumped = payload.model_dump(mode="json")
    assert dumped["rejected_at"].endswith("+00:00")


def test_subject_for_signals_rejected_format() -> None:
    """Helper builds `signals.rejected.<bot_id>` per L-002 active control."""
    assert subject_for_signals_rejected("alpha") == "signals.rejected.alpha"
    assert subject_for_signals_rejected("beta-2") == "signals.rejected.beta-2"


def test_subject_for_signals_rejected_imported_via_package_root() -> None:
    """Helper re-exported from ``packages.bus.schemas`` __init__.py."""
    from packages.bus.schemas import subject_for_signals_rejected as helper

    assert helper("zeta") == "signals.rejected.zeta"
