"""Unit tests for :class:`packages.bus.MessageEnvelope` (§8.3)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from packages.bus import MessageEnvelope
from packages.core import CorrelationId

_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _minimal(**overrides: object) -> MessageEnvelope:
    """Construct an envelope with required fields filled, overridable."""
    kwargs: dict[str, object] = {
        "correlation_id": CorrelationId("cid-1"),
        "publisher": "signal-gateway",
        "payload": {"action": "LONG"},
    }
    kwargs.update(overrides)
    return MessageEnvelope(**kwargs)  # type: ignore[arg-type]


def test_defaults_filled_when_absent() -> None:
    env = _minimal()
    assert env.schema_version == "1.0"
    assert isinstance(env.message_id, uuid.UUID)
    assert env.message_id.version == 4
    assert env.published_at.tzinfo is UTC


def test_required_fields_raise_when_missing() -> None:
    with pytest.raises(ValidationError) as exc_info:
        MessageEnvelope()  # type: ignore[call-arg]
    missing = {err["loc"][0] for err in exc_info.value.errors() if err["type"] == "missing"}
    assert missing == {"correlation_id", "publisher", "payload"}


def test_message_id_is_unique_per_instance() -> None:
    a = _minimal()
    b = _minimal()
    assert a.message_id != b.message_id


def test_correlation_id_accepts_plain_str() -> None:
    """CorrelationId is a NewType over str — runtime accepts any str."""
    env = _minimal(correlation_id="cid-from-raw-str")
    assert env.correlation_id == "cid-from-raw-str"
    assert isinstance(env.correlation_id, str)


def test_correlation_id_rejects_non_str() -> None:
    with pytest.raises(ValidationError):
        _minimal(correlation_id=123)


def test_published_at_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _minimal(published_at=datetime(2026, 4, 20, 10, 0, 0))  # noqa: DTZ001  # naive input is the test
    assert "UTC" in str(exc_info.value)


def test_published_at_non_utc_timezone_is_rejected() -> None:
    """Non-UTC offset (e.g., CEST +02:00) must be rejected even though tzinfo is aware."""
    cest = timezone(timedelta(hours=2), "CEST")
    with pytest.raises(ValidationError):
        _minimal(published_at=datetime(2026, 4, 20, 10, 0, 0, tzinfo=cest))


def test_zero_offset_aware_datetime_is_normalized_to_utc() -> None:
    """Zero-offset non-singleton tzinfo is accepted and relabelled to datetime.UTC."""
    named_zero = timezone(timedelta(0), "custom-utc-alias")
    env = _minimal(published_at=datetime(2026, 4, 20, 10, 0, 0, tzinfo=named_zero))
    assert env.published_at.tzinfo is UTC


def test_published_at_utc_is_accepted() -> None:
    explicit = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
    env = _minimal(published_at=explicit)
    assert env.published_at == explicit


def test_payload_accepts_nested_structures() -> None:
    nested = {
        "action": "LONG",
        "nested": {"qty": "0.01", "meta": {"source": "tv"}},
        "list": [1, 2, 3],
    }
    env = _minimal(payload=nested)
    assert env.payload == nested


def test_frozen_model_rejects_attribute_assignment() -> None:
    env = _minimal()
    with pytest.raises(ValidationError):
        env.publisher = "other"


def test_to_bytes_round_trips_via_from_bytes() -> None:
    original = _minimal(
        correlation_id=CorrelationId("cid-round-trip"),
        publisher="execution",
        payload={"order_id": 42, "side": "buy", "qty": "0.01"},
    )
    raw = original.to_bytes()
    restored = MessageEnvelope.from_bytes(raw)
    assert restored == original
    assert restored.published_at.tzinfo is UTC


def test_to_bytes_produces_spec_wire_format() -> None:
    """JSON on the wire: UUID as canonical string, published_at as ISO-8601 +00:00."""
    env = _minimal()
    raw = env.to_bytes()
    parsed = json.loads(raw.decode("utf-8"))
    assert _UUID4.fullmatch(parsed["message_id"]) is not None
    assert _ISO_UTC.fullmatch(parsed["published_at"]) is not None
    assert parsed["schema_version"] == "1.0"
    assert parsed["correlation_id"] == "cid-1"
    assert parsed["publisher"] == "signal-gateway"
    assert parsed["payload"] == {"action": "LONG"}


def test_from_bytes_rejects_invalid_json() -> None:
    with pytest.raises(ValidationError):
        MessageEnvelope.from_bytes(b"not-json")


def test_from_bytes_rejects_json_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        MessageEnvelope.from_bytes(b'{"schema_version": "1.0"}')


def test_explicit_schema_version_overrides_default() -> None:
    env = _minimal(schema_version="1.5")
    assert env.schema_version == "1.5"
