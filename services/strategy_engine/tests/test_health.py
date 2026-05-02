"""Tests for ``GET /health`` liveness."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def test_health_returns_200_empty_body(client: TestClient) -> None:
    """Liveness is unconditional while the process runs."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {}
