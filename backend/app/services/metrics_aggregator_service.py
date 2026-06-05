"""Cross-entity metrics aggregator (PR-9).

Computes the `cross_entity_metrics` block (LTV total + 12-month trends
for monthly spend, viewing hours, and PPV count) from the
`transactions` collection. Reused by:

  - `app/workers/customer_360_aggregator.py` (nightly batch)
  - The on-demand recompute endpoint (PR-12)

Aggregation strategy: a single `$match` + `$group` over the
`transactions` collection. No `$lookup` — the V3 transaction docs
already carry every field we need (`total_myr`, `transaction_type`,
`metadata.viewing_minutes`, etc). Settled-only filter via
`status: "settled"`; refunds and unsettled events do not contribute.

The Python side then builds a contiguous 12-month timeline from the
`as_of` anchor backwards, zero-filling any month with no data so the
dashboard's chart axis stays stable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.repositories.transaction_repo import TransactionRepository

logger = get_logger(__name__)


# Default cap on the rows returned by `get_burst_status`. The latest
# 60 samples covers the most-recent hour at the default 1-minute
# `metrics_recorder_interval_seconds`, which is what the BurstModeTile
# needs to render a sparkline.
_BURST_STATUS_DEFAULT_LIMIT = 60


# Charge / event types that count as PPV for the trend.
_PPV_TYPES: tuple[str, ...] = ("ppv_purchase", "ppv_charge")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _month_key(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _months_back(anchor: datetime, n: int) -> list[str]:
    """Return n month keys ending at `anchor`'s month, oldest first."""
    year = anchor.year
    month = anchor.month
    keys: list[str] = []
    for _ in range(n):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(keys))


class MetricsAggregatorService:
    """Computes `cross_entity_metrics` from the transactions collection."""

    def __init__(
        self,
        transaction_repo: TransactionRepository,
        system_metrics_collection: Any | None = None,
        *,
        db: Any | None = None,
    ) -> None:
        self._txns = transaction_repo
        # Optional dependency — only needed by `get_burst_status`. The
        # PR-9 customer_360 path constructs the service with just the
        # transaction repo and never calls into burst status, so this
        # stays backward compatible.
        #
        # Two equivalent ways to supply the dependency:
        #   - `system_metrics_collection=db[SYSTEM_METRICS]` (PR-11 sister
        #     spec — pre-resolved collection handle)
        #   - `db=db` (cleaner — service resolves the collection itself
        #     via the `SYSTEM_METRICS` constant, mirroring the
        #     TransactionRepository pattern of accepting the db handle)
        # `db=` wins if both are provided; the resolved collection is
        # cached on `self._system_metrics`.
        if db is not None:
            from app.core.constants import SYSTEM_METRICS
            self._system_metrics = db[SYSTEM_METRICS]
        else:
            self._system_metrics = system_metrics_collection
        self._db = db

    async def compute_for_customer(
        self,
        customer_id: str,
        *,
        as_of: datetime | None = None,
    ) -> dict:
        """Return the `cross_entity_metrics` block for `customer_id`.

        Shape:
            ltv: {total_myr: float, currency: "MYR"}
            monthly_spend_trend_12m: list[{month: "YYYY-MM", amount_myr: float}]
            viewing_hours_trend_12m: list[{month, hours}]
            ppv_count_trend_12m: list[{month, count}]
            last_computed_at: datetime

        All three trend lists carry exactly 12 entries oldest-first;
        gaps are zero-filled. `as_of` defaults to now (UTC); the trend
        window is the 12 calendar months ending at `as_of`.
        """
        anchor = as_of or _utcnow()
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)

        # Floor to the start of the month 12 months back so the $match
        # excludes documents we are about to discard anyway.
        first_month_key = _months_back(anchor, 12)[0]
        year_str, month_str = first_month_key.split("-")
        window_start = datetime(int(year_str), int(month_str), 1, tzinfo=timezone.utc)

        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "customer_id": customer_id,
                    "status": "settled",
                    "timestamp": {"$gte": window_start},
                }
            },
            {
                "$group": {
                    "_id": {
                        "year": {"$year": "$timestamp"},
                        "month": {"$month": "$timestamp"},
                    },
                    "spend": {"$sum": {"$ifNull": ["$total_myr", "$amount"]}},
                    "viewing_minutes": {
                        "$sum": {"$ifNull": ["$metadata.viewing_minutes", 0]}
                    },
                    "ppv_count": {
                        "$sum": {
                            "$cond": [
                                {"$in": ["$transaction_type", list(_PPV_TYPES)]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
        ]

        rows = await self._txns.aggregate(pipeline)

        # Build the contiguous 12-month timeline.
        months = _months_back(anchor, 12)
        spend_by_month: dict[str, float] = {m: 0.0 for m in months}
        hours_by_month: dict[str, float] = {m: 0.0 for m in months}
        ppv_by_month: dict[str, int] = {m: 0 for m in months}

        for row in rows:
            ident = row.get("_id") or {}
            y = ident.get("year")
            m = ident.get("month")
            if y is None or m is None:
                continue
            key = f"{int(y):04d}-{int(m):02d}"
            if key not in spend_by_month:
                # Outside the window — skip rather than throw.
                continue
            spend_by_month[key] = round(float(row.get("spend") or 0.0), 2)
            minutes = float(row.get("viewing_minutes") or 0.0)
            hours_by_month[key] = round(minutes / 60.0, 2)
            ppv_by_month[key] = int(row.get("ppv_count") or 0)

        ltv_total = round(sum(spend_by_month.values()), 2)

        return {
            "ltv": {"total_myr": ltv_total, "currency": "MYR"},
            "monthly_spend_trend_12m": [
                {"month": m, "amount_myr": spend_by_month[m]} for m in months
            ],
            "viewing_hours_trend_12m": [
                {"month": m, "hours": hours_by_month[m]} for m in months
            ],
            "ppv_count_trend_12m": [
                {"month": m, "count": ppv_by_month[m]} for m in months
            ],
            "last_computed_at": _utcnow(),
        }

    # ------------------------------------------------------------------
    # PR-11 — burst window status reader
    # ------------------------------------------------------------------

    async def get_burst_status(
        self,
        run_id: str | None = None,
        *,
        limit: int = _BURST_STATUS_DEFAULT_LIMIT,
    ) -> dict:
        """Return the burst-window status for a `system_metrics` run.

        Without `run_id`: returns the most-recent burst run we can find
        in `system_metrics` (a row whose `burst_run_id` is non-null);
        if no such row has ever been written, returns a benign empty
        envelope. With `run_id`: returns the rows for that specific run.

        Output shape (kept dual to satisfy both the sister-PR caller
        contract and the PR-11 spec — the two key sets coexist on the
        same envelope so dashboards on either branch keep working):

            {
              "run_id": str | None,
              "active": bool,                # True iff most-recent row's mode == "burst"
              "started_at": datetime | None,
              "ended_at": datetime | None,   # None when still active
              "rows": list[dict],            # newest-first, capped at `limit`
              "samples": list[dict],         # same data, oldest-first
              "summary": {
                "row_count": int,
                "sample_count": int,         # alias of row_count for PR-11
                "peak_tps": float,
                "peak_observed_tps": float,  # alias of peak_tps for PR-11
                "mean_tps": float,
                "p99_rule_eval_ms_max": float,
                "peak_p99_ms": float,        # alias for PR-11
                "target_tps_compliance": float,  # 0..1, fraction of rows >=
                                                  # 90% of the run's peak TPS
                "rule_eval_p99_threshold_breaches": int,  # samples where
                                                          # rule_eval_p99_ms > 200
                "started_at": datetime | None,
                "ended_at": datetime | None,
                "duration_seconds": float,
              }
            }
        """
        if self._system_metrics is None:
            raise RuntimeError(
                "MetricsAggregatorService.get_burst_status requires "
                "`system_metrics_collection` (or `db=`) at construction "
                "time — db not configured"
            )

        coll = self._system_metrics
        capped = max(1, int(limit))

        target_run_id = run_id
        if target_run_id is None:
            # No explicit run_id: scope to the latest run we can see.
            # Two cases:
            #   1. The latest row is itself burst — we are mid-run; pick
            #      its `burst_run_id` (which may be None if the recorder
            #      did not stamp it).
            #   2. The latest row is steady/idle — fall back to the most
            #      recent burst row in history; if none, report empty.
            latest_any = await coll.find_one({}, sort=[("recorded_at", -1)])
            if not latest_any:
                return self._empty_burst_envelope()

            if latest_any.get("mode") == "burst":
                target_run_id = latest_any.get("burst_run_id")
            else:
                latest_burst = await coll.find_one(
                    {"mode": "burst"},
                    sort=[("recorded_at", -1)],
                )
                if not latest_burst:
                    return self._empty_burst_envelope()
                target_run_id = latest_burst.get("burst_run_id")

        # Build the row filter. When `target_run_id` is set, scope to it;
        # when None (recorder was running burst mode without stamping a
        # run_id), fall back to mode="burst" so we still surface useful
        # samples to the dashboard.
        row_filter: dict[str, Any]
        if target_run_id is not None:
            row_filter = {"burst_run_id": target_run_id}
        else:
            row_filter = {"mode": "burst"}

        cursor = (
            coll.find(row_filter)
            .sort([("recorded_at", -1)])
            .limit(capped)
        )
        # Strip the BSON `_id` so the FastAPI JSON encoder doesn't trip over
        # `ObjectId` when this envelope is serialized for /api/metrics/burst.
        rows_newest_first: list[dict] = [
            {k: v for k, v in r.items() if k != "_id"} async for r in cursor
        ]

        if not rows_newest_first:
            return self._empty_burst_envelope(run_id=target_run_id)

        # `started_at` should reflect the entire run — pull the earliest
        # matching row independently of the (capped) display window.
        earliest = await coll.find_one(
            row_filter,
            sort=[("recorded_at", 1)],
        )
        started_at = (earliest or rows_newest_first[-1]).get("recorded_at")

        # The latest *overall* row determines whether the run is still
        # active — its mode tells us if a burst is currently in flight.
        latest_overall = await coll.find_one({}, sort=[("recorded_at", -1)])
        active = bool(latest_overall and latest_overall.get("mode") == "burst")
        # `ended_at` reflects the most-recent burst sample we surfaced.
        ended_at = None if active else rows_newest_first[0].get("recorded_at")

        # Summary stats — defensively coerce to float, default to 0.0.
        observed = [float(r.get("observed_tps") or 0.0) for r in rows_newest_first]
        p99_values = [
            float(r.get("rule_eval_p99_ms") or r.get("p99_ms_ingest") or 0.0)
            for r in rows_newest_first
        ]

        peak_tps = max(observed) if observed else 0.0
        mean_tps = (sum(observed) / len(observed)) if observed else 0.0
        p99_max = max(p99_values) if p99_values else 0.0

        # PR-11 spec: count samples whose `rule_eval_p99_ms` exceeds the
        # 200ms target. We deliberately read `rule_eval_p99_ms` only
        # (not the `p99_ms_ingest` fallback) — the threshold contract is
        # specifically about rule evaluation latency.
        breaches = sum(
            1 for r in rows_newest_first
            if float(r.get("rule_eval_p99_ms") or 0.0) > 200.0
        )

        # Compliance: per spec we use the run's peak observed TPS as the
        # de-facto target (the simulator does not currently persist a
        # plan record). 90 % of peak is the threshold.
        if peak_tps > 0.0:
            threshold = 0.9 * peak_tps
            compliant = sum(1 for v in observed if v >= threshold)
            compliance = compliant / len(observed)
        else:
            compliance = 0.0

        # `samples` is the oldest→newest reverse of `rows`, kept as a
        # separate list so the rows-list reference doesn't alias.
        samples_oldest_first = list(reversed(rows_newest_first))

        # Duration is computed from the run's started_at to the latest
        # surfaced sample's recorded_at (or now-equivalent if still
        # active — but ended_at is None then, so we use the newest row).
        newest_at = rows_newest_first[0].get("recorded_at")
        if started_at is not None and newest_at is not None:
            try:
                duration_seconds = (newest_at - started_at).total_seconds()
            except TypeError:
                duration_seconds = 0.0
        else:
            duration_seconds = 0.0

        return {
            "run_id": target_run_id,
            "active": active,
            "started_at": started_at,
            "ended_at": ended_at,
            "rows": rows_newest_first,
            "samples": samples_oldest_first,
            "summary": {
                "row_count": len(rows_newest_first),
                "sample_count": len(rows_newest_first),
                "peak_tps": round(peak_tps, 2),
                "peak_observed_tps": round(peak_tps, 2),
                "mean_tps": round(mean_tps, 2),
                "p99_rule_eval_ms_max": round(p99_max, 1),
                "peak_p99_ms": round(p99_max, 1),
                "target_tps_compliance": round(compliance, 4),
                "rule_eval_p99_threshold_breaches": breaches,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_seconds": round(float(duration_seconds), 3),
            },
        }

    @staticmethod
    def _empty_burst_envelope(*, run_id: str | None = None) -> dict:
        return {
            "run_id": run_id,
            "active": False,
            "started_at": None,
            "ended_at": None,
            "rows": [],
            "samples": [],
            "summary": {
                "row_count": 0,
                "sample_count": 0,
                "peak_tps": 0.0,
                "peak_observed_tps": 0.0,
                "mean_tps": 0.0,
                "p99_rule_eval_ms_max": 0.0,
                "peak_p99_ms": 0.0,
                "target_tps_compliance": 0.0,
                "rule_eval_p99_threshold_breaches": 0,
                "started_at": None,
                "ended_at": None,
                "duration_seconds": 0.0,
            },
        }
