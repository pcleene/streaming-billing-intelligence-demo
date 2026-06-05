"""PR-3 — backfill_transactions_extref idempotency + shape tests.

Seeds the in-memory FakeDB with a handful of legacy transactions, runs
the backfill, and asserts:
  * every row is upgraded to `_schema_version == 3`
  * customer_summary is projected from the current customer doc
  * cycle_id / bill_period are populated (synthesised when no
    bill_cycles row exists — PR-4 will replace the synth fallback)
  * unknown-customer rows are skipped, not corrupted
  * dry-run leaves data untouched
  * a second run upgrades zero additional rows
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.constants import (
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
    SCHEMA_VERSION_V3,
    TRANSACTIONS,
)
from app import deps
from scripts import backfill_transactions_extref as bf
from tests._fakes import FakeDB

_NOW = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_with_legacy_data(monkeypatch: pytest.MonkeyPatch) -> FakeDB:
    db = FakeDB()
    db[CUSTOMERS_RESIDENTIAL]._docs.append({  # type: ignore[attr-defined]
        "customer_id": "cust_r1",
        "customer_type": "residential",
        "account_id": "acc_r1",
        "name": "Aiman",
        "address": {"state": "Selangor"},
        "subscriptions": [{
            "package_code": "PREMIUM",
            "package_name": "Premium",
            "status": "active",
            "monthly_fee_myr": 199.0,
            "started_at": _NOW,
        }],
    })
    db[CUSTOMERS_COMMERCIAL]._docs.append({  # type: ignore[attr-defined]
        "customer_id": "cust_C1",
        "customer_type": "commercial",
        "account_id": "acc_C1",
        "name": "Acme Sports",
        "business_profile": {
            "business_name": "Acme Sports SDN BHD",
            "outlet_label": "KL",
        },
        "address": {"state": "Kuala Lumpur"},
    })
    # 3 legacy rows: 2 known customers + 1 orphan.
    legacy_rows = [
        {
            "transaction_id": "txn_legacy_1",
            "customer_id": "cust_r1",
            "timestamp": _NOW,
            "transaction_type": "subscription_charge",
            "amount": 199.0,
            "discount_amount": 0.0,
            "merchant_id": "acme-direct",
            "location": {"state": "Selangor"},
            "quarantined": False,
        },
        {
            "transaction_id": "txn_legacy_2",
            "customer_id": "cust_C1",
            "timestamp": _NOW,
            "transaction_type": "subscription_charge",
            "amount": 500.0,
            "discount_amount": 0.0,
            "merchant_id": "acme-direct",
        },
        {
            "transaction_id": "txn_legacy_orphan",
            "customer_id": "cust_unknown",
            "timestamp": _NOW,
            "transaction_type": "subscription_charge",
            "amount": 10.0,
        },
    ]
    db[TRANSACTIONS]._docs.extend(legacy_rows)  # type: ignore[attr-defined]
    monkeypatch.setattr(deps, "_db", db)
    return db


@pytest.mark.asyncio
async def test_backfill_dry_run_does_not_write(db_with_legacy_data: FakeDB) -> None:
    stats = await bf.backfill(dry_run=True, batch_size=0)
    assert stats["scanned"] == 3
    assert stats["upgraded"] == 2
    assert stats["skipped_unknown_customer"] == 1
    txns = db_with_legacy_data[TRANSACTIONS]._docs  # type: ignore[attr-defined]
    # Nothing actually written.
    assert all("_schema_version" not in t for t in txns)


@pytest.mark.asyncio
async def test_backfill_upgrades_known_rows(db_with_legacy_data: FakeDB) -> None:
    stats = await bf.backfill(dry_run=False, batch_size=0)
    assert stats["upgraded"] == 2
    txns = db_with_legacy_data[TRANSACTIONS]._docs  # type: ignore[attr-defined]
    upgraded = {t["transaction_id"]: t for t in txns if t.get("_schema_version") == SCHEMA_VERSION_V3}
    assert set(upgraded) == {"txn_legacy_1", "txn_legacy_2"}

    r1 = upgraded["txn_legacy_1"]
    assert r1["customer_type"] == "residential"
    assert r1["customer_summary"]["name"] == "Aiman"
    assert r1["cycle_id"].startswith("cyc_cust_r1_")
    assert r1["bill_period"]["start"].year == 2026

    c1 = upgraded["txn_legacy_2"]
    assert c1["customer_type"] == "commercial"
    assert c1["customer_summary"]["parent_business_name"] == "Acme Sports SDN BHD"

    # Orphan row left untouched (no _schema_version stamped).
    orphan = next(t for t in txns if t["transaction_id"] == "txn_legacy_orphan")
    assert orphan.get("_schema_version") != SCHEMA_VERSION_V3


@pytest.mark.asyncio
async def test_backfill_is_idempotent(db_with_legacy_data: FakeDB) -> None:
    first = await bf.backfill(dry_run=False, batch_size=0)
    assert first["upgraded"] == 2
    second = await bf.backfill(dry_run=False, batch_size=0)
    # Second pass scans only the orphan (already-V3 rows fall out of the
    # `_schema_version < 3` filter); upgraded count is zero.
    assert second["upgraded"] == 0
