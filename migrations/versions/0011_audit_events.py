"""audit_events hypertable.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-03

Eleventh migration for scalper-v2 (brief §7.2:1108-1126, §16.8:2261-2264,
§15.6:2182-2184).

Creates the ``audit_events`` hypertable: append-only audit trail for
admin write actions per §16.8:2261 ("Write endpoints log every action
to ``audit_events`` with ``actor``, ``before_state``, ``after_state``").
T-401b symbol-map CRUD is the first writer; T-405 audit-log viewer is
the first reader. T-401a ships the schema + helper without an in-task
caller per L-007 pre-emptive split rationale (alternative — merging
T-401a+T-401b — would overshoot §0.3 LOC cap at ~505 LOC).

Schema verbatim per §7.2:1110-1122:
* 10 columns: ``id BIGSERIAL`` + ``occurred_at TIMESTAMPTZ`` +
  ``actor TEXT`` + ``action TEXT`` + ``entity_type TEXT`` +
  ``entity_id TEXT`` + ``before_state JSONB nullable`` +
  ``after_state JSONB nullable`` + ``correlation_id TEXT nullable`` +
  ``meta JSONB DEFAULT '{}'``.
* PK ``(occurred_at, id)`` per TimescaleDB hypertable convention.
* Hypertable on ``occurred_at`` with **30-day** chunks per §7.2:1124
  (audit events are sparser than 7-day signals/features).
* Index ``ae_entity (entity_type, entity_id, occurred_at DESC)``
  verbatim per §7.2:1125 — for T-405 reader's typical filter
  "show events for entity_type=X entity_id=Y last N days".

No FK on any column — audit-stream stands alone (mirror
``trade_pnl_deltas`` + ``scoring_evaluations`` convention).

L-008 active control: ``meta`` server default uses
``sa.text("'{}'::jsonb")`` — NOT Python ``{}`` literal, NOT a Python
type name in SQL.

Forward-only per §N8 — downgrade is a symmetric stub.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("before_state", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("after_state", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column(
            "meta",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("occurred_at", "id", name="audit_events_pkey"),
    )

    op.execute(
        "SELECT create_hypertable("
        "'audit_events', 'occurred_at', "
        "chunk_time_interval => interval '30 days'"
        ")"
    )

    op.execute("CREATE INDEX ae_entity ON audit_events (entity_type, entity_id, occurred_at DESC)")


def downgrade() -> None:
    # Symmetric-stub per §N8 forward-only.
    op.execute("DROP INDEX IF EXISTS ae_entity")
    op.drop_table("audit_events")
