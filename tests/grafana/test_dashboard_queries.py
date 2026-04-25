"""Validate every PromQL expression in ``infra/grafana/dashboards/*.json``.

Each ``panels[*].targets[*].expr`` is sent to ``/api/v1/query`` against
a zero-data Prometheus. The endpoint returns ``status="success"`` on a
parseable expression even when the result set is empty, and
``status="error"`` with an ``errorType`` of ``bad_data`` /
``execution`` when PromQL fails to parse or to type-check (function
arity, ``histogram_quantile`` on non-histogram, etc.).

Asserts only ``status="success"`` — result data is not inspected, so
this stub is robust to scrape-coverage drift. F4+ graduation: seed
metrics into Prometheus and assert non-empty results to catch
metric-name typos. Out of scope for the F0 stub.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import discover_dashboard_queries


@pytest.mark.parametrize(
    ("dashboard", "panel_id", "ref_id", "expr"),
    list(discover_dashboard_queries()),
    ids=lambda v: str(v),
)
def test_dashboard_query_parses(
    prometheus_url: str,
    dashboard: str,
    panel_id: int,
    ref_id: str,
    expr: str,
) -> None:
    """``status="success"`` from ``/api/v1/query`` proves the expr is valid PromQL."""
    response = httpx.get(
        f"{prometheus_url}/api/v1/query",
        params={"query": expr},
        timeout=5.0,
    )
    assert response.status_code == 200, (
        f"{dashboard}#{panel_id}[{ref_id}]: HTTP {response.status_code} "
        f"querying {expr!r}: {response.text}"
    )
    body = response.json()
    if body.get("status") != "success":
        msg = (
            f"{dashboard}#{panel_id}[{ref_id}]: PromQL failed to parse "
            f"({body.get('errorType', '?')}): {body.get('error', body)}\n"
            f"  expr: {expr}"
        )
        raise AssertionError(msg)
