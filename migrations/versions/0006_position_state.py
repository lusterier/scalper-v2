"""position_state regular table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-27

Sixth migration for scalper-v2 (brief §7.2 lines 1058-1080, §N8
forward-only + tested, §19 F2 milestone — live in-flight position
state landing).

Creates the ``position_state`` regular table consumed by
execution-service for live position lifecycle management:

* T-216b INSERTs the row inside the order placement transaction
  (alongside ``orders`` + ``trades``).
* T-217 PositionLifecycle FSM ticks at ``POSITION_POLL_INTERVAL``
  (§9.5 line 1586), updating ``mfe_price`` / ``mae_price`` /
  ``running_pnl`` / ``trailing_active`` / ``sl_type`` / ``sl_price``.
* T-218 dispatcher updates ``running_pnl`` and ``remaining_qty``
  on each fill event.
* T-219 cumulative-delta close DELETEs the row at full close
  (row-lifetime = position-lifetime).
* T-221 post-restart reconciliation reads all rows on startup per
  H-020 — orphan-DB rows close as ``reconcile_gone``; matching rows
  resume monitor task (FSM rehydrated from row state per Q3 default).

Schema verbatim from §7.2 lines 1058-1080:

* Composite PK ``(bot_id, symbol)`` — one row per active bot-symbol
  pair. Naturally enforces "one open position per bot-symbol" at
  schema level.
* FK ``trade_id → trades.id`` (NOT NULL). Plain ``REFERENCES`` per
  §7.2 verbatim → Postgres NO ACTION default. Cannot DELETE a
  ``trades`` row while a ``position_state`` row references it;
  practically harmless because §18.3 keeps trades forever.
* No FK on ``bot_id`` per §7.2 verbatim (mirror T-202 W#2 precedent
  for ``executions.bot_id``). Plain ``TEXT NOT NULL``.
* No discrete ``current_state`` enum column. FSM state is
  flag-based per §7.2: (``tp_hit``, ``trailing_active``, ``sl_type``,
  ``remaining_qty``) tuple uniquely identifies T-217's FSM state.
* Server defaults ``DEFAULT FALSE`` / ``DEFAULT 0`` shipped via
  ``sa.text("false")`` / ``sa.text("0")`` (server-side per §7.2
  verbatim, NOT Python literals).
* ``updated_at TIMESTAMPTZ NOT NULL`` — **NO ``server_default``**
  per §N1 (no ``CURRENT_TIMESTAMP`` / ``NOW()`` in SQL).
  Application sets via ``packages.core.now_utc()`` on every
  INSERT/UPDATE.
* No secondary indexes per §7.2 verbatim. Composite PK doubles as
  natural lookup index for ``(bot_id, symbol)`` queries; T-221
  reconciliation full-table-scans (one row per active bot-symbol
  pair, small table). F1+ opportunistic if a downstream task
  surfaces a bottleneck.

§18.3 retention summary (lines 2427-2440) does NOT list
``position_state`` — it is operational state, not historical
record. Regular table; no retention/compression policies (regular
tables cannot have TimescaleDB policies anyway, so no anti-test).

H-020 (post-restart reconciliation): T-221 reads all rows from this
table on startup. Schema-level: table-existence verified by
per-migration test smoke E2E. Behavioural test
(``test_reconciliation_closes_db_orphans_and_markets_exchange_orphans``)
lives at T-221.

H-018 N/A here — H-018 is the BIGSERIAL ``trades.id`` invariant
addressed at 0005. ``position_state`` has composite ``(bot_id,
symbol)`` PK with no BIGSERIAL surface; not the H-018 binding site.

Forward-only per §N8 — downgrade is a symmetric stub.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "position_state",
        sa.Column("bot_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column(
            "trade_id",
            sa.BigInteger(),
            sa.ForeignKey("trades.id"),
            nullable=False,
        ),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("entry_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("remaining_qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("sl_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("tp_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("sl_type", sa.Text(), nullable=True),  # 'protective' | 'be' | 'trail'
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
        # packages.core.now_utc() on every INSERT/UPDATE. Test sub-check
        # asserts column_default IS NULL.
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("bot_id", "symbol", name="position_state_pkey"),
    )


def downgrade() -> None:
    # Symmetric-stub per §N8 forward-only.
    op.drop_table("position_state")
