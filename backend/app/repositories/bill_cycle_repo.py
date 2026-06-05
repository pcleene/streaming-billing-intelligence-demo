"""Bill cycle repository (PR-4).

`bill_cycles` is the cycle anchor of record (ADR-023). Transactions
embed `cycle_id` at write time; rules and panels read directly from the
cycle document, never via `$lookup`.

Public surface:
- `resolve(customer_id, ts, *, customer_type, account_id=None,
  parent_account_id=None, create_on_miss=True)` →
  `(cycle_id, bill_period)`. When no cycle covers `ts` and
  `create_on_miss=True`, synthesises one anchored on the calendar month
  of `ts` and persists it idempotently (`$setOnInsert`).
- `resolve_cycle(customer_id, ts, *, customer=None, ...)` — convenience
  wrapper used by `transaction_repo.BatchCache`. Forwards to `resolve`
  by reading `customer_type` / `account_id` / `parent_account_id` from
  the customer dict.
- `get(cycle_id)`, `find_for_customer(customer_id, *, limit)`,
  `find_covering(customer_id, ts)`.
- `update_breakdown(cycle_id, *, set_fields=None, inc_fields=None)`.
- `append_status(cycle_id, status, *, by)`.
- `record_transaction(cycle_id, *, transaction_id, total_myr)`.

Module helpers:
- `cycle_id_for(customer_id, ts)` — deterministic cycle id builder.
- `synthesise_period(customer_id, ts, *, customer=None)` —
  `(cycle_id, bill_period)` without I/O.
- `trailing_cycles_for_customer(customer, *, count, anchor)` —
  N trailing monthly cycle docs; used by `scripts.backfill_bill_cycles`.
"""

from __future__ import annotations

import uuid
from calendar import monthrange
from datetime import datetime, timezone

from app.core.constants import BILL_CYCLES, SCHEMA_VERSION_V3
from app.core.logging import get_logger
from app.repositories.base import BaseRepository

logger = get_logger(__name__)

DEFAULT_BILL_DAY = 1
DEFAULT_TZ = "Asia/Kuala_Lumpur"

_GENERATOR_RESOLVE = "bill_cycle_repo.resolve"
_GENERATOR_BACKFILL = "scripts.backfill_bill_cycles"


# --- Pure helpers ----------------------------------------------------


def cycle_id_for(customer_id: str, ts: datetime) -> str:
    """Deterministic cycle id keyed on (customer_id, YYYYMM)."""
    return f"cyc_{customer_id}_{ts.year:04d}{ts.month:02d}"


def _month_window(ts: datetime) -> tuple[datetime, datetime, int]:
    """Return `(start, end, length_days)` for the calendar month of `ts`."""
    tz = ts.tzinfo or timezone.utc
    year, month = ts.year, ts.month
    start = datetime(year, month, 1, tzinfo=tz)
    last_day = monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=tz)
    return start, end, last_day


def _bill_period_from_anchors(anchors: dict) -> dict:
    """Project a `bill_cycles.cycle_anchors` block into a transaction
    `bill_period` block."""
    start = anchors.get("cycle_start")
    end = anchors.get("cycle_end")
    return {
        "start": start,
        "end": end,
        "cycle_length_days": anchors.get("cycle_length_days") or _days_between(start, end),
        "bill_day_of_month": anchors.get("bill_day_of_month") or DEFAULT_BILL_DAY,
    }


def _days_between(start: datetime | None, end: datetime | None) -> int:
    if not (start and end):
        return 30
    return max(1, (end - start).days + 1)


def _month_offset(ts: datetime, delta: int) -> datetime:
    """Return `ts` shifted by `delta` months (negative = past)."""
    month = ts.month + delta
    year = ts.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    last = monthrange(year, month)[1]
    day = min(ts.day, last)
    return ts.replace(year=year, month=month, day=day)


def synthesise_period(
    customer_id: str, ts: datetime, *, customer: dict | None = None
) -> tuple[str, dict]:
    """Pure: calendar-month cycle id + bill_period block."""
    start, end, length_days = _month_window(ts)
    cycle_id = cycle_id_for(customer_id, ts)
    bill_day = DEFAULT_BILL_DAY
    if customer and customer.get("billing_day_of_month"):
        bill_day = int(customer["billing_day_of_month"])
    return cycle_id, {
        "start": start,
        "end": end,
        "cycle_length_days": length_days,
        "bill_day_of_month": bill_day,
    }


def trailing_cycles_for_customer(
    customer: dict,
    *,
    count: int = 12,
    anchor: datetime | None = None,
) -> list[dict]:
    """Yield N trailing monthly cycle docs ending at `anchor`'s month.

    Status convention: offset 0 (anchor's month) is `"open"`; older
    months are `"billed"`. The backfill script consumes the result and
    upserts each via `$setOnInsert` keyed on `cycle_id`.
    """
    anchor = anchor or datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)
    customer_id = customer["customer_id"]
    customer_type = customer.get("customer_type") or "residential"
    account_id = customer.get("account_id") or f"acc_{customer_id}"
    parent_account_id = customer.get("parent_account_id")
    bill_day = int(customer.get("billing_day_of_month") or DEFAULT_BILL_DAY)

    out: list[dict] = []
    for offset in range(count):
        ts = _month_offset(anchor, -offset)
        start, end, length_days = _month_window(ts)
        cycle_id = cycle_id_for(customer_id, ts)
        status = "open" if offset == 0 else "billed"
        doc: dict = {
            "_schema_version":   SCHEMA_VERSION_V3,
            "cycle_id":          cycle_id,
            "customer_id":       customer_id,
            "customer_type":     customer_type,
            "account_id":        account_id,
            "cycle_anchors": {
                "cycle_start":       start,
                "cycle_end":         end,
                "cycle_length_days": length_days,
                "bill_day_of_month": bill_day,
                "tz":                DEFAULT_TZ,
            },
            "current_status":  status,
            "status_timeline": [
                {"at": start, "status": "open", "by": _GENERATOR_BACKFILL},
            ],
            "expected_amount_myr":   0.0,
            "billed_amount_myr":     0.0,
            "transaction_count":     0,
            "transaction_total_myr": 0.0,
            "transaction_ids":       [],
            "audit": {
                "billing_run_id": f"backfill_{uuid.uuid4().hex[:8]}",
                "generated_at":   now,
                "generator":      _GENERATOR_BACKFILL,
            },
            "created_at": now,
            "updated_at": now,
        }
        if parent_account_id is not None:
            doc["parent_account_id"] = parent_account_id
        if status == "billed":
            doc["status_timeline"].append(
                {"at": end, "status": "billed", "by": _GENERATOR_BACKFILL}
            )
        out.append(doc)
    return out


# --- Repository ------------------------------------------------------


class BillCycleRepository(BaseRepository):
    """CRUD + cycle resolution for `bill_cycles`."""

    COLLECTION_NAME = BILL_CYCLES

    # --- Reads --------------------------------------------------------

    async def get(self, cycle_id: str) -> dict | None:
        return await self.find_one({"cycle_id": cycle_id})

    async def find_for_customer(
        self, customer_id: str, *, limit: int = 12
    ) -> list[dict]:
        return await self.find_many(
            {"customer_id": customer_id},
            sort=[("cycle_anchors.cycle_start", -1)],
            limit=limit,
        )

    async def find_covering(
        self, customer_id: str, ts: datetime
    ) -> dict | None:
        """The cycle whose `[cycle_start, cycle_end]` contains `ts`."""
        return await self.find_one({
            "customer_id": customer_id,
            "cycle_anchors.cycle_start": {"$lte": ts},
            "cycle_anchors.cycle_end": {"$gte": ts},
        })

    # --- Cycle resolution --------------------------------------------

    async def resolve(
        self,
        customer_id: str,
        ts: datetime,
        *,
        customer_type: str = "residential",
        account_id: str | None = None,
        parent_account_id: str | None = None,
        create_on_miss: bool = True,
    ) -> tuple[str, dict]:
        """Return `(cycle_id, bill_period)` for the cycle covering `ts`.

        When no cycle covers `ts` and `create_on_miss=True`, synthesise
        a calendar-month cycle and persist it idempotently
        (`$setOnInsert` keyed on `cycle_id`). The synth doc carries
        `audit.generator="bill_cycle_repo.resolve"` so PR-12's
        billing-engine refresh can spot rows it can refine.
        """
        existing = await self.find_covering(customer_id, ts)
        if existing is not None:
            anchors = existing.get("cycle_anchors") or {}
            return existing["cycle_id"], _bill_period_from_anchors(anchors)

        cycle_id, bill_period = synthesise_period(customer_id, ts)
        if not create_on_miss:
            return cycle_id, bill_period

        await self._upsert_synth(
            cycle_id=cycle_id,
            customer_id=customer_id,
            customer_type=customer_type,
            account_id=account_id or f"acc_{customer_id}",
            parent_account_id=parent_account_id,
            bill_period=bill_period,
        )
        return cycle_id, bill_period

    async def resolve_cycle(
        self,
        customer_id: str,
        ts: datetime,
        *,
        customer: dict | None = None,
        create_on_miss: bool = True,
    ) -> tuple[str, dict]:
        """`resolve()` adapter for `transaction_repo.BatchCache`.

        Pulls `customer_type` / `account_id` / `parent_account_id` from
        the customer dict so the hot-path caller doesn't have to unpack
        them itself.
        """
        c = customer or {}
        ctype = c.get("_resolved_type") or c.get("customer_type") or "residential"
        return await self.resolve(
            customer_id, ts,
            customer_type=ctype,
            account_id=c.get("account_id"),
            parent_account_id=c.get("parent_account_id"),
            create_on_miss=create_on_miss,
        )

    async def _upsert_synth(
        self,
        *,
        cycle_id: str,
        customer_id: str,
        customer_type: str,
        account_id: str,
        parent_account_id: str | None,
        bill_period: dict,
    ) -> None:
        now = datetime.now(timezone.utc)
        anchors = {
            "cycle_start":       bill_period["start"],
            "cycle_end":         bill_period["end"],
            "cycle_length_days": bill_period["cycle_length_days"],
            "bill_day_of_month": bill_period["bill_day_of_month"],
            "tz": DEFAULT_TZ,
        }
        soi: dict = {
            "_schema_version":      SCHEMA_VERSION_V3,
            "cycle_id":             cycle_id,
            "customer_id":          customer_id,
            "customer_type":        customer_type,
            "account_id":           account_id,
            "cycle_anchors":        anchors,
            "current_status":       "open",
            "status_timeline": [{
                "at":     now,
                "status": "open",
                "by":     _GENERATOR_RESOLVE,
            }],
            "expected_amount_myr":   0.0,
            "billed_amount_myr":     0.0,
            "transaction_count":     0,
            "transaction_total_myr": 0.0,
            "transaction_ids":       [],
            "audit": {
                "billing_run_id": f"synth_{uuid.uuid4().hex[:12]}",
                "generated_at":   now,
                "generator":      _GENERATOR_RESOLVE,
            },
            "created_at": now,
        }
        if parent_account_id is not None:
            soi["parent_account_id"] = parent_account_id
        await self._coll.update_one(
            {"cycle_id": cycle_id},
            {"$setOnInsert": soi, "$set": {"updated_at": now}},
            upsert=True,
        )

    # --- Updates ------------------------------------------------------

    async def update_breakdown(
        self,
        cycle_id: str,
        *,
        set_fields: dict | None = None,
        inc_fields: dict | None = None,
    ) -> int:
        """Apply `$set` and / or `$inc` to a cycle doc.

        Returns 0 when both `set_fields` and `inc_fields` are empty —
        callers can use the truthy-zero return as a no-op signal.
        """
        if not set_fields and not inc_fields:
            return 0
        update: dict = {"$set": {**(set_fields or {}),
                                  "updated_at": datetime.now(timezone.utc)}}
        if inc_fields:
            update["$inc"] = inc_fields
        result = await self._coll.update_one({"cycle_id": cycle_id}, update)
        return result.modified_count

    async def append_status(self, cycle_id: str, status: str, *, by: str) -> int:
        """Push a status timeline entry and update `current_status`."""
        now = datetime.now(timezone.utc)
        result = await self._coll.update_one(
            {"cycle_id": cycle_id},
            {
                "$set":  {"current_status": status, "updated_at": now},
                "$push": {"status_timeline": {"at": now, "status": status, "by": by}},
            },
        )
        return result.modified_count

    async def record_transaction(
        self,
        cycle_id: str,
        *,
        transaction_id: str,
        total_myr: float,
    ) -> int:
        """Hot-path counter bump.

        `transaction_ids` uses `$addToSet` to dedupe, but `transaction_count`
        and `transaction_total_myr` are `$inc`-only — callers must guard
        with the same idempotency key as the transaction insert itself.
        """
        result = await self._coll.update_one(
            {"cycle_id": cycle_id},
            {
                "$addToSet": {"transaction_ids": transaction_id},
                "$inc": {
                    "transaction_count": 1,
                    "transaction_total_myr": float(total_myr or 0.0),
                },
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )
        return result.modified_count
