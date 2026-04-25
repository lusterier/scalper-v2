"""Unit tests for :mod:`services.signal_gateway.app.models`.

Covers :class:`SignalEnvelope` validation + the
``model_validator(mode="before")`` extras-migration semantics, plus
the three response models (Validated / Duplicate / Error) including
the closed-Literal ``reason`` field on the error shape.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.signal_gateway.app.models import (
    SignalEnvelope,
    WebhookDuplicateResponse,
    WebhookErrorResponse,
    WebhookValidatedResponse,
)


def _required() -> dict[str, object]:
    """Minimal required-field dict for SignalEnvelope construction."""
    return {
        "symbol": "BTCUSDT.P",
        "action": "LONG",
        "source": "tv_rsi_div_v3",
        "idempotency_key": "k-001",
    }


# ---- SignalEnvelope happy paths --------------------------------------------


def test_happy_envelope_no_extras() -> None:
    env = SignalEnvelope.model_validate(_required())
    assert env.symbol == "BTCUSDT.P"
    assert env.action == "LONG"
    assert env.source == "tv_rsi_div_v3"
    assert env.idempotency_key == "k-001"
    assert env.payload == {}


# ---- Extras migration ------------------------------------------------------


def test_extras_migration_into_payload() -> None:
    """TV v3 flat alert shape: top-level indicators land inside ``payload``."""
    env = SignalEnvelope.model_validate({**_required(), "rsi": 14.2, "sl_pct": 0.01})
    assert env.payload == {"rsi": 14.2, "sl_pct": 0.01}


def test_explicit_payload_kept_alongside_extras() -> None:
    """Explicit payload + non-colliding extras both end up merged."""
    env = SignalEnvelope.model_validate(
        {**_required(), "payload": {"a": 1}, "b": 2},
    )
    assert env.payload == {"a": 1, "b": 2}


def test_collision_explicit_payload_wins() -> None:
    """Explicit payload key wins over migrated top-level duplicate (TV-shape spillover)."""
    env = SignalEnvelope.model_validate(
        {**_required(), "payload": {"x": 1}, "x": 2},
    )
    assert env.payload == {"x": 1}


def test_payload_as_none_treated_as_missing() -> None:
    """payload=None is indistinguishable from omitted; migrator coalesces to {}."""
    env = SignalEnvelope.model_validate({**_required(), "payload": None, "rsi": 5})
    assert env.payload == {"rsi": 5}


def test_extra_forbid_unrelated_keys_after_migration_passes() -> None:
    """After migration moves extras to payload, ``extra="forbid"`` sees no extras."""
    env = SignalEnvelope.model_validate(
        {**_required(), "rsi": 1, "ema": 2, "vol": 3},
    )
    assert env.payload == {"rsi": 1, "ema": 2, "vol": 3}


# ---- Pass-through guards (operator-locked) ---------------------------------


def test_non_dict_payload_passes_through_for_authoritative_field_error() -> None:
    """Non-dict ``payload`` returns unchanged; field validator emits the authoritative error.

    Operator-locked invariant: a future "fix" that wraps non-dict
    payload in a list/dict/whatever silently changes semantics. The
    migrator must NOT touch a non-dict ``payload``; the type complaint
    must be on ``payload``, not on the extras.
    """
    with pytest.raises(ValidationError) as exc_info:
        SignalEnvelope.model_validate(
            {**_required(), "payload": "not a dict", "extra_top_level": 1},
        )
    locs = {tuple(e["loc"]) for e in exc_info.value.errors()}
    assert ("payload",) in locs


def test_payload_as_list_passes_through_for_authoritative_field_error() -> None:
    """payload=[...] same pass-through guarantee as non-dict scalar."""
    with pytest.raises(ValidationError) as exc_info:
        SignalEnvelope.model_validate(
            {**_required(), "payload": [1, 2, 3], "rsi": 5},
        )
    locs = {tuple(e["loc"]) for e in exc_info.value.errors()}
    assert ("payload",) in locs


def test_non_dict_input_passes_through() -> None:
    """``model_validate(<non-dict>)`` exercises the validator's pass-through guard."""
    with pytest.raises(ValidationError):
        SignalEnvelope.model_validate([1, 2, 3])


# ---- Field constraints -----------------------------------------------------


def test_action_enum_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        SignalEnvelope.model_validate({**_required(), "action": "HOLD"})


def test_min_length_one_idempotency_key_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        SignalEnvelope.model_validate({**_required(), "idempotency_key": ""})


def test_min_length_one_symbol_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        SignalEnvelope.model_validate({**_required(), "symbol": ""})


def test_min_length_one_source_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        SignalEnvelope.model_validate({**_required(), "source": ""})


# ---- Response models -------------------------------------------------------


def test_response_validated_signal_id_int() -> None:
    body = WebhookValidatedResponse(signal_id=42)
    assert body.model_dump() == {"signal_id": 42}


def test_response_duplicate_status_default() -> None:
    body = WebhookDuplicateResponse()
    assert body.model_dump() == {"status": "duplicate"}


def test_response_error_reason_literal_rejects_unknown() -> None:
    """``reason`` is a closed Literal — out-of-set values fail at construction."""
    with pytest.raises(ValidationError):
        WebhookErrorResponse(detail="x", reason="bogus")  # type: ignore[arg-type]
