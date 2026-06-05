"""PR-2 test — type-aware dispatch through `customer_index`.

With `flags.STORAGE_SPLIT=True`:
  - `CustomerService.get_360("cust_R1")` returns the residential doc
    without the caller specifying `customer_type`.
  - `CustomerService.get_360("cust_C1")` returns the commercial doc.
  - When the index has no row, the call falls back to the legacy
    collection (preserves pre-PR-2 behaviour during partial migrations).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.constants import (
    CUSTOMER_INDEX,
    CUSTOMERS,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
)
from app.core.feature_flags import flags
from app.repositories.customer_repo import CustomerRepository
from app.services.customer_service import CustomerService
from tests._fakes import FakeDB


_NOW = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def storage_split_on(monkeypatch):
    monkeypatch.setattr(flags, "STORAGE_SPLIT", True)


@pytest.fixture
def storage_split_off(monkeypatch):
    monkeypatch.setattr(flags, "STORAGE_SPLIT", False)


def _make_service(db: FakeDB) -> CustomerService:
    return CustomerService(CustomerRepository(db))


@pytest.mark.asyncio
async def test_get_360_dispatches_residential_via_index(storage_split_on) -> None:
    db = FakeDB()
    await db[CUSTOMERS_RESIDENTIAL].insert_one({
        "customer_id":  "cust_R1",
        "customer_type": "residential",
        "name":          "Aisha",
        "account_id":    "acc_R1",
    })
    await db[CUSTOMER_INDEX].insert_one({
        "customer_id":  "cust_R1",
        "customer_type": "residential",
        "account_id":    "acc_R1",
        "updated_at":    _NOW,
    })

    svc = _make_service(db)
    view = await svc.get_360("cust_R1")
    assert view["customer_id"] == "cust_R1"
    assert view["customer_type"] == "residential"


@pytest.mark.asyncio
async def test_get_360_dispatches_commercial_via_index(storage_split_on) -> None:
    db = FakeDB()
    await db[CUSTOMERS_COMMERCIAL].insert_one({
        "customer_id":  "cust_C1",
        "customer_type": "commercial",
        "name":          "Acme Sports Cafe",
        "account_id":    "acc_C1",
    })
    await db[CUSTOMER_INDEX].insert_one({
        "customer_id":  "cust_C1",
        "customer_type": "commercial",
        "account_id":    "acc_C1",
        "updated_at":    _NOW,
    })

    svc = _make_service(db)
    view = await svc.get_360("cust_C1")
    assert view["customer_id"] == "cust_C1"
    assert view["customer_type"] == "commercial"


@pytest.mark.asyncio
async def test_get_360_explicit_hint_wins(storage_split_on) -> None:
    """`customer_type` hint bypasses the index lookup."""
    db = FakeDB()
    await db[CUSTOMERS_COMMERCIAL].insert_one({
        "customer_id":  "cust_C2",
        "customer_type": "commercial",
        "name":          "Direct hit",
        "account_id":    "acc_C2",
    })
    # NB: deliberately NO row in customer_index — proves we never went through it.

    svc = _make_service(db)
    view = await svc.get_360("cust_C2", customer_type="commercial")
    assert view["customer_id"] == "cust_C2"


@pytest.mark.asyncio
async def test_get_360_falls_back_to_legacy_when_unindexed(storage_split_on) -> None:
    """Unindexed customer → fall back to the legacy alias.

    This is the migration safety net: while `split_customers.py` is still
    populating the index, reads must keep working.
    """
    db = FakeDB()
    await db[CUSTOMERS].insert_one({
        "customer_id":  "cust_LEG",
        "name":          "Legacy hold-out",
        "account_id":    "acc_LEG",
    })
    # No customer_index row.

    svc = _make_service(db)
    view = await svc.get_360("cust_LEG")
    assert view["customer_id"] == "cust_LEG"


@pytest.mark.asyncio
async def test_get_360_legacy_when_split_disabled(storage_split_off) -> None:
    """With STORAGE_SPLIT off the repo uses the legacy alias only.

    Confirms behaviour is bit-identical to pre-PR-2 when the flag is off.
    """
    db = FakeDB()
    await db[CUSTOMERS].insert_one({
        "customer_id":  "cust_LEGACY",
        "name":          "no-split",
        "account_id":    "acc_LEG",
    })
    svc = _make_service(db)
    view = await svc.get_360("cust_LEGACY")
    assert view["customer_id"] == "cust_LEGACY"
