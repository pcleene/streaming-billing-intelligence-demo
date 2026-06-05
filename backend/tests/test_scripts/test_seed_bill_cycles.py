"""PR-14 — tests for `scripts/seed_bill_cycles.py`.

Drives `main` directly with a FakeDB. Asserts cycles-per-customer,
status distribution, cycle_id uniqueness, idempotent re-runs (upsert
semantics), and that residential customers are covered when present.
"""

from __future__ import annotations

import pytest

from app.core.constants import (
    BILL_CYCLES,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
)
from scripts import seed_bill_cycles as sbc
from tests._fakes import FakeDB


def _seed_some_customers(db: FakeDB) -> None:
    db[CUSTOMERS_RESIDENTIAL]._docs.append({
        "customer_id": "cust_r1",
        "customer_type": "residential",
    })
    db[CUSTOMERS_RESIDENTIAL]._docs.append({
        "customer_id": "cust_r2",
        "customer_type": "residential",
    })
    db[CUSTOMERS_COMMERCIAL]._docs.append({
        "customer_id": "comm_parent_01",
        "customer_type": "commercial",
        "parent_account_id": None,
    })


@pytest.mark.asyncio
async def test_three_cycles_per_customer() -> None:
    db = FakeDB()
    _seed_some_customers(db)
    counters = await sbc.main(db, cycles_per_customer=3)
    assert counters["customers_covered"] == 3
    assert counters["cycles_upserted"] == 9
    cycles = db[BILL_CYCLES]._docs
    assert len(cycles) == 9
    by_cust: dict[str, list[dict]] = {}
    for c in cycles:
        by_cust.setdefault(c["customer_id"], []).append(c)
    for cid, docs in by_cust.items():
        assert len(docs) == 3, f"{cid} should have 3 cycles, got {len(docs)}"


@pytest.mark.asyncio
async def test_status_distribution_one_open_two_closed() -> None:
    db = FakeDB()
    _seed_some_customers(db)
    await sbc.main(db, cycles_per_customer=3)
    cycles = db[BILL_CYCLES]._docs
    by_cust: dict[str, list[dict]] = {}
    for c in cycles:
        by_cust.setdefault(c["customer_id"], []).append(c)
    for cid, docs in by_cust.items():
        opens = [d for d in docs if d["status"] == "open"]
        closeds = [d for d in docs if d["status"] == "closed"]
        assert len(opens) == 1, f"{cid} expected 1 open, got {len(opens)}"
        assert len(closeds) == 2, (
            f"{cid} expected 2 closed, got {len(closeds)}"
        )


@pytest.mark.asyncio
async def test_cycle_ids_are_unique() -> None:
    db = FakeDB()
    _seed_some_customers(db)
    await sbc.main(db, cycles_per_customer=3)
    cycles = db[BILL_CYCLES]._docs
    ids = [c["cycle_id"] for c in cycles]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_idempotent_rerun_upserts_no_dupes() -> None:
    db = FakeDB()
    _seed_some_customers(db)
    first = await sbc.main(db, cycles_per_customer=3)
    first_count = len(db[BILL_CYCLES]._docs)
    second = await sbc.main(db, cycles_per_customer=3)
    second_count = len(db[BILL_CYCLES]._docs)
    assert first_count == second_count == 9
    # Same cycles_upserted (script touches each row); but no new rows created.
    assert first["customers_covered"] == second["customers_covered"]
    # Every doc still carries the seed marker.
    for d in db[BILL_CYCLES]._docs:
        assert d["_seed_marker"] == "pr14"


@pytest.mark.asyncio
async def test_residential_customer_is_covered_when_present() -> None:
    db = FakeDB()
    _seed_some_customers(db)
    await sbc.main(db, cycles_per_customer=3)
    cust_ids = {c["customer_id"] for c in db[BILL_CYCLES]._docs}
    assert "cust_r1" in cust_ids
    assert "cust_r2" in cust_ids
    assert "comm_parent_01" in cust_ids


@pytest.mark.asyncio
async def test_synthetic_fallback_when_collections_empty() -> None:
    db = FakeDB()
    counters = await sbc.main(db, cycles_per_customer=3)
    # Falls back to 3 synthetic customers.
    assert counters["customers_covered"] == 3
    assert counters["cycles_upserted"] == 9
    cust_ids = {c["customer_id"] for c in db[BILL_CYCLES]._docs}
    for cid in cust_ids:
        assert cid.startswith("cust_synth_")
