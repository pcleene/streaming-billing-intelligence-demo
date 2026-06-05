"""PR-4 — bill_cycles repository unit tests.

Verifies:
  * `resolve_cycle` returns the existing cycle when one covers `ts`
  * `resolve_cycle` creates a new cycle on miss (idempotent — repeated
    calls for the same (customer_id, ts) return the same cycle_id and
    don't duplicate the doc)
  * adjacent calendar months get distinct cycles
  * `update_breakdown` applies $set / $inc selectively
  * `record_transaction` $addToSet's transaction_ids and $inc's counters
  * `append_status` appends to `status_timeline` and updates
    `current_status`
  * `trailing_cycles_for_customer` produces N distinct monthly cycles,
    correct statuses, and carries account metadata
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.constants import BILL_CYCLES
from app.repositories.bill_cycle_repo import (
    BillCycleRepository,
    cycle_id_for,
    trailing_cycles_for_customer,
)
from tests._fakes import FakeDB

_TS_MAY = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
_TS_JUN = datetime(2026, 6, 3, 9, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def repo(db: FakeDB) -> BillCycleRepository:
    return BillCycleRepository(db)


def _customer(cid: str = "cust_r1") -> dict:
    return {
        "customer_id": cid,
        "customer_type": "residential",
        "account_id": f"acc_{cid}",
        "billing_day_of_month": 5,
    }


# --- resolve_cycle ---------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_cycle_creates_when_missing(
    repo: BillCycleRepository, db: FakeDB
) -> None:
    customer = _customer()
    cycle_id, bill_period = await repo.resolve_cycle(
        customer["customer_id"], _TS_MAY, customer=customer
    )
    assert cycle_id == cycle_id_for(customer["customer_id"], _TS_MAY)
    assert cycle_id == "cyc_cust_r1_202605"
    assert bill_period["start"].month == 5
    assert bill_period["end"].month == 5
    docs = db[BILL_CYCLES]._docs  # type: ignore[attr-defined]
    assert len(docs) == 1
    assert docs[0]["current_status"] == "open"
    assert docs[0]["status_timeline"][0]["by"] == "bill_cycle_repo.resolve"
    assert docs[0]["audit"]["generator"] == "bill_cycle_repo.resolve"


@pytest.mark.asyncio
async def test_resolve_cycle_is_idempotent(
    repo: BillCycleRepository, db: FakeDB
) -> None:
    customer = _customer()
    a, _ = await repo.resolve_cycle("cust_r1", _TS_MAY, customer=customer)
    b, _ = await repo.resolve_cycle("cust_r1", _TS_MAY, customer=customer)
    c, _ = await repo.resolve_cycle(
        "cust_r1", _TS_MAY.replace(day=22), customer=customer
    )
    assert a == b == c
    docs = db[BILL_CYCLES]._docs  # type: ignore[attr-defined]
    assert len(docs) == 1


@pytest.mark.asyncio
async def test_resolve_cycle_distinguishes_months(
    repo: BillCycleRepository, db: FakeDB
) -> None:
    customer = _customer()
    may, _ = await repo.resolve_cycle("cust_r1", _TS_MAY, customer=customer)
    jun, _ = await repo.resolve_cycle("cust_r1", _TS_JUN, customer=customer)
    assert may != jun
    assert may.endswith("_202605")
    assert jun.endswith("_202606")
    docs = db[BILL_CYCLES]._docs  # type: ignore[attr-defined]
    assert len(docs) == 2


@pytest.mark.asyncio
async def test_resolve_cycle_returns_existing_doc(
    repo: BillCycleRepository, db: FakeDB
) -> None:
    customer = _customer()
    cycle_id, _ = await repo.resolve_cycle(
        "cust_r1", _TS_MAY, customer=customer
    )
    await repo.update_breakdown(cycle_id, set_fields={"billed_amount_myr": 199.0})
    again_id, _ = await repo.resolve_cycle(
        "cust_r1", _TS_MAY, customer=customer
    )
    assert again_id == cycle_id
    fetched = await repo.get(cycle_id)
    assert fetched is not None
    assert fetched["billed_amount_myr"] == 199.0


@pytest.mark.asyncio
async def test_resolve_cycle_no_create_when_disabled(
    repo: BillCycleRepository, db: FakeDB
) -> None:
    customer = _customer()
    cycle_id, _ = await repo.resolve_cycle(
        "cust_r1", _TS_MAY, customer=customer, create_on_miss=False
    )
    assert cycle_id == "cyc_cust_r1_202605"
    docs = db[BILL_CYCLES]._docs  # type: ignore[attr-defined]
    assert docs == []


# --- update_breakdown / record_transaction --------------------------


@pytest.mark.asyncio
async def test_update_breakdown_set_and_inc(
    repo: BillCycleRepository, db: FakeDB
) -> None:
    customer = _customer()
    cycle_id, _ = await repo.resolve_cycle("cust_r1", _TS_MAY, customer=customer)

    await repo.update_breakdown(
        cycle_id,
        set_fields={
            "billed_amount_myr": 750.0,
            "billed_breakdown": {"subscription_myr": 199.0, "ppv_myr": 551.0},
            "variance_myr": 12.5,
            "variance_pct": 1.7,
        },
    )
    await repo.record_transaction(cycle_id, transaction_id="txn_a", total_myr=750.0)
    await repo.record_transaction(cycle_id, transaction_id="txn_b", total_myr=49.9)
    await repo.record_transaction(cycle_id, transaction_id="txn_c", total_myr=0.0)

    doc = await repo.get(cycle_id)
    assert doc is not None
    assert doc["billed_amount_myr"] == 750.0
    assert doc["billed_breakdown"]["subscription_myr"] == 199.0
    assert doc["variance_myr"] == 12.5
    assert doc["variance_pct"] == 1.7
    assert doc["transaction_count"] == 3
    assert doc["transaction_total_myr"] == pytest.approx(799.9)
    assert set(doc["transaction_ids"]) == {"txn_a", "txn_b", "txn_c"}


@pytest.mark.asyncio
async def test_record_transaction_addtoset_dedupes(
    repo: BillCycleRepository, db: FakeDB
) -> None:
    customer = _customer()
    cycle_id, _ = await repo.resolve_cycle("cust_r1", _TS_MAY, customer=customer)
    await repo.record_transaction(cycle_id, transaction_id="txn_x", total_myr=10.0)
    await repo.record_transaction(cycle_id, transaction_id="txn_x", total_myr=10.0)
    doc = await repo.get(cycle_id)
    assert doc is not None
    assert doc["transaction_ids"] == ["txn_x"]


@pytest.mark.asyncio
async def test_update_breakdown_noop_when_empty(
    repo: BillCycleRepository, db: FakeDB
) -> None:
    customer = _customer()
    cycle_id, _ = await repo.resolve_cycle("cust_r1", _TS_MAY, customer=customer)
    modified = await repo.update_breakdown(cycle_id)
    assert modified == 0


# --- append_status --------------------------------------------------


@pytest.mark.asyncio
async def test_append_status_appends_timeline(
    repo: BillCycleRepository, db: FakeDB
) -> None:
    customer = _customer()
    cycle_id, _ = await repo.resolve_cycle("cust_r1", _TS_MAY, customer=customer)
    await repo.append_status(cycle_id, "billed", by="billing_engine_v2")
    await repo.append_status(cycle_id, "closed", by="billing_engine_v2")
    doc = await repo.get(cycle_id)
    assert doc is not None
    assert doc["current_status"] == "closed"
    statuses = [e["status"] for e in doc["status_timeline"]]
    assert statuses == ["open", "billed", "closed"]


# --- trailing_cycles_for_customer -----------------------------------


def test_trailing_cycles_for_customer_yields_n_distinct_with_statuses() -> None:
    customer = {
        "customer_id": "cust_r1",
        "customer_type": "residential",
        "account_id": "acc_r1",
        "billing_day_of_month": 1,
    }
    anchor = datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc)
    cycles = trailing_cycles_for_customer(customer, count=12, anchor=anchor)
    assert len(cycles) == 12
    ids = {c["cycle_id"] for c in cycles}
    assert len(ids) == 12  # all distinct
    months = sorted({c["cycle_anchors"]["cycle_start"].strftime("%Y%m") for c in cycles})
    assert months[0] == "202506"   # 12 trailing months ending 2026-05
    assert months[-1] == "202605"
    # Status: anchor month is "open"; older months are "billed".
    statuses_by_month = {
        c["cycle_anchors"]["cycle_start"].strftime("%Y%m"): c["current_status"]
        for c in cycles
    }
    assert statuses_by_month["202605"] == "open"
    for m in months[:-1]:
        assert statuses_by_month[m] == "billed"


def test_trailing_cycles_for_customer_carries_account_metadata() -> None:
    customer = {
        "customer_id": "cust_C1",
        "customer_type": "commercial",
        "account_id": "acc_C1",
        "parent_account_id": "parent_C1",
    }
    anchor = datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc)
    cycles = trailing_cycles_for_customer(customer, count=3, anchor=anchor)
    for c in cycles:
        assert c["customer_type"] == "commercial"
        assert c["account_id"] == "acc_C1"
        assert c["parent_account_id"] == "parent_C1"
        assert c["audit"]["generator"] == "scripts.backfill_bill_cycles"
