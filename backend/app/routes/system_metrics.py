"""Read API for the BurstModeTile (Phase B.3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query

from app.core.constants import SYSTEM_METRICS
from app.deps import DBDep

router = APIRouter(prefix="/api/system-metrics", tags=["system-metrics"])


@router.get("")
async def list_recent(
    db: DBDep,
    minutes: int = Query(default=60, ge=1, le=24 * 60),
) -> dict[str, Any]:
    """Most-recent system_metrics samples for the burst tile.

    Default window: 60 minutes — long enough to show a typical
    end-of-month burst (300s) plus shoulder context.
    """
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    cursor = db[SYSTEM_METRICS].find(
        {"recorded_at": {"$gte": since}},
        {"_id": 0},
        sort=[("recorded_at", 1)],
    )
    samples = [s async for s in cursor]
    burst_run_ids = sorted(
        {s["burst_run_id"] for s in samples if s.get("burst_run_id")}
    )
    return {
        "since": since.isoformat(),
        "samples": samples,
        "burst_run_ids": burst_run_ids,
        "current_mode": samples[-1]["mode"] if samples else "idle",
    }
