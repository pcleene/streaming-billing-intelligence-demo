"""Read-only aggregations powering the dashboard tiles.

Each handler is a thin `DBDep`-backed aggregation that returns a plain
`dict` matching the TS contracts in `frontend/src/lib/api.ts`. No new
repositories/services — these are pure read paths and the frontend
already renders graceful empty states, so every handler wraps its main
aggregation in a try/except that falls back to an empty-but-well-shaped
response on failure rather than 500'ing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query

from app.core.constants import (
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
    FEATURE_DRIFT_METRICS,
    FEATURES,
    QUARANTINE_CASES,
    TRANSACTIONS,
)
from app.deps import DBDep

router = APIRouter(prefix="/api/_dashboard", tags=["dashboard"])


# --- Helpers --------------------------------------------------------------

# Tiny unit catalog for the "latest features" card. Anything not listed
# falls through to no unit (frontend just renders the bare value).
_FEATURE_UNITS: dict[str, str] = {
    "spend_24h_myr": "myr",
    "spend_7d_myr": "myr",
    "package_value_myr": "myr",
    "amount_sum_5m": "myr",
    "discount_sum_5m": "myr",
    "txn_count_5m": "count",
    "txn_count_1h": "count",
    "txn_count_24h": "count",
    "txn_count_7d": "count",
    "txn_count_total": "count",
    "quarantine_count_30d": "count",
    "discount_rate_7d": "ratio",
    "discount_rate_30d": "ratio",
    "spend_to_package_ratio": "ratio",
    "amount_vs_avg_30d_pct": "pct",
    "discount_pct_vs_avg_30d": "pct",
    "txn_velocity_5m": "rate",
    "model_score": "score",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- 1. Net new revenue (rolling window) ---------------------------------

@router.get("/net-new-revenue")
async def net_new_revenue(
    db: DBDep,
    hours: int = Query(default=1, ge=1, le=24),
) -> dict[str, Any]:
    """Sum of `total_myr` on `transactions` in the last `hours` window,
    grouped by `entity`. This is a real, time-windowed cash-flow metric
    (unlike LTV which is a slow-moving customer attribute) and reflects
    the simulator's live emission.

    Filters/buckets on ``_ingested_at`` (always a BSON Date) instead of
    ``timestamp`` (persisted as an ISO string by the V3 write path), so
    ``$gte`` against a datetime actually matches rows.
    """
    since = _now() - timedelta(hours=hours)
    try:
        # Inner per-bucket trend (minute buckets for ≤2h, otherwise hourly).
        bucket_unit = "minute" if hours <= 2 else "hour"
        pipeline: list[dict[str, Any]] = [
            {"$match": {
                "_ingested_at": {"$gte": since},
                "entity": {"$ne": None},
            }},
            {
                "$group": {
                    "_id": {
                        "entity": "$entity",
                        "bucket": {
                            "$dateTrunc": {
                                "date": "$_ingested_at",
                                "unit": bucket_unit,
                            }
                        },
                    },
                    "value_myr": {"$sum": {"$ifNull": ["$total_myr", 0]}},
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id.bucket": 1}},
            {
                "$group": {
                    "_id": "$_id.entity",
                    "total_myr": {"$sum": "$value_myr"},
                    "count": {"$sum": "$count"},
                    "trend": {
                        "$push": {
                            "ts": "$_id.bucket",
                            "value_myr": "$value_myr",
                        }
                    },
                }
            },
            {"$sort": {"total_myr": -1}},
            {
                "$project": {
                    "_id": 0,
                    "entity": "$_id",
                    "total_myr": 1,
                    "count": 1,
                    "trend": 1,
                }
            },
        ]
        cursor = await db[TRANSACTIONS].aggregate(pipeline)
        rows: list[dict[str, Any]] = []
        total = 0.0
        total_count = 0
        async for r in cursor:
            # Stringify timestamps for JSON.
            trend = [
                {
                    "ts": (t["ts"].isoformat() if isinstance(t.get("ts"), datetime) else t.get("ts")),
                    "value_myr": t.get("value_myr", 0),
                }
                for t in (r.get("trend") or [])
            ]
            rows.append({
                "entity": r["entity"],
                "total_myr": r.get("total_myr", 0),
                "count": r.get("count", 0),
                "trend": trend,
            })
            total += r.get("total_myr", 0)
            total_count += r.get("count", 0)
        return {
            "total_myr": total,
            "count": total_count,
            "window_hours": hours,
            "bucket_unit": bucket_unit,
            "rows": rows,
            "generated_at": _now().isoformat(),
        }
    except Exception:
        return {
            "total_myr": 0,
            "count": 0,
            "window_hours": hours,
            "bucket_unit": "minute" if hours <= 2 else "hour",
            "rows": [],
            "generated_at": _now().isoformat(),
        }


# --- 2. Entity health -----------------------------------------------------

@router.get("/entity-health")
async def entity_health(db: DBDep) -> dict[str, Any]:
    """Per-entity subscriber count, MRR and churn band."""
    try:
        pipeline: list[dict[str, Any]] = [
            {"$match": {"entities": {"$exists": True, "$ne": []}}},
            {"$unwind": "$entities"},
            {
                "$group": {
                    "_id": "$entities",
                    "subscriber_count": {"$sum": 1},
                    "mrr_myr": {
                        "$sum": {
                            "$ifNull": [
                                {
                                    "$getField": {
                                        "field": "monthly_mrr_myr",
                                        "input": {
                                            "$getField": {
                                                "field": "$entities",
                                                "input": "$entity_profiles",
                                            }
                                        },
                                    }
                                },
                                0,
                            ]
                        }
                    },
                    "avg_churn": {
                        "$avg": {"$ifNull": ["$cross_entity_metrics.churn_risk", 0]}
                    },
                }
            },
        ]
        union = [
            *pipeline,
            {"$unionWith": {"coll": CUSTOMERS_COMMERCIAL, "pipeline": pipeline}},
            {
                "$group": {
                    "_id": "$_id",
                    "subscriber_count": {"$sum": "$subscriber_count"},
                    "mrr_myr": {"$sum": "$mrr_myr"},
                    "avg_churn": {"$avg": "$avg_churn"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "entity": "$_id",
                    "subscriber_count": 1,
                    "mrr_myr": 1,
                    "churn_band": {
                        "$switch": {
                            "branches": [
                                {"case": {"$lt": ["$avg_churn", 0.25]}, "then": "low"},
                                {"case": {"$lt": ["$avg_churn", 0.5]}, "then": "medium"},
                            ],
                            "default": "high",
                        }
                    },
                }
            },
            {"$sort": {"subscriber_count": -1}},
        ]
        cursor = await db[CUSTOMERS_RESIDENTIAL].aggregate(union)
        rows = [r async for r in cursor]
        return {"rows": rows, "generated_at": _now().isoformat()}
    except Exception:
        return {"rows": [], "generated_at": _now().isoformat()}


# --- 3. Latest features ---------------------------------------------------

@router.get("/latest-features")
async def latest_features(db: DBDep) -> dict[str, Any]:
    """Most-recent value per feature_name across all customers."""
    try:
        pipeline = [
            {"$match": {"updated_at": {"$exists": True}}},
            {"$sort": {"updated_at": -1}},
            {"$limit": 5000},
            {
                "$project": {
                    "updated_at": 1,
                    "features_kv": {"$objectToArray": "$$ROOT"},
                }
            },
            {"$unwind": "$features_kv"},
            {
                "$match": {
                    "features_kv.k": {
                        "$in": list(_FEATURE_UNITS.keys()),
                    },
                    "features_kv.v": {"$ne": None},
                }
            },
            {
                "$group": {
                    "_id": "$features_kv.k",
                    "value": {"$first": "$features_kv.v"},
                    "updated_at": {"$first": "$updated_at"},
                }
            },
            {"$limit": 12},
            {
                "$project": {
                    "_id": 0,
                    "feature_name": "$_id",
                    "value": 1,
                    "updated_at": 1,
                }
            },
        ]
        cursor = await db[FEATURES].aggregate(pipeline)
        items: list[dict[str, Any]] = []
        async for r in cursor:
            name = r.get("feature_name")
            unit = _FEATURE_UNITS.get(name) if name else None
            ts = r.get("updated_at")
            items.append({
                "feature_name": name,
                "value": r.get("value"),
                "unit": unit,
                "updated_at": ts.isoformat() if isinstance(ts, datetime) else ts,
            })
        return {"items": items, "generated_at": _now().isoformat()}
    except Exception:
        return {"items": [], "generated_at": _now().isoformat()}


# --- 4. Cases by severity ------------------------------------------------

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@router.get("/cases-by-severity")
async def cases_by_severity(
    db: DBDep,
    hours: int = Query(default=24, ge=1, le=24 * 30),
) -> dict[str, Any]:
    """Quarantine case volume in the last `hours` window grouped by
    severity. The dashboard previously split by service state but that
    field is sparse in the seeded data — severity is always populated."""
    since = _now() - timedelta(hours=hours)
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {
                "$group": {
                    "_id": "$severity",
                    "cases_count": {"$sum": 1},
                    "open_cases": {
                        "$sum": {
                            "$cond": [
                                {"$in": ["$status", ["open", "under_review"]]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
            {"$match": {"_id": {"$ne": None}}},
            {
                "$project": {
                    "_id": 0,
                    "severity": "$_id",
                    "cases_count": 1,
                    "open_cases": 1,
                    "cases_per_day": {
                        "$divide": ["$cases_count", hours / 24.0]
                    },
                }
            },
        ]
        cursor = await db[QUARANTINE_CASES].aggregate(pipeline)
        rows = [r async for r in cursor]
        # Sort by canonical severity order, unknowns at the end.
        rows.sort(key=lambda r: _SEVERITY_ORDER.get(r.get("severity"), 99))
        return {
            "rows": rows,
            "window_hours": hours,
            "generated_at": _now().isoformat(),
        }
    except Exception:
        return {
            "rows": [],
            "window_hours": hours,
            "generated_at": _now().isoformat(),
        }


# --- 4b. Transaction rate over time --------------------------------------

@router.get("/transaction-rate")
async def transaction_rate(
    db: DBDep,
    minutes: int = Query(default=60, ge=1, le=24 * 60),
) -> dict[str, Any]:
    """Per-minute transaction counts + MYR sums for a bar-chart strip.

    Defaults to the last 60 minutes — a tight live-activity view that
    complements the SSE `metric_tick`. Each bucket is exactly one
    minute wide so the frontend can render a fixed grid.

    Filters/buckets on ``_ingested_at`` (always a BSON Date) — the V3
    persistence path stores ``timestamp`` as an ISO string, so a
    datetime ``$gte`` against it would silently match nothing.
    """
    since = _now() - timedelta(minutes=minutes)
    bucket_unit = "minute" if minutes <= 240 else "hour"
    try:
        pipeline = [
            {"$match": {"_ingested_at": {"$gte": since}}},
            {
                "$group": {
                    "_id": {
                        "$dateTrunc": {"date": "$_ingested_at", "unit": bucket_unit}
                    },
                    "count": {"$sum": 1},
                    "total_myr": {"$sum": {"$ifNull": ["$total_myr", 0]}},
                    "quarantined": {
                        "$sum": {"$cond": [{"$eq": ["$quarantined", True]}, 1, 0]}
                    },
                }
            },
            {"$sort": {"_id": 1}},
            {
                "$project": {
                    "_id": 0,
                    "ts": "$_id",
                    "count": 1,
                    "total_myr": 1,
                    "quarantined": 1,
                }
            },
        ]
        cursor = await db[TRANSACTIONS].aggregate(pipeline)
        buckets: list[dict[str, Any]] = []
        total = 0
        total_myr = 0.0
        async for r in cursor:
            ts = r.get("ts")
            buckets.append({
                "ts": ts.isoformat() if isinstance(ts, datetime) else ts,
                "count": r.get("count", 0),
                "total_myr": r.get("total_myr", 0),
                "quarantined": r.get("quarantined", 0),
            })
            total += r.get("count", 0)
            total_myr += r.get("total_myr", 0)
        return {
            "buckets": buckets,
            "window_minutes": minutes,
            "bucket_unit": bucket_unit,
            "total_count": total,
            "total_myr": total_myr,
            "generated_at": _now().isoformat(),
        }
    except Exception:
        return {
            "buckets": [],
            "window_minutes": minutes,
            "bucket_unit": bucket_unit,
            "total_count": 0,
            "total_myr": 0,
            "generated_at": _now().isoformat(),
        }


# --- 5. Top drift ---------------------------------------------------------

@router.get("/top-drift")
async def top_drift(
    db: DBDep,
    top: int = Query(default=5, ge=1, le=50),
) -> dict[str, Any]:
    """Top-N drifting features by KS statistic."""
    try:
        pipeline: list[dict[str, Any]] = [
            {"$sort": {"measured_at": 1}},
            {
                "$group": {
                    "_id": "$feature_name",
                    "feature_name": {"$last": "$feature_name"},
                    "severity": {"$last": "$severity"},
                    "ks_statistic": {"$last": "$ks_statistic"},
                    "affected_consumers": {"$last": "$affected_consumers"},
                    "affected_consumers_count": {"$last": "$affected_consumers_count"},
                }
            },
            {"$sort": {"ks_statistic": -1}},
            {"$limit": top},
            {"$project": {"_id": 0}},
        ]
        cursor = await db[FEATURE_DRIFT_METRICS].aggregate(pipeline)
        rows = [r async for r in cursor]

        # If the upstream doc didn't carry a precomputed count, fall back
        # to counting distinct customers in `features` that have written
        # values for this feature_name. Bounded concurrency = 5.
        async def _count_for(name: str) -> int:
            try:
                return await db[FEATURES].count_documents(
                    {name: {"$ne": None}}, limit=10_000
                )
            except Exception:
                return 0

        items: list[dict[str, Any]] = []
        sem = asyncio.Semaphore(5)

        async def _build(row: dict[str, Any]) -> dict[str, Any]:
            name = row.get("feature_name")
            count = row.get("affected_consumers_count")
            if count is None:
                consumers = row.get("affected_consumers") or []
                count = len(consumers) if consumers else 0
                if count == 0 and name:
                    async with sem:
                        count = await _count_for(name)
            return {
                "feature_name": name,
                "severity": row.get("severity") or "none",
                "ks_statistic": row.get("ks_statistic") or 0.0,
                "affected_consumers_count": int(count or 0),
            }

        items = await asyncio.gather(*(_build(r) for r in rows))
        return {"items": items}
    except Exception:
        return {"items": []}


# --- 6. Resolution velocity ----------------------------------------------

@router.get("/resolution-velocity")
async def resolution_velocity(db: DBDep) -> dict[str, Any]:
    """Median + p95 resolution time (in minutes) over resolved cases."""
    match_stage = {
        "$match": {
            "status": "resolved",
            "resolved_at": {"$ne": None, "$exists": True},
            "created_at": {"$exists": True},
        }
    }
    try:
        # MongoDB 7.0+ — use $percentile.
        pipeline = [
            match_stage,
            {
                "$project": {
                    "mins": {
                        "$divide": [
                            {"$subtract": ["$resolved_at", "$created_at"]},
                            60_000,
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": None,
                    "percentiles": {
                        "$percentile": {
                            "input": "$mins",
                            "p": [0.5, 0.95],
                            "method": "approximate",
                        }
                    },
                    "sample": {"$sum": 1},
                }
            },
        ]
        cursor = await db[QUARANTINE_CASES].aggregate(pipeline)
        rows = [r async for r in cursor]
        if rows and rows[0].get("percentiles"):
            pcts = rows[0]["percentiles"]
            return {
                "median_minutes": pcts[0] if len(pcts) > 0 else None,
                "p95_minutes": pcts[1] if len(pcts) > 1 else None,
                "sample": rows[0].get("sample", 0),
            }
    except Exception:
        pass

    # Fallback: pull minutes client-side and compute percentiles in Python.
    try:
        pipeline = [
            match_stage,
            {
                "$project": {
                    "_id": 0,
                    "mins": {
                        "$divide": [
                            {"$subtract": ["$resolved_at", "$created_at"]},
                            60_000,
                        ]
                    },
                }
            },
            {"$sort": {"mins": 1}},
        ]
        cursor = await db[QUARANTINE_CASES].aggregate(pipeline)
        mins = [r["mins"] async for r in cursor if r.get("mins") is not None]
        n = len(mins)
        if n == 0:
            return {"median_minutes": None, "p95_minutes": None, "sample": 0}

        def _pct(p: float) -> float:
            # Nearest-rank percentile on a sorted list.
            idx = max(0, min(n - 1, int(round(p * (n - 1)))))
            return mins[idx]

        return {
            "median_minutes": _pct(0.5),
            "p95_minutes": _pct(0.95),
            "sample": n,
        }
    except Exception:
        return {"median_minutes": None, "p95_minutes": None, "sample": 0}


# --- 7. What changed today ------------------------------------------------

@router.get("/what-changed")
async def what_changed(
    db: DBDep,
    hours: int = Query(default=24, ge=1, le=24 * 30),
) -> dict[str, Any]:
    """Drift alerts + SLA breaches in the last `hours`."""
    since = _now() - timedelta(hours=hours)

    async def _drift_alerts() -> list[dict[str, Any]]:
        try:
            cursor = db[FEATURE_DRIFT_METRICS].find(
                {
                    "measured_at": {"$gte": since},
                    "severity": {"$in": ["warn", "alert"]},
                },
                {
                    "_id": 0,
                    "feature_name": 1,
                    "severity": 1,
                    "ks_statistic": 1,
                    "measured_at": 1,
                },
                sort=[("measured_at", -1)],
                limit=50,
            )
            out: list[dict[str, Any]] = []
            async for r in cursor:
                ts = r.get("measured_at")
                ks = r.get("ks_statistic") or 0.0
                out.append({
                    "kind": "drift_alert",
                    "ts": ts.isoformat() if isinstance(ts, datetime) else ts,
                    "summary": (
                        f"{r.get('feature_name')} drifted "
                        f"({r.get('severity')}, KS {ks:.2f})"
                    ),
                    "ref": f"/features/{r.get('feature_name')}",
                })
            return out
        except Exception:
            return []

    async def _sla_breaches() -> list[dict[str, Any]]:
        try:
            cursor = db[QUARANTINE_CASES].find(
                {"sla.breached_at": {"$gte": since}},
                {"_id": 0, "case_id": 1, "sla.breached_at": 1},
                sort=[("sla.breached_at", -1)],
                limit=50,
            )
            out: list[dict[str, Any]] = []
            async for r in cursor:
                ts = (r.get("sla") or {}).get("breached_at")
                case_id = r.get("case_id")
                out.append({
                    "kind": "sla_breach",
                    "ts": ts.isoformat() if isinstance(ts, datetime) else ts,
                    "summary": f"Case {case_id} breached SLA",
                    "ref": f"/quarantine/{case_id}",
                })
            return out
        except Exception:
            return []

    drift_rows, sla_rows = await asyncio.gather(_drift_alerts(), _sla_breaches())
    merged = [*drift_rows, *sla_rows]
    merged.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return {"items": merged[:50]}
