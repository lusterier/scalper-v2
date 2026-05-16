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


def subject_for_shadow_rejected_start(bot_id: str) -> str:
    """Build ``shadow.rejected.start.<bot_id>`` publish subject (T-513a / L-002 helper).

    Topic for the rejected-signal 60-min observation FSM per BRIEF §13.5.
    Strategy-engine producer publishes when a signal is rejected by scoring;
    execution-service ``ShadowRejectedWorker`` subscribes and spawns the
    observation task.
    """
    return f"shadow.rejected.start.{bot_id}"


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


class SizingTierWire(BaseModel):
    """One §B.1 ``sizing.tiers`` rung on the wire (T-527b2b).

    Bus-owned wire mirror of ``packages.scoring.types.SizingTier`` —
    ``packages.bus`` must NOT import ``packages.scoring`` (scoring already
    imports bus → reverse = cycle), so the producer maps
    ``SizingSection.tiers`` → ``SizingTierWire`` and the execution placement
    seam rehydrates ``SizingTier`` from these (mirror the ``VariantSpec``
    pattern T-511b2). ``Decimal`` fields round-trip via the envelope
    (``model_dump(mode="json")`` → str → ``model_validate`` → ``Decimal``;
    same convention as ``VariantSpec.overrides``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    balance_min: Decimal
    size: Decimal


class SizingSpecForWire(BaseModel):
    """§B.1 ``sizing:`` block on the ``OrderRequest`` wire (T-527b2b /
    T-528b, OQ-6b).

    Producer (strategy-engine) maps ``BotConfig.sizing: SizingSection`` →
    this; execution-service placement seam consumes it (ADR-0013). Thin
    transport: carries the compute inputs for both ``method`` paths —
    ``method: "tier"`` (T-527b2b: tiers/score_multipliers) or
    ``method: "risk_per_sl"`` (T-528b: ``risk_pct``);
    ``max_notional_per_symbol`` + the cap apply to BOTH. ``method`` /
    ``risk_pct`` are ADDITIVE with defaults (``"tier"`` / ``None``) so old
    payloads validate (``OrderRequest.schema_version`` stays "1.0").
    ``SizingSection`` is the validation authority (the producer maps from an
    already-validated instance — incl. the method↔risk_pct coupling); this
    model deliberately does NOT re-validate that coupling (thin transport;
    the execution seam narrows ``risk_pct`` defensively per L-019).
    ``tiers`` / ``score_multipliers`` stay REQUIRED — the producer always
    passes them explicitly (``[]`` / ``{}`` for a risk_per_sl bot).
    ``tier_promotion`` / ``tier_demotion`` are operator OQ-2=A deferred and
    not modeled (separate ``T-F5+``). ``extra="forbid"`` catches wire
    corruption.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["tier", "risk_per_sl"] = "tier"
    tiers: list[SizingTierWire]
    score_multipliers: dict[str, Decimal]
    risk_pct: Decimal | None = None
    max_notional_per_symbol: dict[str, Decimal]


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


class ShadowRejectedStartPayload(BaseModel):
    """``shadow.rejected.start.<bot_id>`` envelope per BRIEF §13.5 (T-513a).

    Published by :func:`services.strategy_engine.app.consumer._publish_shadow_rejected_start`
    when a signal is rejected by scoring (paralelne s ``_publish_signal_rejected``
    on ``signals.rejected.<bot_id>``). Always-on per BRIEF §13.5; operational
    kill-switch via ``Settings.shadow_rejected_enabled``.

    Carries ``virtual_entry_price`` (latest closed-candle close from
    ``ohlc_1m`` table at rejection time via NEW
    :func:`packages.db.queries.market_data.select_latest_close` helper) +
    ``bot_config.execution`` thresholds (``sl_pct``, ``tp_pct``, ``be_trigger``,
    ``be_sl_level``) for the in-process observation FSM. Receiver decodes +
    ``insert_shadow_rejected`` (T-510b shipped) + spawns 60-min observation task.

    NO ``parent_kind`` field — rejected signals have no parent trade
    (rejection happens BEFORE placement); ``shadow_rejected`` table has
    no FK to ``trades`` or ``paper_trades`` (T-510a OQ-6=A no-FK convention).

    NO ``trail_pct`` field — rejected signals don't trade, so trail SL
    is not part of the observation window. T-513a observes TP/SL/BE
    thresholds only; trail-recompute logic is shadow-variant-specific
    (T-511b1) and irrelevant for rejected-signal observation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_version: Literal[1] = 1
    signal_id: int
    bot_id: str
    symbol: str
    action: Literal["LONG", "SHORT", "CLOSE"]
    virtual_entry_price: Decimal
    sl_pct: Decimal
    tp_pct: Decimal
    be_trigger: Decimal
    be_sl_level: Decimal
    rejected_at: datetime

    @field_validator("rejected_at")
    @classmethod
    def _ts_must_be_utc(cls, value: datetime) -> datetime:
        return _validate_utc(value)

    @field_serializer("rejected_at")
    def _ts_serialize_utc(self, value: datetime) -> str:
        return value.isoformat()
