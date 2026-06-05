"""Feature drift telemetry routes (PR-12).

Mounts on the same `/api/features` prefix as `routes/features.py` but
at distinct sub-paths (`/{feature_name}/drift-status`,
`/{feature_name}/impact-analysis`, `/drift-snapshot`,
`/{feature_name}/investigate-action`). FastAPI's path matcher routes
literal segments (`drift-snapshot`) ahead of single-param patterns
(`{customer_id}`) only when this router is included before the
features router; `app.main` includes it that way.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.deps import DBDep
from app.repositories.feature_drift_metrics_repo import (
    FeatureDriftMetricsRepository,
)
from app.services.drift_telemetry_service import DriftTelemetryService

router = APIRouter(prefix="/api/features", tags=["features-drift"])


# Cap multi-feature snapshot calls so we don't fan out unbounded reads.
SNAPSHOT_NAMES_CAP = 20

# Allowed analyst-action verbs. Mirrors the spec; extending the set
# means adding to this tuple AND the dashboard's action picker.
_VALID_ACTIONS = ("acknowledge", "snooze", "escalate")


class InvestigateActionPayload(BaseModel):
    action: str = Field(..., description="One of acknowledge|snooze|escalate")
    note: str | None = None
    snooze_until: datetime | None = None


def _service(db: DBDep) -> DriftTelemetryService:
    return DriftTelemetryService(FeatureDriftMetricsRepository(db))


@router.get("/drift-snapshot")
async def drift_snapshot(
    names: Annotated[str, Query(description="Comma-separated feature names")],
    svc: DriftTelemetryService = Depends(_service),
) -> dict:
    name_list = [n.strip() for n in names.split(",") if n.strip()]
    if len(name_list) > SNAPSHOT_NAMES_CAP:
        raise HTTPException(
            status_code=422,
            detail=f"too many feature names (max {SNAPSHOT_NAMES_CAP})",
        )
    return await svc.snapshot(name_list)


@router.get("/{feature_name}/drift-status")
async def drift_status(
    feature_name: str,
    svc: DriftTelemetryService = Depends(_service),
) -> dict:
    doc = await svc.get_status(feature_name)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"no drift docs for feature {feature_name!r}",
        )
    return doc


@router.get("/{feature_name}/history")
async def drift_history(
    feature_name: str,
    days: int = Query(30, ge=1, le=90),
    svc: DriftTelemetryService = Depends(_service),
) -> dict:
    """Time-series of recent drift measurements for a feature.

    Returns 200 with `{"items": []}` for an empty time window so the
    UI can render an empty state without an error toast.
    """
    return await svc.get_history(feature_name, days=days)


@router.get("/{feature_name}/impact-analysis")
async def impact_analysis(
    feature_name: str,
    svc: DriftTelemetryService = Depends(_service),
) -> dict:
    impact = await svc.get_impact(feature_name)
    if impact is None:
        raise HTTPException(
            status_code=404,
            detail=f"no drift docs for feature {feature_name!r}",
        )
    return impact


@router.post("/{feature_name}/investigate-action")
async def investigate_action(
    feature_name: str,
    payload: InvestigateActionPayload,
    svc: DriftTelemetryService = Depends(_service),
) -> dict:
    if payload.action not in _VALID_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"action must be one of {_VALID_ACTIONS}",
        )
    recorded = await svc.record_action(
        feature_name,
        action=payload.action,
        note=payload.note,
        snooze_until=payload.snooze_until,
    )
    if recorded is None:
        raise HTTPException(
            status_code=404,
            detail=f"no drift docs for feature {feature_name!r}",
        )
    return recorded
