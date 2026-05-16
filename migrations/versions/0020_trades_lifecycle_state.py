"""trades.lifecycle_state additive observable column + legacy backfill.

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-16

Twentieth migration for scalper-v2 (T-533a; ADR-0011 pre-live operational
hardening — named-state ``TradeLifecycleState`` FSM cluster, L-007
pre-emptive-split foundation leaf 1/2; T-533b dual-write blocked-by this).

Adds a nullable ``trades.lifecycle_state TEXT`` column — the single
canonical *observable* lifecycle state (``packages.core.TradeLifecycleState``,
13-state ``StrEnum``) consolidating the legacy 4-column model. **Additive /
observability-only (T-533 OQ-1=A)**: the legacy columns
(``trades.status`` / ``trades.close_reason`` /
``position_state.{tp_hit,sl_type,trailing_active}``) REMAIN authoritative
for every read and decision; nothing reads ``lifecycle_state`` (no
behavioral consumer — T-533b owns the forward dual-write). Zero behavior
change, 0 §20 regression, no new H-NNN (§0.8). Plain ``TEXT`` no CHECK
(repo convention — ``trades.status``/``close_reason`` are ``sa.Text()``;
no PG ENUM exists anywhere; value additions are app-layer only).

Backfill maps every historical row from the legacy authoritative state.
``status`` is the sole top-level discriminator (precedence verbatim ==
``packages.db.queries.lifecycle.derive_lifecycle_state``; the L-003
golden cross-check pins SQL ≡ Python over the full combination matrix —
the migration ``CASE`` is frozen/self-contained, Alembic must NOT import
mutable app code):

* ``status='error'`` → ``failed`` (defensive / enum-vocab-complete: no
  current writer, the ``TradeStatus.ERROR`` T-221 orphan/partial-failure
  domain value — keeps the mapping total).
* ``status='closed'`` → ``reconciled`` iff ``close_reason='reconcile_gone'``
  (T-221 reconcile-close), else ``closed``.
* ``status='open'`` → first match: no position_state row → ``orphaned``
  (DB-divergence / H-020); ``sl_type='trail'`` OR ``trailing_active`` →
  ``trailing_active``; ``sl_type='be'`` → ``breakeven_set``;
  ``tp_hit`` → ``partially_closed``; else → ``open``.

**L-020 ``trade_id``-anchored join**: ``LEFT JOIN position_state ps ON
ps.trade_id = trades.id`` — ``position_state.trade_id`` is
``BigInteger ForeignKey('trades.id') NOT NULL`` (0006), the *immutable*
anchor. NOT ``ps.bot_id=... AND ps.symbol=...``: position_state PK is
``(bot_id,symbol)`` and is deleted-on-close + re-inserted for the NEXT
trade on the same ``(bot,symbol)`` — a composite-key join would
mis-associate a historical closed trade with a new open trade's
position_state row (the L-020 composite-PK-reuse hazard). Anchoring on
``trade_id`` is correct for ALL rows: open → its own live ps; closed /
error → no ps for that ``trade_id`` → ``ps.*`` NULL → status-driven.

Transient states (``signal_received`` / ``order_requested`` /
``order_placed`` / ``closing`` / ``tp_hit``) are forward-only — no legacy
column records them; the backfill never produces them (T-533b dual-write).

L-015 sibling-migration-test-impact: ``tests/integration/migrations/
test_0005_migration.py`` is the sole live-``trades`` column-shape
assertion (``_EXPECTED_TRADES_COLUMNS`` 21-tuple) — element-22
``("lifecycle_state","text","YES")`` appended in T-533a. test_0006 /
test_0014 ``INSERT INTO trades`` omit ``lifecycle_state`` but are
nullable-safe (no NOT NULL / no server default → no NotNullViolationError;
no shape-tuple → no count mismatch); test_0008 (``paper_trades``) /
test_0013 (``backtest_trades``) out of scope.

Forward-only per §N8 — downgrade is a symmetric ``drop_column``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_BACKFILL_SQL = """
UPDATE trades t
SET lifecycle_state = CASE
    WHEN t.status = 'error' THEN 'failed'
    WHEN t.status = 'closed' AND t.close_reason = 'reconcile_gone' THEN 'reconciled'
    WHEN t.status = 'closed' THEN 'closed'
    WHEN t.status = 'open' AND ps.trade_id IS NULL THEN 'orphaned'
    WHEN t.status = 'open' AND (ps.sl_type = 'trail' OR ps.trailing_active) THEN 'trailing_active'
    WHEN t.status = 'open' AND ps.sl_type = 'be' THEN 'breakeven_set'
    WHEN t.status = 'open' AND ps.tp_hit THEN 'partially_closed'
    WHEN t.status = 'open' THEN 'open'
END
FROM trades base
LEFT JOIN position_state ps ON ps.trade_id = base.id
WHERE t.id = base.id
"""


def upgrade() -> None:
    op.add_column(
        "trades",
        sa.Column("lifecycle_state", sa.Text(), nullable=True),
    )
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    op.drop_column("trades", "lifecycle_state")
