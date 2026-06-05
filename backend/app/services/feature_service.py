"""Feature store online serving + freshness.

The features collection is written by the feature_engineer worker (rolling
counters from change-stream tail) AND by Spark Structured Streaming for the
heavier 30/90-day rollups. This service is the read path for the UI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.repositories.feature_repo import FeatureRepository

logger = get_logger(__name__)


class FeatureService:
    def __init__(self, feature_repo: FeatureRepository) -> None:
        self._features = feature_repo

    async def get_features(self, customer_id: str) -> dict | None:
        return await self._features.get(customer_id)

    async def freshness_snapshot(self, *, sample: int = 200) -> dict[str, Any]:
        rows = await self._features.list_freshness(limit=sample)
        if not rows:
            return {"sampled": 0, "median_lag_seconds": None, "p95_lag_seconds": None, "max_lag_seconds": None}
        now = datetime.now(timezone.utc)
        lags: list[float] = []
        for r in rows:
            ts = r.get("updated_at")
            if isinstance(ts, datetime):
                lags.append(max(0.0, (now - ts).total_seconds()))
        if not lags:
            return {"sampled": len(rows), "median_lag_seconds": None, "p95_lag_seconds": None, "max_lag_seconds": None}
        lags.sort()
        n = len(lags)
        # Median: average the two middle samples for even n.
        if n % 2 == 1:
            median = lags[n // 2]
        else:
            median = 0.5 * (lags[n // 2 - 1] + lags[n // 2])
        # p95 via nearest-rank: index ceil(0.95 * n) - 1, clamped.
        from math import ceil
        p95_idx = max(0, min(n - 1, ceil(0.95 * n) - 1))
        p95 = lags[p95_idx]
        return {
            "sampled": n,
            "median_lag_seconds": round(median, 2),
            "p95_lag_seconds": round(p95, 2),
            "max_lag_seconds": round(lags[-1], 2),
            "fresh_threshold_seconds": 60,
            "fresh_share": round(sum(1 for l in lags if l <= 60) / n, 3),
        }

    @staticmethod
    def is_stale(updated_at: datetime | None, *, threshold: timedelta = timedelta(minutes=2)) -> bool:
        if updated_at is None:
            return True
        return (datetime.now(timezone.utc) - updated_at) > threshold
