"""``orders.requests`` / ``orders.events`` / ``trading.events`` payloads (§8.4 lines 1316-1363).

T-216a ships the schema contract:

* :class:`OrderRequest` — ``orders.requests.<bot_id>`` payload built by
  strategy-engine (F3), consumed by execution-service (T-216a handler).
* :class:`OrderEventBase` + 4 subclasses (:class:`OrderPlaced`,
  :class:`OrderFilled`, :class:`OrderClosed`, :class:`SLMoved`) —
  ``orders.events.<bot_id>`` discriminated union; published by
  execution-service post-tx-commit (T-216b).
* :class:`TradingEvent` — ``trading.events`` stream payload, persisted
  by analytics-api (F4) into ``trading_events`` hypertable (§7.2 line 1091).

Subclass ``event_type`` discriminator narrowing (Decision #2 / CONCERN #5):
brief §8.4 shows base ``event_type: str`` + empty subclass bodies; we
narrow each subclass with ``event_type: Literal[<value>] = <value>`` for
type-narrowing + Pydantic discriminated-union support. Brief is silent
on this; intentional improvement, not deviation.

Decimal precision preserved on every numeric field (§5.13 / §N1). UTC
enforcement on datetime fields mirrors :class:`packages.bus.MessageEnvelope`:
naive or non-zero-offset values rejected at validation; serialisation
emits explicit ``+00:00`` offset per §5.12.

Subject literals (``orders.requests.{bot_id}`` / ``orders.events.{bot_id}`` /
``orders.dlq.{bot_id}``) live as helper functions per L-002 active control
— callers MUST import the helpers rather than f-string the subject inline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal  # noqa: TC003 — runtime annotation on Pydantic Decimal fields
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from packages.bus.payloads import VariantSpec  # noqa: TC001 — runtime use in Pydantic validation

__all__ = [
    "OrderClosed",
    "OrderEventBase",
    "OrderFilled",
    "OrderPlaced",
    "OrderRequest",
    "SLMoved",
    "TradingEvent",
    "subject_for_orders_dlq",
    "subject_for_orders_event",
    "subject_for_orders_request",
]


def subject_for_orders_request(bot_id: str) -> str:
    """Build the §8 line 1206 publish subject ``orders.requests.<bot_id>``."""
    return f"orders.requests.{bot_id}"


def subject_for_orders_event(bot_id: str) -> str:
    """Build the §8 line 1207 publish subject ``orders.events.<bot_id>``."""
    return f"orders.events.{bot_id}"


def subject_for_orders_dlq(bot_id: str) -> str:
    """Build the dead-letter subject ``orders.dlq.<bot_id>`` (T-216a OQ-3)."""
    return f"orders.dlq.{bot_id}"


def _validate_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware (tzinfo=datetime.UTC)")
    if value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be in UTC (utcoffset must be zero)")
    return value if value.tzinfo is UTC else value.replace(tzinfo=UTC)


class OrderRequest(BaseModel):
    """§8.4 line 1319 ``orders.requests.<bot_id>`` payload, frozen.

    ``schema_version`` stays ``"1.0"`` despite T-511b2 additive
    ``shadow_variants`` + ``shadow_max_duration_hours`` fields and the T-527a
    additive ``score`` field — defaults are present, no ``extra="forbid"`` on
    this model so old payloads still validate. Future breaking changes
    (rename / type narrow / required field) bump to ``"2.0"``.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    bot_id: str
    signal_id: int
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["market"] = "market"
    qty: Decimal
    leverage: int
    sl_pct: Decimal
    tp_pct: Decimal
    tp_qty_pct: Decimal
    be_trigger: Decimal
    be_sl_level: Decimal
    trail_pct: Decimal
    exchange_mode: Literal["live", "testnet", "paper"]
    # T-511b2 / ADR-0010: shadow runtime config carried from strategy-engine
    # producer (which reads BotConfig.shadow). Empty list = shadow disabled.
    shadow_variants: list[VariantSpec] = Field(default_factory=list)
    # Per-bot ceiling; consumed in T-512+ for restart-recovery TTL bound.
    # T-511b2 plumbs the field through wire schema; per-variant
    # max_duration_hours from VariantSpec.overrides remains the active
    # ceiling source at shadow_worker.py:171.
    shadow_max_duration_hours: Decimal | None = None
    # T-527a: scoring score threaded from strategy-engine producer (reads
    # ScoringResult.total_score) for T-527b §B.1 score_multipliers sizing.
    # float (dimensionless scoring metric, mirrors ScoringConfig.trigger_
    # threshold: float — §5.13 Decimal is money/price/qty only, not the score).
    # None default + no extra="forbid" → old payloads validate (schema_version
    # stays "1.0"). Carried producer→wire in T-527a; UNCONSUMED until T-527b.
    score: float | None = None


class OrderEventBase(BaseModel):
    """§8.4 line 1340 base for ``orders.events.<bot_id>`` discriminated union."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    bot_id: str
    event_type: str
    order_id: int
    exchange_order_id: str
    symbol: str
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def _ts_must_be_utc(cls, value: datetime) -> datetime:
        return _validate_utc(value)

    @field_serializer("timestamp")
    def _ts_serialize_utc(self, value: datetime) -> str:
        return value.isoformat()


class OrderPlaced(OrderEventBase):
    """§8.4 line 1349 — emitted after ``place_market_order`` ack + DB commit (T-216b)."""

    event_type: Literal["order_placed"] = "order_placed"


class OrderFilled(OrderEventBase):
    """§8.4 lines 1350-1355 — emitted per fill (open / partial_tp / sl / trail / close)."""

    event_type: Literal["order_filled"] = "order_filled"
    exec_id: str
    price: Decimal
    qty: Decimal
    fee: Decimal
    exec_type: Literal["open", "partial_tp", "sl", "trail", "close"]


class OrderClosed(OrderEventBase):
    """§8.4 lines 1356-1358 — emitted on size=0 close per cumulative-delta close flow (T-219)."""

    event_type: Literal["order_closed"] = "order_closed"
    realized_pnl: Decimal
    close_reason: str


class SLMoved(OrderEventBase):
    """§8.4 lines 1359-1361 — emitted on SL move (protective / be / trail)."""

    event_type: Literal["sl_moved"] = "sl_moved"
    new_sl_price: Decimal
    sl_type: Literal["protective", "be", "trail"]


class TradingEvent(BaseModel):
    """§8 line 1216 ``trading.events`` payload — same OrderEvent body + persistence metadata.

    Persisted by analytics-api (F4) into ``trading_events`` hypertable (§7.2
    line 1091). ``event_type`` is the routing discriminator; ``payload``
    carries the full OrderEvent body as JSON-serializable dict.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    occurred_at: datetime
    bot_id: str | None = None
    correlation_id: str | None = None
    event_type: str
    payload: dict[str, Any]

    @field_validator("occurred_at")
    @classmethod
    def _occurred_must_be_utc(cls, value: datetime) -> datetime:
        return _validate_utc(value)

    @field_serializer("occurred_at")
    def _occurred_serialize_utc(self, value: datetime) -> str:
        return value.isoformat()
