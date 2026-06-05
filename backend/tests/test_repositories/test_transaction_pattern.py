"""PR-AG — TransactionRepository.customer_pattern unit tests.

Exercises the 30-day customer transaction signature the agent's
analytics node consumes. Uses the FakeDB so tests run without a live
Mongo; all aggregation logic lives in the Python helper
`_summarise_customer_pattern` which the repo wraps.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.repositories.transaction_repo import TransactionRepository
from tests._fakes import FakeDB

_NOW = datetime.now(timezone.utc)


def _txn(
    *,
    customer_id: str = "cust_1",
    timestamp: datetime | None = None,
    amount: float = 100.0,
    items: list[dict] | None = None,
    transaction_id: str | None = None,
) -> dict:
    return {
        "transaction_id": transaction_id or f"txn_{int((timestamp or _NOW).timestamp())}",
        "customer_id": customer_id,
        "timestamp": timestamp or _NOW,
        "total_myr": amount,
        "items": items or [],
    }


async def _seed(db, txns: list[dict]) -> None:
    coll = db["transactions"]
    for t in txns:
        await coll.insert_one(t)


async def test_customer_pattern_basic_stats() -> None:
    db = FakeDB()
    repo = TransactionRepository(db)  # type: ignore[arg-type]

    amounts = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    txns = [
        _txn(
            customer_id="cust_basic",
            amount=a,
            timestamp=_NOW - timedelta(days=i),
            transaction_id=f"txn_b_{i}",
        )
        for i, a in enumerate(amounts)
    ]
    await _seed(db, txns)

    result = await repo.customer_pattern("cust_basic", days=30)

    assert result["customer_id"] == "cust_basic"
    assert result["window_days"] == 30
    assert result["txn_count"] == 10
    assert result["amount_mean_myr"] == pytest.approx(55.0)
    # population stddev of 10..100 step 10 ≈ 28.72
    assert result["amount_stddev_myr"] == pytest.approx(28.72, abs=0.01)
    assert result["first_txn_at"] is not None
    assert result["last_txn_at"] is not None


async def test_customer_pattern_filters_by_window() -> None:
    db = FakeDB()
    repo = TransactionRepository(db)  # type: ignore[arg-type]

    in_window = [
        _txn(
            customer_id="cust_w",
            amount=50.0,
            timestamp=_NOW - timedelta(days=i),
            transaction_id=f"in_{i}",
        )
        for i in range(5)
    ]
    out_of_window = [
        _txn(
            customer_id="cust_w",
            amount=50.0,
            timestamp=_NOW - timedelta(days=60 + i),
            transaction_id=f"out_{i}",
        )
        for i in range(5)
    ]
    await _seed(db, in_window + out_of_window)

    result = await repo.customer_pattern("cust_w", days=30)

    assert result["txn_count"] == 5


async def test_customer_pattern_top_charge_codes() -> None:
    db = FakeDB()
    repo = TransactionRepository(db)  # type: ignore[arg-type]

    # Charge code distribution after counting line items:
    # CC_A x4, CC_B x3, CC_C x2, CC_D x1 → top 3 in count-desc order.
    txns = [
        _txn(
            customer_id="cust_cc",
            timestamp=_NOW - timedelta(days=1),
            items=[{"charge_code": "CC_A"}, {"charge_code": "CC_B"}],
            transaction_id="t1",
        ),
        _txn(
            customer_id="cust_cc",
            timestamp=_NOW - timedelta(days=2),
            items=[{"charge_code": "CC_A"}, {"charge_code": "CC_B"}],
            transaction_id="t2",
        ),
        _txn(
            customer_id="cust_cc",
            timestamp=_NOW - timedelta(days=3),
            items=[
                {"charge_code": "CC_A"},
                {"charge_code": "CC_B"},
                {"charge_code": "CC_C"},
                {"charge_code": "CC_D"},
            ],
            transaction_id="t3",
        ),
        _txn(
            customer_id="cust_cc",
            timestamp=_NOW - timedelta(days=4),
            items=[{"charge_code": "CC_A"}, {"charge_code": "CC_C"}],
            transaction_id="t4",
        ),
    ]
    await _seed(db, txns)

    result = await repo.customer_pattern("cust_cc", days=30)

    assert [tc["charge_code"] for tc in result["top_charge_codes"]] == [
        "CC_A", "CC_B", "CC_C",
    ]
    assert result["top_charge_codes"][0]["count"] == 4
    assert result["top_charge_codes"][1]["count"] == 3
    assert result["top_charge_codes"][2]["count"] == 2


async def test_customer_pattern_empty_returns_zeros() -> None:
    db = FakeDB()
    repo = TransactionRepository(db)  # type: ignore[arg-type]

    result = await repo.customer_pattern("cust_missing", days=30)

    assert result["txn_count"] == 0
    assert result["amount_mean_myr"] == 0.0
    assert result["amount_stddev_myr"] == 0.0
    assert result["top_charge_codes"] == []
    assert result["first_txn_at"] is None
    assert result["last_txn_at"] is None


async def test_customer_pattern_falls_back_to_legacy_amount_field() -> None:
    """Legacy docs use `amount` rather than `total_myr` — both supported."""
    db = FakeDB()
    repo = TransactionRepository(db)  # type: ignore[arg-type]

    legacy = {
        "transaction_id": "legacy_1",
        "customer_id": "cust_legacy",
        "timestamp": _NOW - timedelta(days=1),
        "amount": 42.0,  # no total_myr
    }
    await db["transactions"].insert_one(legacy)

    result = await repo.customer_pattern("cust_legacy", days=30)

    assert result["txn_count"] == 1
    assert result["amount_mean_myr"] == pytest.approx(42.0)
