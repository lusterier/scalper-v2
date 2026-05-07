"""shadow_variants + shadow_rejected tables — F5 shadow runtime persistence.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-07

Fourteenth migration for scalper-v2 (T-510a / brief §13.3 + §13.5).

Creates two regular (NOT hypertable) tables backing F5 shadow-runtime
persistence:

* ``shadow_variants`` — per-variant parallel-simulation outcomes for
  accepted trades (T-511 shadow-worker writes; T-512 OHLC-replay
  restart-recovery reads ``WHERE terminated_at IS NULL``; T-516 UI
  per-trade variants drill-down reads via ``parent_trade_id`` FK).
  14 columns; FK ``parent_trade_id → trades(id) ON DELETE CASCADE``.
* ``shadow_rejected`` — per-rejected-signal 60-minute observation
  labels (T-513 writes + restart-recovery; T-517 explorer reads).
  11 columns; **NO FK on signal_id** — signals is a hypertable with
  composite PK ``(received_at, id)``; PostgreSQL rejects FK on ``id``
  alone. Mirror 4 sibling-table convention (`0005` orders/trades/
  executions, `0008` paper_*, `0010` scoring_evaluations, `0013`
  backtest_trades) which all hold ``signal_id`` plain.

Spec-clarification: BRIEF §7.2:1083 + 1087 stub-headers label both
shadow tables as ``(hypertable)``, but §7.2:838 authoritative
hypertable-list does NOT include them. Plan mirrors T-501 backtest_trades
precedent + OQ-2=A volume rationale (<500 variants/day, <100
rejected/day) — NOT hypertable. F5+ promotion is a forward-only
optimization migration if runtime measurement reveals capacity pressure.

``terminal_outcome`` columns are plain TEXT with no CHECK constraint
per OQ-4=A — forward-compat for ``replay-error`` / ``shutdown-mid-replay``
without schema migration. Application-layer narrowing (StrEnum) per
T-407 BacktestStatus precedent.

Indexes (4 btree, 2 per table):

* ``shadow_variants_parent (parent_trade_id)`` — drill-down "variants
  for trade X" (T-516 reader).
* ``shadow_variants_bot_active (bot_id) WHERE terminated_at IS NULL``
  — partial index for restart-recovery scan (T-512).
* ``shadow_rejected_signal (signal_id)`` — lookup by source signal.
* ``shadow_rejected_bot_active (bot_id) WHERE terminated_at IS NULL``
  — partial index for 60-min observation restart-recovery (T-513).

Forward-only per §N8 — downgrade is symmetric stub (drops indexes +
both tables; pgcrypto preserved per T-407 WG#6).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_variants",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("parent_trade_id", sa.BigInteger(), nullable=False),
        sa.Column("bot_id", sa.Text(), nullable=False),
        sa.Column("variant_name", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("entry_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("terminated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("terminal_outcome", sa.Text(), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(20, 4), nullable=True),
        sa.Column("mfe_pct", sa.Double(), nullable=True),
        sa.Column("mae_pct", sa.Double(), nullable=True),
        sa.Column(
            "meta",
            sa.dialects.postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="shadow_variants_pkey"),
        sa.ForeignKeyConstraint(
            ["parent_trade_id"],
            ["trades.id"],
            name="shadow_variants_parent_trade_id_fkey",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "shadow_rejected",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # No FK on signal_id — signals hypertable composite PK; mirror
        # 0005/0008/0010/0013 convention. See plan-doc §FK cascade behavior.
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.Column("bot_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("would_side", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("terminated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("terminal_outcome", sa.Text(), nullable=True),
        sa.Column("mfe_pct", sa.Double(), nullable=True),
        sa.Column("mae_pct", sa.Double(), nullable=True),
        sa.Column(
            "meta",
            sa.dialects.postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="shadow_rejected_pkey"),
    )

    op.execute("CREATE INDEX shadow_variants_parent ON shadow_variants (parent_trade_id)")
    op.execute(
        "CREATE INDEX shadow_variants_bot_active ON shadow_variants (bot_id) "
        "WHERE terminated_at IS NULL"
    )
    op.execute("CREATE INDEX shadow_rejected_signal ON shadow_rejected (signal_id)")
    op.execute(
        "CREATE INDEX shadow_rejected_bot_active ON shadow_rejected (bot_id) "
        "WHERE terminated_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS shadow_rejected_bot_active")
    op.execute("DROP INDEX IF EXISTS shadow_rejected_signal")
    op.execute("DROP INDEX IF EXISTS shadow_variants_bot_active")
    op.execute("DROP INDEX IF EXISTS shadow_variants_parent")
    op.drop_table("shadow_rejected")
    op.drop_table("shadow_variants")
