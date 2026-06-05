"""Transaction repository.

V3-only write path: `insert_extref(event, *, cache=None)` resolves cycle,
projects customer_summary, computes signals, persists the full
denormalised TransactionDocumentV3, and fans out to either
`customers_residential.recent_transactions` (capped, newest-first) or
`customers_commercial.txn_buckets` (bucket pattern, ≤500 per cycle).

Cycle resolution uses `bill_cycles` when present (PR-4 wires the create-
on-miss); when absent we synthesise a deterministic cycle_id keyed on
(customer_id, YYYYMM) so the demo path keeps working. Charge-code
validation is a no-op stub here; PR-5 wires the real cache. Both stubs
are isolated so PR-4/PR-5 can swap them without touching the V3 write
path.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from app.core.constants import (
    BILL_CYCLES,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
    SCHEMA_VERSION_V3,
    TRANSACTIONS,
)
from app.core.logging import get_logger
from app.repositories.base import BaseRepository
from app.repositories.bill_cycle_repo import BillCycleRepository
from app.repositories.transaction_helpers import (
    PPV_TYPES,
    build_recent_embed as _build_recent_embed,
    coerce_dt as _coerce_dt,
    compute_signals as _compute_signals,
    project_customer_summary as _project_customer_summary,
    resolve_cycle as _resolve_cycle,
    summarise_customer_pattern as _summarise_customer_pattern,
    utcnow as _utcnow,
)

logger = get_logger(__name__)

# Cap matches schemas.customer.RecentTransactionEmbed docstring (≤50).
RECENT_TXN_CAP = 50
# Per ADR-020 — alarm at 480, hard cap at 500 per cycle per outlet.
BUCKET_CAP = 500
BUCKET_ALARM = 480


def _primary_entity(customer: dict[str, Any]) -> str | None:
    """Pick the customer's primary entity for transactions that arrive
    untagged. Prefer ``entities[0]`` (already canonical-ordered by the
    customer pipeline); fall back to any single key in ``entity_profiles``
    so partially-shaped fixtures still group correctly.
    """
    entities = customer.get("entities")
    if isinstance(entities, list) and entities:
        first = entities[0]
        if isinstance(first, str) and first:
            return first
    profiles = customer.get("entity_profiles")
    if isinstance(profiles, dict) and profiles:
        for key in profiles.keys():
            if isinstance(key, str) and key:
                return key
    return None


# -----------------------------------------------------------------------
# Per-batch cache. Pass a single instance through a stream-batch loop so
# repeated customer / cycle lookups don't hammer Mongo. Tests construct
# one ad-hoc per assertion.
# -----------------------------------------------------------------------


class BatchCache:
    """Caches customer docs and cycle docs for the duration of one batch.

    Not safe for cross-task reuse. Construct fresh per stream-batch /
    per-test invocation. `charge_codes` is a `dict[str, dict] | None`
    populated by PR-5; until then unknown codes are logged as warnings
    and accepted.
    """

    def __init__(
        self,
        *,
        charge_codes: dict[str, dict] | None = None,
        cycle_repo: "BillCycleRepository | None" = None,
    ) -> None:
        self._customers: dict[str, dict | None] = {}
        self._cycles: dict[str, dict | None] = {}
        self._resolved_cycles: dict[str, tuple[str, dict]] = {}
        self._charge_codes = charge_codes
        self._cycle_repo = cycle_repo

    async def customer(
        self,
        coll_residential,
        coll_commercial,
        customer_id: str,
    ) -> dict | None:
        """Find the customer in either typed collection.

        Returns the doc + injects `_resolved_type` into the cache value.
        """
        if customer_id in self._customers:
            return self._customers[customer_id]
        # Try residential first (most common); fall back to commercial.
        doc = await coll_residential.find_one({"customer_id": customer_id})
        ctype = "residential"
        if doc is None:
            doc = await coll_commercial.find_one({"customer_id": customer_id})
            ctype = "commercial" if doc else None
        if doc is not None:
            doc.setdefault("_resolved_type", ctype)
        self._customers[customer_id] = doc
        return doc

    async def cycle(
        self,
        coll_cycles,
        customer_id: str,
        ts: datetime,
    ) -> dict | None:
        """Find the bill_cycle whose anchors contain `ts`.

        Read-only cache lookup; doesn't create on miss. Use
        `resolve_cycle` for the create-on-miss path.
        """
        key = f"{customer_id}|{ts.strftime('%Y%m')}"
        if key in self._cycles:
            return self._cycles[key]
        doc = await coll_cycles.find_one({
            "customer_id": customer_id,
            "cycle_anchors.cycle_start": {"$lte": ts},
            "cycle_anchors.cycle_end": {"$gte": ts},
        })
        self._cycles[key] = doc
        return doc

    async def resolve_cycle(
        self,
        coll_cycles,
        customer_id: str,
        ts: datetime,
        *,
        customer: dict | None = None,
    ) -> tuple[str, dict]:
        """Resolve `(cycle_id, bill_period)` for `ts`.

        Prefers the wired `BillCycleRepository.resolve_cycle` (creates a
        cycle on miss). Falls back to a read-only lookup +
        deterministic synth when no repo is present — keeps PR-3 tests
        and pre-PR-4 deployments working.
        """
        key = f"{customer_id}|{ts.strftime('%Y%m')}"
        if key in self._resolved_cycles:
            return self._resolved_cycles[key]
        if self._cycle_repo is not None:
            cycle_id, bill_period = await self._cycle_repo.resolve_cycle(
                customer_id, ts, customer=customer
            )
        else:
            cycle_doc = await self.cycle(coll_cycles, customer_id, ts)
            cycle_id, bill_period = _resolve_cycle(cycle_doc, customer_id, ts)
        self._resolved_cycles[key] = (cycle_id, bill_period)
        return cycle_id, bill_period

    def validate_charge_code(self, code: str | None) -> bool:
        if code is None:
            return False
        if self._charge_codes is not None:
            # Test path: a per-batch dict was injected. Honour it verbatim.
            return code in self._charge_codes
        # PR-5 — fall through to the process-local catalog cache. When the
        # cache has not yet been loaded (cold start, unit-test scope), it
        # returns True so the write path doesn't block. Callers still log
        # an `unknown_charge_code` warning at the call site.
        from app.services.charge_code_cache import charge_code_cache
        return charge_code_cache.validate(code)


# -----------------------------------------------------------------------


class TransactionRepository(BaseRepository):
    COLLECTION_NAME = TRANSACTIONS

    def __init__(self, db, *, cycle_repo: BillCycleRepository | None = None) -> None:
        super().__init__(db)
        # Hot-path collection handles for the V3 fan-out.
        self._coll_residential = db[CUSTOMERS_RESIDENTIAL]
        self._coll_commercial = db[CUSTOMERS_COMMERCIAL]
        self._coll_cycles = db[BILL_CYCLES]
        # PR-4: bill cycle resolution. The repo is optional for tests
        # that only exercise the synth-fallback path.
        self._cycle_repo = cycle_repo or BillCycleRepository(db)

    # --- Reads (unchanged from PR-2) ----------------------------------

    async def get_by_id(self, transaction_id: str) -> dict | None:
        return await self.find_one({"transaction_id": transaction_id})

    async def list_for_customer(
        self, customer_id: str, *, limit: int = 50
    ) -> list[dict]:
        return await self.find_many(
            {"customer_id": customer_id},
            sort=[("timestamp", -1)],
            limit=limit,
        )

    async def list_recent(self, *, limit: int = 1000) -> list[dict]:
        return await self.find_many(
            {},
            sort=[("timestamp", -1)],
            limit=limit,
        )

    async def list_for_window(self, *, since: datetime, limit: int = 5000) -> list[dict]:
        return await self.find_many(
            {"timestamp": {"$gte": since}},
            sort=[("timestamp", -1)],
            limit=limit,
        )

    async def amount_stats_for_customer(
        self, customer_id: str, *, lookback_days: int
    ) -> dict | None:
        since = _utcnow() - timedelta(days=lookback_days)
        pipeline = [
            {"$match": {"customer_id": customer_id, "timestamp": {"$gte": since}}},
            {"$group": {
                "_id": None,
                "mean": {"$avg": "$total_myr"},
                "stddev": {"$stdDevSamp": "$total_myr"},
                "count": {"$sum": 1},
            }},
            {"$project": {"_id": 0, "mean": 1, "stddev": 1, "count": 1}},
        ]
        results = await self.aggregate(pipeline)
        return results[0] if results else None

    async def customer_pattern(
        self, customer_id: str, *, days: int = 30
    ) -> dict:
        """Aggregate the customer's recent transaction pattern.

        Returns:
            ``{
                "customer_id": str,
                "window_days": int,
                "txn_count": int,
                "amount_mean_myr": float,
                "amount_stddev_myr": float,
                "top_charge_codes": [{"charge_code": str, "count": int}, ...],
                "first_txn_at": datetime | None,
                "last_txn_at": datetime | None,
            }``

        Empty windows return zeros / empty lists rather than ``None``
        so the agent's analytics node has a safe shape to format.

        Implementation note: PR-AG agent path. Uses ``find_many`` +
        Python aggregation rather than a Mongo ``$facet`` so the FakeDB
        unit tests don't need an aggregation engine. Production reads
        are bounded by the ``timestamp`` window + ``customer_id`` index.
        Amount stats prefer ``total_myr``, falling back to legacy ``amount``.
        """
        since = _utcnow() - timedelta(days=days)
        rows = await self.find_many(
            {"customer_id": customer_id, "timestamp": {"$gte": since}},
        )
        return _summarise_customer_pattern(
            rows, customer_id=customer_id, window_days=days
        )

    async def mark_quarantined(self, transaction_id: str, case_id: str) -> int:
        return await self.update_one(
            {"transaction_id": transaction_id},
            {"$set": {"quarantined": True}, "$addToSet": {"quarantine_case_ids": case_id}},
        )

    # --- V3 extended-reference write path -----------------------------

    async def insert_extref(
        self,
        event: dict,
        *,
        cache: BatchCache | None = None,
    ) -> dict:
        """Persist an event in the V3 extended-reference shape.

        `event` is the simulator/event-bus payload (see schemas.transaction
        TransactionEvent shape, optionally extended with `items`,
        `transaction_type`, `entity`, `metadata`). This method:

        1. Resolves customer + cycle (cache-friendly).
        2. Projects `customer_summary` from the current customer doc.
        3. Computes `computed_signals` from cached + event-local data.
        4. Persists the full V3 transaction doc.
        5. Fans out to `recent_transactions` (residential) or
           `txn_buckets` (commercial outlet).

        Raises ``KeyError`` if the customer is unknown — data-integrity
        issue surfaced, no stub creation.
        """
        cache = cache or BatchCache(cycle_repo=self._cycle_repo)
        # If a caller provided a cache without a cycle_repo, inject ours.
        if cache._cycle_repo is None:
            cache._cycle_repo = self._cycle_repo

        cid = event["customer_id"]
        ts = _coerce_dt(event["timestamp"])
        customer = await cache.customer(
            self._coll_residential, self._coll_commercial, cid
        )
        if customer is None:
            logger.warning("insert_extref_unknown_customer", customer_id=cid)
            raise KeyError(f"unknown customer_id: {cid}")
        ctype = customer.get("_resolved_type") or customer.get("customer_type") or "residential"

        cycle_id, bill_period = await cache.resolve_cycle(
            self._coll_cycles, cid, ts, customer=customer
        )

        # Items / charge codes ------------------------------------------------
        items = list(event.get("items") or [])
        for item in items:
            code = item.get("charge_code")
            if not cache.validate_charge_code(code):
                logger.warning(
                    "unknown_charge_code",
                    transaction_id=event.get("transaction_id"),
                    charge_code=code,
                )

        subtotal = sum(float(i.get("line_total_myr") or 0.0) for i in items)
        total_discount = sum(float(d.get("amount_myr") or 0.0) for d in (event.get("discounts") or []))
        tax = event.get("tax") or {"rate": 0.0, "name": "SST", "amount_myr": 0.0}
        total = subtotal - total_discount + float(tax.get("amount_myr") or 0.0)
        if not items:
            # Allow simple events (e.g. pre-PR-3 simulator output) to flow.
            total = float(event.get("total_myr") or event.get("amount") or 0.0)
            subtotal = total

        # Customer summary projection ----------------------------------------
        summary = _project_customer_summary(customer, ctype)

        # Signals ------------------------------------------------------------
        signals = _compute_signals(event, customer, items, total)

        doc: dict[str, Any] = {
            "_schema_version": SCHEMA_VERSION_V3,
            "transaction_id": event["transaction_id"],
            "customer_id": cid,
            "customer_type": ctype,
            "account_id": customer.get("account_id") or f"acc_{cid}",
            "outlet_id": customer.get("outlet_id"),
            "parent_account_id": customer.get("parent_account_id"),
            "cycle_id": cycle_id,
            "bill_period": bill_period,
            "timestamp": ts,
            "transaction_type": event.get("transaction_type") or "subscription_charge",
            "channel": event.get("channel") or "system",
            # Carry the event's `entity` when present; otherwise fall back
            # to the customer's primary entity so per-entity dashboards
            # ("Cross-entity net new revenue") aren't starved when the
            # simulator/upstream doesn't tag events.
            "entity": event.get("entity") or _primary_entity(customer),
            "merchant_id": event.get("merchant_id") or "acme-direct",
            "currency": event.get("currency", "MYR"),
            "location": event.get("location") or {},
            "customer_summary": summary,
            "items": items,
            "subtotal_myr": round(subtotal, 2),
            "discounts": event.get("discounts") or [],
            "total_discount_myr": round(total_discount, 2),
            "tax": tax,
            "total_myr": round(total, 2),
            "payment": event.get("payment"),
            "loyalty": event.get("loyalty"),
            "quarantined": False,
            "quarantine_case_ids": [],
            "campaign_attribution": event.get("campaign_attribution"),
            "is_returned": bool(event.get("is_returned", False)),
            "is_refund": event.get("transaction_type") == "refund",
            "computed_signals": signals,
            "_ingested_at": _utcnow(),
            "_source_topic": event.get("_source_topic"),
            "_source_partition": event.get("_source_partition"),
            "_source_offset": event.get("_source_offset"),
            "_event_id": event.get("_event_id") or f"evt_{uuid.uuid4().hex[:16]}",
        }

        await self._coll.insert_one(doc)
        await self._fan_out(doc, customer, ctype)
        return doc

    async def _fan_out(self, doc: dict, customer: dict, ctype: str) -> int:
        """Residential → recent_transactions; Commercial → txn_buckets."""
        embed = _build_recent_embed(doc)
        if ctype == "commercial":
            return await self._push_to_bucket(doc, customer, embed)
        return await self._push_to_recent(doc, ctype)

    async def _push_to_recent(self, doc: dict, ctype: str) -> int:
        coll = self._coll_residential if ctype == "residential" else self._coll
        result = await coll.update_one(
            {"customer_id": doc["customer_id"]},
            {"$push": {
                "recent_transactions": {
                    "$each": [_build_recent_embed(doc)],
                    "$position": 0,
                    "$slice": RECENT_TXN_CAP,
                }
            }},
        )
        return result.modified_count

    async def _push_to_bucket(
        self, doc: dict, customer: dict, embed: dict
    ) -> int:
        """Bucket-pattern fan-out for commercial outlets.

        Per ADR-020, append into the open bucket for the doc's cycle; if
        no open bucket exists or the current one is at cap, allocate a
        new bucket. Implementation uses read-modify-replace because
        positional updates over array-of-arrays are awkward to fake;
        production code may revisit for atomicity (see follow-up).
        """
        cycle_id = doc["cycle_id"]
        bp = doc["bill_period"]
        is_ppv = doc["transaction_type"] in PPV_TYPES
        amount = float(doc.get("total_myr") or 0.0)

        buckets = list(customer.get("txn_buckets") or [])
        target_idx: int | None = None
        for idx, b in enumerate(buckets):
            if (
                b.get("cycle_id") == cycle_id
                and (b.get("transaction_count") or 0) < BUCKET_CAP
            ):
                target_idx = idx
                break

        if target_idx is None:
            new_bucket = {
                "bucket_id": f"bkt_{cycle_id}_{len(buckets) + 1}",
                "cycle_id": cycle_id,
                "cycle_start": bp["start"],
                "cycle_end": bp["end"],
                "transaction_count": 1,
                "ppv_count": 1 if is_ppv else 0,
                "total_myr": round(amount, 2),
                "transactions": [embed],
            }
            buckets.append(new_bucket)
        else:
            b = buckets[target_idx]
            b["transactions"] = list(b.get("transactions") or []) + [embed]
            b["transaction_count"] = (b.get("transaction_count") or 0) + 1
            b["ppv_count"] = (b.get("ppv_count") or 0) + (1 if is_ppv else 0)
            b["total_myr"] = round((b.get("total_myr") or 0.0) + amount, 2)
            if b["transaction_count"] >= BUCKET_ALARM:
                logger.warning(
                    "txn_bucket_near_cap",
                    customer_id=doc["customer_id"],
                    bucket_id=b["bucket_id"],
                    count=b["transaction_count"],
                )
            buckets[target_idx] = b

        # Keep our cached customer doc in sync so subsequent insertions
        # within the same batch see the updated bucket counts.
        customer["txn_buckets"] = buckets

        result = await self._coll_commercial.update_one(
            {"customer_id": doc["customer_id"]},
            {"$set": {"txn_buckets": buckets}},
        )
        return result.modified_count
