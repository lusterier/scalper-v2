"""Unit tests for :mod:`packages.bus.schemas.market_ohlc` (T-104a).

Coverage matrix:

* **Field defaults** — ``schema_version="1.0"``, ``interval="1m"``,
  ``source="binance"`` pinned via ``Literal``; only the variable
  fields require explicit construction kwargs.
* **UTC enforcement on ``bucket_start``** — naive rejected, non-zero-
  offset rejected, zero-offset aware accepted and relabelled to
  :data:`datetime.UTC`. Mirrors :class:`MessageEnvelope` /
  :class:`SignalValidated`.
* **Decimal precision** — Binance string-encoded numerics survive
  the schema construction without float drift; the JSON-mode dump
  emits Decimal as string per Pydantic convention so the wire format
  carries the same precision T-110 will reconstruct.
* **Frozen** — assignment after construction raises.
* **``message_id_for_closed_candle`` determinism** — same
  ``(symbol, bucket_start)`` → same UUID; differs on either input;
  output is deterministic UUIDv5; cross-namespace isolation against
  the SIGNALS_VALIDATED helper (no collision).
* **``message_id_for_closed_candle`` UTC contract** — naive
  ``bucket_start`` raises (preserves the schema's UTC discipline so
  callers cannot smuggle a local-time bucket through the helper).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.bus.schemas import (
    OhlcCandlePayload,
    message_id_for,
    message_id_for_closed_candle,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bucket(year: int = 2026, month: int = 4, day: int = 25, minute: int = 0) -> datetime:
    return datetime(year, month, day, 12, minute, 0, tzinfo=UTC)


def _payload(**overrides: object) -> OhlcCandlePayload:
    kwargs: dict[str, object] = {
        "symbol": "BTCUSDT",
        "bucket_start": _bucket(),
        "open": Decimal("50000.12345678"),
        "high": Decimal("50100.00000001"),
        "low": Decimal("49950.99999999"),
        "close": Decimal("50050.55555555"),
        "volume": Decimal("123.456789012"),
        "is_closed": True,
    }
    kwargs.update(overrides)
    return OhlcCandlePayload(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Field defaults
# ---------------------------------------------------------------------------


def test_defaults_filled_when_absent() -> None:
    candle = _payload()
    assert candle.schema_version == "1.0"
    assert candle.interval == "1m"
    assert candle.source == "binance"


def test_required_fields_raise_when_missing() -> None:
    with pytest.raises(ValidationError) as exc_info:
        OhlcCandlePayload()  # type: ignore[call-arg]
    missing = {err["loc"][0] for err in exc_info.value.errors() if err["type"] == "missing"}
    assert {
        "symbol",
        "bucket_start",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "is_closed",
    } <= missing


def test_pinned_literals_reject_other_values() -> None:
    """``interval`` and ``source`` are Literal-typed: anything else fails."""
    with pytest.raises(ValidationError):
        _payload(interval="5m")
    with pytest.raises(ValidationError):
        _payload(source="bybit")
    with pytest.raises(ValidationError):
        _payload(schema_version="2.0")


# ---------------------------------------------------------------------------
# UTC enforcement on bucket_start
# ---------------------------------------------------------------------------


def test_naive_bucket_start_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _payload(bucket_start=datetime(2026, 4, 25, 12, 0, 0))  # noqa: DTZ001  # naive input is the test
    assert "UTC" in str(exc_info.value)


def test_non_utc_bucket_start_is_rejected() -> None:
    cest = timezone(timedelta(hours=2), "CEST")
    with pytest.raises(ValidationError):
        _payload(bucket_start=datetime(2026, 4, 25, 12, 0, 0, tzinfo=cest))


def test_zero_offset_aware_bucket_start_is_normalised_to_utc() -> None:
    """Zero-offset non-singleton tzinfo is accepted and relabelled to :data:`datetime.UTC`."""
    named_zero = timezone(timedelta(0), "custom-utc-alias")
    candle = _payload(bucket_start=datetime(2026, 4, 25, 12, 0, 0, tzinfo=named_zero))
    assert candle.bucket_start.tzinfo is UTC


# ---------------------------------------------------------------------------
# Frozen + Decimal precision
# ---------------------------------------------------------------------------


def test_frozen_assignment_raises() -> None:
    candle = _payload()
    with pytest.raises(ValidationError):
        candle.symbol = "ETHUSDT"


def test_decimal_precision_preserved_through_json_roundtrip() -> None:
    """Pydantic JSON-mode dump emits Decimal as string; round-trip is lossless."""
    candle = _payload(
        open=Decimal("0.000000000123"),
        high=Decimal("0.000000000456"),
        low=Decimal("0.000000000111"),
        close=Decimal("0.000000000333"),
        volume=Decimal("987654321.123456789012"),
    )
    raw = candle.model_dump_json()
    reloaded = OhlcCandlePayload.model_validate_json(raw)
    assert reloaded.open == Decimal("0.000000000123")
    assert reloaded.volume == Decimal("987654321.123456789012")


def test_bucket_start_serialises_with_explicit_offset() -> None:
    """``+00:00`` form per §5.12, not the bare ``Z``."""
    candle = _payload(bucket_start=_bucket(minute=15))
    raw = candle.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["bucket_start"].endswith("+00:00")
    assert "Z" not in parsed["bucket_start"]


# ---------------------------------------------------------------------------
# message_id_for_closed_candle determinism
# ---------------------------------------------------------------------------


def test_message_id_is_deterministic_for_same_inputs() -> None:
    a = message_id_for_closed_candle("BTCUSDT", _bucket())
    b = message_id_for_closed_candle("BTCUSDT", _bucket())
    assert a == b
    assert isinstance(a, UUID)
    assert a.version == 5


def test_message_id_differs_on_different_symbol() -> None:
    btc = message_id_for_closed_candle("BTCUSDT", _bucket())
    eth = message_id_for_closed_candle("ETHUSDT", _bucket())
    assert btc != eth


def test_message_id_differs_on_different_bucket_start() -> None:
    a = message_id_for_closed_candle("BTCUSDT", _bucket(minute=0))
    b = message_id_for_closed_candle("BTCUSDT", _bucket(minute=1))
    assert a != b


def test_message_id_namespace_is_isolated_from_signals_helper() -> None:
    """Cross-helper collision check.

    A signal with ``idempotency_key="BTCUSDT:2026-04-25T12:00:00+00:00"``
    must NOT produce the same UUID as the corresponding closed-candle
    helper call — that would cross-stream-collide if both were ever
    routed through a shared dedup path. Distinct namespace UUIDs make
    this impossible by construction; the test guards against an
    accidental shared NS in a future refactor.
    """
    bucket = _bucket()
    candle_id = message_id_for_closed_candle("BTCUSDT", bucket)
    signal_id = message_id_for(f"BTCUSDT:{bucket.isoformat()}")
    assert candle_id != signal_id


def test_message_id_naive_bucket_start_raises() -> None:
    """Helper must enforce UTC on its own input; naive smuggle is bug-level."""
    with pytest.raises(ValueError, match="UTC"):
        message_id_for_closed_candle("BTCUSDT", datetime(2026, 4, 25, 12, 0, 0))  # noqa: DTZ001  # naive input is the test


def test_message_id_non_utc_bucket_start_raises() -> None:
    cest = timezone(timedelta(hours=2), "CEST")
    with pytest.raises(ValueError, match="UTC"):
        message_id_for_closed_candle("BTCUSDT", datetime(2026, 4, 25, 12, 0, 0, tzinfo=cest))
