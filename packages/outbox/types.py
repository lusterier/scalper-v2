"""Type definitions for the outbox pattern (T-537a1).

Two domain types:

* :class:`OutboxEvent` — read-side projection of an ``outbox_events`` row.
  Returned by :func:`packages.outbox.queries.select_pending_outbox_events`.
* :class:`OutboxRelaySettings` — env-sourced configuration for the relay
  worker (T-537a2 consumer). Lives in T-537a1 because the shape is
  declarable independently of the worker module; T-537a2's tests will
  import it from here without circular-import risk.

Env prefix convention is flat ``OUTBOX_RELAY_*`` (mirror existing
service-Settings flat-prefix convention). T-537b's signal-gateway
``Settings`` will compose this via ``OutboxRelaySettings()``
factory call rather than nested model — keeps env mapping
deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["OutboxEvent", "OutboxRelaySettings"]


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """Domain projection of an ``outbox_events`` row.

    Read-only; relay worker (T-537a2) constructs from row dict via
    :func:`packages.outbox.queries.select_pending_outbox_events`. Field
    order matches DB column order for ergonomic ``**dict(row)``
    construction.
    """

    id: int
    service: str
    subject: str
    correlation_id: str | None
    payload: dict[str, Any]
    created_at: datetime
    published_at: datetime | None
    attempt_count: int
    last_attempt_at: datetime | None
    last_error: str | None
    failed_at: datetime | None


class OutboxRelaySettings(BaseSettings):
    """Settings model for :class:`packages.outbox.relay.OutboxRelayWorker`.

    Consumed by T-537a2 worker. Lives in T-537a1 so its env shape is
    declarable + import-stable before the worker module exists.

    Env prefix ``OUTBOX_RELAY_*`` (flat). Examples:
    ``OUTBOX_RELAY_POLL_INTERVAL_S=2.0``,
    ``OUTBOX_RELAY_MAX_ATTEMPTS=200``.
    """

    model_config = SettingsConfigDict(env_prefix="OUTBOX_RELAY_", extra="ignore")

    poll_interval_s: float = Field(
        default=1.0,
        gt=0.0,
        description=(
            "Base poll cadence between relay batches when no pending events; "
            "active relay loops faster. Lower = more PG load; higher = more "
            "publish latency."
        ),
    )
    batch_size: int = Field(
        default=100,
        gt=0,
        description=(
            "Max rows per relay loop iteration. Bigger batch = better PG "
            "round-trip amortization; risk of holding row locks (FOR UPDATE "
            "SKIP LOCKED) longer."
        ),
    )
    max_attempts: int = Field(
        default=100,
        ge=1,
        description=(
            "Number of publish attempts before ``failed_at`` is set on the "
            "row. After exhaustion the row stays in DB for admin replay "
            "(``UPDATE published_at = NULL`` resumes)."
        ),
    )
    backoff_base_s: float = Field(
        default=2.0,
        gt=0.0,
        description=(
            "Base seconds for exponential backoff. Next-attempt delay = "
            "min(base * 2^attempt_count, cap). Computed in SQL via PG "
            "``power`` function — see select_pending_outbox_events."
        ),
    )
    backoff_cap_s: float = Field(
        default=60.0,
        gt=0.0,
        description=(
            "Maximum delay between attempts. Caps exponential growth; "
            "MUST be >= backoff_base_s (validated below)."
        ),
    )

    @field_validator("backoff_cap_s")
    @classmethod
    def _cap_must_be_at_least_base(cls, value: float, info: Any) -> float:
        base = info.data.get("backoff_base_s")
        if base is not None and value < base:
            msg = (
                f"backoff_cap_s={value} must be >= backoff_base_s={base} "
                "(cap below base would invert the exponential ramp)"
            )
            raise ValueError(msg)
        return value
