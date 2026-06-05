"""Tests for `POST /api/features/score/{customer_id}`."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.deps import get_db
from app.main import create_app
from app.services.quarantine_iforest_scorer import QuarantineIforestScorer, get_scorer
from tests._fakes import FakeDB


class _FakeScorer:
    def __init__(self) -> None:
        self.version = "v-test"

    async def score_customer(self, db, customer_id: str) -> dict | None:
        _ = db
        if customer_id == "missing":
            return None
        return {
            "customer_id": customer_id,
            "model_score": 0.42,
            "model_version": self.version,
            "scored_at": datetime.now(UTC).isoformat(),
        }


def _client() -> TestClient:
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]

    def _fake_db():
        return FakeDB()

    def _fake_scorer() -> QuarantineIforestScorer | None:
        return _FakeScorer()  # type: ignore[return-value]

    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_scorer] = _fake_scorer
    return TestClient(app)


def test_score_customer_returns_payload() -> None:
    resp = _client().post("/api/features/score/cust-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["customer_id"] == "cust-1"
    assert body["model_score"] == 0.42
    assert body["model_version"] == "v-test"


def test_score_customer_missing_returns_404() -> None:
    resp = _client().post("/api/features/score/missing")
    assert resp.status_code == 404


def test_score_customer_no_scorer_returns_503() -> None:
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]

    def _fake_db():
        return FakeDB()

    def _none() -> None:
        return None

    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_scorer] = _none
    client = TestClient(app)
    resp = client.post("/api/features/score/cust-1")
    assert resp.status_code == 503
