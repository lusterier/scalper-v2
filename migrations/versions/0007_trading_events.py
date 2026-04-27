"""trading_events hypertable.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-27

Seventh migration for scalper-v2 (brief §7.2 lines 1091-1106, §18.3
line 1176 + line 2438, §N8 forward-only + tested, §19 F2 milestone —
audit event-stream landing).

Creates the ``trading_events`` hypertable: append-only persisted mirror
of the ``trading.events`` NATS subject (§8.1 line 1216 — "same data as
orders.events but persisted") plus reconciliation events that are not
on NATS (e.g., ``reconcile_adjust``).

Per T-200 Q5, execution-service emits BOTH the NATS publish AND the
``trading_events`` row inside the order-pipeline transaction:

* T-216b — INSERT in placement transaction alongside orders + trades.
* T-218 — INSERT fill events.
* T-219 — INSERT close + reconcile_adjust events.
* T-220 — INSERT P&L audit drift events.
* T-221 — INSERT orphan-close + reconcile events.

Schema verbatim from §7.2 lines 1091-1106:

* ``id``             BIGSERIAL                  — composite PK part 2
* ``occurred_at``    TIMESTAMPTZ NOT NULL       — hypertable time column;
                                                 PK part 1; **NO server_default**
                                                 per §N1 (application sets via
                                                 packages.core.now_utc())
* ``bot_id``         TEXT (nullable)            — per-bot tag; NULL for
                                                 system-level events; **NO FK**
                                                 on bots(bot_id) per §7.2
                                                 verbatim (mirror T-202 W#2 /
                                                 T-203 W#4 precedent — append-only
                                                 audit stream outlives source rows)
* ``correlation_id`` TEXT (nullable)            — cross-service trace ID;
                                                 NULL when reconcile/audit
                                                 emitted without upstream trace
* ``event_type``     TEXT NOT NULL              — open-ended discriminator;
                                                 brief enumerates 'order_placed' |
                                                 'fill' | 'sl_set' | 'sl_move_be' |
                                                 'trail_update' | 'close' |
                                                 'reconcile_adjust' | ...
* ``payload``        JSONB NOT NULL             — event body, per event_type
* ``PRIMARY KEY (occurred_at, id)``             — composite; required for
                                                 TimescaleDB hypertable
                                                 partitioning on occurred_at

Hypertable: ``create_hypertable('trading_events', 'occurred_at',
chunk_time_interval => interval '7 days')`` per §7.2 line 1103.

Indexes (both per §7.2 lines 1104-1105 verbatim):

* ``te_bot_type ON (bot_id, event_type, occurred_at DESC)`` — composite,
  supports per-bot per-event-type time-range queries (audit replay,
  alerting filter).
* ``te_correlation ON (correlation_id)`` — single-column, supports
  cross-service correlation lookup for end-to-end trace.

§18.3 lifecycle policies on trading_events:

* ``add_retention_policy('trading_events', INTERVAL '365 days')`` per
  §18.3 line 1176 + line 2438 — drops chunks older than 365 days.
  **NO compression policy**: §18.3 lines 1178-1183 specify compression
  for ``ohlc_1m`` + ``features`` only; trading_events has shorter
  retention (365d) and JSONB-heavy high-cardinality storage where
  compression's marginal gain is offset by decompress overhead during
  audit replay queries. Combined positive-retention + negative-compression
  test ``test_trading_events_hypertable_retention_365d_no_compression``
  pins both regression directions.

H-018 N/A here — ``trading_events`` has composite ``(occurred_at, id)``
PK with no surrogate-key WHERE-clause hazard surface; H-018 binds at
``trades.id`` (migration 0005, T-202).

H-021 (UTC scheduler) does not bind here — APScheduler emits timestamps
via ``now_utc()`` at T-220 emit time; schema only stores them.

Forward-only per §N8 — downgrade is a symmetric stub. Drop retention
policy with ``if_exists => true`` so bgw_job entries do not leak when
the underlying hypertable disappears (T-103/T-202 precedent).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_events",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("bot_id", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("occurred_at", "id", name="trading_events_pkey"),
    )

    op.execute(
        "SELECT create_hypertable("
        "'trading_events', 'occurred_at', "
        "chunk_time_interval => interval '7 days'"
        ")"
    )

    op.execute("CREATE INDEX te_bot_type ON trading_events (bot_id, event_type, occurred_at DESC)")
    op.execute("CREATE INDEX te_correlation ON trading_events (correlation_id)")

    op.execute("SELECT add_retention_policy('trading_events', INTERVAL '365 days')")

    # NO compression policy on trading_events per §18.3 lines 1178-1183
    # (compression specified for ohlc_1m + features only). Anti-test in
    # test_trading_events_hypertable_retention_365d_no_compression pins
    # the no-compression invariant.


def downgrade() -> None:
    # Symmetric-stub per §N8 forward-only.
    # Drop retention policy with if_exists => true so bgw_job entries do
    # not leak when the underlying hypertable disappears (T-103/T-202
    # precedent). DROP TABLE removes the hypertable entry and all chunks;
    # explicit DROP INDEX statements first for clarity.
    op.execute("SELECT remove_retention_policy('trading_events', if_exists => true)")
    op.execute("DROP INDEX IF EXISTS te_correlation")
    op.execute("DROP INDEX IF EXISTS te_bot_type")
    op.drop_table("trading_events")
