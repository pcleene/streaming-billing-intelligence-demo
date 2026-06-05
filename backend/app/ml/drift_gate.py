"""Block model promotion when drift is `alert` on features this model consumes."""

from __future__ import annotations

from typing import Any

from pymongo import MongoClient

from app.core.constants import FEATURE_DRIFT_METRICS
from app.ml.quarantine_iforest import MODEL_CONSUMER_ID


class DriftGateError(RuntimeError):
    """Raised when `feature_drift_metrics` shows blocking drift."""

    def __init__(self, features: list[str], *, consumer_id: str) -> None:
        self.features = features
        self.consumer_id = consumer_id
        msg = (
            f"Drift gate blocked consumer {consumer_id!r}: "
            f"severity=alert on {features!r}"
        )
        super().__init__(msg)


def _consumer_name(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return str(entry.get("name") or "")
    return None


def blocking_alert_features(
    latest_by_feature: dict[str, dict],
    *,
    consumer_id: str = MODEL_CONSUMER_ID,
) -> list[str]:
    """Given latest drift doc per feature name, return names on alert for this consumer."""
    blocked: list[str] = []
    for fname, doc in latest_by_feature.items():
        if doc.get("severity") != "alert":
            continue
        for raw in doc.get("affected_consumers") or []:
            if _consumer_name(raw) == consumer_id:
                blocked.append(fname)
                break
    return sorted(blocked)


def fetch_latest_drift_by_feature(
    client: MongoClient,
    database: str,
) -> dict[str, dict]:
    coll = client[database][FEATURE_DRIFT_METRICS]
    out: dict[str, dict] = {}
    for row in coll.aggregate(
        [
            {"$sort": {"measured_at": -1}},
            {"$group": {"_id": "$feature_name", "doc": {"$first": "$$ROOT"}}},
        ]
    ):
        out[str(row["_id"])] = row["doc"]
    return out


def assert_no_blocking_drift(
    client: MongoClient,
    database: str,
    *,
    consumer_id: str = MODEL_CONSUMER_ID,
) -> None:
    """Raise `DriftGateError` if any latest drift row is alert for `consumer_id`."""
    latest = fetch_latest_drift_by_feature(client, database)
    bad = blocking_alert_features(latest, consumer_id=consumer_id)
    if bad:
        raise DriftGateError(bad, consumer_id=consumer_id)
