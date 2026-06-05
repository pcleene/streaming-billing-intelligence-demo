"""DriftTelemetryService — wraps FeatureDriftMetricsRepository for the API.

PR-12: surfaces the latest drift state for a feature, plus a derived
"impact" view (blast_radius rolled up from `affected_consumers`), a
multi-feature snapshot passthrough, and an analyst action recorder
that appends onto the latest drift document for a feature.

The repo holds one document per (feature_name, measured_at) tick. We
read the most-recent doc by sorting `measured_at desc` and limiting to
1, mirroring how `snapshot_for_features` already navigates the
collection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.repositories.feature_drift_metrics_repo import (
    FeatureDriftMetricsRepository,
)

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _strip_id(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    out = dict(doc)
    out.pop("_id", None)
    return out


class DriftTelemetryService:
    """Read + minor write surface for feature drift telemetry.

    Construction takes the repo by injection so route-level wiring can
    pass either the real `FeatureDriftMetricsRepository` or a fake
    backed by `tests._fakes.FakeDB`.
    """

    def __init__(self, drift_repo: FeatureDriftMetricsRepository) -> None:
        self._repo = drift_repo

    async def _latest(self, feature_name: str) -> dict | None:
        rows = await self._repo.find_many(
            {"feature_name": feature_name},
            sort=[("measured_at", -1)],
            limit=1,
        )
        if not rows:
            return None
        return _strip_id(rows[0])

    async def get_status(self, feature_name: str) -> dict | None:
        """Latest drift document for `feature_name`, enriched.

        Returns None when no doc exists so the route can map to 404.
        """
        doc = await self._latest(feature_name)
        if doc is None:
            return None
        # The doc shape from `_build_drift_doc` already carries the
        # severity, ks_statistic, drift_detected, recommended_action,
        # and severity_progression fields. We surface a stable
        # last_observed_at alias for the front-end.
        out = dict(doc)
        out["last_observed_at"] = doc.get("measured_at")
        return out

    async def get_impact(self, feature_name: str) -> dict | None:
        """Derive `blast_radius` from `affected_consumers`.

        Counts consumers grouped by `type` (rule|model). Entries with
        no `type` field are treated as model (matches the worker's
        FEATURE_CONSUMERS shape, which historically lists raw model
        names without an explicit type tag).
        """
        doc = await self._latest(feature_name)
        if doc is None:
            return None
        consumers_raw = list(doc.get("affected_consumers") or [])
        affected: list[dict[str, Any]] = []
        rule_count = 0
        model_count = 0
        for entry in consumers_raw:
            if isinstance(entry, dict):
                ctype = entry.get("type") or "model"
                affected.append(
                    {
                        "type": ctype,
                        "name": entry.get("name"),
                        "last_seen": entry.get("last_seen"),
                    }
                )
            else:
                # Bare string — legacy shape from the worker registry;
                # treat as model per the spec.
                ctype = "model"
                affected.append(
                    {"type": ctype, "name": entry, "last_seen": None}
                )
            if ctype == "rule":
                rule_count += 1
            else:
                model_count += 1
        return {
            "feature_name": feature_name,
            "drift": doc,
            "affected_consumers": affected,
            "recommended_action": doc.get("recommended_action") or "monitor",
            "blast_radius": {
                "rule_count": rule_count,
                "model_count": model_count,
            },
        }

    async def snapshot(self, feature_names: list[str]) -> dict:
        """Passthrough to `FeatureDriftMetricsRepository.snapshot_for_features`."""
        return await self._repo.snapshot_for_features(feature_names)

    async def get_history(
        self, feature_name: str, *, days: int
    ) -> dict:
        """Time series of (measured_at, ks_statistic, severity) for a feature.

        Sorted ascending so the chart renders left-to-right oldest→newest.
        Always returns a `{"items": [...]}` envelope — an empty window
        yields `{"items": []}` (200) rather than 404 so the chart can
        render a "no measurements in the last N days" state.
        """
        since = _utcnow() - timedelta(days=days)
        rows = await self._repo.find_many(
            {
                "feature_name": feature_name,
                "measured_at": {"$gte": since},
            },
            sort=[("measured_at", 1)],
        )
        items = [
            {
                "measured_at": r.get("measured_at"),
                "ks_statistic": float(r.get("ks_statistic") or 0.0),
                "severity": r.get("severity") or "none",
            }
            for r in rows
        ]
        return {"items": items}

    async def record_action(
        self,
        feature_name: str,
        *,
        action: str,
        note: str | None,
        snooze_until: datetime | None,
    ) -> dict | None:
        """Append an analyst action to the latest drift doc.

        Returns the appended action (with `recorded_at`), or None when
        no drift doc exists for the feature so the route can 404.
        """
        recorded = {
            "action": action,
            "note": note,
            "snooze_until": snooze_until,
            "recorded_at": _utcnow(),
        }
        applied = await self._repo.record_analyst_action(feature_name, recorded)
        if applied is None:
            return None
        return recorded
