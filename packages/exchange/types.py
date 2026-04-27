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
    "ExecutionEvent",
    "OrderPlaceResult",
    "Position",
    "PositionEvent",
]


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
    """

    exchange_order_id: str
    placed_at: datetime  # UTC; adapter constructs via packages.core.now_utc()


@dataclass(frozen=True, slots=True)
class Position:
    """REST snapshot of a single symbol's position state — returned by
    :meth:`ExchangeClient.get_positions`.

    ``size`` is **absolute non-negative**: direction lives in ``side``,
    not in the sign of ``size``. ``size == 0`` means the bot is flat for
    this symbol; in that case ``side`` is ``None`` and ``entry_price`` /
    ``leverage`` / ``unrealized_pnl`` are all ``None`` (no live position
    metadata to report). This convention mirrors Bybit V5's position API
    and avoids sign-flip bugs at the wire/domain seam.

    Field set is intentionally minimal (§0.8). T-216 (placement) and
    T-221 (post-restart reconciliation) are the first downstream
    consumers; additional fields (``mark_price``, ``liq_price``,
    ``position_idx``, etc.) land via their own task plan-docs only when
    a concrete consumer surfaces.
    """

    symbol: str
    side: Literal["buy", "sell"] | None
    size: Decimal
    entry_price: Decimal | None
    leverage: int | None
    unrealized_pnl: Decimal | None


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

    Same field set as :class:`Position` plus ``occurred_at`` so consumers
    (T-218 dispatcher) can correlate with execution events by timestamp
    ordering. ``Position`` and ``PositionEvent`` are kept as distinct
    types because the semantics differ: ``Position`` is "current state
    right now" (REST snapshot), ``PositionEvent`` is "discrete moment
    in a stream".
    """

    symbol: str
    side: Literal["buy", "sell"] | None
    size: Decimal
    entry_price: Decimal | None
    leverage: int | None
    unrealized_pnl: Decimal | None
    occurred_at: datetime  # UTC
