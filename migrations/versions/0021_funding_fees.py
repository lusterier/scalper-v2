"""funding_fees hypertable.

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-16

Twenty-first migration for scalper-v2 (T-532a; ADR-0011 pre-live operational
hardening — account-balance/equity sub-cluster, funding-fee tracking;
ADR-0006 cumulative-delta financial-truth context; ADR-0007 D7 APScheduler
tick idempotency).

**Doubly-stale-doc correction (L-018, T-533a "0018→0020" precedent class):**
TASKS.md:237 says "migration 0017" and ADR-0011 says "0016+0017" — BOTH
stale (0017 = the shipped ``0017_symbol_map_cleanup``; the migration head at
T-532a time is ``0020_trades_lifecycle_state`` revision='0020'). This
migration is **0021, down_revision='0020'**.

Creates the ``funding_fees`` hypertable: per-bot perpetual-funding
settlement time-series. The execution-service APScheduler funding-poll tick
(T-532b — operator OQ-4=A, a SEPARATE tick mirroring the T-531
equity_snapshot tick) calls ``ExchangeClient.get_funding_fees_window()``
(T-532a) per sub-account and fans the result out to one row per settlement
— ``(bot_id, symbol, settled_at, funding)``. T-532b feeds the T-220
cumulative-delta P&L audit a SEPARATE cumulative funding term (operator
OQ-3=A — H-017-clean, NEVER folded into ``trades.realized_pnl``;
``trades.realized_pnl`` stays the T-219 close-flow source of truth per
ADR-0006).

``funding`` is ``Numeric(20, 4)`` — the repo-wide USD-money/P&L convention
(mirror 0019 ``bot_equity_snapshots`` 5 money cols + 0009 ``trade_pnl_deltas``
+ 0006 ``position_state.running_pnl``; same ADR-0011 financial-truth-adjacent
context). Signed: negative = funding paid, positive = funding received.
Unbounded Bybit ``Decimal`` → PostgreSQL round-half-even to scale 4 at
INSERT; accepted, NOT a silent degradation — $0.0001 USD granularity is
ample for funding-cost tracking (financial truth is the T-220
cumulative-delta audit per ADR-0006).

PRIMARY KEY ``(settled_at, id)`` per TimescaleDB hypertable convention
(partition column must be in any PK; surrogate ``id`` BIGSERIAL via
``sa.Identity(always=False)`` disambiguates same-instant multi-settlement
fan-out — load-bearing). Mirror 0019 ``bot_equity_snapshots`` hypertable
shape. No FK (hypertable-sibling convention — trading_events /
trade_pnl_deltas / bot_equity_snapshots carry none). Pure append — no
UNIQUE constraint (multiple settlements per bot over time is the point).

Forward-only per §N8 — downgrade is a symmetric stub.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "funding_fees",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("bot_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("settled_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("funding", sa.Numeric(20, 4), nullable=False),
        sa.PrimaryKeyConstraint(
            "settled_at",
            "id",
            name="funding_fees_pkey",
        ),
    )

    op.execute(
        "SELECT create_hypertable("
        "'funding_fees', 'settled_at', "
        "chunk_time_interval => interval '7 days'"
        ")"
    )

    op.execute("CREATE INDEX ix_funding_fees_bot_settled ON funding_fees (bot_id, settled_at DESC)")


def downgrade() -> None:
    # Symmetric-stub per §N8 forward-only.
    op.execute("DROP INDEX IF EXISTS ix_funding_fees_bot_settled")
    op.drop_table("funding_fees")
