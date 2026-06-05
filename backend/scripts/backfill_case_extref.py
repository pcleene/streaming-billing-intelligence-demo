"""PR-7 migration — upgrade legacy `quarantine_cases` to V3 (rich shape:
lifecycle, sla, priority_score, revenue_impact). Idempotent. Best-effort:
cases whose customer is unknown are logged + skipped.

Usage:
    python -m scripts.backfill_case_extref --dry-run
    python -m scripts.backfill_case_extref --batch-size 500
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from app.core.constants import (
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
    QUARANTINE_CASES,
    SCHEMA_VERSION_V3,
    TRANSACTIONS,
)
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.repositories.quarantine_case_repo import QuarantineCaseRepository
from app.services.case_lifecycle import (
    compute_priority,
    initial_sla,
    open_lifecycle_event,
    revenue_impact_from_summary,
    transition_lifecycle_event,
)

logger = get_logger(__name__)

TERMINAL_STATUSES: tuple[str, ...] = ("resolved", "dismissed")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _find_customer(db, customer_id) -> tuple[dict | None, str | None]:
    """Look up a customer doc by id across the typed collections.

    Returns `(doc, ctype)` where `ctype` is "residential" or "commercial"
    or None when the customer is unknown.
    """
    if not customer_id:
        return None, None
    doc = await db[CUSTOMERS_RESIDENTIAL].find_one({"customer_id": customer_id})
    if doc is not None:
        return doc, "residential"
    doc = await db[CUSTOMERS_COMMERCIAL].find_one({"customer_id": customer_id})
    if doc is not None:
        return doc, "commercial"
    return None, None


def _project_transaction_summary(txn: dict) -> dict:
    """Project a `TransactionDocumentV3` (or legacy txn) doc to the
    `TransactionSummary` shape used on the case tile.
    """
    items = list(txn.get("items") or [])
    items_summary = []
    for it in items[:5]:
        if not isinstance(it, dict):
            continue
        items_summary.append({
            "name": it.get("name") or "",
            "category": it.get("category"),
            "unit_price_myr": float(it.get("unit_price_myr") or 0.0),
            "discount_per_unit_myr": float(it.get("discount_per_unit_myr") or 0.0),
            "line_total_myr": float(it.get("line_total_myr") or 0.0),
            "promo_id": it.get("promo_id"),
            "charge_code": it.get("charge_code") or "CC_BILLING_ADJUSTMENT",
        })
    payment = txn.get("payment") or {}
    return {
        "transaction_id": txn.get("transaction_id"),
        "timestamp": txn.get("timestamp"),
        "transaction_type": txn.get("transaction_type") or "subscription_charge",
        "channel": txn.get("channel"),
        "subtotal_myr": float(txn.get("subtotal_myr") or 0.0),
        "total_discount_myr": float(txn.get("total_discount_myr") or 0.0),
        "tax_myr": float((txn.get("tax") or {}).get("amount_myr") or 0.0),
        "total_myr": float(txn.get("total_myr") or 0.0),
        "items_count": len(items),
        "items_summary": items_summary,
        "payment_method": payment.get("method") if isinstance(payment, dict) else None,
        "card_last4": payment.get("card_last4") if isinstance(payment, dict) else None,
    }


async def _upgrade_row(legacy: dict, *, db) -> dict | None:
    """Build a V3 case doc from a legacy quarantine case. Returns None
    when the referenced customer is unknown.
    """
    cid = legacy.get("customer_id")
    customer_doc, ctype = await _find_customer(db, cid)
    if customer_doc is None:
        return None

    now = _utcnow()
    transaction_summary: dict | None = None
    txn_id = legacy.get("transaction_id")
    if txn_id:
        txn = await db[TRANSACTIONS].find_one({"transaction_id": txn_id})
        if txn is not None:
            transaction_summary = _project_transaction_summary(txn)

    customer_snapshot = QuarantineCaseRepository._project_customer_snapshot(customer_doc)
    severity = legacy.get("severity")
    revenue_impact = revenue_impact_from_summary(
        transaction_summary=transaction_summary, severity=severity
    )
    lifetime_count = int(customer_doc.get("lifetime_quarantine_count") or 0)
    is_commercial = (ctype == "commercial")
    score, band, drivers = compute_priority(
        severity=severity,
        amount_at_risk_myr=revenue_impact["amount_at_risk_myr"],
        customer_lifetime_quarantine_count=lifetime_count,
        is_commercial=is_commercial,
    )
    opened_at = legacy.get("created_at") or now
    sla = initial_sla(severity=severity, opened_at=opened_at)

    rule_types = [
        r.get("rule_type")
        for r in (legacy.get("rules_triggered") or [])
        if isinstance(r, dict) and r.get("rule_type")
    ]
    lifecycle = [open_lifecycle_event(
        actor_id="backfill", rule_types_fired=rule_types
    )]
    status = legacy.get("status", "open")
    if status in TERMINAL_STATUSES:
        lifecycle.append(transition_lifecycle_event(
            from_status="open",
            to_status=status,
            actor_id=legacy.get("resolved_by") or "unknown",
            actor_type="analyst",
            note=legacy.get("analyst_notes"),
        ))
        # Stamp the resolution timestamp into the transition event so the
        # timeline reflects the original close, not the backfill run.
        lifecycle[-1]["at"] = legacy.get("resolved_at") or now

    is_terminal = status in TERMINAL_STATUSES
    return {
        "_schema_version": SCHEMA_VERSION_V3,
        "case_id": legacy["case_id"],
        "customer_id": cid,
        "customer_type": ctype,
        "transaction_id": txn_id,
        "cycle_id": legacy.get("cycle_id"),
        "severity": severity,
        "status": status,
        "priority_score": score,
        "priority_band": band,
        "auto_priority_drivers": drivers,
        "lifecycle": lifecycle,
        "rules_triggered": legacy.get("rules_triggered") or [],
        "customer_snapshot": customer_snapshot,
        "transaction_summary": transaction_summary,
        "ai_assist": None,
        "similar_cases_preview": [],
        "analyst_notes": legacy.get("analyst_notes") if is_terminal else None,
        "disposition": legacy.get("disposition") if is_terminal else None,
        "resolved_at": legacy.get("resolved_at") if is_terminal else None,
        "resolved_by": legacy.get("resolved_by") if is_terminal else None,
        "sla": sla,
        "revenue_impact": revenue_impact,
        "tags": list(legacy.get("tags") or []),
        "created_at": legacy.get("created_at") or now,
        "updated_at": now,
    }


async def backfill(*, dry_run: bool, batch_size: int) -> dict:
    """Walk the quarantine_cases collection, upgrade each legacy row.

    Returns a small stats dict for the CLI printout.
    """
    db = get_db()
    coll = db[QUARANTINE_CASES]

    stats = {
        "scanned": 0,
        "upgraded": 0,
        "skipped_v3": 0,
        "skipped_unknown_customer": 0,
    }
    cursor = coll.find(
        {"$or": [
            {"_schema_version": {"$lt": SCHEMA_VERSION_V3}},
            {"_schema_version": {"$exists": False}},
        ]}
    ).limit(batch_size if batch_size else 0)

    async for legacy in cursor:
        stats["scanned"] += 1
        if legacy.get("_schema_version") == SCHEMA_VERSION_V3:
            stats["skipped_v3"] += 1
            continue
        upgraded = await _upgrade_row(legacy, db=db)
        if upgraded is None:
            stats["skipped_unknown_customer"] += 1
            logger.info(
                "backfill_case_extref_skip_unknown_customer",
                case_id=legacy.get("case_id"),
                customer_id=legacy.get("customer_id"),
            )
            continue
        if not dry_run:
            await coll.replace_one(
                {"case_id": upgraded["case_id"]}, upgraded
            )
        stats["upgraded"] += 1
    return stats


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="backfill_case_extref")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute upgrades but do not write.")
    p.add_argument("--batch-size", type=int, default=0,
                   help="Cap the number of rows processed (0 = no cap).")
    return p.parse_args()


async def main() -> None:
    configure_logging()
    args = _parse_args()
    await connect_mongo()
    try:
        stats = await backfill(dry_run=args.dry_run, batch_size=args.batch_size)
        logger.info(
            "backfill_case_extref_done",
            dry_run=args.dry_run,
            **stats,
        )
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
