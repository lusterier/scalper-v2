"""§11.1 ExchangeClient port — protocol that BybitV5Adapter (T-207..T-208)
and PaperExchange (T-211..T-213) implement.

Per §5.3 the contract is structural and mypy-time only.
``@runtime_checkable`` is intentionally **not** applied; mirroring the
:class:`packages.features.protocols.Feature` rationale, an
``isinstance(obj, ExchangeClient)`` check would silently accept
implementations missing methods or with mismatched signatures, since
``runtime_checkable`` only verifies attribute presence. T-206 conformance
test does explicit method introspection where it needs runtime checks.

Idempotency markers (§5.8 / §N3) on every external-write method enforce
hazard H-003: every concrete adapter MUST inherit a labeled method or
raise :class:`packages.core.UnlabeledMethodError` at conformance time.
``stream_executions``, ``stream_positions``, and ``close`` are exempt
per :data:`_UNLABELED_METHODS` — see the rationale block below.

Brief artifacts overruled in this file:

- Q11 (T-200 plan-doc §Decisions committed Q11 — source of truth):
  :meth:`set_trading_stop` ships ``tpsl_mode: Literal["Full", "Partial"]``
  with **NO default**, despite §11.1 line 1857 showing ``= "Full"``.
  H-013 invariant (no default; compile error if omitted) wins.
- OQ-1 (T-201 plan-doc §Decisions committed OQ-1): :meth:`stream_executions`
  and :meth:`stream_positions` declared as ``def`` (NOT ``async def``),
  returning :class:`AsyncIterator`. Brief §11.1 lines 1878-1879 show
  ``async def`` — drafting artifact (same class as Q11). Caller idiom is
  ``async for event in client.stream_executions(): ...``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from packages.core import idempotent, non_idempotent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime
    from decimal import Decimal

    from .types import (
        AccountBalance,
        ExecutionEvent,
        FundingFee,
        InstrumentInfo,
        OrderPlaceResult,
        Position,
        PositionEvent,
    )

__all__ = ["ExchangeClient"]


# Frozenset codifies the protocol-level exemption from §N3 marker invariant.
# T-206 conformance test (later F2 task) will read this set to assert: every
# Protocol method NOT in this set carries either @idempotent or
# @non_idempotent. stream_* + close() are lifecycle/IO patterns, not
# request-response writes — marker semantics don't apply.
#
# Module-private (`_` prefix); accessed only by sibling test machinery in this
# package, not external callers — intentionally NOT re-exported through
# `__init__.py`.
_UNLABELED_METHODS: frozenset[str] = frozenset({"stream_executions", "stream_positions", "close"})


class ExchangeClient(Protocol):
    """Port for exchange adapters (live Bybit, paper, future venues).

    Method groups by §11.2 retry matrix:

    - **Writes** (require idempotency labeling per §N3):
      :meth:`set_leverage` (idempotent, 3x retry),
      :meth:`place_market_order` (non-idempotent, 0 retries — H-003),
      :meth:`set_trading_stop` (idempotent, 3x retry),
      :meth:`cancel_order` (idempotent, 3x retry).
    - **Reads** (also labeled idempotent — side-effect-free, retry-eligible):
      :meth:`get_positions`, :meth:`get_fill_price`,
      :meth:`get_closed_pnl_cumulative`.
    - **Streams** (exempt from markers — open async iterators, not writes):
      :meth:`stream_executions`, :meth:`stream_positions`.
    - **Lifecycle** (exempt from markers — cleanup, not retryable):
      :meth:`close`.

    See :data:`_UNLABELED_METHODS` for the conformance-test exemption set.
    """

    @idempotent
    async def set_leverage(self, symbol: str, leverage: int) -> None: ...

    @non_idempotent
    async def place_market_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: Decimal,
        reduce_only: bool = False,
    ) -> OrderPlaceResult: ...

    @idempotent
    async def set_trading_stop(
        self,
        symbol: str,
        tpsl_mode: Literal["Full", "Partial"],
        sl_price: Decimal | None = None,
        tp_price: Decimal | None = None,
        tp_size: Decimal | None = None,
    ) -> None: ...

    @idempotent
    async def cancel_order(self, symbol: str, order_id: str) -> None: ...

    @idempotent
    async def get_positions(self, symbol: str | None = None) -> list[Position]: ...

    @idempotent
    async def get_fill_price(self, symbol: str, order_id: str) -> Decimal | None: ...

    @idempotent
    async def get_instrument_info(self, symbol: str) -> InstrumentInfo:
        """Return per-symbol metadata (qty_step + min_order_qty + min_notional_usd).

        T-529 / H-036 — pre-flight qty validation source. Cached per-adapter
        with TTL (default 1h). ``@idempotent`` because metadata is
        deterministic for same symbol within cache TTL window.

        Live: Bybit GET /v5/market/instruments-info; paper: hardcoded fixture.
        Unknown symbol → :class:`OrderRejected`.
        """
        ...

    @idempotent
    async def get_closed_pnl_cumulative(self, sub_account: str) -> Decimal: ...

    @idempotent
    async def get_closed_pnl_window(self, sub_account: str, since: datetime) -> Decimal: ...

    @idempotent
    async def get_account_balance(self, sub_account: str) -> AccountBalance:
        """Read-only account financial snapshot (§11.1 extension; T-530).

        ``@idempotent`` — a pure read; retry-safe (mirror
        :meth:`get_closed_pnl_cumulative` / :meth:`get_instrument_info`). The
        CI conformance test enforces the marker. ``sub_account`` is the bot's
        sub-account string; the adapter validates it == its own bound
        sub_account BEFORE any rate-limit acquire (caller-mistake guard,
        verbatim mirror :meth:`get_closed_pnl_cumulative`).

        Live: Bybit ``GET /v5/account/wallet-balance?accountType=UNIFIED`` →
        ``result.list[0]`` account-level totals. Paper: derived from
        ``paper_trades`` (seed + Σ realized; ``unrealized_pnl=Decimal('0')`` —
        no paper mark-to-market in T-530 scope, documented limitation).
        Unblocks T-531 (equity snapshots).
        """
        ...

    @idempotent
    async def get_mark_price(self, symbol: str) -> Decimal:
        """Pre-trade reference price for §B.1 sizing (T-527b2 first consumer).

        ``@idempotent`` — a pure read; retry-safe (mirror
        :meth:`get_instrument_info`). The CI conformance test enforces the
        marker. Public-market read: NO sub-account scope (contrast
        :meth:`get_account_balance`). **NOT cached** — mark price is live
        market data (deliberate divergence from :meth:`get_instrument_info`'s
        1h-TTL deterministic-metadata cache; ADR-0013).

        Live: Bybit ``GET /v5/market/tickers?category=linear&symbol=…`` →
        ``result.list[0].markPrice`` (the liquidation/PnL reference price,
        manipulation-resistant — NOT lastPrice/indexPrice). Paper: last
        observed OHLC close (the same source PaperExchange simulates fills
        from — backtest/replay-deterministic). Unknown symbol →
        :class:`OrderRejected`.
        """
        ...

    @idempotent
    async def get_funding_fees_window(self, sub_account: str, since: datetime) -> list[FundingFee]:
        """Funding settlements in ``[since, now]`` (T-532a; T-532b first consumer).

        ``@idempotent`` — a pure read; retry-safe (mirror
        :meth:`get_closed_pnl_window` — the windowed-paginated-pull sibling).
        The CI conformance test enforces the marker. ``sub_account`` is the
        bot's sub-account string; the adapter validates it == its own bound
        sub_account BEFORE any rate-limit acquire (verbatim mirror
        :meth:`get_closed_pnl_window`). UTC contract: caller MUST pass an
        aware UTC datetime; ``int(since.timestamp() * 1000)`` → Bybit
        ``startTime`` Unix ms.

        Live: Bybit ``GET /v5/asset/transaction-log`` ``type=SETTLEMENT``,
        cursor-paginated → one :class:`FundingFee` per row (``symbol`` /
        ``transactionTime`` ms → ``settled_at`` / ``funding`` signed
        ``Decimal``). Deliberate divergence from
        :meth:`get_closed_pnl_window`'s ``-> Decimal`` aggregate: storage
        (migration 0021 ``funding_fees``) + the T-220 cumulative-delta audit
        (OQ-3=A separate cumulative funding term, H-017-clean) need
        per-settlement records, NOT a sum. Paper: ``[]`` — no
        perpetual-funding model (documented limitation).
        """
        ...

    def stream_executions(self) -> AsyncIterator[ExecutionEvent]: ...

    def stream_positions(self) -> AsyncIterator[PositionEvent]: ...

    async def close(self) -> None: ...
