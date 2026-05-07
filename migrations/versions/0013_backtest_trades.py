"""backtest_trades table — per-trade ledger for backtest runs.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-07

Thirteenth migration for scalper-v2 (T-501 / brief §12.2:1969-1971,
§7.2:983-1009).

Creates the ``backtest_trades`` table — per-trade row for each open-close
cycle generated during a backtest run (T-507 CLI / T-509 worker write
trades; T-516 UI per-trade variants drill-down reads). Schema mirrors
live ``trades`` (§7.2:983-1009) with backtest adaptations:

* **NEW** ``run_id UUID NOT NULL REFERENCES backtest_runs(id) ON DELETE
  CASCADE`` — links each trade to its parent run; cascade-delete cleans
  up trades when operator deletes a run.
* ``bot_id`` no FK (mirror ``backtest_runs`` convention — archived bots
  may still own backtest history).
* ``open_order_id`` / ``close_order_id`` no FK (paper backtest doesn't
  write live ``orders`` table; columns kept nullable for forward-compat).

Indexes (per WG#2 + §Test strategy):

* ``backtest_trades_run_id (run_id)`` — primary lookup.
* ``backtest_trades_run_closed (run_id, closed_at DESC) WHERE status =
  'closed'`` — partial index for chronological closed-trade queries.
* ``backtest_trades_run_status (run_id, status)`` — open vs closed
  within run.

NOT a hypertable per OQ-2=A — low volume (<500 trades per run, <100
runs/year per single operator).

Forward-only per §N8 — downgrade is symmetric stub (drops indexes +
table; pgcrypto preserved per T-407 WG#6).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "run_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("bot_id", sa.Text(), nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=True),
        sa.Column("open_order_id", sa.BigInteger(), nullable=True),
        sa.Column("close_order_id", sa.BigInteger(), nullable=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("entry_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("exit_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("notional_usd", sa.Numeric(20, 4), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(20, 4), nullable=True),
        sa.Column("fees_paid", sa.Numeric(20, 4), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("mfe_pct", sa.Double(), nullable=True),
        sa.Column("mae_pct", sa.Double(), nullable=True),
        sa.Column("confidence_score", sa.Double(), nullable=True),
        sa.Column(
            "meta",
            sa.dialects.postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="backtest_trades_pkey"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["backtest_runs.id"],
            name="backtest_trades_run_id_fkey",
            ondelete="CASCADE",
        ),
    )

    op.execute("CREATE INDEX backtest_trades_run_id ON backtest_trades (run_id)")
    op.execute(
        "CREATE INDEX backtest_trades_run_closed ON backtest_trades (run_id, closed_at DESC) "
        "WHERE status = 'closed'"
    )
    op.execute("CREATE INDEX backtest_trades_run_status ON backtest_trades (run_id, status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS backtest_trades_run_status")
    op.execute("DROP INDEX IF EXISTS backtest_trades_run_closed")
    op.execute("DROP INDEX IF EXISTS backtest_trades_run_id")
    op.drop_table("backtest_trades")
