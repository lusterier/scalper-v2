"""``signals.validated`` NATS payload schema (§8.4).

Published by signal-gateway (T-015b2) onto the ``signals.validated``
subject of the SIGNALS stream after the §9.1 pipeline validates a
webhook. Consumed by strategy-engine (F3) for per-bot scoring fan-out.

UTC enforcement on datetime fields mirrors
:class:`packages.bus.MessageEnvelope`: naive or non-zero-offset values
are rejected at validation; serialisation emits the explicit ``+00:00``
form per §5.12.

:data:`_SIGNALS_VALIDATED_NS` is the namespace UUID for deterministic
``Nats-Msg-Id`` derivation — :func:`uuid.uuid5` on ``(namespace,
idempotency_key)`` produces a stable ``message_id`` so SIGNALS
server-side dedup (``duplicate_window=2m``, T-012) aligns with the
in-process dedup ring (10 s TTL, T-015b1) across a signal-gateway
restart. :func:`message_id_for` is the sole entry point; call sites
must not recompute the UUID by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

__all__ = ["SignalValidated", "message_id_for"]


# Project-unique namespace UUID, minted once for signals.validated
# deterministic message_id derivation. Do NOT reuse this constant for
# other stream subjects; each (namespace, key) pair must be scoped to
# its own subject to avoid cross-stream collisions.
_SIGNALS_VALIDATED_NS: UUID = UUID("1f0371b0-6dda-4ab5-88dd-0102a2a013af")


def message_id_for(idempotency_key: str) -> UUID:
    """Return the deterministic ``Nats-Msg-Id`` for a ``signals.validated`` publish.

    :func:`uuid.uuid5` is stable over ``(namespace, name)`` pairs, so two
    publishes sharing an ``idempotency_key`` produce the same ``UUID`` →
    the same ``Nats-Msg-Id`` header → SIGNALS ``duplicate_window`` dedups.
    """
    return uuid5(_SIGNALS_VALIDATED_NS, idempotency_key)


class SignalValidated(BaseModel):
    """§8.4 ``signals.validated`` payload, frozen."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    source: str
    idempotency_key: str
    received_at: datetime
    symbol: str
    original_symbol: str
    action: Literal["LONG", "SHORT", "CLOSE"]
    expires_at: datetime
    payload: dict[str, Any]

    @field_validator("received_at", "expires_at")
    @classmethod
    def _must_be_utc(cls, value: datetime) -> datetime:
        """Require UTC-aware datetimes; normalise tzinfo to :data:`datetime.UTC`."""
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (tzinfo=datetime.UTC)")
        if value.utcoffset() != timedelta(0):
            raise ValueError("datetime must be in UTC (utcoffset must be zero)")
        return value if value.tzinfo is UTC else value.replace(tzinfo=UTC)

    @field_serializer("received_at", "expires_at")
    def _serialize_utc(self, value: datetime) -> str:
        """ISO-8601 with explicit ``+00:00`` offset (§5.12)."""
        return value.isoformat()
