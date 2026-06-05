"""Feature store read API + freshness + drift."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.constants import FEATURE_DRIFT_METRICS
from app.deps import DBDep
from app.repositories.feature_repo import FeatureRepository
from app.services.feature_service import FeatureService
from app.services.quarantine_iforest_scorer import QuarantineIforestScorer, get_scorer

router = APIRouter(prefix="/api/features", tags=["features"])


def _service(db: DBDep) -> FeatureService:
    return FeatureService(FeatureRepository(db))


@router.get("/freshness")
async def freshness(svc: FeatureService = Depends(_service)) -> dict:
    return await svc.freshness_snapshot()


@router.get("/drift")
async def drift(
    db: DBDep,
    top: int = Query(default=10, ge=1, le=50),
) -> dict:
    """Top-N drifting features ordered by ks_statistic desc.

    Reads the most-recent doc per feature_name (a `$group` over
    feature_drift_metrics with `$last`) so the dashboard always gets a
    stable per-feature row count regardless of detector cadence.
    """
    pipeline = [
        {"$sort": {"measured_at": 1}},
        {
            "$group": {
                "_id": "$feature_name",
                "feature_name": {"$last": "$feature_name"},
                "measured_at": {"$last": "$measured_at"},
                "severity": {"$last": "$severity"},
                "ks_statistic": {"$last": "$ks_statistic"},
                "p_value": {"$last": "$p_value"},
                "current": {"$last": "$current"},
                "baseline": {"$last": "$baseline"},
            }
        },
        {"$sort": {"ks_statistic": -1}},
        {"$limit": top},
        {"$project": {"_id": 0}},
    ]
    cursor = await db[FEATURE_DRIFT_METRICS].aggregate(pipeline)
    items = [d async for d in cursor]
    return {"items": items, "count": len(items)}


@router.post("/score/{customer_id}")
async def score_customer_with_iforest(
    customer_id: str,
    db: DBDep,
    scorer: Annotated[QuarantineIforestScorer | None, Depends(get_scorer)],
) -> dict:
    """On-demand IsolationForest score for one customer (writes `model_score`)."""
    if scorer is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Quarantine IsolationForest is not configured "
                "(enable QUARANTINE_IFOREST_ENABLED and set MODEL_PATH or MLFLOW_RUN_ID)"
            ),
        )
    result = await scorer.score_customer(db, customer_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no features for {customer_id}")
    return result


@router.get("/{customer_id}")
async def get_features(
    customer_id: str, svc: FeatureService = Depends(_service)
) -> dict:
    feat = await svc.get_features(customer_id)
    if not feat:
        raise HTTPException(status_code=404, detail=f"no features for {customer_id}")
    return feat
