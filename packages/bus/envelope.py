"""`MessageEnvelope` — outer wrapper around every NATS message (§8.3).

Every inter-service message on the bus is a Pydantic-serialized JSON
object starting with this envelope. Concrete payload models live
under :mod:`packages.bus.schemas` and ship alongside the services that
own them — this module ships only the envelope itself.

The envelope is **frozen**: once constructed, fields cannot be
reassigned. Callers own the envelope and pass it to the publish side
by value. ``publisher`` is **required with no default** so each call
site states the emitting service explicitly (T-008 operator Q6).

Field defaults applied when absent at construction:

* ``schema_version`` — ``"1.0"`` (envelope spec version, independent of
  the payload's own ``schema_version`` which lives inside
  ``payload``; see §8.3 / §8.4).
* ``message_id`` — fresh UUID4 per instance.
* ``published_at`` — :func:`packages.core.now_utc` at construction.

``correlation_id`` is typed as :data:`packages.core.CorrelationId` (a
``NewType`` over :class:`str`). Static checkers see the alias; runtime
and JSON wire format are :class:`str` (§8.3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from packages.core import CorrelationId, now_utc

__all__ = ["MessageEnvelope"]


class MessageEnvelope(BaseModel):
    """NATS message envelope per §8.3.

    Serialized via :meth:`to_bytes` / :meth:`from_bytes`: UUIDs render
    as canonical strings, ``published_at`` as ISO-8601 with explicit
    ``+00:00`` (§5.12; ``Z`` is rejected as output form so downstream
    consumers do not have to handle both).
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    message_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    correlation_id: CorrelationId
    published_at: datetime = Field(default_factory=now_utc)
    publisher: str
    payload: dict[str, Any]

    @field_validator("published_at")
    @classmethod
    def _published_at_must_be_utc(cls, value: datetime) -> datetime:
        """Require a UTC representation; normalize tzinfo to :data:`datetime.UTC`.

        Naive datetimes are rejected. Non-zero-offset timezones are
        rejected. Zero-offset aware datetimes (including the
        pydantic-core tzinfo attached on JSON round-trip and arbitrary
        ``timezone(timedelta(0), name)`` instances) are accepted and
        relabelled to :data:`datetime.UTC` so storage is uniform.

        The ``utcoffset() == 0`` gate admits zero-offset named zones
        like ``Europe/London`` in winter. That's a deliberate
        trade-off over a strict ``is UTC`` identity check, which
        would break the ``to_bytes`` → ``from_bytes`` round-trip
        (pydantic-core parses ``+00:00`` / ``Z`` into its own UTC
        tzinfo, not :data:`datetime.UTC`). The absolute instant is
        preserved; only the originating tz name is lost, and that
        never survived the JSON wire format anyway.
        """
        if value.tzinfo is None:
            raise ValueError("published_at must be timezone-aware (tzinfo=datetime.UTC)")
        if value.utcoffset() != timedelta(0):
            raise ValueError("published_at must be in UTC (utcoffset must be zero)")
        return value if value.tzinfo is UTC else value.replace(tzinfo=UTC)

    @field_serializer("published_at")
    def _serialize_published_at(self, value: datetime) -> str:
        """Emit ISO-8601 with explicit ``+00:00`` (§5.12).

        Pydantic's default datetime serializer renders UTC datetimes
        with the ``Z`` suffix; :meth:`datetime.isoformat` renders
        ``+00:00``, which the brief specifies as the wire format.
        """
        return value.isoformat()

    def to_bytes(self) -> bytes:
        """Serialize the envelope to UTF-8 JSON bytes for NATS publish."""
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> Self:
        """Parse UTF-8 JSON bytes (as received from NATS) into an envelope."""
        return cls.model_validate_json(raw.decode("utf-8"))
