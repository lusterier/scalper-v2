"""H-021 invariant: NO ``timezone=`` kwarg at any ``add_job(...)`` site.

Per ADR-0007 D2: scheduler ctor sets ``timezone=UTC`` as the single audit
point; per-job overrides are FORBIDDEN. AST scan walks scheduler-using
modules under ``services/execution/app/`` and asserts no Call node to
``add_job`` / ``scheduled_job`` carries a ``timezone`` keyword.

Plain grep would miss multi-line formatted calls; AST scan is structurally
robust per ADR-0007 D2 implementation directive.

**Scope** (per WG#14 of T-220b plan): only ``services/execution/app/`` is
scanned. Test files in ``services/execution/tests/`` are NOT scanned —
test fixtures may legitimately use ``add_job(timezone=...)`` for negative
pins. If a future task adds scheduler use outside execution-service, this
regression test must be replicated in that service's test tree.
"""

from __future__ import annotations

import ast
from pathlib import Path

_EXEC_APP_DIR = Path(__file__).parent.parent / "app"


def test_no_timezone_kwarg_on_add_job_calls_in_execution_app() -> None:
    """ADR-0007 D2 enforcement: no ``add_job(..., timezone=...)`` calls in app/."""
    offenders: list[str] = []
    for py_file in _EXEC_APP_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr_name: str | None = None
            if isinstance(func, ast.Attribute):
                attr_name = func.attr
            if attr_name not in ("add_job", "scheduled_job"):
                continue
            for kw in node.keywords:
                if kw.arg == "timezone":
                    rel = py_file.relative_to(_EXEC_APP_DIR.parent.parent)
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], (
        "H-021 violation: add_job/scheduled_job calls must NEVER pass timezone= "
        f"kwarg (UTC-only per ADR-0007 D2). Offenders: {offenders}"
    )
