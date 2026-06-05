"""PR-AG — FeatureDriftMetricsRepository.snapshot_for_features tests.

Drives the read-side repo the agent's analytics node uses to map
{feature_names} → latest drift severity.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.constants import FEATURE_DRIFT_METRICS
from app.repositories.feature_drift_metrics_repo import (
    FeatureDriftMetricsRepository,
)
from tests._fakes import FakeDB

_NOW = datetime.now(timezone.utc)


def _drift(
    *,
    feature_name: str,
    measured_at: datetime | None = None,
    severity: str = "none",
    ks_statistic: float = 0.0,
    drift_detected: bool = False,
    recommended_action: str = "monitor",
) -> dict:
    return {
        "feature_name": feature_name,
        "measured_at": measured_at or _NOW,
        "severity": severity,
        "ks_statistic": ks_statistic,
        "drift_detected": drift_detected,
        "recommended_action": recommended_action,
    }


async def _seed(db, rows: list[dict]) -> None:
    coll = db[FEATURE_DRIFT_METRICS]
    for r in rows:
        await coll.insert_one(r)


async def test_snapshot_returns_latest_per_feature() -> None:
    db = FakeDB()
    repo = FeatureDriftMetricsRepository(db)  # type: ignore[arg-type]
    await _seed(
        db,
        [
            _drift(
                feature_name="avg_amount_30d",
                measured_at=_NOW - timedelta(days=3),
                severity="low",
                ks_statistic=0.05,
            ),
            _drift(
                feature_name="avg_amount_30d",
                measured_at=_NOW - timedelta(days=1),
                severity="high",
                ks_statistic=0.42,
                drift_detected=True,
                recommended_action="alert_oncall",
            ),
            _drift(
                feature_name="avg_amount_30d",
                measured_at=_NOW - timedelta(days=2),
                severity="medium",
                ks_statistic=0.20,
            ),
        ],
    )

    snap = await repo.snapshot_for_features(["avg_amount_30d"])

    feat = snap["by_feature"]["avg_amount_30d"]
    assert feat["severity"] == "high"
    assert feat["ks_statistic"] == 0.42
    assert feat["drift_detected"] is True
    assert feat["recommended_action"] == "alert_oncall"


async def test_snapshot_only_named_features() -> None:
    db = FakeDB()
    repo = FeatureDriftMetricsRepository(db)  # type: ignore[arg-type]
    await _seed(
        db,
        [
            _drift(feature_name=f"feat_{i}", severity="none")
            for i in range(5)
        ],
    )

    snap = await repo.snapshot_for_features(["feat_0", "feat_2"])

    assert set(snap["by_feature"].keys()) == {"feat_0", "feat_2"}


async def test_snapshot_any_drift_detected_true() -> None:
    db = FakeDB()
    repo = FeatureDriftMetricsRepository(db)  # type: ignore[arg-type]
    await _seed(
        db,
        [
            _drift(feature_name="quiet_feat", severity="none"),
            _drift(
                feature_name="loud_feat",
                severity="high",
                drift_detected=True,
            ),
        ],
    )

    snap = await repo.snapshot_for_features(["quiet_feat", "loud_feat"])

    assert snap["any_drift_detected"] is True


async def test_snapshot_any_drift_detected_false() -> None:
    db = FakeDB()
    repo = FeatureDriftMetricsRepository(db)  # type: ignore[arg-type]
    await _seed(
        db,
        [
            _drift(feature_name="a", severity="none"),
            _drift(feature_name="b", severity="none"),
        ],
    )

    snap = await repo.snapshot_for_features(["a", "b"])

    assert snap["any_drift_detected"] is False
    assert all(
        v["severity"] == "none" for v in snap["by_feature"].values()
    )


async def test_snapshot_unknown_feature_omitted() -> None:
    db = FakeDB()
    repo = FeatureDriftMetricsRepository(db)  # type: ignore[arg-type]
    await _seed(db, [_drift(feature_name="known", severity="none")])

    snap = await repo.snapshot_for_features(["known", "missing_feat"])

    assert "known" in snap["by_feature"]
    assert "missing_feat" not in snap["by_feature"]
