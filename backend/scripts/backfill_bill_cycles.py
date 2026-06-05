"""PR-4 migration — seed `bill_cycles` for every customer.

For each residential / commercial customer, synthesise the trailing N
calendar-month cycles via
`bill_cycle_repo.trailing_cycles_for_customer` and upsert them keyed
on `cycle_id`. Idempotent — re-runs leave existing cycles untouched
(`$setOnInsert`).

Usage:
    python -m scripts.backfill_bill_cycles --dry-run
    python -m scripts.backfill_bill_cycles --months 12
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from app.core.constants import (
    BILL_CYCLES,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
)
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.repositories.bill_cycle_repo import trailing_cycles_for_customer

logger = get_logger(__name__)


async def _backfill_collection(
    db,
    coll_name: str,
    *,
    months: int,
    anchor: datetime,
    dry_run: bool,
) -> dict:
    coll = db[coll_name]
    cycles_coll = db[BILL_CYCLES]
    stats = {"customers": 0, "cycles_seeded": 0, "cycles_skipped": 0}

    cursor = coll.find(
        {},
        {
            "_id": 0,
            "customer_id": 1,
            "customer_type": 1,
            "account_id": 1,
            "parent_account_id": 1,
            "billing_day_of_month": 1,
        },
    )
    async for customer in cursor:
        stats["customers"] += 1
        docs = trailing_cycles_for_customer(
            customer, count=months, anchor=anchor
        )
        for doc in docs:
            existing = await cycles_coll.find_one(
                {"cycle_id": doc["cycle_id"]},
                {"_id": 1},
            )
            if existing is not None:
                stats["cycles_skipped"] += 1
                continue
            if not dry_run:
                await cycles_coll.update_one(
                    {"cycle_id": doc["cycle_id"]},
                    {"$setOnInsert": doc},
                    upsert=True,
                )
            stats["cycles_seeded"] += 1
    return stats


async def backfill(
    *, dry_run: bool, months: int, anchor: datetime | None = None
) -> dict:
    db = get_db()
    anchor = anchor or datetime.now(timezone.utc)
    res = await _backfill_collection(
        db, CUSTOMERS_RESIDENTIAL,
        months=months, anchor=anchor, dry_run=dry_run,
    )
    com = await _backfill_collection(
        db, CUSTOMERS_COMMERCIAL,
        months=months, anchor=anchor, dry_run=dry_run,
    )
    return {
        "residential": res,
        "commercial": com,
        "total_customers": res["customers"] + com["customers"],
        "total_cycles_seeded": res["cycles_seeded"] + com["cycles_seeded"],
        "total_cycles_skipped": res["cycles_skipped"] + com["cycles_skipped"],
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="backfill_bill_cycles")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute upserts but do not write.")
    p.add_argument("--months", type=int, default=12,
                   help="Trailing month count to seed per customer.")
    return p.parse_args()


async def main() -> None:
    configure_logging()
    args = _parse_args()
    await connect_mongo()
    try:
        stats = await backfill(dry_run=args.dry_run, months=args.months)
        logger.info("backfill_bill_cycles_done", dry_run=args.dry_run, **stats)
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
