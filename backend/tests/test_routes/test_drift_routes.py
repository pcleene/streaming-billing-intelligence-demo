"""Drift telemetry route tests (PR-12).

We use FastAPI's `dependency_overrides` to swap the route's `_service`
factory for one backed by `tests._fakes.FakeDB`, so the request lifecycle
runs without touching real Mongo (and without invoking the lifespan that
would).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.constants import FEATURE_DRIFT_METRICS
from app.main import create_app
from app.repositories.feature_drift_metrics_repo import (
    FeatureDriftMetricsRepository,
)
from app.routes import drift as drift_routes
from app.services.drift_telemetry_service import DriftTelemetryService
from tests._fakes import FakeDB

_NOW = datetime.now(timezone.utc)


def _drift_doc(
    *,
    feature_name: str,
    measured_at: datetime | None = None,
    severity: str = "warn",
    ks_statistic: float = 0.25,
    drift_detected: bool = True,
    recommended_action: str = "investigate",
    affected_consumers: list | None = None,
    severity_progression: list[dict] | None = None,
) -> dict:
    return {
        "feature_name": feature_name,
        "measured_at": measured_at or _NOW,
        "severity": severity,
        "ks_statistic": ks_statistic,
        "drift_detected": drift_detected,
        "recommended_action": recommended_action,
        "affected_consumers": affected_consumers or [],
        "severity_progression": severity_progression or [],
    }


def _client(db: FakeDB) -> TestClient:
    app = create_app()
    # Skip the lifespan — it would try to connect to a real Mongo
    # cluster (see app.main.lifespan); we stub the DB via the
    # dependency override instead.
    app.router.lifespan_context = None  # type: ignore[assignment]

    def _override_service() -> DriftTelemetryService:
        return DriftTelemetryService(
            FeatureDriftMetricsRepository(db),  # type: ignore[arg-type]
        )

    app.dependency_overrides[drift_routes._service] = _override_service
    return TestClient(app)


# --- /drift-status ----------------------------------------------------


def test_drift_status_happy_path() -> None:
    db = FakeDB()
    import asyncio

    asyncio.run(
        db[FEATURE_DRIFT_METRICS].insert_one(
            _drift_doc(
                feature_name="amount_sum_5m",
                severity="alert",
                ks_statistic=0.55,
                recommended_action="alert_oncall",
            )
        )
    )

    resp = _client(db).get("/api/features/amount_sum_5m/drift-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["feature_name"] == "amount_sum_5m"
    assert body["severity"] == "alert"
    assert body["recommended_action"] == "alert_oncall"
    assert "last_observed_at" in body


def test_drift_status_missing_returns_404() -> None:
    resp = _client(FakeDB()).get("/api/features/ghost_feat/drift-status")
    assert resp.status_code == 404


# --- /impact-analysis -------------------------------------------------


def test_impact_analysis_happy_path() -> None:
    db = FakeDB()
    import asyncio

    asyncio.run(
        db[FEATURE_DRIFT_METRICS].insert_one(
            _drift_doc(
                feature_name="spend_24h_myr",
                severity="warn",
                affected_consumers=[
                    {"type": "rule", "name": "fraud_rule_velocity"},
                    {"type": "model", "name": "churn_model_v2"},
                ],
            )
        )
    )

    resp = _client(db).get("/api/features/spend_24h_myr/impact-analysis")
    assert resp.status_code == 200
    body = resp.json()
    assert body["feature_name"] == "spend_24h_myr"
    assert body["blast_radius"] == {"rule_count": 1, "model_count": 1}
    names = {c["name"] for c in body["affected_consumers"]}
    assert names == {"fraud_rule_velocity", "churn_model_v2"}


def test_impact_analysis_missing_returns_404() -> None:
    resp = _client(FakeDB()).get("/api/features/ghost_feat/impact-analysis")
    assert resp.status_code == 404


# --- /drift-snapshot --------------------------------------------------


def test_drift_snapshot_happy_path() -> None:
    db = FakeDB()
    import asyncio

    asyncio.run(
        db[FEATURE_DRIFT_METRICS].insert_one(
            _drift_doc(feature_name="a", severity="alert", drift_detected=True)
        )
    )
    asyncio.run(
        db[FEATURE_DRIFT_METRICS].insert_one(
            _drift_doc(feature_name="b", severity="none", drift_detected=False)
        )
    )

    resp = _client(db).get("/api/features/drift-snapshot?names=a,b,missing")
    assert resp.status_code == 200
    body = resp.json()
    assert "a" in body["by_feature"]
    assert "b" in body["by_feature"]
    assert "missing" not in body["by_feature"]
    assert body["any_drift_detected"] is True


def test_drift_snapshot_too_many_names_422() -> None:
    names = ",".join(f"f{i}" for i in range(21))
    resp = _client(FakeDB()).get(f"/api/features/drift-snapshot?names={names}")
    assert resp.status_code == 422


# --- /investigate-action ----------------------------------------------


def test_investigate_action_records_and_returns() -> None:
    db = FakeDB()
    import asyncio

    asyncio.run(
        db[FEATURE_DRIFT_METRICS].insert_one(
            _drift_doc(feature_name="amount_sum_5m", measured_at=_NOW)
        )
    )

    resp = _client(db).post(
        "/api/features/amount_sum_5m/investigate-action",
        json={
            "action": "snooze",
            "note": "watching post-deploy",
            "snooze_until": (_NOW + timedelta(hours=4)).isoformat(),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "snooze"
    assert body["note"] == "watching post-deploy"
    assert "recorded_at" in body


def test_investigate_action_unknown_feature_returns_404() -> None:
    resp = _client(FakeDB()).post(
        "/api/features/ghost_feat/investigate-action",
        json={"action": "acknowledge", "note": None, "snooze_until": None},
    )
    assert resp.status_code == 404


def test_investigate_action_invalid_action_returns_422() -> None:
    db = FakeDB()
    import asyncio

    asyncio.run(
        db[FEATURE_DRIFT_METRICS].insert_one(
            _drift_doc(feature_name="amount_sum_5m")
        )
    )

    resp = _client(db).post(
        "/api/features/amount_sum_5m/investigate-action",
        json={"action": "bogus", "note": None, "snooze_until": None},
    )
    assert resp.status_code == 422
