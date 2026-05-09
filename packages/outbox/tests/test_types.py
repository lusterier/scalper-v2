"""§N4 unit tests for :mod:`packages.outbox.types` (T-537a1)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest

from packages.outbox.types import OutboxEvent, OutboxRelaySettings

_FIXED_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


def test_outbox_event_field_order_matches_spec() -> None:
    """Field order matches DB column order for ergonomic ``**dict(row)`` ctor."""
    expected = (
        "id",
        "service",
        "subject",
        "correlation_id",
        "payload",
        "created_at",
        "published_at",
        "attempt_count",
        "last_attempt_at",
        "last_error",
        "failed_at",
    )
    assert tuple(f.name for f in fields(OutboxEvent)) == expected


def test_outbox_event_is_frozen() -> None:
    """Read-only projection — mutation attempts must raise."""
    event = OutboxEvent(
        id=1,
        service="signal_gateway",
        subject="signals.validated",
        correlation_id="cid-1",
        payload={"k": "v"},
        created_at=_FIXED_NOW,
        published_at=None,
        attempt_count=0,
        last_attempt_at=None,
        last_error=None,
        failed_at=None,
    )
    with pytest.raises(FrozenInstanceError):
        event.id = 2  # type: ignore[misc]


def test_outbox_event_uses_slots() -> None:
    """slots=True — no __dict__ allocation."""
    event = OutboxEvent(
        id=1,
        service="signal_gateway",
        subject="signals.validated",
        correlation_id=None,
        payload={},
        created_at=_FIXED_NOW,
        published_at=None,
        attempt_count=0,
        last_attempt_at=None,
        last_error=None,
        failed_at=None,
    )
    assert not hasattr(event, "__dict__")


def test_outbox_relay_settings_defaults() -> None:
    """Default values match plan spec; field-level constraints honored."""
    settings = OutboxRelaySettings()
    assert settings.poll_interval_s == 1.0
    assert settings.batch_size == 100
    assert settings.max_attempts == 100
    assert settings.backoff_base_s == 2.0
    assert settings.backoff_cap_s == 60.0


def test_outbox_relay_settings_env_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env prefix OUTBOX_RELAY_* maps to fields."""
    monkeypatch.setenv("OUTBOX_RELAY_POLL_INTERVAL_S", "2.5")
    monkeypatch.setenv("OUTBOX_RELAY_BATCH_SIZE", "50")
    monkeypatch.setenv("OUTBOX_RELAY_MAX_ATTEMPTS", "200")
    monkeypatch.setenv("OUTBOX_RELAY_BACKOFF_BASE_S", "1.5")
    monkeypatch.setenv("OUTBOX_RELAY_BACKOFF_CAP_S", "120.0")
    settings = OutboxRelaySettings()
    assert settings.poll_interval_s == 2.5
    assert settings.batch_size == 50
    assert settings.max_attempts == 200
    assert settings.backoff_base_s == 1.5
    assert settings.backoff_cap_s == 120.0


def test_outbox_relay_settings_rejects_cap_below_base() -> None:
    """field_validator enforces backoff_cap_s >= backoff_base_s."""
    with pytest.raises(ValueError, match="must be >= backoff_base_s"):
        OutboxRelaySettings(backoff_base_s=10.0, backoff_cap_s=5.0)


def test_outbox_relay_settings_rejects_zero_poll_interval() -> None:
    """gt=0.0 constraint on poll_interval_s."""
    with pytest.raises(ValueError, match="greater than 0"):
        OutboxRelaySettings(poll_interval_s=0.0)


def test_outbox_relay_settings_rejects_zero_max_attempts() -> None:
    """ge=1 constraint on max_attempts."""
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        OutboxRelaySettings(max_attempts=0)
