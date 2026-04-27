"""paper_* mirror tables for PaperExchange (T-211/T-213).

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-27

Eighth migration (§12.1 lines 1930-1948, §N8 forward-only, §19 F2).
4-table mirror per T-200 plan-doc canonical + TASKS.md line 81:
paper_orders, paper_trades, paper_executions, paper_positions.

OQ-1 brief inconsistency (third F2 instance after T-201 Q11 +
OQ-1 stream sig; full rationale in docs/plans/T-212.md): §12.1's
3-table list is drafting artifact; §3.1 line 268 paper-live
symmetry invariant requires the 4th table (paper_trades). T-200
commit is canonical. T-218 / T-219 / T-220 / T-221 plan-docs
inherit 4-table assumption.

FK chain (2 to bots + 4 paper-internal; NO FKs to live tables
per §12.1 + TASKS.md line 81):

    bots                ← paper_orders.bot_id           (NOT NULL)
    bots                ← paper_trades.bot_id           (NOT NULL)
    paper_orders.id     ← paper_trades.open_order_id    (NOT NULL)
    paper_orders.id     ← paper_executions.order_id     (NOT NULL)
    paper_trades.id     ← paper_executions.trade_id     (nullable)
    paper_trades.id     ← paper_positions.trade_id      (NOT NULL)

Notable non-FKs (mirror live verbatim): paper_trades.close_order_id
plain BIGINT (T-202 W#4); paper_executions.bot_id plain TEXT
(T-202 W#2); paper_positions.bot_id plain TEXT (T-203 W#4).

§18.3 retention: mirror live forever (trades + executions) — NO
retention, NO compression on paper_executions hypertable. Combined
anti-test test_paper_executions_hypertable_no_retention_no_compression
per T-204 W#3 grep-friendly naming.

§N1 UTC: every TIMESTAMPTZ via sa.TIMESTAMP(timezone=True); NO
server_default on any timestamp column (application sets via
packages.core.now_utc()). L-005: DOUBLE PRECISION via sa.Double()
not sa.Float() (paper_trades.mfe_pct/mae_pct/confidence_score).

OQ-3 default A: ``exchange='paper'`` discriminator column ONLY on
paper_orders (mirror live orders.exchange); other paper_* tables
don't have it because live counterparts don't either.

Forward-only per §N8. Symmetric-stub downgrade with FK-aware drop
order: paper_executions + paper_positions → paper_trades → paper_orders.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # 1. paper_orders — regular table; mirror live `orders` per T-202.
    op.create_table(
        "paper_orders",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column(
            "bot_id",
            sa.Text(),
            sa.ForeignKey("bots.bot_id"),
            nullable=False,
        ),
        sa.Column("signal_id", sa.BigInteger(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("exchange_order_id", sa.Text(), nullable=True),
        sa.Column("exchange", sa.Text(), nullable=False),  # always 'paper' for this table
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("order_type", sa.Text(), nullable=False),
        sa.Column("qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("placed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("filled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("idempotent", sa.Boolean(), nullable=False),
        sa.Column(
            "meta",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute("CREATE INDEX paper_orders_bot_status ON paper_orders (bot_id, status)")
    op.execute("CREATE INDEX paper_orders_correlation ON paper_orders (correlation_id)")

    # 2. paper_trades — regular table; mirror live `trades` per T-202.
    #    close_order_id plain BIGINT NO FK per §7.2 / T-202 W#4 verbatim.
    op.create_table(
        "paper_trades",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column(
            "bot_id",
            sa.Text(),
            sa.ForeignKey("bots.bot_id"),
            nullable=False,
        ),
        sa.Column("signal_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "open_order_id",
            sa.BigInteger(),
            sa.ForeignKey("paper_orders.id"),
            nullable=False,
        ),
        sa.Column("close_order_id", sa.BigInteger(), nullable=True),  # NO FK per T-202 W#4
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
        # sa.Double() per L-005 — sa.Float() would silently degrade to PG `real`.
        sa.Column("mfe_pct", sa.Double(), nullable=True),
        sa.Column("mae_pct", sa.Double(), nullable=True),
        sa.Column("confidence_score", sa.Double(), nullable=True),
        sa.Column(
            "meta",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute("CREATE INDEX paper_trades_bot_status ON paper_trades (bot_id, status)")
    op.execute(
        "CREATE INDEX paper_trades_closed_at ON paper_trades (closed_at DESC) "
        "WHERE status = 'closed'"
    )

    # 3. paper_executions — hypertable; mirror live `executions` per T-202.
    #    bot_id plain TEXT NO FK per §7.2 / T-202 W#2 verbatim. NO retention,
    #    NO compression policies per §18.3 (live executions forever) /
    #    OQ-2 default A. Anti-test pins both negatives.
    op.create_table(
        "paper_executions",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("exchange_exec_id", sa.Text(), nullable=False),
        sa.Column(
            "order_id",
            sa.BigInteger(),
            sa.ForeignKey("paper_orders.id"),
            nullable=False,
        ),
        sa.Column(
            "trade_id",
            sa.BigInteger(),
            sa.ForeignKey("paper_trades.id"),
            nullable=True,  # backfilled when trade row materialises
        ),
        sa.Column("bot_id", sa.Text(), nullable=False),  # NO FK per T-202 W#2
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=False),
        sa.Column("qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("fee", sa.Numeric(20, 8), nullable=False),
        sa.Column("exec_type", sa.Text(), nullable=False),
        sa.Column("executed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("executed_at", "id", name="paper_executions_pkey"),
    )

    op.execute(
        "SELECT create_hypertable("
        "'paper_executions', 'executed_at', "
        "chunk_time_interval => interval '7 days'"
        ")"
    )

    op.execute(
        "CREATE UNIQUE INDEX paper_executions_exchange_id "
        "ON paper_executions (exchange_exec_id, executed_at)"
    )
    op.execute("CREATE INDEX paper_executions_trade ON paper_executions (trade_id)")

    # NO retention/compression policies on paper_executions per §18.3 line 2439
    # (live executions kept forever; paper mirrors live retention shape per
    # OQ-2 default A). Anti-test in
    # test_paper_executions_hypertable_no_retention_no_compression pins both
    # negatives.

    # 4. paper_positions — regular table; mirror live `position_state` per T-203.
    #    bot_id plain TEXT NO FK per §7.2 / T-203 W#4 verbatim.
    #    updated_at NO server_default per §N1 / T-203 W#1.
    op.create_table(
        "paper_positions",
        sa.Column("bot_id", sa.Text(), nullable=False),  # NO FK per T-203 W#4
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column(
            "trade_id",
            sa.BigInteger(),
            sa.ForeignKey("paper_trades.id"),
            nullable=False,
        ),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("entry_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("remaining_qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("sl_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("tp_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("sl_type", sa.Text(), nullable=True),
        sa.Column("best_price", sa.Numeric(30, 12), nullable=True),
        sa.Column(
            "tp_hit",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "trailing_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "running_pnl",
            sa.Numeric(20, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("mfe_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("mae_price", sa.Numeric(30, 12), nullable=True),
        # NO server_default on updated_at per §N1 — application sets via
        # packages.core.now_utc() on every INSERT/UPDATE (mirror T-203 W#1).
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("bot_id", "symbol", name="paper_positions_pkey"),
    )


def downgrade() -> None:
    # Symmetric-stub per §N8 forward-only.
    # FK ordering: drop dependents first (paper_executions, paper_positions),
    # then paper_trades, then paper_orders.
    op.execute("DROP INDEX IF EXISTS paper_executions_trade")
    op.execute("DROP INDEX IF EXISTS paper_executions_exchange_id")
    op.drop_table("paper_executions")
    op.drop_table("paper_positions")
    op.execute("DROP INDEX IF EXISTS paper_trades_closed_at")
    op.execute("DROP INDEX IF EXISTS paper_trades_bot_status")
    op.drop_table("paper_trades")
    op.execute("DROP INDEX IF EXISTS paper_orders_correlation")
    op.execute("DROP INDEX IF EXISTS paper_orders_bot_status")
    op.drop_table("paper_orders")
