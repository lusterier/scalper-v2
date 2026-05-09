"""outbox_events table — transactional outbox pattern for reliable NATS publish.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-09

Sixteenth migration for scalper-v2 (T-537a1 / BRIEF §8 outbox).

Single generic ``outbox_events`` table backing the outbox pattern across
multiple services (signal-gateway, execution-service, strategy-engine).
``service`` column is a TEXT discriminator; relay worker (T-537a2) filters
``WHERE service = $1`` per-service. Per OQ-1 round 2 operator decision
2026-05-09: single table avoids per-service migrations for T-537c/d
follow-ups; mirrors ``trading_events`` hypertable single-table pattern.

11 columns:

* ``id BIGSERIAL`` PK.
* ``service TEXT NOT NULL`` — service discriminator.
* ``subject TEXT NOT NULL`` — NATS subject.
* ``correlation_id TEXT`` — NULLable; ties back to webhook idempotency_key
  / order correlation_id for audit traceback.
* ``payload JSONB NOT NULL`` — serialized MessageEnvelope. Writer uses
  L-013 codec-immune ``json.dumps(_to_jsonable(payload))`` form (per
  T-510b operator default A precedent for cross-service JSONB writes).
* ``created_at TIMESTAMPTZ NOT NULL`` — when business tx committed (UTC
  per §N1).
* ``published_at TIMESTAMPTZ`` — NULL until relay publishes successfully.
* ``attempt_count INTEGER NOT NULL DEFAULT 0`` — relay-incremented on
  failure.
* ``last_attempt_at TIMESTAMPTZ`` — NULL until first attempt; backoff
  window calc anchors on this.
* ``last_error TEXT`` — NULL until first failure; persists last
  exception str for admin investigation.
* ``failed_at TIMESTAMPTZ`` — NULL until ``attempt_count >= max_attempts``;
  failed rows kept forever per OQ-3 round 2 (admin replay via
  ``UPDATE published_at = NULL`` resumes).

Indexes (2 partial btree):

* ``outbox_events_pending_idx (service, created_at) WHERE published_at IS
  NULL AND failed_at IS NULL`` — relay's hot path (scan only pending,
  ordered FIFO per service).
* ``outbox_events_correlation_idx (correlation_id) WHERE correlation_id
  IS NOT NULL`` — admin/audit lookups by correlation_id.

NOT a hypertable — outbox is a write-once-then-flip-flag table; the
relay's hot path is bounded by retention (failed rows + recently
published rows kept forever per OQ-3; growth is bounded by signal
volume not by time-series cardinality). Forward-only per §N8.
Downgrade drops indexes + table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("failed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="outbox_events_pkey"),
    )

    op.execute(
        "CREATE INDEX outbox_events_pending_idx "
        "ON outbox_events (service, created_at) "
        "WHERE published_at IS NULL AND failed_at IS NULL"
    )
    op.execute(
        "CREATE INDEX outbox_events_correlation_idx "
        "ON outbox_events (correlation_id) "
        "WHERE correlation_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS outbox_events_correlation_idx")
    op.execute("DROP INDEX IF EXISTS outbox_events_pending_idx")
    op.drop_table("outbox_events")
