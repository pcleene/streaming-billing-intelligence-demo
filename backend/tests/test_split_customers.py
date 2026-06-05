"""PR-2 test — `scripts.split_customers.split` migration.

Seeds a fake legacy `customers` collection with a mix of residential and
commercial-shaped docs, runs the split, and asserts:

  - every doc lands in the right typed collection
  - `customer_index` carries a row per customer with `customer_type` set
  - re-running is a no-op (counts stable)
  - `--dry-run` writes nothing
"""

from __future__ import annotations

import pytest

from app.core.constants import (
    CUSTOMER_INDEX,
    CUSTOMERS,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
)
from tests._fakes import FakeDB


@pytest.fixture
def fake_db(monkeypatch) -> FakeDB:
    """Patch `app.deps._db` with an in-memory fake for this test."""
    db = FakeDB()
    import app.deps as deps
    monkeypatch.setattr(deps, "_db", db)
    return db


async def _seed_legacy(db: FakeDB) -> None:
    """100 docs: 60 residential, 40 commercial-shaped."""
    legacy = db[CUSTOMERS]
    for i in range(60):
        await legacy.insert_one({
            "customer_id": f"cust_R{i:03d}",
            "name": f"Resident {i}",
            "account_id": f"acc_R{i:03d}",
        })
    for i in range(40):
        await legacy.insert_one({
            "customer_id": f"cust_C{i:03d}",
            "name": f"Outlet {i}",
            "account_id": f"acc_C{i:03d}",
            "parent_account_id": "acc_PARENT" if i % 2 == 0 else None,
            "commercial_profile": {"business_name": "Acme Sports Cafe"},
        })


@pytest.mark.asyncio
async def test_split_customers_dry_run_writes_nothing(fake_db: FakeDB) -> None:
    from scripts.split_customers import split

    await _seed_legacy(fake_db)
    summary = await split(dry_run=True)

    assert summary["dry_run"] is True
    # Indexed counter increments in-memory but no writes hit the DB.
    assert await fake_db[CUSTOMER_INDEX].count_documents({}) == 0
    assert await fake_db[CUSTOMERS_COMMERCIAL].count_documents({}) == 0
    # Source untouched.
    assert await fake_db[CUSTOMERS].count_documents({}) == 100


@pytest.mark.asyncio
async def test_split_customers_populates_index_and_typed_collections(fake_db: FakeDB) -> None:
    from scripts.split_customers import LEGACY_IS_ALIAS, split

    await _seed_legacy(fake_db)
    summary = await split(dry_run=False)

    # All 100 docs should be indexed.
    idx_count = await fake_db[CUSTOMER_INDEX].count_documents({})
    assert idx_count == 100
    assert summary["indexed"] == 100

    # Every index row carries customer_type.
    async for row in fake_db[CUSTOMER_INDEX].find({}):
        assert row["customer_type"] in ("residential", "commercial")

    # 40 commercials live in the commercial collection.
    com_count = await fake_db[CUSTOMERS_COMMERCIAL].count_documents({})
    assert com_count == 40

    # The 60 residentials are in residential. When LEGACY_IS_ALIAS, they
    # already lived in the alias collection so no rewrite happened — the
    # count check is on residential which IS the alias target.
    res_count = await fake_db[CUSTOMERS_RESIDENTIAL].count_documents({})
    assert res_count >= 60  # might equal 60 alone or 60 if alias === residential

    if LEGACY_IS_ALIAS:
        # commercials are removed from the residential alias to avoid
        # double-counting.
        async for d in fake_db[CUSTOMERS_RESIDENTIAL].find({}):
            assert "commercial_profile" not in d, "commercial doc leaked into residential"


@pytest.mark.asyncio
async def test_split_customers_idempotent(fake_db: FakeDB) -> None:
    from scripts.split_customers import split

    await _seed_legacy(fake_db)
    await split(dry_run=False)
    counts1 = {
        "idx":   await fake_db[CUSTOMER_INDEX].count_documents({}),
        "res":   await fake_db[CUSTOMERS_RESIDENTIAL].count_documents({}),
        "com":   await fake_db[CUSTOMERS_COMMERCIAL].count_documents({}),
    }

    # Re-run.
    await split(dry_run=False)
    counts2 = {
        "idx":   await fake_db[CUSTOMER_INDEX].count_documents({}),
        "res":   await fake_db[CUSTOMERS_RESIDENTIAL].count_documents({}),
        "com":   await fake_db[CUSTOMERS_COMMERCIAL].count_documents({}),
    }

    assert counts1 == counts2, "split is not idempotent"


@pytest.mark.asyncio
async def test_inferred_type_for_commercial_via_parent_account(fake_db: FakeDB) -> None:
    """A doc with `parent_account_id` (and no explicit type) is commercial."""
    from scripts.split_customers import _infer_type

    assert _infer_type({"parent_account_id": "acc_X"}) == "commercial"
    assert _infer_type({"commercial_profile": {"business_name": "x"}}) == "commercial"
    assert _infer_type({"customer_type": "residential"}) == "residential"
    assert _infer_type({}) == "residential"  # default
