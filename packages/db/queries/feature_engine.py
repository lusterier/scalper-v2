"""feature-engine query module (§5.10, §7.2, §9.3).

Owned by ``services/feature_engine`` (T-110c, future); imported by the
T-110c ``FeaturePipeline`` for per-closed-candle feature persistence
and by the T-110c lifespan for warmup-window load. Raw asyncpg per
brief §5.10 ("all queries in hot paths are raw SQL via asyncpg,
parameterized").

Pure asyncpg port — does NOT import from :mod:`packages.features`.
:func:`fetch_warmup_window` returns rows as :data:`OhlcRow` tuples;
T-110c maps each tuple to :class:`packages.features.types.OhlcCandle`
on the use-case side. This keeps :mod:`packages.db` decoupled from
:mod:`packages.features` (no cross-package import; preserves §N7
hexagonal seam).

JSONB codec: :func:`insert_feature` writes ``value_json`` through
asyncpg's JSONB type. Caller MUST register the codec on the
connection — T-110c lifespan handles registration in production
(either via a future ``packages/db/pool.py:create_pool`` ``init``
callback extension OR via ``$N::jsonb`` cast in SQL with
``json.dumps()`` at the call site, mirror precedent
``packages/db/queries/signal_gateway.py`` line 96). T-110b integration
tests register the codec per-connection in fixtures (mirror T-108
``test_0004_migration`` pattern).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from decimal import Decimal

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]


__all__ = [
    "LatestFeatureRow",
    "OhlcRow",
    "fetch_ohlc_range",
    "fetch_warmup_window",
    "insert_feature",
    "select_latest_feature",
]


# Public row alias — T-110c imports for OhlcCandle(*row) mapping on
# the use-case side. Tuple field order:
# (symbol, bucket_start, open, high, low, close, volume, source)
type OhlcRow = tuple[str, "datetime", "Decimal", "Decimal", "Decimal", "Decimal", "Decimal", str]


@dataclass(frozen=True, slots=True)
class LatestFeatureRow:
    """Flat-projection of the four ``features`` columns T-306 resolver reads.

    §N7 hexagonal seam: this module does NOT import :mod:`packages.features`
    (see file-level docstring lines 9-14). Use-case layer
    (:mod:`packages.scoring.resolver`) maps :class:`LatestFeatureRow`
    primitives to :class:`packages.features.types.FeatureValue`. Mirrors
    :data:`OhlcRow` precedent (line 48): primitives in, domain conversion
    on the use-case side.
    """

    value_num: float | None
    value_bool: bool | None
    value_json: dict[str, object] | None
    computed_at: datetime


# Allow-list of supported intervals → source table. Validated before
# any f-string interpolation in :func:`fetch_warmup_window`, so the
# SQL identifier is never user-controlled. ``ohlc_1m`` is the raw
# hypertable; the rest are continuous aggregates per T-103 migration
# 0003 with identical column shape (verified at `migrations/versions/
# 0003_*.py:147-163`).
_INTERVAL_TO_TABLE: Final[Mapping[str, str]] = {
    "1m": "ohlc_1m",
    "5m": "ohlc_5m",
    "15m": "ohlc_15m",
    "1h": "ohlc_1h",
    "4h": "ohlc_4h",
    "1d": "ohlc_1d",
}


async def insert_feature(
    conn: _DbExecutor,
    *,
    feature_name: str,
    symbol: str,
    computed_at: datetime,
    value_num: float | None,
    value_bool: bool | None,
    value_json: Mapping[str, object] | None,
    source_version: str,
) -> None:
    """Upsert one feature value into ``features`` (§7.2, §9.3 line 1494).

    PK ``(feature_name, symbol, computed_at, source_version)`` per
    T-108 migration 0004. ON CONFLICT DO UPDATE updates the three
    value fields verbatim from EXCLUDED — re-write with identical
    inputs is a no-op against equal values; an in-place algorithm
    fix that re-emits with a refined value writes through.

    Genuinely idempotent (same inputs → same row state regardless of
    call count) — no ``@non_idempotent`` marker per §N3 docstring-
    marker form (mirrors :func:`packages.db.queries.market_data.insert_ohlc_1m`
    precedent at lines 73-96). Differs from
    :func:`packages.db.queries.signal_gateway.insert_signal`, which
    returns a fresh ``id`` per call and is therefore non-idempotent.

    ``computed_at`` must be UTC-aware (caller contract; mirror
    ``insert_ohlc_1m`` precedent — Pydantic validator on the wire side
    catches naive timestamps before this seam, so re-validation here
    would be redundant overhead).

    ``value_json`` is JSON-encoded via asyncpg's JSONB codec. Caller
    MUST register the codec on the connection (mirror T-108
    ``test_0004_migration`` per-connection pattern). T-110c lifespan
    handles registration in production.
    """
    await conn.execute(
        """
        INSERT INTO features (
            feature_name, symbol, computed_at,
            value_num, value_bool, value_json, source_version
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (feature_name, symbol, computed_at, source_version) DO UPDATE SET
            value_num = EXCLUDED.value_num,
            value_bool = EXCLUDED.value_bool,
            value_json = EXCLUDED.value_json
        """,
        feature_name,
        symbol,
        computed_at,
        value_num,
        value_bool,
        value_json,
        source_version,
    )


async def fetch_warmup_window(
    conn: _DbExecutor,
    *,
    symbol: str,
    interval: str,
    n: int,
    source: str,
) -> list[OhlcRow]:
    """Read the last ``n`` closed candles for ``(symbol, interval, source)``.

    For ``interval == "1m"`` queries the raw hypertable ``ohlc_1m``.
    For higher timeframes queries the corresponding cagg
    (``ohlc_5m`` / ``_15m`` / ``_1h`` / ``_4h`` / ``_1d``), which is
    auto-refreshed from the raw 1m hypertable per T-103 schedule.
    Both have identical column shape per migration 0003.

    Returns at most ``n`` materialised tuples ordered by
    ``bucket_start`` ASC (oldest first), so callers can iterate
    ``for row in result: registry.push(...)`` in time order. The body
    materialises via comprehension over ``await conn.fetch(...)`` so
    the return type is ``list[tuple]`` rather than
    ``list[asyncpg.Record]`` — decouples T-110c from
    :class:`asyncpg.Record`.

    Unknown ``interval`` raises :class:`ValueError` **before** any
    SQL emission; the allow-list ``_INTERVAL_TO_TABLE`` is the only
    source of source-table identifiers, so user-controlled SQL
    injection is impossible at this seam (defence in depth even
    though ``interval`` is internal, never user-supplied).

    Empty result returns ``[]``; under-fill (table holds fewer than
    ``n`` rows) returns the available prefix — warmup will under-fill
    and :meth:`Feature.compute` raises
    :class:`packages.features.errors.FeatureUnderflowError` on first
    call until live frames catch up.

    Read-only — no idempotency marker (markers are for external
    writes per §N3).
    """
    table = _INTERVAL_TO_TABLE.get(interval)
    if table is None:
        msg = f"unknown interval {interval!r}; supported intervals: {sorted(_INTERVAL_TO_TABLE)}"
        raise ValueError(msg)
    # Identifier interpolation safe: `table` came from the allow-list above.
    sql = f"""
        SELECT symbol, bucket_start, open, high, low, close, volume, source
        FROM (
            SELECT symbol, bucket_start, open, high, low, close, volume, source
            FROM {table}
            WHERE symbol = $1 AND source = $2
            ORDER BY bucket_start DESC
            LIMIT $3
        ) AS recent
        ORDER BY bucket_start ASC
    """  # noqa: S608  # nosec B608 — `table` is allow-listed above; not user-controlled
    rows = await conn.fetch(sql, symbol, source, n)
    return [
        (
            r["symbol"],
            r["bucket_start"],
            r["open"],
            r["high"],
            r["low"],
            r["close"],
            r["volume"],
            r["source"],
        )
        for r in rows
    ]


async def fetch_ohlc_range(
    conn: _DbExecutor,
    *,
    symbol: str,
    interval: str,
    source: str,
    from_dt: datetime,
    to_dt: datetime,
) -> list[OhlcRow]:
    """Read all OHLC rows in ``[from_dt, to_dt]`` (inclusive) for ``(symbol, interval, source)``.

    For ``interval == "1m"`` queries the raw hypertable ``ohlc_1m``; for
    higher intervals queries the corresponding T-103 cagg
    (``ohlc_5m`` / ``_15m`` / ``_1h`` / ``_4h`` / ``_1d``). Mirrors
    :func:`fetch_warmup_window` shape (allow-list interval routing,
    materialised tuple list, ASC by ``bucket_start``) but with date-range
    filter instead of ``LIMIT N``. Used by T-112 ``backfill_features``
    CLI to iterate historical OHLC and compute feature values.

    Unknown ``interval`` raises :class:`ValueError` (allow-list guard via
    :data:`_INTERVAL_TO_TABLE`). Empty range returns ``[]``. Read-only
    — no idempotency marker (markers are for external writes per §N3).
    """
    table = _INTERVAL_TO_TABLE.get(interval)
    if table is None:
        msg = f"unknown interval {interval!r}; supported intervals: {sorted(_INTERVAL_TO_TABLE)}"
        raise ValueError(msg)
    sql = f"""
        SELECT symbol, bucket_start, open, high, low, close, volume, source
        FROM {table}
        WHERE symbol = $1 AND source = $2
          AND bucket_start >= $3 AND bucket_start <= $4
        ORDER BY bucket_start ASC
    """  # noqa: S608  # nosec B608 — `table` is allow-listed above; not user-controlled
    rows = await conn.fetch(sql, symbol, source, from_dt, to_dt)
    return [
        (
            r["symbol"],
            r["bucket_start"],
            r["open"],
            r["high"],
            r["low"],
            r["close"],
            r["volume"],
            r["source"],
        )
        for r in rows
    ]


_SELECT_LATEST_FEATURE_SQL = """
    SELECT value_num, value_bool, value_json, computed_at
    FROM features
    WHERE feature_name = $1 AND symbol = $2
    ORDER BY computed_at DESC
    LIMIT 1
"""


async def select_latest_feature(
    conn: _DbExecutor,
    *,
    feature_name: str,
    symbol: str,
) -> LatestFeatureRow | None:
    """Return latest ``features`` row for ``(feature_name, symbol)``, or None.

    Uses the ``features_latest`` index ``(feature_name, symbol, computed_at DESC)``
    per migration 0004:104 — single fetch with ``ORDER BY computed_at DESC LIMIT 1``.
    Empty result returns ``None``.

    Returns :class:`LatestFeatureRow` (primitives only — no
    :class:`packages.features.types.FeatureValue` cross-import per §N7
    seam at file:9-14). Use-case (resolver) maps to FeatureValue.

    Read-only — no idempotency marker (markers are for external writes
    per §N3, mirror :func:`fetch_warmup_window` precedent at line 162).
    """
    row = await conn.fetchrow(_SELECT_LATEST_FEATURE_SQL, feature_name, symbol)
    if row is None:
        return None
    return LatestFeatureRow(
        value_num=row["value_num"],
        value_bool=row["value_bool"],
        value_json=row["value_json"],
        computed_at=row["computed_at"],
    )
