"""§N4 unit table for :func:`packages.db.queries.lifecycle.derive_lifecycle_state` (T-533a).

Pure / no DB — every §Mapping-table branch + the `status` sole-discriminator
precedence + the defensive `error→FAILED` totality + the unknown-status
ValueError guard. The testcontainer L-003 cross-check
(`test_0020_migration.py`) separately pins SQL ≡ this function over the full
legacy combination matrix.
"""

from __future__ import annotations

import pytest

from packages.core import TradeLifecycleState
from packages.db.queries.lifecycle import derive_lifecycle_state

# (status, close_reason, tp_hit, sl_type, trailing_active, has_position_state) → expected
_CASES: tuple[
    tuple[str, str | None, bool | None, str | None, bool | None, bool, TradeLifecycleState], ...
] = (
    # status='error' → FAILED (defensive / enum-vocab-complete; no current writer)
    ("error", None, None, None, None, False, TradeLifecycleState.FAILED),
    ("error", "anything", True, "trail", True, True, TradeLifecycleState.FAILED),
    # status='closed' → reconcile_gone → RECONCILED, else → CLOSED
    ("closed", "reconcile_gone", None, None, None, False, TradeLifecycleState.RECONCILED),
    ("closed", "manual", None, None, None, False, TradeLifecycleState.CLOSED),
    ("closed", "emergency", None, None, None, False, TradeLifecycleState.CLOSED),
    ("closed", "sl", None, None, None, False, TradeLifecycleState.CLOSED),
    ("closed", "trail", None, None, None, False, TradeLifecycleState.CLOSED),
    ("closed", "unknown", None, None, None, False, TradeLifecycleState.CLOSED),
    ("closed", None, None, None, None, False, TradeLifecycleState.CLOSED),
    # status='open' precedence: no-ps → ORPHANED (before any ps-flag read)
    ("open", None, None, None, None, False, TradeLifecycleState.ORPHANED),
    # trail wins over be/tp (sl_type) and over trailing_active flag alone
    ("open", None, True, "trail", False, True, TradeLifecycleState.TRAILING_ACTIVE),
    ("open", None, False, None, True, True, TradeLifecycleState.TRAILING_ACTIVE),
    # be wins over tp_hit
    ("open", None, True, "be", False, True, TradeLifecycleState.BREAKEVEN_SET),
    # tp_hit (not trail/be)
    ("open", None, True, "protective", False, True, TradeLifecycleState.PARTIALLY_CLOSED),
    # plain open (protective / no flags)
    ("open", None, False, "protective", False, True, TradeLifecycleState.OPEN),
    ("open", None, False, None, False, True, TradeLifecycleState.OPEN),
)


@pytest.mark.parametrize(
    ("status", "close_reason", "tp_hit", "sl_type", "trailing_active", "has_ps", "expected"),
    _CASES,
)
def test_derive_lifecycle_state(
    status: str,
    close_reason: str | None,
    tp_hit: bool | None,
    sl_type: str | None,
    trailing_active: bool | None,
    has_ps: bool,
    expected: TradeLifecycleState,
) -> None:
    assert (
        derive_lifecycle_state(
            status=status,
            close_reason=close_reason,
            tp_hit=tp_hit,
            sl_type=sl_type,
            trailing_active=trailing_active,
            has_position_state=has_ps,
        )
        == expected
    )


def test_derive_lifecycle_state_unknown_status_raises() -> None:
    """Totality guard: a status outside {open,closed,error} (impossible for
    real rows — only writers are insert_trade→'open'/update_trade_close→
    'closed') raises rather than returning a wrong enum."""
    with pytest.raises(ValueError, match=r"unmapped trades\.status"):
        derive_lifecycle_state(
            status="bogus",
            close_reason=None,
            tp_hit=None,
            sl_type=None,
            trailing_active=None,
            has_position_state=False,
        )
