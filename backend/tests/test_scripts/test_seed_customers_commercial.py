"""PR-14 — tests for `scripts/seed_customers_commercial.py`.

Drives the script's `main` directly with a FakeDB so no live Mongo is
needed. Asserts spec invariants: parent count, outlet bounds, parent
linkage, customer_index coverage, idempotency, and seed-marker stamping.
"""

from __future__ import annotations

import pytest

from app.core.constants import CUSTOMER_INDEX, CUSTOMERS_COMMERCIAL
from scripts import seed_customers_commercial as scc
from tests._fakes import FakeCollection, FakeDB, _DeleteResult, _matches


# --- Bulk-op shim (mirrors test_seed_quarantine_cases.py) -------------


async def _delete_many(self: FakeCollection, flt: dict) -> _DeleteResult:
    keep: list[dict] = []
    removed = 0
    for d in self._docs:
        if _matches(d, flt or {}):
            removed += 1
        else:
            keep.append(d)
    self._docs = keep
    return _DeleteResult(removed)


@pytest.fixture(autouse=True)
def _patch_fake_bulk_ops(monkeypatch):
    monkeypatch.setattr(
        FakeCollection, "delete_many", _delete_many, raising=False
    )
    yield


@pytest.mark.asyncio
async def test_parent_count_matches_request() -> None:
    db = FakeDB()
    counters = await scc.main(db, parent_count=5)
    parents = [
        d for d in db[CUSTOMERS_COMMERCIAL]._docs
        if d.get("parent_account_id") is None
    ]
    assert len(parents) == 5
    assert counters["parents_inserted"] == 5


@pytest.mark.asyncio
async def test_each_parent_has_at_least_min_outlets() -> None:
    db = FakeDB()
    await scc.main(
        db, parent_count=5, outlets_per_parent_min=2, outlets_per_parent_max=4
    )
    docs = db[CUSTOMERS_COMMERCIAL]._docs
    parents = [d for d in docs if d.get("parent_account_id") is None]
    for parent in parents:
        outlets = [
            d for d in docs
            if d.get("parent_account_id") == parent["account_id"]
        ]
        assert 2 <= len(outlets) <= 4


@pytest.mark.asyncio
async def test_each_outlet_has_correct_parent_account_id() -> None:
    db = FakeDB()
    await scc.main(db, parent_count=3)
    docs = db[CUSTOMERS_COMMERCIAL]._docs
    parent_account_ids = {
        d["account_id"] for d in docs if d.get("parent_account_id") is None
    }
    outlets = [d for d in docs if d.get("parent_account_id") is not None]
    assert outlets, "expected at least one outlet seeded"
    for outlet in outlets:
        assert outlet["parent_account_id"] in parent_account_ids
        assert outlet["customer_type"] == "commercial"
        assert outlet["outlet_id"].startswith("OUT_")


@pytest.mark.asyncio
async def test_every_seeded_customer_has_index_row() -> None:
    db = FakeDB()
    counters = await scc.main(db, parent_count=4)
    customer_ids = {d["customer_id"] for d in db[CUSTOMERS_COMMERCIAL]._docs}
    index_ids = {d["customer_id"] for d in db[CUSTOMER_INDEX]._docs}
    assert customer_ids == index_ids
    assert counters["customer_index_inserted"] == len(customer_ids)
    # parents_inserted + outlets_inserted == total customers.
    assert (
        counters["parents_inserted"] + counters["outlets_inserted"]
        == len(customer_ids)
    )


@pytest.mark.asyncio
async def test_commercial_profile_outlet_count_matches_actual() -> None:
    db = FakeDB()
    await scc.main(db, parent_count=5)
    docs = db[CUSTOMERS_COMMERCIAL]._docs
    parents = [d for d in docs if d.get("parent_account_id") is None]
    for parent in parents:
        actual = sum(
            1 for d in docs
            if d.get("parent_account_id") == parent["account_id"]
        )
        assert parent["commercial_profile"]["outlet_count"] == actual
        # entities array mirrors the outlet roster exactly.
        assert len(parent["entities"]) == actual
        for ent in parent["entities"]:
            assert ent["entity_type"] == "outlet"


@pytest.mark.asyncio
async def test_all_seeded_docs_carry_pr14_marker() -> None:
    db = FakeDB()
    await scc.main(db, parent_count=3)
    for d in db[CUSTOMERS_COMMERCIAL]._docs:
        assert d["_seed_marker"] == "pr14"
    for d in db[CUSTOMER_INDEX]._docs:
        assert d["_seed_marker"] == "pr14"


@pytest.mark.asyncio
async def test_idempotent_rerun_produces_same_counts() -> None:
    db = FakeDB()
    first = await scc.main(db, parent_count=4)
    first_total = (
        len(db[CUSTOMERS_COMMERCIAL]._docs),
        len(db[CUSTOMER_INDEX]._docs),
    )
    second = await scc.main(db, parent_count=4)
    second_total = (
        len(db[CUSTOMERS_COMMERCIAL]._docs),
        len(db[CUSTOMER_INDEX]._docs),
    )
    assert first_total == second_total, (
        "Re-running with the same seed must not duplicate rows "
        "(idempotent delete-by-marker)"
    )
    assert first == second
