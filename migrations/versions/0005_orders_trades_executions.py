"""orders + trades + executions FK chain.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-27

Fifth migration for scalper-v2 (brief §7.2 lines 956-1034, §N8
forward-only + tested, §19 F2 milestone — execution ledger first
landing).

Creates the live execution-ledger triad consumed by execution-service
(T-216 placement pipeline, T-217 lifecycle FSM, T-218 dispatcher,
T-219 cumulative-delta reconcile, T-220 P&L audit, T-221 post-restart
reconciliation):

* ``orders``     regular table   — order lifecycle (request → place → fill → close)
* ``trades``     regular table   — one row per open-close cycle
* ``executions`` hypertable      — per-fill ledger, partitioned on
                                   ``executed_at`` with 7-day chunks

FK chain (5 edges):

    bots ← orders.bot_id           (NOT NULL)
    bots ← trades.bot_id           (NOT NULL)
    orders.id ← trades.open_order_id (NOT NULL)
    orders.id ← executions.order_id  (NOT NULL)
    trades.id ← executions.trade_id  (nullable; backfilled when the
                                      trade row materialises post-fill)

Notable non-FKs (per §7.2 verbatim — do NOT add FKs by symmetry):

* ``trades.close_order_id`` is plain BIGINT — close path may take
  reduce_only on the original order, may emergency-close, or may
  produce a separate close-order row; brief leaves the FK off.
* ``executions.bot_id`` is plain TEXT NOT NULL — denormalised, no
  FK, mirroring brief §7.2 line 1021 verbatim.

§18.3 retention (line 2439): trades + executions kept **forever**.
The hypertable on ``executions`` is therefore created **without**
retention or compression policies (contrast 0003/0004). The hypertable
shape still buys insert-rate scaling and natural time-range chunk
pruning during P&L audit window queries (T-220 reads "last 3h per
sub-account").

H-018 ("Close trade by PK only") is a query-layer invariant, not a
schema invariant. The BIGSERIAL ``trades.id`` exposed here is what
``packages/db/queries/trades.py`` (T-216/T-217 owners) will key updates
against. Migration just provides the PK; the helper layer enforces
``WHERE id = ?`` only.

NUMERIC precision split per §5.13 + §7.2 verbatim:

* ``NUMERIC(30, 12)`` — qty + prices on orders/trades/executions
  (Bybit qty-step + price-tick precision; H-015 Decimal preservation).
* ``NUMERIC(20, 8)``  — ``executions.fee`` (Bybit fee precision).
* ``NUMERIC(20, 4)``  — ``trades.notional_usd``, ``realized_pnl``,
  ``fees_paid`` (USD-denominated, basis-point operations).

DOUBLE PRECISION columns (``trades.mfe_pct``, ``mae_pct``,
``confidence_score``) use ``sa.Double()`` per L-005 — ``sa.Float()``
would compile to PG ``real`` (4 bytes / 24-bit mantissa), a silent
precision drop vs the brief's ``DOUBLE PRECISION`` (8 bytes / 53-bit).

Forward-only per §N8 — downgrade is a symmetric stub. FK ordering
constrains drop order: ``executions`` first (depends on orders + trades),
then ``trades`` (depends on orders), then ``orders`` (depends on bots).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # 1. orders — regular table.
    op.create_table(
        "orders",
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
        sa.Column("signal_id", sa.BigInteger(), nullable=True),  # may be null for manual
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("exchange_order_id", sa.Text(), nullable=True),  # null until placed
        sa.Column("exchange", sa.Text(), nullable=False),  # 'bybit' | 'paper'
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),  # 'buy' | 'sell'
        sa.Column("order_type", sa.Text(), nullable=False),  # 'market' | 'limit' | ...
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
    op.execute("CREATE INDEX orders_bot_status ON orders (bot_id, status)")
    op.execute("CREATE INDEX orders_correlation ON orders (correlation_id)")

    # 2. trades — regular table; FK open_order_id → orders.id (NOT NULL),
    #    close_order_id is plain BIGINT (NO FK per §7.2 line 991 verbatim).
    op.create_table(
        "trades",
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
            sa.ForeignKey("orders.id"),
            nullable=False,
        ),
        sa.Column("close_order_id", sa.BigInteger(), nullable=True),  # NO FK per §7.2
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
    op.execute("CREATE INDEX trades_bot_status ON trades (bot_id, status)")
    op.execute("CREATE INDEX trades_closed_at ON trades (closed_at DESC) WHERE status = 'closed'")

    # 3. executions — hypertable on executed_at; composite PK
    #    (executed_at, id) per §7.2; UNIQUE (exchange_exec_id, executed_at)
    #    + INDEX (trade_id) per §7.2. bot_id is plain TEXT NOT NULL — NO FK
    #    on bots per §7.2 line 1021 verbatim (do NOT add by symmetry).
    op.create_table(
        "executions",
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
            sa.ForeignKey("orders.id"),
            nullable=False,
        ),
        sa.Column(
            "trade_id",
            sa.BigInteger(),
            sa.ForeignKey("trades.id"),
            nullable=True,  # backfilled when trade row materialises
        ),
        sa.Column("bot_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=False),
        sa.Column("qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("fee", sa.Numeric(20, 8), nullable=False),
        sa.Column("exec_type", sa.Text(), nullable=False),
        sa.Column("executed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("executed_at", "id", name="executions_pkey"),
    )

    op.execute(
        "SELECT create_hypertable("
        "'executions', 'executed_at', "
        "chunk_time_interval => interval '7 days'"
        ")"
    )

    op.execute(
        "CREATE UNIQUE INDEX executions_exchange_id ON executions (exchange_exec_id, executed_at)"
    )
    op.execute("CREATE INDEX executions_trade ON executions (trade_id)")

    # NO retention/compression policies on executions per §18.3 line 2439
    # (trades + executions kept forever). Anti-test (f) in test_0005_migration
    # asserts ZERO policy_retention / policy_compression rows for executions.


def downgrade() -> None:
    # Symmetric-stub per §N8 forward-only.
    # FK ordering: drop dependents first (executions, trades), then orders.
    op.execute("DROP INDEX IF EXISTS executions_trade")
    op.execute("DROP INDEX IF EXISTS executions_exchange_id")
    op.drop_table("executions")
    op.execute("DROP INDEX IF EXISTS trades_closed_at")
    op.execute("DROP INDEX IF EXISTS trades_bot_status")
    op.drop_table("trades")
    op.execute("DROP INDEX IF EXISTS orders_correlation")
    op.execute("DROP INDEX IF EXISTS orders_bot_status")
    op.drop_table("orders")
