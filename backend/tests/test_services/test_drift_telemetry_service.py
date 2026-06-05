"""DriftTelemetryService unit tests (PR-12).

Drives the service against a FakeDB-backed
`FeatureDriftMetricsRepository` so the wiring matches production
without needing a live Mongo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.constants import FEATURE_DRIFT_METRICS
from app.repositories.feature_drift_metrics_repo import (
    FeatureDriftMetricsRepository,
)
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


def _build(db: FakeDB) -> DriftTelemetryService:
    repo = FeatureDriftMetricsRepository(db)  # type: ignore[arg-type]
    return DriftTelemetryService(repo)


# --- get_status --------------------------------------------------------


async def test_get_status_returns_latest_doc_with_alias() -> None:
    db = FakeDB()
    svc = _build(db)
    coll = db[FEATURE_DRIFT_METRICS]
    await coll.insert_one(
        _drift_doc(
            feature_name="amount_sum_5m",
            measured_at=_NOW - timedelta(hours=2),
            severity="watch",
            ks_statistic=0.12,
        )
    )
    await coll.insert_one(
        _drift_doc(
            feature_name="amount_sum_5m",
            measured_at=_NOW,
            severity="alert",
            ks_statistic=0.55,
            recommended_action="alert_oncall",
            severity_progression=[{"at": _NOW, "from": "watch", "to": "alert"}],
        )
    )

    out = await svc.get_status("amount_sum_5m")

    assert out is not None
    assert out["severity"] == "alert"
    assert out["ks_statistic"] == 0.55
    assert out["recommended_action"] == "alert_oncall"
    # `last_observed_at` is the alias the dashboard prefers; it must
    # mirror the underlying `measured_at` of the latest doc.
    assert out["last_observed_at"] == _NOW
    assert out["severity_progression"] == [
        {"at": _NOW, "from": "watch", "to": "alert"}
    ]


async def test_get_status_missing_returns_none() -> None:
    svc = _build(FakeDB())
    assert await svc.get_status("nope") is None


# --- get_impact --------------------------------------------------------


async def test_get_impact_blast_radius_groups_by_type() -> None:
    db = FakeDB()
    svc = _build(db)
    await db[FEATURE_DRIFT_METRICS].insert_one(
        _drift_doc(
            feature_name="spend_24h_myr",
            severity="warn",
            recommended_action="investigate",
            affected_consumers=[
                {"type": "rule", "name": "fraud_rule_velocity", "last_seen": _NOW},
                {"type": "model", "name": "churn_model_v2", "last_seen": _NOW},
                {"type": "rule", "name": "promo_abuse_rule", "last_seen": None},
            ],
        )
    )

    impact = await svc.get_impact("spend_24h_myr")

    assert impact is not None
    assert impact["recommended_action"] == "investigate"
    assert impact["blast_radius"] == {"rule_count": 2, "model_count": 1}
    names = {c["name"] for c in impact["affected_consumers"]}
    assert names == {"fraud_rule_velocity", "churn_model_v2", "promo_abuse_rule"}


async def test_get_impact_treats_missing_type_as_model() -> None:
    """Bare strings (legacy worker registry shape) count as models."""
    db = FakeDB()
    svc = _build(db)
    await db[FEATURE_DRIFT_METRICS].insert_one(
        _drift_doc(
            feature_name="txn_count_5m",
            affected_consumers=["model_v3_fraud", "fraud_rule_velocity"],
        )
    )

    impact = await svc.get_impact("txn_count_5m")

    # Both entries lack a `type` field → both treated as model.
    assert impact is not None
    assert impact["blast_radius"] == {"rule_count": 0, "model_count": 2}
    assert all(c["type"] == "model" for c in impact["affected_consumers"])


async def test_get_impact_empty_consumers_yields_zero_blast_radius() -> None:
    db = FakeDB()
    svc = _build(db)
    await db[FEATURE_DRIFT_METRICS].insert_one(
        _drift_doc(feature_name="quiet_feat", affected_consumers=[])
    )

    impact = await svc.get_impact("quiet_feat")

    assert impact is not None
    assert impact["affected_consumers"] == []
    assert impact["blast_radius"] == {"rule_count": 0, "model_count": 0}


async def test_get_impact_missing_returns_none() -> None:
    svc = _build(FakeDB())
    assert await svc.get_impact("missing_feat") is None


# --- snapshot ---------------------------------------------------------


async def test_snapshot_passes_through_to_repo() -> None:
    db = FakeDB()
    svc = _build(db)
    await db[FEATURE_DRIFT_METRICS].insert_one(
        _drift_doc(feature_name="a", severity="alert", drift_detected=True)
    )
    await db[FEATURE_DRIFT_METRICS].insert_one(
        _drift_doc(feature_name="b", severity="none", drift_detected=False)
    )

    snap = await svc.snapshot(["a", "b", "missing"])

    assert "a" in snap["by_feature"]
    assert "b" in snap["by_feature"]
    assert "missing" not in snap["by_feature"]
    assert snap["any_drift_detected"] is True


# --- record_action ----------------------------------------------------


async def test_record_action_appends_to_latest_doc() -> None:
    db = FakeDB()
    svc = _build(db)
    coll = db[FEATURE_DRIFT_METRICS]
    older = _drift_doc(feature_name="amount_sum_5m", measured_at=_NOW - timedelta(hours=1))
    newer = _drift_doc(feature_name="amount_sum_5m", measured_at=_NOW)
    await coll.insert_one(older)
    await coll.insert_one(newer)

    out = await svc.record_action(
        "amount_sum_5m",
        action="acknowledge",
        note="seen, monitoring",
        snooze_until=None,
    )

    assert out is not None
    assert out["action"] == "acknowledge"
    assert out["note"] == "seen, monitoring"
    assert isinstance(out["recorded_at"], datetime)

    # The newer doc — not the older one — got the action.
    newer_after = await coll.find_one({"measured_at": _NOW})
    older_after = await coll.find_one({"measured_at": _NOW - timedelta(hours=1)})
    assert newer_after is not None and older_after is not None
    assert len(newer_after.get("analyst_actions") or []) == 1
    assert older_after.get("analyst_actions") in (None, [])


async def test_record_action_slices_to_50_most_recent() -> None:
    db = FakeDB()
    svc = _build(db)
    await db[FEATURE_DRIFT_METRICS].insert_one(
        _drift_doc(feature_name="amount_sum_5m", measured_at=_NOW)
    )

    # Push 60 actions; the $slice -50 must keep only the last 50.
    for i in range(60):
        await svc.record_action(
            "amount_sum_5m", action="acknowledge", note=f"n{i}", snooze_until=None,
        )

    doc = await db[FEATURE_DRIFT_METRICS].find_one({"feature_name": "amount_sum_5m"})
    assert doc is not None
    actions = doc.get("analyst_actions") or []
    assert len(actions) == 50
    # The bottom of the array is the most-recent slice; the first
    # surviving entry should be n10 (we dropped n0..n9).
    assert actions[0]["note"] == "n10"
    assert actions[-1]["note"] == "n59"


async def test_record_action_missing_feature_returns_none() -> None:
    svc = _build(FakeDB())
    out = await svc.record_action(
        "ghost_feat", action="snooze", note=None, snooze_until=_NOW,
    )
    assert out is None
