"""features hypertable.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-26

Fourth migration for scalper-v2 (brief §7.2 ``features`` DDL,
§18.3 retention/compression, §19 F1 bullet 4, §9.3 line 1494
ON CONFLICT contract).

Creates the ``features`` hypertable that ``feature-engine`` (T-110)
writes to (per-bar indicator values from EMA/RSI/ATR/VWAP/Bollinger/
MACD per T-107a/T-107b) and ``backfill_features`` (T-112) upserts into
(historical recompute via §9.3 line 1494 ON CONFLICT DO UPDATE on the
composite PK).

features shape verbatim from §7.2:

* ``feature_name``    TEXT NOT NULL                 — e.g., 'ind.btcusdt.15m.ema_20'
* ``symbol``          TEXT NOT NULL                 — denormalized for query speed
* ``computed_at``     TIMESTAMPTZ NOT NULL          — hypertable time column
* ``value_num``       DOUBLE PRECISION              — NULL when bool/json variant
* ``value_bool``      BOOLEAN                       — NULL when num/json variant
* ``value_json``      JSONB                         — NULL when num/bool variant
* ``source_version``  TEXT NOT NULL                 — e.g., 'builtin.ema.v1'
* ``PRIMARY KEY (feature_name, symbol, computed_at, source_version)``
                                                    — §9.3 line 1494 ON CONFLICT target
* ``CREATE INDEX features_latest ON features (feature_name, symbol, computed_at DESC)``
                                                    — §7.2 line 914 verbatim

``value_num`` is :class:`sqlalchemy.Double` (compiles to PG
``DOUBLE PRECISION``, 8 bytes / 53-bit mantissa) per §7.2 line 907
verbatim. :class:`sqlalchemy.Float` would compile to ``FLOAT`` /
``real`` (4 bytes / 24-bit mantissa) — silent precision loss vs the
brief and the §8.4 ``FeatureUpdate`` wire schema's ``Optional[float]``.

``value_num`` / ``value_bool`` / ``value_json`` are all nullable at
the DB layer; the exactly-one-non-None invariant is enforced in app
code via :class:`packages.features.FeatureValue.__post_init__` (T-106).
No DB-side CHECK constraint: T-110 is the only writer, prevalidated
through ``FeatureValue``; a CHECK would add per-row write overhead
without new protection.

No GIN index on ``value_json`` — F1 queries (T-110 dispatch +
scoring-engine KV reads) go through the composite PK / ``features_latest``
index. Brief §7.2 line 914 specifies only ``features_latest``. F1+
entry queued for analytics-driven JSONB extraction in F4+ if observed.

§18.3 lifecycle policies on features:

* ``add_retention_policy('features', INTERVAL '180 days')`` — drops
  chunks older than 180 days.
* ``add_compression_policy('features', INTERVAL '30 days')`` —
  compresses chunks older than 30 days. ``compress_segmentby =
  'feature_name, symbol'`` matches the natural query pattern
  (per-feature time-range scans for warmup, scoring snapshots,
  analytics drilldown). ``compress_orderby = 'computed_at DESC'``
  keeps recent rows first within each segment for fast tail queries
  (per the T-103 precedent for ``ohlc_1m``); brief §18.3 line 1182
  specifies only segmentby, orderby is an opt-in optimisation.

Forward-only per §N8 — downgrade is a symmetric stub (drop policies,
drop table; the hypertable's chunks cascade-drop with the parent
table per the T-103/0003 precedent).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "features",
        sa.Column("feature_name", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("value_num", sa.Double(), nullable=True),
        sa.Column("value_bool", sa.Boolean(), nullable=True),
        sa.Column("value_json", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("source_version", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "feature_name",
            "symbol",
            "computed_at",
            "source_version",
            name="features_pkey",
        ),
    )

    op.execute(
        "SELECT create_hypertable("
        "'features', 'computed_at', "
        "chunk_time_interval => interval '7 days'"
        ")"
    )

    op.execute("CREATE INDEX features_latest ON features (feature_name, symbol, computed_at DESC)")

    # Compression must be enabled before add_compression_policy; the
    # ALTER TABLE form is the §18.3-shape entrypoint. segmentby groups
    # rows by (feature_name, symbol) within a chunk so per-feature
    # scans only decompress the relevant segment; orderby keeps recent
    # rows at the head for tail queries.
    op.execute(
        "ALTER TABLE features SET ("
        "timescaledb.compress, "
        "timescaledb.compress_segmentby = 'feature_name, symbol', "
        "timescaledb.compress_orderby = 'computed_at DESC'"
        ")"
    )
    op.execute("SELECT add_compression_policy('features', INTERVAL '30 days')")
    op.execute("SELECT add_retention_policy('features', INTERVAL '180 days')")


def downgrade() -> None:
    # Symmetric-stub downgrade per §N8 forward-only policy. Drop
    # policies (with `if_exists => true`) before the table so bgw_job
    # entries do not leak when the underlying hypertable disappears.
    # `op.drop_table` cascade-drops the hypertable's chunks per the
    # T-103/0003 precedent (DROP TABLE removes the hypertable entry
    # and all chunks; TimescaleDB metadata is cleaned up automatically).
    op.execute("SELECT remove_retention_policy('features', if_exists => true)")
    op.execute("SELECT remove_compression_policy('features', if_exists => true)")
    op.drop_table("features")
