"""Feature drift metrics repository.

Backs `feature_drift_metrics` — one document per (feature_name,
measured_at) tick from the drift detector. The agentic AI-assist
(PR-AG) reads the latest tick per feature when a rule cites a
feature, so analysts see whether the rule fired during a known
drift period.

The collection is small and append-mostly; we keep this repo
deliberately thin (snapshot reads only). Writes happen in the
detector job, not through this class.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.constants import FEATURE_DRIFT_METRICS
from app.repositories.base import BaseRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FeatureDriftMetricsRepository(BaseRepository):
    """Read-side repo for the drift detector's per-tick metrics."""

    COLLECTION_NAME = FEATURE_DRIFT_METRICS

    async def snapshot_for_features(self, feature_names: list[str]) -> dict:
        """Latest drift document per requested feature name.

        Returns:
            ``{
                "as_of": datetime,
                "by_feature": {
                    feature_name: {
                        "severity": str,
                        "ks_statistic": float,
                        "drift_detected": bool,
                        "measured_at": datetime,
                        "recommended_action": str,
                    }, ...
                },
                "any_drift_detected": bool,
            }``

        Implementation: per-feature `find_many` + sort + limit-1. We
        deliberately avoid a single ``$group`` pipeline so the FakeDB
        unit tests don't need an aggregation engine; in production this
        is at most ``len(feature_names)`` index hits on
        ``(feature_name, measured_at desc)``.
        """
        names = list(feature_names or [])
        by_feature: dict[str, dict[str, Any]] = {}
        any_drift = False
        for name in names:
            rows = await self.find_many(
                {"feature_name": name},
                sort=[("measured_at", -1)],
                limit=1,
            )
            if not rows:
                continue
            doc = rows[0]
            severity = doc.get("severity") or "none"
            entry = {
                "severity": severity,
                "ks_statistic": float(doc.get("ks_statistic") or 0.0),
                "drift_detected": bool(doc.get("drift_detected", False)),
                "measured_at": doc.get("measured_at"),
                "recommended_action": doc.get("recommended_action") or "monitor",
            }
            by_feature[name] = entry
            if severity != "none" or entry["drift_detected"]:
                any_drift = True
        return {
            "as_of": _utcnow(),
            "by_feature": by_feature,
            "any_drift_detected": any_drift,
        }

    async def record_analyst_action(
        self, feature_name: str, action: dict
    ) -> dict | None:
        """Append `action` onto the latest drift doc's `analyst_actions`.

        Sorts by `measured_at` desc, takes the first doc, and pushes
        with `$slice: -50` to bound array growth. Returns the action
        on success, or None if no drift doc exists for the feature.
        """
        rows = await self.find_many(
            {"feature_name": feature_name},
            sort=[("measured_at", -1)],
            limit=1,
        )
        if not rows:
            return None
        latest = rows[0]
        target_id = latest.get("_id")
        filter_: dict[str, Any]
        if target_id is not None:
            filter_ = {"_id": target_id}
        else:
            # FakeDB tests insert without an `_id`; fall back to the
            # natural key + measured_at composite, which is unique per
            # tick.
            filter_ = {
                "feature_name": feature_name,
                "measured_at": latest.get("measured_at"),
            }
        await self._coll.update_one(
            filter_,
            {
                "$push": {
                    "analyst_actions": {
                        "$each": [action],
                        "$slice": -50,
                    }
                }
            },
        )
        return action
