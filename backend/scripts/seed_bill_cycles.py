"""PR-14 — seed `bill_cycles` for residential + commercial customers.

Generates the trailing N monthly bill cycles per customer (default 3:
last 2 closed + current open). Idempotent — uses `update_one(...,
upsert=True)` keyed by `cycle_id`, and stamps every doc with
`_seed_marker = "pr14"` so it can be wiped or audited later.

Usage:
    python -m scripts.seed_bill_cycles
"""

from __future__ import annotations

import calendar
import random
from datetime import datetime, timezone

from app.core.constants import (
    BILL_CYCLES,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_SEED_MARKER = "pr14"
_RES_LIMIT = 100  # cap residential customers per spec


def _month_period(anchor: datetime, offset_back: int) -> tuple[datetime, datetime]:
    """Return (start, end) of the month `offset_back` months before anchor.

    Both timestamps are tz-aware UTC. Start is the 1st at 00:00:00,
    end is the last day at 23:59:59.999999.
    """
    year = anchor.year
    month = anchor.month - offset_back
    while month <= 0:
        month += 12
        year -= 1
    last_day = calendar.monthrange(year, month)[1]
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(year, month, last_day, 23, 59, 59, 999_999, tzinfo=timezone.utc)
    return start, end


def _yyyymm(dt: datetime) -> str:
    return f"{dt.year:04d}{dt.month:02d}"


async def _collect_customer_ids(db) -> list[str]:
    """Read up to 100 residential ids and all commercial ids."""
    ids: list[str] = []
    cursor = db[CUSTOMERS_RESIDENTIAL].find(
        {}, {"_id": 0, "customer_id": 1}
    )
    cursor = cursor.limit(_RES_LIMIT)
    async for doc in cursor:
        cid = doc.get("customer_id")
        if cid:
            ids.append(cid)
    cursor = db[CUSTOMERS_COMMERCIAL].find(
        {}, {"_id": 0, "customer_id": 1}
    )
    async for doc in cursor:
        cid = doc.get("customer_id")
        if cid:
            ids.append(cid)
    return ids


async def main(db, *, cycles_per_customer: int = 3) -> dict:
    """Seed `bill_cycles` for residential + commercial customers.

    Returns counters: cycles_upserted, customers_covered.
    """
    if cycles_per_customer < 1:
        raise ValueError("cycles_per_customer must be >= 1")

    rng = random.Random(41)
    coll = db[BILL_CYCLES]

    customer_ids = await _collect_customer_ids(db)
    if not customer_ids:
        # Synthetic fallback so this script can run in isolation /
        # against an empty DB during demos.
        customer_ids = [f"cust_synth_{i}" for i in range(3)]

    anchor = datetime.now(timezone.utc)

    cycles_upserted = 0
    for cid in customer_ids:
        # Build cycles oldest -> newest. The newest (offset 0) is `open`,
        # all older offsets are `closed`.
        for offset in range(cycles_per_customer - 1, -1, -1):
            start, end = _month_period(anchor, offset)
            cycle_id = f"{cid}-{_yyyymm(start)}"
            status = "open" if offset == 0 else "closed"
            doc = {
                "cycle_id": cycle_id,
                "customer_id": cid,
                "cycle_start": start,
                "cycle_end": end,
                "status": status,
                "total_myr": round(rng.uniform(0.0, 300.0), 2),
                "_seed_marker": _SEED_MARKER,
            }
            await coll.update_one(
                {"cycle_id": cycle_id},
                {"$set": doc},
                upsert=True,
            )
            cycles_upserted += 1

    counters = {
        "cycles_upserted": cycles_upserted,
        "customers_covered": len(customer_ids),
    }
    logger.info("seed_bill_cycles_done", **counters)
    return counters
