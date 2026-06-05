"""Atlas Search / Vector Search index health probe.

Returns one entry per index the application cares about so the dashboard
can render a green/yellow/red badge without poking Atlas itself. Uses
the `$listSearchIndexes` idiom from `scripts/setup_indexes.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from app.core.constants import (
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
    IDX_CASE_HISTORY_AUTOEMBED,
    IDX_CUSTOMERS_COMMERCIAL_AUTOEMBED,
    IDX_CUSTOMERS_RESIDENTIAL_AUTOEMBED,
    IDX_CUSTOMERS_SEARCH,
    QUARANTINE_CASES_HISTORY,
)
from app.deps import DBDep

router = APIRouter(prefix="/api/_atlas", tags=["atlas"])


# Canonical list of (collection, expected_index_name, type) that this
# app cares about. Keep small — these are surfaced as individual rows
# in the dashboard badge. Vector indexes are included so the badge can
# never report "READY" while the customer AutoEmbed indexes are still
# building or missing (which would silently kill semantic search).
_INDEXES: tuple[tuple[str, str, str], ...] = (
    (CUSTOMERS_RESIDENTIAL, IDX_CUSTOMERS_SEARCH, "search"),
    (CUSTOMERS_RESIDENTIAL, IDX_CUSTOMERS_RESIDENTIAL_AUTOEMBED, "vectorSearch"),
    (CUSTOMERS_COMMERCIAL,  IDX_CUSTOMERS_COMMERCIAL_AUTOEMBED,  "vectorSearch"),
    (QUARANTINE_CASES_HISTORY, IDX_CASE_HISTORY_AUTOEMBED, "vectorSearch"),
)


@router.get("/index-health")
async def index_health(db: DBDep) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    overall_states: list[str] = []
    for coll, expected, kind in _INDEXES:
        entry: dict[str, Any] = {
            "collection": coll,
            "index_name": expected,
            "state": "MISSING",
            "queryable": False,
            "type": kind,
            "message": None,
        }
        try:
            cursor = await db[coll].aggregate([{"$listSearchIndexes": {}}])
            async for row in cursor:
                if row.get("name") == expected:
                    entry["state"] = row.get("status") or "READY"
                    entry["queryable"] = bool(row.get("queryable", False))
                    break
        except Exception as exc:  # noqa: BLE001
            entry["state"] = "FAILED"
            entry["message"] = str(exc)[:200]
        entries.append(entry)
        overall_states.append(entry["state"])

    if overall_states and all(s == "READY" for s in overall_states):
        overall = "ready"
    elif any(s == "FAILED" for s in overall_states):
        overall = "failed"
    elif any(s in ("BUILDING", "STALE_START", "PENDING") for s in overall_states):
        overall = "syncing"
    else:
        overall = "unknown"

    return {
        "indexes": entries,
        "overall": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
