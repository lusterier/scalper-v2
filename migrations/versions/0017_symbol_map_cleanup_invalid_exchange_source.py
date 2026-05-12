"""symbol_map cleanup — DELETE invalid exchange_source rows (T-520 sub-commit #3).

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-12

Defensive cleanup migration. ``packages.core.types.ExchangeSource`` enum
defines 3 valid values: ``binance``, ``bybit``, ``custom`` (per
:mod:`packages.core.types:132-141`). Operator-side dev DB had 2 stale
``tradingview`` rows (manually DELETE-d 2026-05-05 pre-runbook); this
migration ensures prod parity on next deploy + future fresh-DB integrity.

Per CONCERN#1 plan-reviewer Gate 1 REVISE 2026-05-12: pure DELETE on a
config table is operationally risky. Migration logs deleted-row count
via alembic logger so postmortem is possible if operator finds rows
missing post-deploy. Per L-012 active control: integration test uses
explicit ``downgrade 0016`` (NOT relative ``-1``).

Forward-only per §N8 — invalid rows are gone; re-insert is operator-side
(operator-runnable INSERT statement documented in commit message).
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "DELETE FROM symbol_map WHERE exchange_source NOT IN ('binance', 'bybit', 'custom')"
        )
    )
    deleted = result.rowcount
    _logger.warning(
        "migration_0017_deleted_rows count=%d table=symbol_map "
        "rationale=invalid_exchange_source_cleanup",
        deleted,
    )


def downgrade() -> None:
    # Forward-only per §N8 — invalid rows are gone; re-insert is operator-side
    # via manual INSERT (no alembic seed).
    pass
