"""Before/after service (Phase B.7).

Read-only. Given a case, produce the two projections used by the
`BeforeAfterPanel` tile. Reads from `quarantine_cases`, `customers`, and
optionally `crm_snapshots` (a 24h-stale mirror written by an out-of-band
warehouse-feed worker — not provisioned in this repo, so we fall back to a
derived stale view).

The point of the panel is to make the operational-data-platform pitch
visible: the warehouse snapshot misses the new transactions, the
quarantine case, and the ai_assist that the live platform already has.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.core.constants import CRM_SNAPSHOTS, CUSTOMERS, QUARANTINE_CASES
from app.core.errors import CaseNotFound, CustomerNotFound
from app.core.logging import get_logger
from app.schemas.before_after import BeforeAfter, BeforeAfterProjection

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _customer_summary(cust: dict) -> dict[str, Any]:
    addr = cust.get("address") or {}
    return {
        "customer_id": cust.get("customer_id"),
        "name": cust.get("name"),
        "tier": cust.get("tier"),
        "state": addr.get("state"),
        "total_monthly_value_myr": cust.get("total_monthly_value_myr"),
        "active_subs": sum(
            1 for s in (cust.get("subscriptions") or [])
            if s.get("status") == "active"
        ),
    }


class BeforeAfterService:
    """Read-only composer; takes a db handle and ages-out the live doc."""

    def __init__(self, db) -> None:
        self._db = db

    async def get(self, case_id: str) -> BeforeAfter:
        case = await self._db[QUARANTINE_CASES].find_one(
            {"case_id": case_id}, {"_id": 0}
        )
        if not case:
            raise CaseNotFound(case_id)
        customer_id = case.get("customer_id")
        cust = await self._db[CUSTOMERS].find_one(
            {"customer_id": customer_id}, {"_id": 0}
        )
        if not cust:
            raise CustomerNotFound(customer_id or "<unknown>")

        cutoff = _utcnow() - timedelta(hours=settings.crm_lag_hours)
        before = await self._build_before(customer_id, cutoff)
        after = self._build_after(cust, case)
        return BeforeAfter(
            case_id=case_id,
            customer_id=customer_id,
            generated_at=_utcnow(),
            before=before,
            after=after,
            would_quarantine=self._would_quarantine(case, before),
            auto_resolution_latency_seconds=self._latency(case),
        )

    # ----- before ------------------------------------------------------

    async def _build_before(
        self, customer_id: str, cutoff: datetime,
    ) -> BeforeAfterProjection:
        # Prefer a real warehouse snapshot if the deployment has one.
        snap = None
        try:
            snap = await self._db[CRM_SNAPSHOTS].find_one(
                {"customer_id": customer_id, "snapshot_at": {"$lte": cutoff}},
                sort=[("snapshot_at", -1)],
                projection={"_id": 0},
            )
        except Exception as exc:  # noqa: BLE001
            # Collection may not exist on a fresh demo cluster.
            logger.debug("crm_snapshots_lookup_failed", error=str(exc))
        if snap:
            payload = snap.get("payload") or {}
            return BeforeAfterProjection(
                source="crm_snapshot",
                snapshot_at=_coerce_dt(snap.get("snapshot_at")) or cutoff,
                customer_summary=_customer_summary(payload),
                open_case_count=len(payload.get("open_cases") or []),
                recent_transaction_count=len(payload.get("recent_transactions") or []),
                has_ai_assist=False,  # ai_assist is operational, never warehoused
                ai_assist_summary=None,
                rules_visible=[],
                notes=[
                    f"Loaded warehouse snapshot from "
                    f"{cutoff.isoformat(timespec='minutes')}.",
                    "AI-assist not in the warehouse — analysts triaged blind.",
                ],
            )
        return await self._derive_lag_projection(customer_id, cutoff)

    async def _derive_lag_projection(
        self, customer_id: str, cutoff: datetime,
    ) -> BeforeAfterProjection:
        cust = await self._db[CUSTOMERS].find_one(
            {"customer_id": customer_id}, {"_id": 0}
        )
        cust = cust or {}
        # Drop anything 'born' after the cutoff to mimic a stale snapshot.
        old_txns = [
            t for t in (cust.get("recent_transactions") or [])
            if (_coerce_dt(t.get("timestamp")) or cutoff) <= cutoff
        ]
        old_cases = [
            c for c in (cust.get("open_cases") or [])
            if (_coerce_dt(c.get("created_at")) or cutoff) <= cutoff
        ]
        return BeforeAfterProjection(
            source="derived_lag",
            snapshot_at=cutoff,
            customer_summary=_customer_summary(cust),
            open_case_count=len(old_cases),
            recent_transaction_count=len(old_txns),
            has_ai_assist=False,
            ai_assist_summary=None,
            rules_visible=[],
            notes=[
                "No warehouse snapshot found — derived a "
                f"{settings.crm_lag_hours}h-stale view from the live doc.",
                "Items written in the lag window are hidden.",
            ],
        )

    # ----- after -------------------------------------------------------

    def _build_after(self, cust: dict, case: dict) -> BeforeAfterProjection:
        ai_assist = case.get("ai_assist") or {}
        rule_types = sorted({
            r.get("rule_type") for r in (case.get("rules_triggered") or [])
            if isinstance(r, dict) and r.get("rule_type")
        })
        return BeforeAfterProjection(
            source="live",
            snapshot_at=_utcnow(),
            customer_summary=_customer_summary(cust),
            open_case_count=len(cust.get("open_cases") or []),
            recent_transaction_count=len(cust.get("recent_transactions") or []),
            has_ai_assist=bool(ai_assist),
            ai_assist_summary=ai_assist.get("summary"),
            rules_visible=rule_types,
            notes=[
                "Live operational doc — change-stream tail keeps it ≤ 1s stale.",
            ],
        )

    # ----- derived flags ----------------------------------------------

    def _would_quarantine(self, case: dict, before: BeforeAfterProjection) -> bool:
        # The case exists *now*; it would have been quarantined in the
        # warehouse-only world only if the offending transaction predates
        # the snapshot cutoff.
        snapshot_at = before.snapshot_at
        if not snapshot_at:
            return False
        created = _coerce_dt(case.get("created_at"))
        return bool(created and created <= snapshot_at)

    def _latency(self, case: dict) -> float | None:
        created = _coerce_dt(case.get("created_at"))
        resolved = _coerce_dt(case.get("resolved_at"))
        if not created or not resolved:
            return None
        delta = (resolved - created).total_seconds()
        return round(delta, 1) if delta >= 0 else None
