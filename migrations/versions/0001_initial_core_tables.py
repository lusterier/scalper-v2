"""initial core tables: bots, bot_configs, symbol_map (+ TimescaleDB extension).

Revision ID: 0001
Revises:
Create Date: 2026-04-20

First migration for scalper-v2 (brief §7.2, §7.4, §19 F0 bullet 3).

Creates the three config-plane tables that do not hold time-series
data and therefore stay on regular Postgres tables:

* ``bots``         — bot registry (PK ``bot_id``).
* ``bot_configs``  — versioned YAML config history per bot
                     (monotonic ``(bot_id, version)``).
* ``symbol_map``   — TradingView input → Bybit canonical alias map.

Also installs the TimescaleDB extension (``CREATE EXTENSION IF NOT
EXISTS timescaledb``) because this is the first migration to run on
any fresh database (§7.4: "migration that first needs timescaledb owns
the CREATE EXTENSION statement", deferred to here per the T-009
compose smoke: image ships the extension available-but-not-created).

Hypertable tables (``signals`` and friends) land in subsequent
migrations — T-011 creates ``signals``; per-service migrations bring
the rest in F1+.

Seed data: two rows in ``symbol_map`` from Appendix B.4 so the
signal-gateway (T-015) can resolve ``BTCUSDT.P`` / ``ETHUSDT.P`` out of
the box without an operator first applying a YAML config. ``bots`` and
``bot_configs`` stay empty — rows there require a ``config_hash``
produced by the F3 YAML-apply flow that does not exist yet.

``config_hash NOT NULL`` on ``bots`` and ``bot_configs`` is a §7.2
schema invariant, not a §20 hazard: empty table + NOT NULL is
syntactically fine; the first row will arrive with its hash populated.
"""

from __future__ import annotations

import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# Seed rows carry a single UTC timestamp used for both created_at and
# updated_at on insert. Computed Python-side per §5.12 / N1 — never
# `NOW()` / `CURRENT_TIMESTAMP` in SQL — and inlined rather than
# importing `packages.core.time.now_utc` to keep the migration file
# free of workspace-package imports (migrations run under their own
# sys.path and should stay self-contained).
_SEED_TIMESTAMP = datetime.datetime(2026, 4, 20, tzinfo=datetime.UTC)

_SYMBOL_MAP_SEED: tuple[dict[str, object], ...] = (
    {
        "input_symbol": "BTCUSDT.P",
        "canonical_symbol": "BTCUSDT",
        "exchange_source": "binance",
        "notes": None,
        "created_at": _SEED_TIMESTAMP,
        "updated_at": _SEED_TIMESTAMP,
    },
    {
        "input_symbol": "ETHUSDT.P",
        "canonical_symbol": "ETHUSDT",
        "exchange_source": "binance",
        "notes": None,
        "created_at": _SEED_TIMESTAMP,
        "updated_at": _SEED_TIMESTAMP,
    },
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "bots",
        sa.Column("bot_id", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("exchange_mode", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("config_applied_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "meta",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_table(
        "bot_configs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column(
            "bot_id",
            sa.Text(),
            sa.ForeignKey("bots.bot_id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("applied_by", sa.Text(), nullable=False),
        sa.Column("config_yaml", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("bot_id", "version", name="bot_configs_bot_version_unique"),
    )

    symbol_map = op.create_table(
        "symbol_map",
        sa.Column("input_symbol", sa.Text(), primary_key=True),
        sa.Column("canonical_symbol", sa.Text(), nullable=False),
        sa.Column("exchange_source", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )

    op.bulk_insert(symbol_map, list(_SYMBOL_MAP_SEED))


def downgrade() -> None:
    # Forward-only per §N8 — downgrade is a stub kept so `alembic
    # downgrade base` does not fail catastrophically in a dev loop.
    # Extension is left in place: dropping it would cascade to every
    # hypertable created by later migrations, and the extension being
    # installed is harmless on its own.
    op.drop_table("symbol_map")
    op.drop_table("bot_configs")
    op.drop_table("bots")
