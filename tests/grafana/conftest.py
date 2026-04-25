"""Fixtures for the dashboard-query test harness (brief §17.6, §19 F0 bullet 10).

The harness validates every PromQL expression embedded in
``infra/grafana/dashboards/*.json`` against a running Prometheus
``/api/v1/query`` endpoint — catches syntax errors, function arity
mistakes, and type mismatches (e.g. ``histogram_quantile`` on a
non-histogram) without depending on scrape data.

The Prometheus URL comes from the ``PROMETHEUS_TEST_URL`` environment
variable. If unset, every test in this directory is skipped at
collection time — keeping CI-fast green while still satisfying the
"accompanying test exists" literal. T-017 wires the CI-full
integration job to set this var via a Prometheus service container.

Template-variable handling (``$var`` / ``${var}`` substitution into
PromQL) is intentionally deferred — F0 dashboards (overview.json) have
no ``templating.list`` block, so a strip/substitute shim now would be
defensive code with no failing test to motivate the design. F1's first
var-bearing dashboard task extends the harness.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


_PROMETHEUS_ENV_VAR = "PROMETHEUS_TEST_URL"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARDS_DIR = _REPO_ROOT / "infra" / "grafana" / "dashboards"


# Module-level skip — keeps ci-fast collection clean. With a fixture-level
# skip, pytest still enumerates every parametrize case (one JSON glob + parse
# per ci-fast run) before skipping each individually. Module-level guarantees
# the parametrize() call in test_dashboard_queries.py never executes when the
# Prometheus URL is unset, so the skip surface is "skipped (1)" not
# "skipped (N parametrize cases)". T-F1+ aligns the migrations conftest to
# this same pattern.
if not os.environ.get(_PROMETHEUS_ENV_VAR):
    pytest.skip(
        f"{_PROMETHEUS_ENV_VAR} not set — dashboard-query tests require a "
        f"reachable Prometheus /api/v1/query endpoint. T-017 wires CI-full "
        f"to set this via a Prometheus service container.",
        allow_module_level=True,
    )


@pytest.fixture(scope="session")
def prometheus_url() -> str:
    return os.environ[_PROMETHEUS_ENV_VAR]


def discover_dashboard_queries() -> Iterator[tuple[str, int, str, str]]:
    """Yield ``(dashboard_name, panel_id, ref_id, expr)`` for every panel target.

    Used at collection time to parametrize :func:`test_dashboard_query_parses`.
    Returns an empty iterator if the dashboards directory is absent so
    pytest's collection does not fail on partial checkouts.
    """
    if not _DASHBOARDS_DIR.is_dir():
        return
    for path in sorted(_DASHBOARDS_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as handle:
            dashboard = json.load(handle)
        for panel in dashboard.get("panels", []):
            for target in panel.get("targets", []):
                expr = target.get("expr")
                if not expr:
                    continue
                yield (path.stem, panel["id"], target.get("refId", "?"), expr)
