"""scoring_evaluations hypertable.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-02

Tenth migration for scalper-v2 (brief §7.2:1036-1055, §9.4:1543).

Creates the ``scoring_evaluations`` hypertable: per-signal, per-bot
evaluation audit. T-310 strategy-engine writes one row per signal
evaluation (always, regardless of decision) per §9.4:1543 ("Always write
``scoring_evaluations`` row before ack").

Schema verbatim per §7.2:1039-1050:
* 11 columns: ``id BIGSERIAL`` + ``bot_id TEXT`` + ``signal_id BIGINT`` +
  ``evaluated_at TIMESTAMPTZ`` + ``trigger_threshold DOUBLE PRECISION`` +
  ``total_score DOUBLE PRECISION`` + ``decision TEXT`` +
  ``config_version INT`` + ``rule_results JSONB`` +
  ``feature_snapshot JSONB`` + ``correlation_id TEXT``.
* PK ``(evaluated_at, id)`` per TimescaleDB hypertable convention.
* Hypertable on ``evaluated_at`` with 30-day chunks per §7.2:1053.
* Index ``se_bot_signal (bot_id, signal_id)`` per §7.2:1054.
* Index ``se_decision (decision, evaluated_at DESC)`` per §7.2:1055.

L-005 active control: DOUBLE PRECISION columns use ``sa.Double()`` (NOT
``sa.Float()`` — that produces PG ``real``, 4-byte single precision).

No FK on ``bot_id`` / ``signal_id`` — audit-stream stands alone (mirror
trade_pnl_deltas convention; sub_account-style opacity).

Forward-only per §N8 — downgrade is a symmetric stub.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "scoring_evaluations",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("bot_id", sa.Text(), nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.Column("evaluated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("trigger_threshold", sa.Double(), nullable=False),
        sa.Column("total_score", sa.Double(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("rule_results", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("feature_snapshot", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("evaluated_at", "id", name="scoring_evaluations_pkey"),
    )

    op.execute(
        "SELECT create_hypertable("
        "'scoring_evaluations', 'evaluated_at', "
        "chunk_time_interval => interval '30 days'"
        ")"
    )

    op.execute("CREATE INDEX se_bot_signal ON scoring_evaluations (bot_id, signal_id)")

    op.execute("CREATE INDEX se_decision ON scoring_evaluations (decision, evaluated_at DESC)")


def downgrade() -> None:
    # Symmetric-stub per §N8 forward-only.
    op.execute("DROP INDEX IF EXISTS se_decision")
    op.execute("DROP INDEX IF EXISTS se_bot_signal")
    op.drop_table("scoring_evaluations")
