"""bot_kill_switch_state table — persistent risk kill-switch latch (H-027).

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-15

Eighteenth migration for scalper-v2 (T-525a1 / ADR-0011 risk-management
hardening cluster; H-027).

Single per-bot latch row backing the daily-loss kill-switch (T-525a2 writer)
and, forward-compat, the max-drawdown hard-stop (T-525b). The latch MUST
persist across a strategy-engine restart and be re-evaluated on startup
(H-027) — an in-memory-only latch would reset on restart and silently
re-enable a bot the operator's risk limit had stopped (capital-loss
exposure).

7 columns:

* ``bot_id TEXT`` PK, FK → ``bots(bot_id)`` — one latch row per bot.
* ``tripped BOOLEAN NOT NULL DEFAULT false`` — the latch flag.
* ``trip_reason TEXT`` — NULL when not tripped; ``'daily_loss_limit'``
  (T-525a2 writer) or ``'max_drawdown'`` (T-525b, reserved). The reconcile
  predicate (``is_stale_daily_latch``) treats only the daily reason as
  UTC-day-auto-clearable; ``'max_drawdown'`` is a hard-stop (T-525b).
* ``tripped_at TIMESTAMPTZ`` — UTC instant of the trip; NULL when not
  tripped. Writers pass explicit UTC datetimes (§N1; never NOW()).
* ``daily_anchor_date DATE`` — the UTC date the current daily latch
  window belongs to. Reconcile clears a daily latch whose anchor precedes
  the current UTC date (a new trading day); a same-UTC-day latch is
  retained across restart (H-027).
* ``cumulative_loss_usd NUMERIC(20,4)`` — audit: the realized-P&L sum
  that tripped the daily latch (T-525a2 writes; §5.13 money precision).
* ``updated_at TIMESTAMPTZ NOT NULL`` — last write (trip or clear); UTC
  explicit per §N1.

No index beyond the PK — access is always ``WHERE bot_id = $1`` (PK
lookup) or a per-bot upsert/clear; the table is one row per bot, never
scanned. NOT a hypertable. Forward-only per §N8. Downgrade drops the
table.

§N1: NO ``server_default`` time on any TIMESTAMPTZ column (WG#4) — writers
supply explicit UTC datetimes; ``CURRENT_TIMESTAMP``/``NOW()`` never used.
The only ``server_default`` is the boolean constant ``false`` on
``tripped`` (a non-time literal default, mirror 0016 ``attempt_count``
integer-constant precedent).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_kill_switch_state",
        sa.Column("bot_id", sa.Text(), nullable=False),
        sa.Column(
            "tripped",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("trip_reason", sa.Text(), nullable=True),
        sa.Column("tripped_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("daily_anchor_date", sa.Date(), nullable=True),
        sa.Column("cumulative_loss_usd", sa.Numeric(20, 4), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("bot_id", name="bot_kill_switch_state_pkey"),
        sa.ForeignKeyConstraint(
            ["bot_id"],
            ["bots.bot_id"],
            name="bot_kill_switch_state_bot_id_fkey",
        ),
    )


def downgrade() -> None:
    op.drop_table("bot_kill_switch_state")
