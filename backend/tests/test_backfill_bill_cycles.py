"""PR-4 — backfill_bill_cycles idempotency + shape tests.

Seeds the in-memory FakeDB with a residential and a commercial
customer, runs the backfill, and asserts:
  * trailing N=3 cycles are seeded per customer
  * dry-run leaves bill_cycles untouched
  * a second run upgrades zero additional rows (idempotent via
    `cycles_skipped`)
  * commercial customers receive `customer_type=commercial` cycles
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.constants import (
    BILL_CYCLES,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
)
from app import deps
from scripts import backfill_bill_cycles as bf
from tests._fakes import FakeDB

_ANCHOR = datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_with_customers(monkeypatch: pytest.MonkeyPatch) -> FakeDB:
    db = FakeDB()
    db[CUSTOMERS_RESIDENTIAL]._docs.append({  # type: ignore[attr-defined]
        "customer_id": "cust_r1",
        "customer_type": "residential",
        "account_id": "acc_r1",
        "name": "Aiman",
    })
    db[CUSTOMERS_COMMERCIAL]._docs.append({  # type: ignore[attr-defined]
        "customer_id": "cust_C1",
        "customer_type": "commercial",
        "account_id": "acc_C1",
        "parent_account_id": "parent_C1",
        "name": "Acme Sports",
    })
    monkeypatch.setattr(deps, "_db", db)
    return db


@pytest.mark.asyncio
async def test_backfill_dry_run_does_not_write(
    db_with_customers: FakeDB,
) -> None:
    stats = await bf.backfill(dry_run=True, months=3, anchor=_ANCHOR)
    assert stats["total_customers"] == 2
    assert stats["total_cycles_seeded"] == 6
    cycles = db_with_customers[BILL_CYCLES]._docs  # type: ignore[attr-defined]
    assert cycles == []  # nothing actually written


@pytest.mark.asyncio
async def test_backfill_seeds_trailing_cycles_per_customer(
    db_with_customers: FakeDB,
) -> None:
    stats = await bf.backfill(dry_run=False, months=3, anchor=_ANCHOR)
    assert stats["residential"]["customers"] == 1
    assert stats["commercial"]["customers"] == 1
    assert stats["total_cycles_seeded"] == 6

    cycles = db_with_customers[BILL_CYCLES]._docs  # type: ignore[attr-defined]
    assert len(cycles) == 6
    by_customer = {}
    for c in cycles:
        by_customer.setdefault(c["customer_id"], []).append(c)
    assert sorted(by_customer.keys()) == ["cust_C1", "cust_r1"]
    assert all(c["customer_type"] == "commercial" for c in by_customer["cust_C1"])
    assert all(c["customer_type"] == "residential" for c in by_customer["cust_r1"])
    # Per-customer cycles are distinct months.
    for cid, docs in by_customer.items():
        ids = {d["cycle_id"] for d in docs}
        assert len(ids) == 3, f"{cid} should have 3 distinct cycles, got {len(ids)}"


@pytest.mark.asyncio
async def test_backfill_is_idempotent(db_with_customers: FakeDB) -> None:
    first = await bf.backfill(dry_run=False, months=3, anchor=_ANCHOR)
    assert first["total_cycles_seeded"] == 6
    second = await bf.backfill(dry_run=False, months=3, anchor=_ANCHOR)
    assert second["total_cycles_seeded"] == 0
    assert second["total_cycles_skipped"] == 6
    cycles = db_with_customers[BILL_CYCLES]._docs  # type: ignore[attr-defined]
    assert len(cycles) == 6
