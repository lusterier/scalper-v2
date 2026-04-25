"""``market.ohlc.1m.<symbol>`` NATS payload schema (§8.4, §9.2).

Published by ``market-data-svc`` (T-104 ``OhlcPipeline``) onto the
``market.ohlc.1m.<symbol>`` subject of the MARKET_OHLC stream after
each Binance kline frame is classified. Consumed by
``feature-engine`` (T-110) for closed-candle feature recompute and
by the UI live-tail (post-F4) for in-progress price updates.

The schema mirrors the §8.4 ``OHLCCandle`` definition; ``interval``
and ``source`` are pinned to ``Literal["1m"]`` / ``Literal["binance"]``
because T-104 publishes only Binance 1m candles. Higher-timeframe
consumers read continuous aggregates from Timescale (T-103), not the
NATS stream.

UTC enforcement on ``bucket_start`` mirrors
:class:`packages.bus.MessageEnvelope` and
:class:`packages.bus.schemas.SignalValidated`: naive or non-zero-offset
values are rejected at validation; serialisation emits the explicit
``+00:00`` form per §5.12.

:data:`_MARKET_OHLC_1M_NS` is the namespace UUID for deterministic
``Nats-Msg-Id`` derivation on **closed** publishes only.
:func:`message_id_for_closed_candle` is the sole entry point; in-progress
publishes use the envelope's default :func:`uuid.uuid4` because every
intra-bucket tick is its own message and server-side dedup would lose
intermediate ticks. (MARKET_OHLC stream config does not yet set
``duplicate_window`` per §8.2; the deterministic ID is forward-compatible
groundwork — see TASKS.md F1+ entry.)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal  # noqa: TC003  # Pydantic resolves annotation at class-creation
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

__all__ = ["OhlcCandlePayload", "message_id_for_closed_candle"]


# Project-unique namespace UUID, minted once for market.ohlc.1m
# deterministic message_id derivation. Do NOT reuse this constant for
# other stream subjects; each (namespace, key) pair must be scoped to
# its own subject to avoid cross-stream collisions.
_MARKET_OHLC_1M_NS: UUID = UUID("c7c1de13-b8e6-4ad6-9b21-7b7e6e5e0a9a")


def message_id_for_closed_candle(symbol: str, bucket_start: datetime) -> UUID:
    """Return the deterministic ``Nats-Msg-Id`` for a closed-candle publish.

    :func:`uuid.uuid5` is stable over ``(namespace, name)`` pairs, so two
    publishes for the same ``(symbol, bucket_start)`` produce the same
    ``UUID`` → the same ``Nats-Msg-Id`` header. When MARKET_OHLC's
    ``duplicate_window`` is configured (see TASKS.md F1+), this dedups
    re-published closed candles (e.g., from T-105 backfill resync after
    a reconnect) server-side.

    Only call for **closed** candles — in-progress publishes want a fresh
    ``uuid4()`` per tick so JetStream stores every intra-bucket update.
    """
    if bucket_start.tzinfo is None or bucket_start.utcoffset() != timedelta(0):
        msg = "bucket_start must be timezone-aware UTC"
        raise ValueError(msg)
    return uuid5(_MARKET_OHLC_1M_NS, f"{symbol}:{bucket_start.isoformat()}")


class OhlcCandlePayload(BaseModel):
    """§8.4 ``market.ohlc.1m.<symbol>`` payload, frozen.

    Decimal prices/volume preserve the precision of Binance's
    string-encoded numerics across the §7.2 ``NUMERIC(30, 12)``
    persistence boundary; downstream consumers (T-110 feature-engine)
    therefore receive the same precision they would read from the DB.

    ``is_closed`` discriminates closed vs in-progress publishes on the
    same subject (§9.2 line 1462: in-progress emitted for UI live tail
    but not persisted; only closed cause feature recompute, §8.4
    docstring).
    """

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    symbol: str
    interval: Literal["1m"] = "1m"
    bucket_start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: Literal["binance"] = "binance"
    is_closed: bool

    @field_validator("bucket_start")
    @classmethod
    def _must_be_utc(cls, value: datetime) -> datetime:
        """Require UTC-aware datetimes; normalise tzinfo to :data:`datetime.UTC`."""
        if value.tzinfo is None:
            msg = "bucket_start must be timezone-aware (tzinfo=datetime.UTC)"
            raise ValueError(msg)
        if value.utcoffset() != timedelta(0):
            msg = "bucket_start must be in UTC (utcoffset must be zero)"
            raise ValueError(msg)
        return value if value.tzinfo is UTC else value.replace(tzinfo=UTC)

    @field_serializer("bucket_start")
    def _serialize_bucket_start(self, value: datetime) -> str:
        """ISO-8601 with explicit ``+00:00`` offset (§5.12)."""
        return value.isoformat()
