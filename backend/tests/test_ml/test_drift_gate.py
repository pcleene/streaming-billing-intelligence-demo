"""Unit tests for `app.ml.drift_gate` (promotion gate)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.ml.drift_gate import (
    DriftGateError,
    blocking_alert_features,
    fetch_latest_drift_by_feature,
)
from app.ml.quarantine_iforest import MODEL_CONSUMER_ID


def test_blocking_alert_features_empty_when_none() -> None:
    latest = {
        "spend_24h_myr": {
            "feature_name": "spend_24h_myr",
            "severity": "none",
            "affected_consumers": ["quarantine_iforest"],
        },
    }
    assert blocking_alert_features(latest) == []


def test_blocking_alert_features_detects_consumer_on_alert() -> None:
    latest = {
        "spend_24h_myr": {
            "severity": "alert",
            "affected_consumers": ["churn_model_v2", "quarantine_iforest"],
        },
    }
    assert blocking_alert_features(latest) == ["spend_24h_myr"]


def test_blocking_alert_ignores_alert_without_consumer() -> None:
    latest = {
        "discount_sum_5m": {
            "severity": "alert",
            "affected_consumers": ["promo_abuse_rule"],
        },
    }
    assert blocking_alert_features(latest, consumer_id=MODEL_CONSUMER_ID) == []


def test_blocking_alert_dict_shaped_consumer() -> None:
    latest = {
        "txn_count_24h": {
            "severity": "alert",
            "affected_consumers": [
                {"type": "model", "name": "quarantine_iforest", "last_seen": "2026-05-07"},
            ],
        },
    }
    assert blocking_alert_features(latest) == ["txn_count_24h"]


def test_fetch_latest_drift_by_feature_uses_aggregate() -> None:
    """Smoke: one doc per feature_name (latest measured_at)."""
    calls: list = []

    class FakeCursor:
        def __init__(self, items: list) -> None:
            self._items = items

        def __iter__(self) -> FakeCursor:
            return self

        def __next__(self) -> dict:
            if not self._items:
                raise StopIteration
            return self._items.pop(0)

    class FakeColl:
        def aggregate(self, pipeline: list) -> FakeCursor:  # noqa: ANN001
            calls.append(pipeline)
            return FakeCursor(
                [
                    {"_id": "a", "doc": {"feature_name": "a", "measured_at": datetime.now(UTC)}},
                ]
            )

    class FakeDB:
        def __getitem__(self, name: str) -> FakeColl:  # noqa: ANN001
            _ = name
            return FakeColl()

    class FakeClient:
        def __getitem__(self, name: str) -> FakeDB:  # noqa: ANN001
            return FakeDB()

    out = fetch_latest_drift_by_feature(FakeClient(), "streaming_billing")  # type: ignore[arg-type]
    assert "a" in out
    assert calls and "$group" in str(calls[0])


def test_drift_gate_error_message_lists_features() -> None:
    err = DriftGateError(["spend_24h_myr"], consumer_id="quarantine_iforest")
    assert "spend_24h_myr" in str(err)
