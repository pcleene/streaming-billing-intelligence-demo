"""DriftTelemetryService.get_history — time-window + projection tests.

Drives the new history method against the FakeDB-backed
`FeatureDriftMetricsRepository` to validate:
- the time window filter excludes docs older than `days`,
- the projection emits `{measured_at, ks_statistic, severity}`,
- an empty result is `{"items": []}` rather than a 404.
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


def _seed(
    db: FakeDB,
    *,
    feature_name: str,
    measured_at: datetime,
    ks_statistic: float = 0.2,
    severity: str = "watch",
) -> None:
    db[FEATURE_DRIFT_METRICS]._docs.append({
        "feature_name": feature_name,
        "measured_at": measured_at,
        "ks_statistic": ks_statistic,
        "severity": severity,
    })


def _build(db: FakeDB) -> DriftTelemetryService:
    return DriftTelemetryService(FeatureDriftMetricsRepository(db))


async def test_get_history_filters_by_window_and_projects_fields() -> None:
    db = FakeDB()
    _seed(db, feature_name="amount_sum_5m", measured_at=_NOW - timedelta(days=1),
          ks_statistic=0.10, severity="watch")
    _seed(db, feature_name="amount_sum_5m", measured_at=_NOW - timedelta(days=5),
          ks_statistic=0.42, severity="warn")
    # Outside the window — should not appear.
    _seed(db, feature_name="amount_sum_5m", measured_at=_NOW - timedelta(days=45),
          ks_statistic=0.99, severity="alert")
    # Different feature — should not appear.
    _seed(db, feature_name="other_feat", measured_at=_NOW, ks_statistic=0.1)

    out = await _build(db).get_history("amount_sum_5m", days=30)
    items = out["items"]
    assert len(items) == 2
    assert {round(p["ks_statistic"], 2) for p in items} == {0.10, 0.42}
    # All entries carry the expected projection keys (and nothing else
    # from the source doc, e.g. no feature_name).
    for p in items:
        assert set(p.keys()) == {"measured_at", "ks_statistic", "severity"}


async def test_get_history_empty_window_returns_empty_items_not_none() -> None:
    db = FakeDB()
    _seed(db, feature_name="quiet_feature", measured_at=_NOW - timedelta(days=200))
    out = await _build(db).get_history("quiet_feature", days=30)
    assert out == {"items": []}


async def test_get_history_unknown_feature_returns_empty_items() -> None:
    db = FakeDB()
    out = await _build(db).get_history("never_recorded", days=30)
    assert out == {"items": []}
