"""Route tests for `GET /api/quarantine/cases/{case_id}/related-customer`.

Overrides the FastAPI `get_db` dep with a `FakeDB` so we exercise the
route handler + repo composition without touching real Mongo.

Validates:
- the response shape `{open: [...], history: [...]}`
- the current case is excluded from both sides
- only same-customer rows are returned
- only open + under_review statuses appear in `open`
- the projection emits `case_id`, `rule_name`/`rule_type`, `severity`,
  `status`, `disposition`, `created_at` / `resolved_at`
- 404 when the case_id is unknown
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.constants import QUARANTINE_CASES, QUARANTINE_CASES_HISTORY
from app.deps import get_db
from app.main import create_app
from tests._fakes import FakeDB


_NOW = datetime.now(timezone.utc)


def _seed_open(db: FakeDB, **fields) -> None:
    base = {
        "severity": "high",
        "status": "open",
        "rules_triggered": [
            {"rule_type": "amount_outlier", "rule_name": "Amount > 10x avg"}
        ],
        "created_at": _NOW,
    }
    base.update(fields)
    db[QUARANTINE_CASES]._docs.append(base)


def _seed_history(db: FakeDB, **fields) -> None:
    base = {
        "severity": "medium",
        "status": "resolved",
        "rules_triggered": [
            {"rule_type": "velocity_anomaly", "rule_name": "Velocity spike"}
        ],
        "disposition": "true_positive",
        "resolved_at": _NOW - timedelta(days=2),
    }
    base.update(fields)
    db[QUARANTINE_CASES_HISTORY]._docs.append(base)


def _client(db: FakeDB) -> TestClient:
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_related_customer_returns_same_customer_open_and_history() -> None:
    db = FakeDB()
    # The current case under view.
    _seed_open(db, case_id="case-1", customer_id="cust-A")
    # Other open cases for the same customer — should appear.
    _seed_open(db, case_id="case-2", customer_id="cust-A", status="open")
    _seed_open(db, case_id="case-3", customer_id="cust-A", status="under_review")
    # Resolved/terminal case in quarantine_cases (not history) → not in open.
    _seed_open(db, case_id="case-4", customer_id="cust-A", status="resolved")
    # Different customer — should NOT appear.
    _seed_open(db, case_id="case-other", customer_id="cust-B")
    # Resolved history for the same customer — should appear in history.
    _seed_history(db, case_id="hist-1", customer_id="cust-A")
    _seed_history(
        db, case_id="hist-2", customer_id="cust-A",
        resolved_at=_NOW - timedelta(days=10),
    )
    # Different customer in history — should NOT appear.
    _seed_history(db, case_id="hist-other", customer_id="cust-B")

    resp = _client(db).get("/api/quarantine/cases/case-1/related-customer")
    assert resp.status_code == 200
    body = resp.json()

    open_ids = {r["case_id"] for r in body["open"]}
    assert open_ids == {"case-2", "case-3"}

    history_ids = {r["case_id"] for r in body["history"]}
    assert history_ids == {"hist-1", "hist-2"}

    # Projection: each open row carries the headline rule + severity.
    row = next(r for r in body["open"] if r["case_id"] == "case-2")
    assert row["rule_type"] == "amount_outlier"
    assert row["rule_name"] == "Amount > 10x avg"
    assert row["severity"] == "high"
    assert row["status"] == "open"
    assert "created_at" in row

    # History row carries disposition + resolved_at.
    hrow = next(r for r in body["history"] if r["case_id"] == "hist-1")
    assert hrow["disposition"] == "true_positive"
    assert "resolved_at" in hrow


def test_related_customer_excludes_current_case_when_same_customer() -> None:
    db = FakeDB()
    _seed_open(db, case_id="case-1", customer_id="cust-A")
    resp = _client(db).get("/api/quarantine/cases/case-1/related-customer")
    assert resp.status_code == 200
    body = resp.json()
    # Only case-1 exists, and it's excluded.
    assert body == {"open": [], "history": []}


def test_related_customer_404_when_case_unknown() -> None:
    db = FakeDB()
    resp = _client(db).get("/api/quarantine/cases/ghost/related-customer")
    assert resp.status_code == 404
