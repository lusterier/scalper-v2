"""bot_equity_snapshots hypertable.

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-15

Nineteenth migration for scalper-v2 (brief §15.3:2161 ``virtual_balance{bot_id}``
gauge mention promoted to mandatory T-531; ADR-0011 pre-live operational
hardening — account-balance/equity sub-cluster; ADR-0007 D7 APScheduler tick
idempotency).

Creates the ``bot_equity_snapshots`` hypertable: per-bot account-equity
time-series. The execution-service APScheduler tick (T-531) calls
``ExchangeClient.get_account_balance()`` (T-530) once per sub-account and
fans the result out to one row per bot — ``(bot_id, snapshot_at,
wallet_balance, available_balance, total_equity, margin_balance,
unrealized_pnl)``.

5 balance columns are ``Numeric(20, 4)`` — the repo-wide USD-money/P&L
convention (mirror 0009 ``trade_pnl_deltas`` cumulative_bybit/db/delta +
0006 ``position_state.running_pnl``; same ADR-0011 financial-truth-adjacent
context). NOT ``Numeric(30, 12)`` — that scale is the price/qty boundary
(OHLC / entry / sl / tp / qty), not money. Unbounded T-530 ``AccountBalance``
``Decimal`` → PostgreSQL round-half-even to scale 4 at INSERT; accepted, NOT
a silent degradation — this is a monitoring time-series, NOT P&L-truth
(financial truth is the T-220 cumulative-delta audit per ADR-0006). $0.0001
USD granularity is ample for equity-trend monitoring.

PRIMARY KEY ``(snapshot_at, id)`` per TimescaleDB hypertable convention
(partition column must be in any PK; surrogate ``id`` BIGSERIAL via
``sa.Identity(always=False)`` disambiguates the same-instant N-bot fan-out
when multiple bots share one sub-account — load-bearing, not cosmetic).
Mirror trade_pnl_deltas (0009:57) / trading_events (0007) hypertable shape.
No FK (hypertable siblings trading_events / trade_pnl_deltas carry none;
``bot_kill_switch_state`` FK→bots is the non-hypertable case). Pure append —
no UNIQUE constraint (multiple snapshots per bot over time is the point).

Forward-only per §N8 — downgrade is a symmetric stub.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_equity_snapshots",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("bot_id", sa.Text(), nullable=False),
        sa.Column("snapshot_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("wallet_balance", sa.Numeric(20, 4), nullable=False),
        sa.Column("available_balance", sa.Numeric(20, 4), nullable=False),
        sa.Column("total_equity", sa.Numeric(20, 4), nullable=False),
        sa.Column("margin_balance", sa.Numeric(20, 4), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(20, 4), nullable=False),
        sa.PrimaryKeyConstraint(
            "snapshot_at",
            "id",
            name="bot_equity_snapshots_pkey",
        ),
    )

    op.execute(
        "SELECT create_hypertable("
        "'bot_equity_snapshots', 'snapshot_at', "
        "chunk_time_interval => interval '7 days'"
        ")"
    )

    op.execute(
        "CREATE INDEX ix_bot_equity_snapshots_bot_snapshot "
        "ON bot_equity_snapshots (bot_id, snapshot_at DESC)"
    )


def downgrade() -> None:
    # Symmetric-stub per §N8 forward-only.
    op.execute("DROP INDEX IF EXISTS ix_bot_equity_snapshots_bot_snapshot")
    op.drop_table("bot_equity_snapshots")
