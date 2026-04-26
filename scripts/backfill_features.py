"""Backfill feature values from historical OHLC data (§9.3 lines 1515-1518).

Idempotent: re-running with the same args yields identical ``features``
rows (T-110b ON CONFLICT DO UPDATE on PK). Property test in
``tests/integration/scripts/test_backfill_features_property.py``
verifies §9.3 line 1525 determinism.

Usage::

    python scripts/backfill_features.py \\
        --feature ind.btcusdt.15m.ema_20 \\
        --from 2026-04-01 \\
        --to 2026-04-26 \\
        [--source binance] \\
        [--indicators-yaml configs/features/indicators.yaml] \\
        [--database-url postgresql://...]

Auto-derives ``(symbol, interval)`` from ``--feature`` per the
``ind.{symbol_lowercase}.{interval}.<rest>`` convention; canonical
Bybit-shape symbol = uppercase form. Validates ``--feature`` parses to
a known YAML entry by re-substituting and matching.

§N7 hexagonal: imports only from ``packages/*`` + stdlib + asyncpg +
structlog. No ``from services.*`` (verified by grep guard at
brief-reviewer pre-commit gate).

Decimal→float seam (verbatim mirror T-110c
``pipeline._build_update``; inline because backfill writes via
``insert_feature`` kwargs not via ``FeatureUpdate`` Pydantic):

* ``value_num``: ``float(fv.value_num) if fv.value_num is not None else None``
* ``value_bool``: pass-through.
* ``value_json``: dict comprehension converting Decimal sub-values to
  ``float``, passing through non-Decimal sub-values per L-004
  ``Mapping[str, object]`` openness.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg

from packages.db.queries.feature_engine import fetch_ohlc_range, insert_feature
from packages.features.intervals import INTERVAL_DELTA
from packages.features.types import OhlcCandle
from packages.features.yaml import INDICATORS_YAML_PATH, load_indicators_yaml
from packages.observability import configure, get_logger

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

    from packages.features.protocols import Feature


__all__ = ["main"]


_FEATURE_NAME_PREFIX = "ind"
_DEFAULT_SOURCE = "binance"


def _parse_feature_name(feature_name: str) -> tuple[str, str, str]:
    """Parse ``ind.<symbol-lowercase>.<interval>.<rest>`` → ``(symbol_canonical, interval, rest)``.

    Symbol returned uppercase (canonical Bybit-shape) per T-110b/T-111
    convention; interval is the second segment; rest is the remaining
    suffix (e.g., ``"ema_20"``, ``"vwap_session"``).

    Raises :class:`ValueError` on too-few segments or wrong prefix.
    """
    segments = feature_name.split(".")
    if len(segments) < 4 or segments[0] != _FEATURE_NAME_PREFIX:
        msg = (
            f"feature_name must match {_FEATURE_NAME_PREFIX!r}.<symbol>.<interval>.<rest>; "
            f"got {feature_name!r}"
        )
        raise ValueError(msg)
    symbol_lower = segments[1]
    interval = segments[2]
    rest = ".".join(segments[3:])
    return symbol_lower.upper(), interval, rest


def _parse_iso_datetime(value: str) -> datetime:
    """Parse ISO-8601 date or datetime; UTC-enforced (naive → ``+00:00`` append).

    Accepts both ``2026-04-01`` (date-only) and ``2026-04-01T00:00:00+00:00``
    (full ISO). Naive inputs (no tzinfo) get UTC appended defensively.
    Non-UTC offsets are rejected per §N1.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        msg = f"datetime must be UTC; got offset {parsed.utcoffset()!r} in {value!r}"
        raise ValueError(msg)
    return parsed


async def _backfill_one_feature(
    conn: asyncpg.Connection[asyncpg.Record],
    *,
    feature: Feature,
    feature_name: str,
    symbol: str,
    interval: str,
    source: str,
    from_dt: datetime,
    to_dt: datetime,
    logger: BoundLogger,
) -> int:
    """Iterate OHLC range, compute per candle, upsert. Returns row count.

    Algorithm:

    1. ``fetch_ohlc_range(conn, …)`` → ASC list.
    2. ``deque(maxlen=feature.warmup_candles)``.
    3. For each candle: append; if buffer full → ``feature.compute(tuple(buf))``
       → ``insert_feature(...)`` with ``computed_at = bucket_start +
       INTERVAL_DELTA[interval]``.
    4. Decimal→float seam mirrors T-110c ``pipeline._build_update`` verbatim.

    Returns the number of rows inserted (excluding warmup undershoot
    where buffer < ``warmup_candles``).
    """
    rows = await fetch_ohlc_range(
        conn,
        symbol=symbol,
        interval=interval,
        source=source,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    buf: deque[OhlcCandle] = deque(maxlen=feature.warmup_candles)
    inserted = 0
    for row in rows:
        candle = OhlcCandle(
            symbol=row[0],
            interval=interval,
            bucket_start=row[1],
            open=row[2],
            high=row[3],
            low=row[4],
            close=row[5],
            volume=row[6],
            source=row[7],
        )
        buf.append(candle)
        if len(buf) < feature.warmup_candles:
            continue
        fv = feature.compute(tuple(buf))
        # Decimal→float seam (verbatim mirror T-110c pipeline._build_update):
        value_num_wire = float(fv.value_num) if fv.value_num is not None else None
        value_bool_wire = fv.value_bool
        value_json_wire = (
            {k: float(v) if isinstance(v, Decimal) else v for k, v in fv.value_json.items()}
            if fv.value_json is not None
            else None
        )
        computed_at = candle.bucket_start + INTERVAL_DELTA[interval]
        await insert_feature(
            conn,
            feature_name=feature_name,
            symbol=symbol,
            computed_at=computed_at,
            value_num=value_num_wire,
            value_bool=value_bool_wire,
            value_json=value_json_wire,
            source_version=feature.source_version,
        )
        inserted += 1
    logger.info(
        "feature_backfill_complete",
        feature=feature_name,
        symbol=symbol,
        interval=interval,
        from_dt=from_dt.isoformat(),
        to_dt=to_dt.isoformat(),
        rows_total=len(rows),
        rows_inserted=inserted,
    )
    return inserted


async def _main(args: argparse.Namespace) -> int:
    """Connect, register JSONB codec, look up feature, run backfill. Returns exit code."""
    configure(level="INFO")
    logger = get_logger("backfill_features", "system")
    try:
        symbol, interval, _rest = _parse_feature_name(args.feature)
        from_dt = _parse_iso_datetime(args.from_)
        to_dt = _parse_iso_datetime(args.to)
    except ValueError as exc:
        logger.error("backfill_args_invalid", error=str(exc))
        return 1

    conn = await asyncpg.connect(dsn=args.database_url)
    try:
        # JSONB codec inline (T-112 BLOCKER 2 option A — verbatim mirror of
        # T-110d _register_jsonb_codec args; no cross-service import).
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )
        registry = load_indicators_yaml(args.indicators_yaml, [symbol])
        feature: Feature | None = None
        for entries in registry.values():
            for fname, f in entries:
                if fname == args.feature:
                    feature = f
                    break
            if feature is not None:
                break
        if feature is None:
            logger.error(
                "feature_not_in_yaml",
                feature=args.feature,
                indicators_yaml=str(args.indicators_yaml),
            )
            return 1
        await _backfill_one_feature(
            conn,
            feature=feature,
            feature_name=args.feature,
            symbol=symbol,
            interval=interval,
            source=args.source,
            from_dt=from_dt,
            to_dt=to_dt,
            logger=logger,
        )
    finally:
        await conn.close()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill feature values from historical OHLC data (§9.3).",
    )
    parser.add_argument(
        "--feature",
        required=True,
        help="Substituted feature name, e.g. ind.btcusdt.15m.ema_20",
    )
    parser.add_argument(
        "--from",
        dest="from_",
        required=True,
        help="ISO-8601 start (inclusive), e.g. 2026-04-01 or 2026-04-01T00:00:00+00:00",
    )
    parser.add_argument(
        "--to",
        required=True,
        help="ISO-8601 end (inclusive), e.g. 2026-04-26 or 2026-04-26T23:59:59+00:00",
    )
    parser.add_argument(
        "--source",
        default=_DEFAULT_SOURCE,
        help=f"OHLC source (default: {_DEFAULT_SOURCE!r}).",
    )
    parser.add_argument(
        "--indicators-yaml",
        type=Path,
        default=INDICATORS_YAML_PATH,
        help=f"Path to indicators.yaml (default: {INDICATORS_YAML_PATH}).",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="asyncpg DSN; falls back to DATABASE_URL env var.",
    )
    return parser


def main() -> int:
    """argparse entry; parse args, run async loop, return exit code (0 / 1)."""
    parser = _build_parser()
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url required (or set DATABASE_URL env var)")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
