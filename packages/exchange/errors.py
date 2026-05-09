"""§11.3 exception hierarchy for :mod:`packages.exchange`.

All exchange-side errors inherit from :class:`ExchangeError`, which
itself inherits from :class:`packages.core.ScalperError`. Upper layers
(``execution-service``) narrow to ``except ExchangeError`` and map each
concrete subclass to a decision per §11.3:

- :class:`RateLimitError` → coordinated pause via shared NATS KV (§11.4 / H-025).
- :class:`AuthError` → abort, alert operator.
- :class:`OrderRejected` → log + drop the request; do not retry the place.
- :class:`NetworkTimeout` → retry per the adapter retry matrix (§11.2).
- :class:`UnknownState` → no retry; reconcile on next startup (H-003).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from packages.core import ScalperError

if TYPE_CHECKING:
    from decimal import Decimal

    from packages.exchange.types import InstrumentInfo

__all__ = [
    "AuthError",
    "ExchangeError",
    "NetworkTimeout",
    "OrderRejected",
    "QtyValidationError",
    "RateLimitError",
    "UnknownState",
]


class ExchangeError(ScalperError):
    """Base class for errors raised by :mod:`packages.exchange` adapters."""


class RateLimitError(ExchangeError):
    """Bybit retCode 10006 / 10016 (per-endpoint or IP rate limit hit).

    Triggers the shared coordinated pause flag (§11.4 / H-025) so every
    adapter on the same IP observes the backoff, not just the offending
    one.
    """


class AuthError(ExchangeError):
    """Auth/signing error — invalid HMAC, expired key, IP not whitelisted."""


class OrderRejected(ExchangeError):
    """Order rejected at submit time.

    ``reason`` examples per §11.3: ``'insufficient_margin'``,
    ``'price_deviation'``. The string is free-form (adapter-mapped from
    the exchange's retCode/retMsg) — no closed enum here, since Bybit's
    rejection-reason space is open and grows over time.
    """

    def __init__(self, reason: str, *args: object) -> None:
        super().__init__(reason, *args)
        self.reason = reason


class NetworkTimeout(ExchangeError):
    """Request timed out at the network layer for an idempotent call.

    Caller may retry per the adapter's retry matrix (§11.2 — three
    attempts with backoff ``[0.5, 1.0, 2.0]s + jitter`` for idempotent
    methods). Non-idempotent calls (``place_market_order``) raise
    :class:`UnknownState` instead — see hazard H-003.
    """


class UnknownState(ExchangeError):
    """Raised when the adapter cannot determine whether an external
    write took effect — typically a :meth:`ExchangeClient.place_market_order`
    timeout (H-003).

    The order may have succeeded; retrying would create a duplicate
    position. Reconciliation on next startup (T-221) determines the real
    state. ``last_known_action`` carries adapter-side context for the
    audit log (e.g., ``'place_market_order_timeout'``).
    """

    def __init__(self, last_known_action: str, *args: object) -> None:
        super().__init__(last_known_action, *args)
        self.last_known_action = last_known_action


class QtyValidationError(ExchangeError):
    """Pre-flight qty validation failed (T-529 / H-036).

    Raised BEFORE :meth:`ExchangeClient.place_market_order` HTTP call when
    qty does not satisfy instrument constraints (qtyStep alignment OR
    minOrderQty floor). Distinct from :class:`OrderRejected` which is
    Bybit-side post-call reject.

    Caller (placement.py) catches and emits ``execution.qty_validation_failed``
    log + returns early (no Bybit round-trip; no NATS publish; no rate-limit
    token spent).
    """

    def __init__(
        self,
        symbol: str,
        constraint: Literal["qty_step", "min_order_qty"],
        actual_qty: Decimal,
        info: InstrumentInfo,
    ) -> None:
        msg = (
            f"qty validation failed for {symbol}: constraint={constraint} "
            f"actual_qty={actual_qty} qty_step={info.qty_step} "
            f"min_order_qty={info.min_order_qty}"
        )
        super().__init__(msg)
        self.symbol = symbol
        self.constraint = constraint
        self.actual_qty = actual_qty
        self.info = info
