"""Tests for ``/api/scoring/by-signal/{signal_id}`` read endpoint (T-403).

Mocks at the router import boundary
(``services.analytics_api.app.routers.scoring``) per WG#10 + T-401a/b/T-402
precedent. Pin: empty list → 200 (NOT 404; collection-shape endpoint),
DOUBLE PRECISION → float (WG#7), JSONB rule_results + feature_snapshot
passthrough (WG#8), decision StrEnum serialization (WG#12).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

from packages.core.types import ScoringDecision
from packages.db.queries.analytics import ScoringEvaluationRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_EVAL = datetime(2026, 5, 1, 10, 0, 1, tzinfo=UTC)


def _make_evaluation(
    *,
    eval_id: int = 1,
    bot_id: str = "alpha",
    signal_id: int = 1,
    decision: ScoringDecision = ScoringDecision.EXECUTE,
    rule_results: list[dict[str, Any]] | None = None,
    feature_snapshot: dict[str, Any] | None = None,
) -> ScoringEvaluationRow:
    return ScoringEvaluationRow(
        id=eval_id,
        bot_id=bot_id,
        signal_id=signal_id,
        evaluated_at=_T_EVAL,
        trigger_threshold=1.0,
        total_score=1.5,
        decision=decision,
        config_version=1,
        rule_results=rule_results
        if rule_results is not None
        else [
            {
                "name": "r1",
                "weight": 1.0,
                "applied_weight": 1.0,
                "result": "True",
                "error": None,
            },
        ],
        feature_snapshot=feature_snapshot
        if feature_snapshot is not None
        else {"ind.btcusdt.15m.ema_20": "50000"},
        correlation_id=f"cid-{signal_id}",
    )


def test_list_evaluations_by_signal_returns_200_with_typed_list(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.scoring.select_scoring_evaluations_by_signal_id",
        AsyncMock(
            return_value=[
                _make_evaluation(eval_id=1, bot_id="alpha", decision=ScoringDecision.EXECUTE),
                _make_evaluation(
                    eval_id=2,
                    bot_id="beta",
                    decision=ScoringDecision.PASSTHROUGH,
                ),
            ]
        ),
    )
    response = client.get("/api/scoring/by-signal/1")
    assert response.status_code == 200
    body = response.json()
    assert len(body["evaluations"]) == 2
    assert body["evaluations"][0]["bot_id"] == "alpha"
    assert body["evaluations"][1]["bot_id"] == "beta"


def test_list_evaluations_by_signal_returns_200_with_empty_list_when_no_evaluations(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty list → 200 (NOT 404). Signal may exist with 0 evaluations
    (received before any bot was active, or rejected at ingestion).
    Distinct from `/api/signals/{id}` 404 entity-not-found semantic.
    """
    monkeypatch.setattr(
        "services.analytics_api.app.routers.scoring.select_scoring_evaluations_by_signal_id",
        AsyncMock(return_value=[]),
    )
    response = client.get("/api/scoring/by-signal/999")
    assert response.status_code == 200
    assert response.json() == {"evaluations": []}


def test_evaluation_decision_serializes_as_string_value(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#12 — `use_enum_values=True` → decision rendered as lowercase string."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.scoring.select_scoring_evaluations_by_signal_id",
        AsyncMock(
            return_value=[
                _make_evaluation(decision=ScoringDecision.EXECUTE),
                _make_evaluation(decision=ScoringDecision.REJECT),
                _make_evaluation(decision=ScoringDecision.PASSTHROUGH),
            ]
        ),
    )
    body = client.get("/api/scoring/by-signal/1").json()
    decisions = [e["decision"] for e in body["evaluations"]]
    assert decisions == ["execute", "reject", "passthrough"]


def test_evaluation_double_precision_fields_serialize_as_float(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#7 — DOUBLE PRECISION columns → JSON numbers (floats), NOT strings."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.scoring.select_scoring_evaluations_by_signal_id",
        AsyncMock(return_value=[_make_evaluation()]),
    )
    body = client.get("/api/scoring/by-signal/1").json()
    e = body["evaluations"][0]
    assert isinstance(e["trigger_threshold"], float)
    assert isinstance(e["total_score"], float)
    assert e["trigger_threshold"] == 1.0
    assert e["total_score"] == 1.5


def test_evaluation_rule_results_jsonb_passthrough_returns_list_of_dicts(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#8 — rule_results JSONB array renders as JSON list of objects."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.scoring.select_scoring_evaluations_by_signal_id",
        AsyncMock(
            return_value=[
                _make_evaluation(
                    rule_results=[
                        {
                            "name": "r1",
                            "weight": 1.0,
                            "applied_weight": 1.0,
                            "result": "True",
                            "error": None,
                        },
                        {
                            "name": "r2",
                            "weight": 0.5,
                            "applied_weight": 0.0,
                            "result": "False",
                            "error": None,
                        },
                    ],
                ),
            ]
        ),
    )
    body = client.get("/api/scoring/by-signal/1").json()
    rule_results = body["evaluations"][0]["rule_results"]
    assert isinstance(rule_results, list)
    assert len(rule_results) == 2
    assert all(isinstance(r, dict) for r in rule_results)
    assert rule_results[0]["name"] == "r1"


def test_evaluation_feature_snapshot_jsonb_passthrough_returns_dict(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#8 — feature_snapshot JSONB object renders as JSON dict."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.scoring.select_scoring_evaluations_by_signal_id",
        AsyncMock(
            return_value=[
                _make_evaluation(
                    feature_snapshot={
                        "ind.btcusdt.15m.ema_20": "50000",
                        "ind.btcusdt.15m.rsi_14": "65.5",
                    },
                ),
            ]
        ),
    )
    body = client.get("/api/scoring/by-signal/1").json()
    snap = body["evaluations"][0]["feature_snapshot"]
    assert isinstance(snap, dict)
    assert snap == {
        "ind.btcusdt.15m.ema_20": "50000",
        "ind.btcusdt.15m.rsi_14": "65.5",
    }
