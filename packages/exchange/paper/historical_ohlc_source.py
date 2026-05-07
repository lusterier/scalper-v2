"""Historical OHLC replay source (T-503 / brief §12.2:1954-1955).

Async-iterable cursor-streamed replay of ``ohlc_1m`` hypertable rows
for a given symbol set + closed-open time window + source. Yields
:class:`OHLCRow` instances ordered by ``bucket_start ASC`` with
**configurable pace control** (``"1x"`` real-time / ``"10x"`` accelerated /
``"max"`` no-wait) per BRIEF §12.2:1955.

Read-only consumer; no writes. Pace algorithm per WG#2: between yields,
``asyncio.sleep(max(0, target_real_dt - elapsed))`` where
``target_real_dt = (bucket_start - first_bucket).total_seconds() / pace_factor``.
``pace="max"`` short-circuits the entire pace computation (``_pace_factor !=
float("inf")`` branch is skipped) — yields complete fast as DB returns.

L-011 read-side **N/A** acknowledgment: ``ohlc_1m`` schema has NO JSONB
columns (just NUMERIC(30,12) OHLCV + TIMESTAMPTZ + TEXT). Defensive
JSONB decode (mirror T-504 historical_signal_source.py) is not needed
here. Explicit note for symmetry with T-504 and future code archeology.

Cursor + transaction lifetime caveat (mirror T-504): ``async for row in
conn.cursor(...)`` consumes within the wrapping ``conn.transaction()``
block — once iteration starts, the pool connection is held from the
first yield until iterator exhaustion or consumer error. Acceptable
for T-507 CLI single-bot replay (one connection, no contention; pool
size of 1 sufficient). T-509 worker concurrency (multiple parallel
backtests in analytics-api lifespan) MUST size the pool accordingly
— flagged out-of-scope for T-503.

Boundary-check belt-and-suspenders (mirror T-505 module docstring 19-26):
constructor raises ``ValueError`` on empty ``symbols``, ``to_at <= from_at``,
invalid ``pace``, invalid ``source``. T-507 CLI is the upstream user-input
boundary; T-503's runtime checks are belt-and-suspenders during F5 build-up.
T-519 hazard audit may reassess if upstream T-507 already enforces.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from packages.core.time import now_utc

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from datetime import datetime
    from decimal import Decimal

    import asyncpg


__all__ = ["HistoricalOHLCSource", "OHLCRow", "Pace"]


Pace = Literal["1x", "10x", "max"]

# pace_factor: divisor on simulated-time gap to compute target real-time
# delay. "1x" = real-time replay (1 simulated minute = 60 real seconds).
# "10x" = 10x acceleration (1 simulated minute = 6 real seconds). "max"
# = no wait. Mathematically fixed per BRIEF §12.2:1955 enum; not a
# tunable knob beyond the discrete 3-value set.
_PACE_FACTOR: dict[str, float] = {"1x": 1.0, "10x": 10.0, "max": float("inf")}

# Asyncpg-cursor chunk size; infra tuning constant, NOT a business knob (no
# observable production behavior beyond memory footprint). §N9-exempt per
# L-001 active control: "polling/timing/pacing constants in BUSINESS LOGIC
# violate §N9 even if sensible defaults" — cursor prefetch is infrastructure,
# not business logic. Unexposed to constructor per §0.8 anti-hypothetical-knob
# discipline.
_DEFAULT_PREFETCH = 1000

_VALID_SOURCES = ("binance", "bybit")

_SELECT_OHLC_REPLAY_SQL = """
    SELECT symbol, bucket_start, open, high, low, close, volume, source
    FROM ohlc_1m
    WHERE symbol = ANY($1)
      AND bucket_start >= $2
      AND bucket_start < $3
      AND source = $4
    ORDER BY bucket_start ASC
"""


@dataclass(frozen=True, slots=True)
class OHLCRow:
    """Full projection of ``ohlc_1m`` row (8 fields per BRIEF §7.2:911-924)."""

    symbol: str
    bucket_start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str  # 'binance' | 'bybit'


def _row_to_ohlc(row: asyncpg.Record) -> OHLCRow:
    """Narrow asyncpg row to typed :class:`OHLCRow`.

    NUMERIC(30,12) OHLCV pass-through Decimal (asyncpg native).
    bucket_start passed through as TIMESTAMPTZ datetime.
    """
    return OHLCRow(
        symbol=str(row["symbol"]),
        bucket_start=row["bucket_start"],
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
        source=str(row["source"]),
    )


class HistoricalOHLCSource:
    """Async-iterable cursor-streamed historical OHLC replay with pace control.

    See module docstring for full algorithm + L-011-N/A note + cursor+tx
    lifetime caveat + boundary-check belt-and-suspenders rationale.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        symbols: list[str],
        from_at: datetime,
        to_at: datetime,
        pace: Pace = "max",
        source: str = "binance",
        now_fn: Callable[[], datetime] = now_utc,
    ) -> None:
        if not symbols:
            msg = "HistoricalOHLCSource: symbols must not be empty"
            raise ValueError(msg)
        if to_at <= from_at:
            msg = (
                f"HistoricalOHLCSource: to_at must be > from_at "
                f"(from_at={from_at!r}, to_at={to_at!r})"
            )
            raise ValueError(msg)
        if pace not in _PACE_FACTOR:
            msg = (
                f"HistoricalOHLCSource: invalid pace {pace!r}; expected one of {list(_PACE_FACTOR)}"
            )
            raise ValueError(msg)
        if source not in _VALID_SOURCES:
            msg = (
                f"HistoricalOHLCSource: invalid source {source!r}; "
                f"expected one of {list(_VALID_SOURCES)}"
            )
            raise ValueError(msg)
        self._pool = pool
        self._symbols = list(symbols)
        self._from_at = from_at
        self._to_at = to_at
        self._pace_factor = _PACE_FACTOR[pace]
        self._source = source
        self._now_fn = now_fn

    async def __aiter__(self) -> AsyncIterator[OHLCRow]:
        async with self._pool.acquire() as conn, conn.transaction():
            replay_start_real: datetime | None = None
            first_bucket_at: datetime | None = None
            async for row in conn.cursor(
                _SELECT_OHLC_REPLAY_SQL,
                self._symbols,
                self._from_at,
                self._to_at,
                self._source,
                prefetch=_DEFAULT_PREFETCH,
            ):
                ohlc = _row_to_ohlc(row)
                if replay_start_real is None:
                    replay_start_real = self._now_fn()
                    first_bucket_at = ohlc.bucket_start
                elif self._pace_factor != float("inf"):
                    # WG#2 short-circuit: skip pace computation entirely at pace="max".
                    sim_dt = (ohlc.bucket_start - first_bucket_at).total_seconds()  # type: ignore[operator]
                    target_real_dt = sim_dt / self._pace_factor
                    elapsed = (self._now_fn() - replay_start_real).total_seconds()
                    sleep_for = max(0.0, target_real_dt - elapsed)
                    if sleep_for > 0:
                        await asyncio.sleep(sleep_for)
                yield ohlc
