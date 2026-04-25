"""ohlc_1m hypertable + 5 continuous aggregates (5m/15m/1h/4h/1d).

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-25

Third migration for scalper-v2 (brief §7.2 ``ohlc_1m`` DDL + cagg
example, §18.3 retention/compression, §19 F1 bullet 4).

Creates the ``ohlc_1m`` 1-minute candle hypertable that
``market-data-svc`` (T-104) writes to, plus five continuous aggregates
(``ohlc_5m``, ``ohlc_15m``, ``ohlc_1h``, ``ohlc_4h``, ``ohlc_1d``)
that ``feature-engine`` (T-110) and ``backfill_features`` (T-112)
read from.

ohlc_1m shape verbatim from §7.2:

* ``symbol``        TEXT NOT NULL                  — Bybit canonical
* ``bucket_start``  TIMESTAMPTZ NOT NULL           — hypertable time column
* ``open``          NUMERIC(30, 12) NOT NULL
* ``high``          NUMERIC(30, 12) NOT NULL
* ``low``           NUMERIC(30, 12) NOT NULL
* ``close``         NUMERIC(30, 12) NOT NULL
* ``volume``        NUMERIC(30, 12) NOT NULL
* ``source``        TEXT NOT NULL                  — 'binance' | 'bybit'
* ``PRIMARY KEY (symbol, bucket_start, source)``   — composite; brief §9.2
                                                     "Idempotent via PK
                                                     (symbol, bucket_start,
                                                     source)"

§18.3 lifecycle policies on ohlc_1m:

* ``add_retention_policy('ohlc_1m', INTERVAL '180 days')`` — drops
  chunks older than 180 days. Caggs do not cascade — each cagg has its
  own retention policy below.
* ``add_compression_policy('ohlc_1m', INTERVAL '30 days')`` —
  compresses chunks older than 30 days. ``segmentby = 'symbol, source'``
  matches the natural query pattern (per-symbol time-range scans);
  ``orderby = 'bucket_start DESC'`` keeps recent rows first within each
  segment for fast tail queries.

Continuous aggregates: five higher-timeframe rollups using the §7.2
literal aggregate shape (first(open), max(high), min(low), last(close),
sum(volume)). All run in real-time mode (no ``materialized_only=true``
flag) so queries against unmaterialized recent buckets transparently
merge live ohlc_1m data — required for T-110 warmup and T-112 backfill
which read the most recent buckets immediately after a candle close.

Per-cagg refresh + retention parameters (chosen to balance
materialization CPU vs cagg correctness):

| cagg       | bucket | schedule_interval | start_offset | end_offset | retention |
| ---------- | ------ | ----------------- | ------------ | ---------- | --------- |
| ohlc_5m    | 5m     | 1 minute          | 1 day        | 1 minute   | 180 days  |
| ohlc_15m   | 15m    | 1 minute          | 1 day        | 1 minute   | 180 days  |
| ohlc_1h    | 1h     | 5 minutes         | 1 day        | 1 minute   | 180 days  |
| ohlc_4h    | 4h     | 15 minutes        | 2 days       | 1 minute   | 180 days  |
| ohlc_1d    | 1d     | 1 hour            | 7 days       | 1 minute   | 180 days  |

* ``schedule_interval`` scales with bucket size: refreshing the 1d cagg
  every minute would re-materialize the same bucket 1440 times/day for
  no observable benefit (real-time mode covers query freshness).
  ``ohlc_15m`` matches the brief §7.2 literal verbatim; the others
  scale proportionally.
* ``start_offset`` is the look-back window for each refresh — must be
  >= 2x bucket interval to safely re-materialize the just-closed bucket.
  ``ohlc_1d`` raised to 7 days because 1 day = 1 bucket would be too
  tight; the others sit comfortably above the floor.
* ``end_offset = '1 minute'`` (uniform, brief literal) is the buffer
  for late-arriving WS data before considering a bucket settled.
* ``retention = 180 days`` matches ohlc_1m per §18.3. Caggs have their
  own internal hypertable; without an explicit policy they grow
  unbounded regardless of underlying chunk drops.

No compression policy on caggs — compression doubles back into the
materialization path, which is unnecessary at our F1 volume (<10 active
symbols). Revisit in F4+ if cagg disk usage becomes measurable.

Forward-only per §N8 — downgrade is a symmetric stub (drop policies,
drop caggs, drop hypertable) so ``alembic downgrade base`` in a dev
loop does not fail catastrophically.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# (cagg_name, bucket_width, schedule_interval, start_offset, end_offset)
# end_offset is uniform "1 minute" per the table in the docstring.
_CAGGS: tuple[tuple[str, str, str, str, str], ...] = (
    ("ohlc_5m", "5 minutes", "1 minute", "1 day", "1 minute"),
    ("ohlc_15m", "15 minutes", "1 minute", "1 day", "1 minute"),
    ("ohlc_1h", "1 hour", "5 minutes", "1 day", "1 minute"),
    ("ohlc_4h", "4 hours", "15 minutes", "2 days", "1 minute"),
    ("ohlc_1d", "1 day", "1 hour", "7 days", "1 minute"),
)


def upgrade() -> None:
    op.create_table(
        "ohlc_1m",
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("bucket_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(30, 12), nullable=False),
        sa.Column("high", sa.Numeric(30, 12), nullable=False),
        sa.Column("low", sa.Numeric(30, 12), nullable=False),
        sa.Column("close", sa.Numeric(30, 12), nullable=False),
        sa.Column("volume", sa.Numeric(30, 12), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "bucket_start", "source", name="ohlc_1m_pkey"),
    )

    op.execute(
        "SELECT create_hypertable("
        "'ohlc_1m', 'bucket_start', "
        "chunk_time_interval => interval '7 days'"
        ")"
    )

    # Compression must be enabled before add_compression_policy; the
    # ALTER TABLE form is the §18.3-shape entrypoint. segmentby groups
    # rows by (symbol, source) within a chunk so per-symbol scans only
    # decompress the relevant segment; orderby keeps recent buckets at
    # the head for tail queries.
    op.execute(
        "ALTER TABLE ohlc_1m SET ("
        "timescaledb.compress, "
        "timescaledb.compress_segmentby = 'symbol, source', "
        "timescaledb.compress_orderby = 'bucket_start DESC'"
        ")"
    )
    op.execute("SELECT add_compression_policy('ohlc_1m', INTERVAL '30 days')")
    op.execute("SELECT add_retention_policy('ohlc_1m', INTERVAL '180 days')")

    for cagg, bucket, schedule, start_off, end_off in _CAGGS:
        # `cagg` / `bucket` / `schedule` / `start_off` / `end_off` come from
        # the hardcoded module-level `_CAGGS` tuple, not user input.
        # F-string interpolation is the natural shape for DDL that varies
        # per cagg; PG parameter binding ($1) is not available for the
        # CREATE MATERIALIZED VIEW statement. The S608 suppression on the
        # first f-string line below is a ruff false-positive ack.
        op.execute(
            f"CREATE MATERIALIZED VIEW {cagg} "  # noqa: S608
            f"WITH (timescaledb.continuous) AS "
            f"SELECT "
            f"  symbol, "
            f"  time_bucket(INTERVAL '{bucket}', bucket_start) AS bucket_start, "
            f"  first(open, bucket_start) AS open, "
            f"  max(high) AS high, "
            f"  min(low) AS low, "
            f"  last(close, bucket_start) AS close, "
            f"  sum(volume) AS volume, "
            f"  source "
            f"FROM ohlc_1m "
            f"GROUP BY symbol, time_bucket(INTERVAL '{bucket}', bucket_start), source "
            f"WITH NO DATA"
        )
        op.execute(
            f"SELECT add_continuous_aggregate_policy('{cagg}', "
            f"start_offset => INTERVAL '{start_off}', "
            f"end_offset => INTERVAL '{end_off}', "
            f"schedule_interval => INTERVAL '{schedule}')"
        )
        op.execute(f"SELECT add_retention_policy('{cagg}', INTERVAL '180 days')")


def downgrade() -> None:
    # Symmetric-stub downgrade per §N8 forward-only policy. Reverse
    # order: drop cagg policies + caggs first (they depend on ohlc_1m),
    # then ohlc_1m policies + table.
    for cagg, _bucket, _schedule, _start_off, _end_off in reversed(_CAGGS):
        op.execute(f"SELECT remove_retention_policy('{cagg}', if_exists => true)")
        op.execute(f"SELECT remove_continuous_aggregate_policy('{cagg}', if_exists => true)")
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {cagg}")

    op.execute("SELECT remove_retention_policy('ohlc_1m', if_exists => true)")
    op.execute("SELECT remove_compression_policy('ohlc_1m', if_exists => true)")
    op.drop_table("ohlc_1m")
