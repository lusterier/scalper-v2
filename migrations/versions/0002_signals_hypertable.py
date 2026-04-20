"""signals — every inbound webhook (TimescaleDB hypertable).

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-20

Second migration for scalper-v2 (brief §7.2 ``signals`` DDL, §N8
forward-only + tested, §19 F0 exit criterion "a DB signals row").

Creates the ``signals`` hypertable that the signal-gateway (T-015)
will insert into on every inbound TradingView webhook, and that the
strategy-engine (F2+) reads via ``signals.validated`` NATS fan-out.

Shape verbatim from brief §7.2:

* ``id``                BIGINT IDENTITY    (brief spells this BIGSERIAL;
                                           rendered as IDENTITY per the
                                           T-010 precedent — SQLAlchemy
                                           2.0 idiom, same on-disk
                                           semantics)
* ``received_at``       TIMESTAMPTZ NOT NULL  — hypertable time column
* ``schema_version``    TEXT NOT NULL
* ``source``            TEXT NOT NULL         — e.g. ``tv_rsi_div_v3``
* ``idempotency_key``   TEXT NOT NULL
* ``symbol``            TEXT NOT NULL         — Bybit canonical
* ``original_symbol``   TEXT                  — pre-mapping, nullable
* ``action``            TEXT NOT NULL         — LONG | SHORT | CLOSE | CUSTOM
* ``payload``           JSONB NOT NULL
* ``ingestion_status``  TEXT NOT NULL         — validated | duplicate | invalid
* ``correlation_id``    TEXT NOT NULL
* ``PRIMARY KEY (received_at, id)``           — composite; required for
                                                TimescaleDB hypertable
                                                partitioning on
                                                ``received_at``

Hypertable: ``create_hypertable('signals', 'received_at',
chunk_time_interval => interval '7 days')`` per §7.2. TimescaleDB
manages chunking transparently; consumers see a regular table.

Indexes (all three per §7.2):

* ``signals_idempotency``  UNIQUE  ``(idempotency_key, received_at)``
                                   — enforces §9.1 dedup; the
                                   ``received_at`` tail is required by
                                   TimescaleDB for UNIQUE indexes on
                                   hypertables (must include the
                                   partitioning column).
* ``signals_symbol_time``          ``(symbol, received_at DESC)``
                                   — the hot read path: "last N
                                   signals for a symbol".
* ``signals_payload_gin``  GIN     ``(payload)`` — ad-hoc queries over
                                   JSONB body; non-unique so the
                                   partitioning column is not required.

Forward-only per §N8 — downgrade mirrors upgrade in reverse
(DROP INDEX → DROP TABLE) as a symmetric stub so ``alembic downgrade
base`` in a dev loop does not fail catastrophically. Hypertable
metadata is cleaned up automatically by ``DROP TABLE``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("original_symbol", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("ingestion_status", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("received_at", "id", name="signals_pkey"),
    )

    op.execute(
        "SELECT create_hypertable("
        "'signals', 'received_at', "
        "chunk_time_interval => interval '7 days'"
        ")"
    )

    op.execute("CREATE UNIQUE INDEX signals_idempotency ON signals (idempotency_key, received_at)")
    op.execute("CREATE INDEX signals_symbol_time ON signals (symbol, received_at DESC)")
    op.execute("CREATE INDEX signals_payload_gin ON signals USING GIN (payload)")


def downgrade() -> None:
    # Symmetric-stub downgrade per §N8 forward-only policy.
    # DROP TABLE removes the hypertable entry and all chunks; the
    # three indexes are dropped by PostgreSQL as table dependencies,
    # but we issue explicit DROP INDEX IF EXISTS first for clarity.
    op.execute("DROP INDEX IF EXISTS signals_payload_gin")
    op.execute("DROP INDEX IF EXISTS signals_symbol_time")
    op.execute("DROP INDEX IF EXISTS signals_idempotency")
    op.drop_table("signals")
