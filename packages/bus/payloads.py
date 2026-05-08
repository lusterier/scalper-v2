"""Pydantic payload envelopes for cross-service NATS topics (§8 + §13).

T-511b1 ships ``ShadowStartPayload`` + ``VariantSpec`` for the
``shadow.start.<bot_id>`` topic per BRIEF §13.3. The terminal-outcome
StrEnum is REUSED from :class:`packages.core.types.ShadowVariantTerminal`
(T-510b shipped 2026-05-07 commit ``6df8859``); this module does NOT
redefine it. Migration 0014 wire-format is snake_case plain TEXT (no
CHECK constraint per OQ-4=A); ``ShadowVariantTerminal`` already aligns.

T-511b2a (2026-05-08; ADR-0010) adds ``parent_kind`` discriminator to
``ShadowStartPayload`` so the shadow runtime can route ``parent_trade_id``
to either ``trades.id`` (live) or ``paper_trades.id`` (paper) per the
strategy-engine producer's ``BotConfig.exchange.mode`` mapping. Migration
0015 drops the original 0014 FK to ``trades(id)`` and writes
``parent_kind`` as a plain TEXT NOT NULL column.

T-511b2 (2026-05-08; ADR-0010) adds ``TradeClosedPayload`` (internal
``trade.closed.<bot_id>`` topic for H-016 cancel hook) + co-located
``subject_for_shadow_start`` / ``subject_for_trade_closed`` helpers per
L-002 active control. Subject helpers co-located with payload definition;
``orders.*`` family helpers stay in :mod:`packages.bus.schemas.orders`
per existing convention (separation by topic family).

Future shadow / backtest payloads land here as the F5 cluster shipped.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal  # noqa: TC003 — runtime annotation on Pydantic Decimal fields
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator


def subject_for_shadow_start(bot_id: str) -> str:
    """Build ``shadow.start.<bot_id>`` publish subject (T-511b2 / L-002 helper)."""
    return f"shadow.start.{bot_id}"


def subject_for_trade_closed(bot_id: str) -> str:
    """Build ``trade.closed.<bot_id>`` publish subject (T-511b2 H-016 cancel hook)."""
    return f"trade.closed.{bot_id}"


def _validate_utc(value: datetime) -> datetime:
    """Mirror of :func:`packages.bus.schemas.orders._validate_utc` per L-007 reuse trade-off."""
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware (tzinfo=datetime.UTC)")
    if value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be in UTC (utcoffset must be zero)")
    return value if value.tzinfo is UTC else value.replace(tzinfo=UTC)


class VariantSpec(BaseModel):
    """One shadow variant — name + override params per BRIEF §13.2 YAML schema.

    Override keys are a subset of execution-config keys: ``be_trigger``,
    ``be_sl_level``, ``trail_pct``, ``sl_pct``, ``tp_pct``, ``tp_qty_pct``,
    ``max_duration_hours``. Unknown keys are rejected via
    ``model_config.extra='forbid'`` plus the per-key validator below.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    overrides: dict[str, Decimal | int]


class ShadowStartPayload(BaseModel):
    """``shadow.start.<bot_id>`` envelope per BRIEF §13.3.

    Published by :mod:`services.execution.app.placement_persist` post-commit
    on trade-open when ``bot_config.shadow.enabled`` (T-511b2 wires the
    publisher; T-511b1 ships only the consumer in :mod:`shadow_worker`).

    ``parent_kind`` (T-511b2a / ADR-0010) routes ``parent_trade_id`` to
    either ``trades.id`` (``"live"``) or ``paper_trades.id`` (``"paper"``).
    Strategy-engine producer maps ``BotConfig.exchange.mode``: ``"paper"``
    → ``"paper"``; ``"live"`` / ``"testnet"`` → ``"live"``. Migration 0015
    drops the original 0014 FK to ``trades(id)`` so paper-mode parent
    trades are addressable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_version: Literal[1] = 1
    parent_trade_id: int
    parent_kind: Literal["live", "paper"]
    bot_id: str
    symbol: str
    side: Literal["buy", "sell"]
    entry_price: Decimal
    qty: Decimal
    variants: list[VariantSpec]


class TradeClosedPayload(BaseModel):
    """Internal ``trade.closed.<bot_id>`` envelope per T-511b2 H-016 hook (ADR-0010).

    Published by:

    * **Live**: :func:`services.execution.app.reconcile.emit_post_commit_close_event`
      paralelne s :class:`packages.bus.schemas.orders.OrderClosed`.
    * **Paper**: :meth:`packages.exchange.paper.adapter.PaperExchange._persist_close`
      gated by ctor flag ``emit_parent_lifecycle`` (default False; primary
      bot PE in adapter pool wiring sets True; variant PE stays False).

    Consumed by :class:`services.execution.app.shadow_worker.ShadowWorker._on_parent_close`
    to cancel ``_active_tasks[parent_trade_id]`` per BRIEF §20 H-016 policy.

    ``closed_at`` is the **execution trigger moment** sourced from the
    fill / WS event timestamp (paper: ``PendingSLTPFill.triggered_at``;
    live: WS fill timestamp), NOT post-commit ``now_utc()``. H-016 cancel
    hook semantics is trigger-time clean.

    ``parent_kind`` symmetric s :class:`ShadowStartPayload` per ADR-0010 +
    T-511b2a foundation; analytics consumers may filter by parent_kind in F5+.
    Internal-only topic; no F4 analytics-api consumer today.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_version: Literal[1] = 1
    parent_trade_id: int
    parent_kind: Literal["live", "paper"]
    bot_id: str
    closed_at: datetime

    @field_validator("closed_at")
    @classmethod
    def _ts_must_be_utc(cls, value: datetime) -> datetime:
        return _validate_utc(value)

    @field_serializer("closed_at")
    def _ts_serialize_utc(self, value: datetime) -> str:
        return value.isoformat()
