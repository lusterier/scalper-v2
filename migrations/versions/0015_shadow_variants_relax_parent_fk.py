"""shadow_variants: drop parent_trade_id FK + add parent_kind discriminator.

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-08

Fifteenth migration for scalper-v2 (T-511b2a / ADR-0010).

Relaxes migration 0014 FK constraint so ``shadow_variants`` can reference
EITHER ``trades.id`` (live execution) OR ``paper_trades.id`` (paper
execution). ``parent_kind`` discriminator carries the routing context;
integrity enforced at application layer via single-source-of-truth
``BotConfig.exchange.mode`` flowing through ``OrderRequest`` →
``ShadowStartPayload.parent_kind`` → DB row.

Mirrors T-510a OQ-6=A no-FK convention for ``shadow_rejected.signal_id``
(structural pattern: parent_trade_id BIGINT, no FK, discriminator-routed).
The rationale here differs from T-510a (signals composite PK technical
constraint) — see ADR-0010 §"Rejected alternatives" Alternative D for
quantitative comparison vs dual-FK + XOR CHECK pattern (~340 vs ~530 LOC,
60% overhead) which justified the discriminator-only approach.

Backfill: parent_kind defaults to 'live' for any existing rows. In practice
the table is empty at 0015 runtime (T-510a shipped 0014; T-511b1 shadow
worker does not write to shadow_variants until T-511b2 producer ships).
Two-step ALTER (server_default='live' + nullable=False, then drop server
default) is defensive for future migrations against non-empty tables.

Downgrade re-adds FK to ``trades(id) ON DELETE CASCADE`` + drops
``parent_kind``. Downgrade WILL FAIL if any rows have ``parent_kind='paper'``
AND ``parent_trade_id`` ∉ ``trades.id`` (FK violation). Operator-acknowledged
risk per ADR-0010 §"Trade-offs"/Downgrade-on-paper-rows.

§7.4:1192 destructive-migration ADR coverage: FK drop is a constraint-
removal (destructive) per BRIEF §7.4 reading; covered by ADR-0010 §"Trade-
offs" + §"Implementation references" line citing §7.4:1192. No data-
migration script needed (table empty at runtime).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "shadow_variants_parent_trade_id_fkey",
        "shadow_variants",
        type_="foreignkey",
    )
    # Two-step ALTER per WG#1: backfill default 'live' so NOT NULL applies
    # cleanly to any existing rows, THEN drop the default so subsequent
    # INSERTs must specify parent_kind explicitly.
    op.add_column(
        "shadow_variants",
        sa.Column(
            "parent_kind",
            sa.Text(),
            server_default=sa.text("'live'"),
            nullable=False,
        ),
    )
    op.alter_column("shadow_variants", "parent_kind", server_default=None)


def downgrade() -> None:
    op.drop_column("shadow_variants", "parent_kind")
    op.create_foreign_key(
        "shadow_variants_parent_trade_id_fkey",
        "shadow_variants",
        "trades",
        ["parent_trade_id"],
        ["id"],
        ondelete="CASCADE",
    )
