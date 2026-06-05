"""HTTP-level tests for the PR-12 customer refresh + analytics routes.

These tests construct a fresh FastAPI app per test, override the
`get_db` dependency with a `FakeDB`, and exercise the four new
endpoints under `/api/customers`. The lifespan is replaced to avoid
the live Mongo `ping` on startup.

Routes covered:
  - POST /api/customers/{id}/refresh-360 — happy path + 404
  - POST /api/customers/batch-refresh-360 — happy path + 422 cap
  - GET  /api/customers/{id}/transaction-pattern — 200 + 404 + clamping
  - GET  /api/customers/{id}/embedding-status — 200 + 404
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.deps import get_db
from app.main import create_app
from tests._fakes import FakeDB


@pytest.fixture()
def app_with_db():
    db = FakeDB()
    app = create_app()
    # The lifespan opens a live Mongo connection — disable it so the
    # in-memory FakeDB is the only thing the app sees during the test.
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.dependency_overrides[get_db] = lambda: db
    return app, db


def _seed(
    db: FakeDB,
    *,
    customer_id: str,
    customer_type: str = "residential",
    cross_entity_metrics: dict | None = None,
    embedding: list | None = None,
    embedding_generated_at: datetime | None = None,
) -> None:
    coll = db["customers_residential"] if customer_type == "residential" else db["customers_commercial"]
    coll._docs.append(  # type: ignore[attr-defined]
        {
            "customer_id": customer_id,
            "customer_type": customer_type,
            "cross_entity_metrics": cross_entity_metrics or {},
            "embedding": embedding,
            "embedding_generated_at": embedding_generated_at,
            "current_cycle": {},
        }
    )


def _seed_txn(db: FakeDB, *, customer_id: str, **fields) -> None:
    """Insert a transaction directly into the legacy `transactions` collection."""
    base = {
        "customer_id": customer_id,
        "timestamp": datetime.now(timezone.utc),
        "total_myr": 100.0,
        "items": [{"charge_code": "PPV"}],
    }
    base.update(fields)
    db["transactions"]._docs.append(base)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------
# refresh-360
# ---------------------------------------------------------------------


def test_refresh_360_happy_path(app_with_db) -> None:
    app, db = app_with_db
    _seed(db, customer_id="c-1")
    client = TestClient(app)

    resp = client.post("/api/customers/c-1/refresh-360")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["customer_id"] == "c-1"
    assert body["refreshed"] is True
    assert body["skipped_reason"] is None


def test_refresh_360_404_when_missing(app_with_db) -> None:
    app, _ = app_with_db
    client = TestClient(app)

    resp = client.post("/api/customers/c-missing/refresh-360")

    assert resp.status_code == 404


def test_refresh_360_force_query_param_bypasses_cooldown(app_with_db) -> None:
    app, db = app_with_db
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    _seed(
        db,
        customer_id="c-cool",
        cross_entity_metrics={"last_computed_at": recent},
    )
    client = TestClient(app)

    resp_skip = client.post("/api/customers/c-cool/refresh-360")
    assert resp_skip.json()["refreshed"] is False

    resp_force = client.post("/api/customers/c-cool/refresh-360?force=true")
    assert resp_force.json()["refreshed"] is True


# ---------------------------------------------------------------------
# batch-refresh-360
# ---------------------------------------------------------------------


def test_batch_refresh_360_happy_path(app_with_db) -> None:
    app, db = app_with_db
    _seed(db, customer_id="c-1")
    _seed(db, customer_id="c-2")
    client = TestClient(app)

    resp = client.post(
        "/api/customers/batch-refresh-360",
        json={"customer_ids": ["c-1", "c-2"], "force": False},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["requested"] == 2
    assert body["refreshed"] == 2
    assert body["skipped"] == 0
    assert body["errors"] == []


def test_batch_refresh_360_422_when_over_cap(app_with_db) -> None:
    app, _ = app_with_db
    client = TestClient(app)

    # 51 ids — one over the 50-id cap. Pydantic returns 422.
    resp = client.post(
        "/api/customers/batch-refresh-360",
        json={"customer_ids": [f"c-{i}" for i in range(51)]},
    )

    assert resp.status_code == 422


def test_batch_refresh_360_422_when_empty(app_with_db) -> None:
    app, _ = app_with_db
    client = TestClient(app)
    resp = client.post(
        "/api/customers/batch-refresh-360",
        json={"customer_ids": []},
    )
    assert resp.status_code == 422


def test_batch_refresh_360_records_missing_as_errors(app_with_db) -> None:
    app, db = app_with_db
    _seed(db, customer_id="c-1")
    client = TestClient(app)

    resp = client.post(
        "/api/customers/batch-refresh-360",
        json={"customer_ids": ["c-1", "c-missing"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["refreshed"] == 1
    assert body["errors"] == [{"customer_id": "c-missing", "reason": "not_found"}]


# ---------------------------------------------------------------------
# transaction-pattern
# ---------------------------------------------------------------------


def test_transaction_pattern_happy_path(app_with_db) -> None:
    app, db = app_with_db
    _seed(db, customer_id="c-1")
    _seed_txn(db, customer_id="c-1", total_myr=200.0)
    _seed_txn(db, customer_id="c-1", total_myr=100.0)
    client = TestClient(app)

    resp = client.get("/api/customers/c-1/transaction-pattern?days=30")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["customer_id"] == "c-1"
    assert body["window_days"] == 30
    assert body["txn_count"] == 2


def test_transaction_pattern_404_when_customer_missing(app_with_db) -> None:
    app, _ = app_with_db
    client = TestClient(app)

    resp = client.get("/api/customers/c-missing/transaction-pattern")

    assert resp.status_code == 404


def test_transaction_pattern_rejects_out_of_range_days(app_with_db) -> None:
    app, db = app_with_db
    _seed(db, customer_id="c-1")
    client = TestClient(app)

    # `Query(ge=1, le=365)` rejects 0 / 1000 at the route layer.
    assert client.get("/api/customers/c-1/transaction-pattern?days=0").status_code == 422
    assert client.get("/api/customers/c-1/transaction-pattern?days=1000").status_code == 422


# ---------------------------------------------------------------------
# embedding-status
# ---------------------------------------------------------------------


def test_embedding_status_fresh(app_with_db) -> None:
    app, db = app_with_db
    fresh = datetime.now(timezone.utc) - timedelta(hours=3)
    _seed(
        db,
        customer_id="c-emb",
        embedding=[0.1] * 1024,
        embedding_generated_at=fresh,
    )
    client = TestClient(app)

    resp = client.get("/api/customers/c-emb/embedding-status")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["customer_id"] == "c-emb"
    assert body["has_embedding"] is True
    assert body["dim"] == 1024
    assert body["is_stale"] is False
    # The raw embedding vector must NEVER appear here.
    assert "embedding" not in body


def test_embedding_status_404_when_missing(app_with_db) -> None:
    app, _ = app_with_db
    client = TestClient(app)

    resp = client.get("/api/customers/c-missing/embedding-status")

    assert resp.status_code == 404


def test_embedding_status_no_embedding_marks_stale(app_with_db) -> None:
    app, db = app_with_db
    _seed(db, customer_id="c-noemb")
    client = TestClient(app)

    resp = client.get("/api/customers/c-noemb/embedding-status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["has_embedding"] is False
    assert body["dim"] is None
    assert body["is_stale"] is True
