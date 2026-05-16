"""Legacy-4-column → :class:`TradeLifecycleState` mapping (T-533a).

The single canonical Python mapping from the legacy authoritative state
(``trades.status`` / ``trades.close_reason`` /
``position_state.{tp_hit,sl_type,trailing_active}`` + position_state
existence) to the new observable :class:`TradeLifecycleState`.

**Two impls, one mapping (L-003).** The migration 0020 backfill is a
frozen self-contained SQL ``CASE`` (Alembic migrations must NOT import
mutable app code); this pure function is the runtime equal for T-533b
dual-write. The L-003 golden cross-check
(``test_lifecycle_state_mapping_sql_matches_python``) pins SQL ≡ Python
over the full legacy combination matrix so the two cannot drift.

Precedence (``status`` is the sole top-level discriminator — T-533 OQ /
plan-reviewer B2; identical ordering in the migration ``CASE``):

* ``status='error'`` → ``FAILED`` (defensive / enum-vocab-complete: no
  current writer per exhaustive grep, but ``TradeStatus.ERROR`` is the
  T-221 orphan/partial-failure domain value — mapping it keeps this
  function *total* over the ``TradeStatus`` domain so the L-003
  SQL ≡ Python totality invariant holds).
* ``status='closed'`` → ``RECONCILED`` iff ``close_reason='reconcile_gone'``
  (T-221 reconcile-close, ``restart.py:156``), else ``CLOSED`` (all other
  close_reasons: ``manual``/``sl``/``trail``/``unknown``/``emergency``/NULL).
* ``status='open'`` → first match top-down: no position_state row
  (``has_position_state`` False) → ``ORPHANED`` (genuine DB-divergence /
  H-020; an open trade normally has a position_state row inserted in the
  placement tx, deleted only on close); ``sl_type='trail'`` OR
  ``trailing_active`` → ``TRAILING_ACTIVE``; ``sl_type='be'`` →
  ``BREAKEVEN_SET``; ``tp_hit`` → ``PARTIALLY_CLOSED``; else → ``OPEN``.

Transient states (``SIGNAL_RECEIVED`` / ``ORDER_REQUESTED`` /
``ORDER_PLACED`` / ``CLOSING`` / ``TP_HIT``) are forward-only — no legacy
column records them; this mapping never produces them (T-533b dual-write
owns them going forward).
"""

from __future__ import annotations

from packages.core import TradeLifecycleState


def derive_lifecycle_state(
    *,
    status: str,
    close_reason: str | None,
    tp_hit: bool | None,
    sl_type: str | None,
    trailing_active: bool | None,
    has_position_state: bool,
) -> TradeLifecycleState:
    """Map the legacy authoritative state to the observable lifecycle state.

    Pure / deterministic. Exact runtime equal of the migration 0020
    backfill ``CASE`` (L-003 cross-check pins equivalence). ``status`` is
    the sole top-level discriminator; see module docstring for precedence.

    Raises:
        ValueError: ``status`` not in the ``TradeStatus`` vocabulary
            (``{'open','closed','error'}``) — impossible for real rows
            (the only writers are ``insert_trade``→'open' /
            ``update_trade_close``→'closed'); guards data corruption and
            keeps the return type total.
    """
    if status == "error":
        return TradeLifecycleState.FAILED
    if status == "closed":
        if close_reason == "reconcile_gone":
            return TradeLifecycleState.RECONCILED
        return TradeLifecycleState.CLOSED
    if status == "open":
        if not has_position_state:
            return TradeLifecycleState.ORPHANED
        if sl_type == "trail" or trailing_active:
            return TradeLifecycleState.TRAILING_ACTIVE
        if sl_type == "be":
            return TradeLifecycleState.BREAKEVEN_SET
        if tp_hit:
            return TradeLifecycleState.PARTIALLY_CLOSED
        return TradeLifecycleState.OPEN
    msg = f"unmapped trades.status: {status!r}"
    raise ValueError(msg)
