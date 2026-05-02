"""trade_pnl_deltas hypertable.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-02

Ninth migration for scalper-v2 (brief §9.5:1601-1605, §20 H-017
line 2727 + H-021 line 2765 + ADR-0006 + ADR-0007 + T-200 Q6).

Creates the ``trade_pnl_deltas`` hypertable: cumulative-delta-per-sub-account
audit divergence flags. Per H-017 ("cumulative attribution per sub-account
and time window; per-record attribution is out, cumulative attribution is in"),
this table stores ``(sub_account, audit_run_at, window_start, window_end,
cumulative_bybit, cumulative_db, delta)`` tuples — NEVER per-trade attribution.

T-220b's audit job INSERTs a row only when ``|delta| > divergence_threshold_usd``
(default ``$0.50`` per §9.5:1605); below threshold, no row is written.

UNIQUE ``(sub_account, audit_run_at)`` per ADR-0007 D7 belt-and-suspenders
against operator-driven concurrent audit runs. PRIMARY KEY ``(audit_run_at, id)``
per TimescaleDB hypertable convention (mirror trading_events §7.2:1091).

H-018 (PK-only on trades) does not bind here — trade_pnl_deltas has its own
surrogate ``id`` BIGSERIAL via ``sa.Identity(always=False)``; H-018 binds at
``trades.id`` (migration 0005, T-202).

Forward-only per §N8 — downgrade is a symmetric stub.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "trade_pnl_deltas",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("sub_account", sa.Text(), nullable=False),
        sa.Column("audit_run_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("window_end", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("cumulative_bybit", sa.Numeric(20, 4), nullable=False),
        sa.Column("cumulative_db", sa.Numeric(20, 4), nullable=False),
        sa.Column("delta", sa.Numeric(20, 4), nullable=False),
        sa.PrimaryKeyConstraint("audit_run_at", "id", name="trade_pnl_deltas_pkey"),
        sa.UniqueConstraint(
            "sub_account",
            "audit_run_at",
            name="uq_trade_pnl_deltas_sub_account_audit_run_at",
        ),
    )

    op.execute(
        "SELECT create_hypertable("
        "'trade_pnl_deltas', 'audit_run_at', "
        "chunk_time_interval => interval '7 days'"
        ")"
    )

    op.execute(
        "CREATE INDEX ix_trade_pnl_deltas_sub_account_audit "
        "ON trade_pnl_deltas (sub_account, audit_run_at DESC)"
    )


def downgrade() -> None:
    # Symmetric-stub per §N8 forward-only.
    op.execute("DROP INDEX IF EXISTS ix_trade_pnl_deltas_sub_account_audit")
    op.drop_table("trade_pnl_deltas")
