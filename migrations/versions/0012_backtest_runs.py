"""backtest_runs table + pgcrypto extension.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-04

Twelfth migration for scalper-v2 (brief §7.2:1141-1156, §9.6:1629,
§14.3:2063).

Creates the ``backtest_runs`` table backing T-407's ``/api/backtests/*``
endpoint group. Schema verbatim per §7.2:1144-1156 (11 columns) plus a
12th ``bot_id TEXT NOT NULL`` column required for T-415 Backtest lab
"per-bot historic runs" filter (no FK — mirrors ``audit_events`` /
``trade_pnl_deltas`` no-FK convention; archived bots may still own
backtest history).

Per T-407 plan WG#5 + Gate 1 BLOCKER #2 fix (2026-05-04):
``CREATE EXTENSION IF NOT EXISTS pgcrypto`` MUST be the FIRST upgrade
statement, BEFORE ``op.create_table``. Pgcrypto provides
``gen_random_uuid()`` used as the ``id`` column server-default. This
migration is the FIRST in the repo to need the extension (verified:
``grep -r 'pgcrypto\\|gen_random_uuid' migrations/`` returned 0
matches before T-407 work).

Per T-407 plan WG#6: ``downgrade()`` does NOT drop pgcrypto — N8
forward-only safety; future migrations may rely on
``gen_random_uuid()``.

Indexes:
* ``backtest_runs_started_at_desc (started_at DESC)`` — chronological
  list scroll (T-407 GET /api/backtests/ default ordering).
* ``backtest_runs_bot_id_started (bot_id, started_at DESC)`` — per-bot
  filtered history (T-415 UI "this bot's runs").

NOT a hypertable — backtest_runs is low-volume (F4 expects <100 rows
/year per single operator). 2 btree indexes serve list filter patterns;
no retention policy (forever per BRIEF §18 audit-trail backup).

Forward-only per §N8 — downgrade is a symmetric stub (drops table +
indexes; pgcrypto extension preserved).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # WG#5: pgcrypto provides gen_random_uuid(). FIRST in repo to need it
    # (verified absent from migrations 0001..0011 before T-407 work).
    # IF NOT EXISTS guards against re-run.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "backtest_runs",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("bot_id", sa.Text(), nullable=False),
        sa.Column("config_yaml", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("date_range_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("date_range_end", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("summary", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="backtest_runs_pkey"),
    )

    op.execute("CREATE INDEX backtest_runs_started_at_desc ON backtest_runs (started_at DESC)")
    op.execute(
        "CREATE INDEX backtest_runs_bot_id_started ON backtest_runs (bot_id, started_at DESC)"
    )


def downgrade() -> None:
    # Symmetric-stub per §N8 forward-only.
    op.execute("DROP INDEX IF EXISTS backtest_runs_bot_id_started")
    op.execute("DROP INDEX IF EXISTS backtest_runs_started_at_desc")
    op.drop_table("backtest_runs")
    # WG#6: Do NOT drop pgcrypto extension — N8 forward-only safety;
    # future migrations may rely on gen_random_uuid().
