"""§11.1 / §11.3 domain types — return values of :class:`ExchangeClient` methods.

These are Python-internal frozen dataclasses, NOT NATS wire schemas.
The §8.4 wire schemas (``OrderFilled``, ``OrderPlaced``, ``OrderClosed``,
``SLMoved``) are Pydantic and live in :mod:`packages.bus.schemas`. T-216b
(order placement pipeline) and T-218 (execution dispatcher) map domain →
wire at the publish boundary; the mapping mirrors the T-110 pattern where
``OhlcCandlePayload`` (wire) → :class:`packages.features.types.OhlcCandle`
(domain) at the seam.

Decimal precision (§5.13) is preserved on every numeric field. Datetimes
are documented UTC by §N1; tz-awareness enforcement happens in adapters
(via :func:`packages.core.now_utc`), not here — Python's :class:`datetime`
type does not discriminate aware vs. naive at the type level, so the
contract is documented in field comments and verified at the conformance
boundary (T-206).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 — runtime annotation on frozen dataclass field
from decimal import Decimal  # noqa: TC003 — runtime annotation on frozen dataclass field
from typing import Literal

__all__ = [
    "AccountBalance",
    "ExecutionEvent",
    "FundingFee",
    "InstrumentInfo",
    "OrderPlaceResult",
    "Position",
    "PositionEvent",
]


@dataclass(frozen=True, slots=True)
class InstrumentInfo:
    """Per-symbol instrument metadata for qty pre-flight validation (T-529 / H-036).

    Sourced from Bybit GET /v5/market/instruments-info (live) or hardcoded
    fixture (paper). Cached per-adapter with TTL (default 1h via
    ``instruments_info_cache_ttl_s`` ctor kwarg, mirroring set_leverage
    ``_DEFAULT_LEVERAGE_CACHE_TTL_S`` precedent).

    Decimal precision (§5.3) preserved on numeric fields. minNotional pre-flight
    is DEFERRED to T-529-future (requires last_price; out of T-529 narrow
    scope); ``min_notional_usd`` populated for forward-compat but not consumed
    by ``quantize_qty`` in T-529.
    """

    symbol: str
    qty_step: Decimal
    min_order_qty: Decimal
    min_notional_usd: Decimal


@dataclass(frozen=True, slots=True)
class AccountBalance:
    """Account financial snapshot — returned by :meth:`ExchangeClient.get_account_balance` (T-530).

    Live: Bybit ``GET /v5/account/wallet-balance?accountType=UNIFIED`` →
    ``result.list[0]`` account-level totals (totalWalletBalance /
    totalAvailableBalance / totalEquity / totalMarginBalance / totalPerpUPL)
    mapped 1:1 to the 5 fields below.

    Paper: derived from ``paper_trades`` — ``wallet_balance = seed_balance +
    Σ realized_pnl`` (reuses the shipped ``sum_paper_trades_realized_pnl``).
    ``unrealized_pnl`` is ``Decimal('0')`` in paper (NO mark-to-market in
    T-530 scope — paper has no live mark price in-memory; a future
    market-data-coupled task may refine this; documented limitation). The
    other three fields alias ``wallet_balance`` in paper (no margin-lockup
    model): ``total_equity == margin_balance == available_balance ==
    wallet_balance`` when ``unrealized_pnl == 0``.

    All 5 fields are :class:`~decimal.Decimal` money (§5.13 — no float on the
    decode or the paper arithmetic). Per TASKS.md T-530 verbatim: no
    currency/coin field (the bot is USDT-only by config; account-level totals
    are the canonical risk-relevant numbers; T-531 equity snapshots + future
    balance-driven sizing consume this).
    """

    wallet_balance: Decimal
    available_balance: Decimal
    total_equity: Decimal
    margin_balance: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class FundingFee:
    """One funding-settlement record — returned by
    :meth:`ExchangeClient.get_funding_fees_window` (T-532a).

    Live: one Bybit ``GET /v5/asset/transaction-log`` ``type=SETTLEMENT``
    row — ``symbol`` / ``transactionTime`` (Unix ms) / ``funding`` (signed:
    negative = funding paid, positive = funding received). T-532b's
    APScheduler poll tick fans these into ``funding_fees`` rows (migration
    0021) and feeds the T-220 cumulative-delta audit a SEPARATE cumulative
    funding term (OQ-3=A — H-017-clean, never folded into
    ``trades.realized_pnl``).

    Paper: ``get_funding_fees_window`` returns ``[]`` — paper has no
    perpetual-funding model (documented limitation, mirror the T-530
    ``AccountBalance`` paper-limitation / T-534a paper ``sl_price=None``
    posture).

    ``funding`` is :class:`~decimal.Decimal` money (§5.13 — no float on the
    decode; signed). ``settled_at`` is UTC-aware (adapter constructs from the
    Bybit ``transactionTime`` Unix-ms epoch; mirror the ``OrderPlaceResult``
    ``placed_at`` UTC convention — internal frozen dataclass, the UTC
    contract is by construction, NOT a Pydantic validator: these are
    Python-internal frozen dataclasses, NOT NATS wire schemas).
    """

    symbol: str
    settled_at: datetime  # UTC; adapter constructs from Bybit transactionTime ms
    funding: Decimal


@dataclass(frozen=True, slots=True)
class OrderPlaceResult:
    """Returned by :meth:`ExchangeClient.place_market_order`.

    Carries the minimum shape upper layers need to (a) persist
    ``orders.exchange_order_id`` and (b) drive subsequent
    :meth:`ExchangeClient.set_trading_stop` calls.

    Fill price is **not** carried here. Exchanges return the order ack
    before the fill record materializes, so a separate
    :meth:`ExchangeClient.get_fill_price` call (§11.1) is the canonical
    way to read it. The client-side ``correlation_id`` from the upper
    layer is the canonical client-side key; ``order_link_id`` from the
    exchange ack is informational and intentionally not surfaced.

    ``paper_trade_id`` is populated by :class:`PaperExchange` from
    ``insert_paper_trade`` return (T-511b2 / ADR-0010 paper-aware shadow
    runtime); Bybit adapter leaves the default ``None``. Consumed by
    ``placement.py:240-252`` paper-fork to source ``parent_trade_id`` for
    ``ShadowStartPayload`` per ADR-0010 parent_kind discriminator.
    """

    exchange_order_id: str
    placed_at: datetime  # UTC; adapter constructs via packages.core.now_utc()
    paper_trade_id: int | None = None


@dataclass(frozen=True, slots=True)
class Position:
    """REST snapshot of a single symbol's position state — returned by
    :meth:`ExchangeClient.get_positions`.

    ``size`` is **absolute non-negative**: direction lives in ``side``,
    not in the sign of ``size``. ``size == 0`` means the bot is flat for
    this symbol; in that case ``side`` is ``None`` and ``entry_price`` /
    ``leverage`` / ``unrealized_pnl`` / ``sl_price`` are all ``None`` (no
    live position metadata to report). This convention mirrors Bybit V5's
    position API and avoids sign-flip bugs at the wire/domain seam.

    Field set is intentionally minimal (§0.8). T-216 (placement) and
    T-221 (post-restart reconciliation) are the first downstream
    consumers; additional fields (``mark_price``, ``liq_price``,
    ``position_idx``, etc.) land via their own task plan-docs only when
    a concrete consumer surfaces. ``sl_price`` (T-534a) is the first such
    addition — its concrete consumer is the T-534b SL-watchdog, which
    polls :meth:`ExchangeClient.get_positions` to verify the exchange-side
    stop-loss still exists for each open position (mid-session naked-
    position protection).
    """

    symbol: str
    side: Literal["buy", "sell"] | None
    size: Decimal
    entry_price: Decimal | None
    leverage: int | None
    unrealized_pnl: Decimal | None
    sl_price: Decimal | None


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """Single fill emitted by the exchange's private WS stream
    (:meth:`ExchangeClient.stream_executions`).

    Mirrors §7.2 ``executions`` table columns at the adapter boundary
    minus ``exec_type``: per hazard H-024 the dispatcher (T-218) derives
    ``exec_type`` via DB ``execId → order_id`` matching, NOT from any
    field on this event. Order-link fields from the exchange are
    informational only — the adapter MUST NOT carry an interpretation of
    fill semantics (open / partial_tp / sl / trail / close) on this
    type.
    """

    exchange_exec_id: str  # Bybit ``execId``
    exchange_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    price: Decimal
    qty: Decimal
    fee: Decimal  # always populated; may be Decimal("0") for maker / zero-fee tiers
    executed_at: datetime  # UTC


@dataclass(frozen=True, slots=True)
class PositionEvent:
    """Single position update from the exchange's private WS stream
    (:meth:`ExchangeClient.stream_positions`).

    Same field set as :class:`Position` **except** ``sl_price`` (which is
    REST-snapshot-only — see below), plus ``occurred_at`` so consumers
    (T-218 dispatcher) can correlate with execution events by timestamp
    ordering. ``Position`` and ``PositionEvent`` are kept as distinct
    types because the semantics differ: ``Position`` is "current state
    right now" (REST snapshot), ``PositionEvent`` is "discrete moment
    in a stream".

    Deliberate decouple (T-534a, OQ-5=b): ``Position`` gained
    ``sl_price`` but ``PositionEvent`` did NOT. The only SL-existence
    consumer is the T-534b watchdog, which is a periodic
    :meth:`ExchangeClient.get_positions` poll (REST), not a WS-stream
    consumer. Per §0.8 (fields land only when a concrete consumer
    surfaces) adding ``sl_price`` to ``PositionEvent`` would be a
    consumer-less field; the two previously field-mirrored types
    intentionally diverge here.
    """

    symbol: str
    side: Literal["buy", "sell"] | None
    size: Decimal
    entry_price: Decimal | None
    leverage: int | None
    unrealized_pnl: Decimal | None
    occurred_at: datetime  # UTC
