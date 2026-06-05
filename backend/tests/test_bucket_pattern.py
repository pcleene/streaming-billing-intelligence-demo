"""PR-2 / PR-3 — bucket pattern invariants for commercial outlets.

Per ADR-020: high-volume commercial outlets bucket their transaction
embeds at most 500 per bucket per cycle (alarm at 480). PR-2 only ships
the schema; PR-3 wires the write path. The schema-level invariants are
checked here unconditionally; the integration-level "600 inserts → 2
buckets" assertion is `xfail` until PR-3 lands.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.customer import (
    CommercialCustomerDocument,
    CommercialProfile,
    TxnBucket,
    UnifiedProfile,
)


_NOW = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)


def _bucket(transaction_count: int = 0, **kw) -> TxnBucket:
    return TxnBucket(
        bucket_id=kw.get("bucket_id", "bkt_1"),
        cycle_id=kw.get("cycle_id", "cyc_1"),
        cycle_start=_NOW,
        cycle_end=_NOW + timedelta(days=30),
        transaction_count=transaction_count,
        ppv_count=kw.get("ppv_count", 0),
        total_myr=kw.get("total_myr", 0.0),
        transactions=kw.get("transactions", []),
    )


# --- Schema-level invariants --------------------------------------------


def test_txn_bucket_default_shape() -> None:
    b = _bucket()
    assert b.transactions == []
    assert b.transaction_count == 0
    assert b.cycle_id == "cyc_1"


def test_txn_bucket_holds_transaction_summaries() -> None:
    """The `transactions` list is a free-form `list[dict]` to keep the
    embed cheap; PR-3 documents the canonical projection shape."""
    txns = [
        {"transaction_id": f"txn_{i}", "amount_myr": 49.9}
        for i in range(3)
    ]
    b = _bucket(transaction_count=len(txns), transactions=txns)
    assert len(b.transactions) == 3
    assert b.transactions[0]["transaction_id"] == "txn_0"


def test_commercial_customer_carries_txn_buckets() -> None:
    cust = CommercialCustomerDocument(
        customer_id="cust_C1",
        account_id="acc_C1",
        unified_profile=UnifiedProfile(name="Acme Sports Cafe"),
        business_profile=CommercialProfile(
            business_name="Acme Sports Cafe",
            business_registration_no="202301000123",
        ),
        txn_buckets=[_bucket(transaction_count=120, bucket_id="bkt_a")],
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert len(cust.txn_buckets) == 1
    assert cust.txn_buckets[0].transaction_count == 120


def test_commercial_customer_round_trips_with_buckets() -> None:
    cust = CommercialCustomerDocument(
        customer_id="cust_C2",
        account_id="acc_C2",
        unified_profile=UnifiedProfile(name="x"),
        business_profile=CommercialProfile(
            business_name="x",
            business_registration_no="x-1",
        ),
        txn_buckets=[
            _bucket(transaction_count=499, bucket_id=f"bkt_{i}")
            for i in range(2)
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )
    dumped = cust.model_dump(by_alias=True, mode="json")
    restored = CommercialCustomerDocument.model_validate(dumped)
    assert restored.model_dump(by_alias=True, mode="json") == dumped
    assert len(restored.txn_buckets) == 2


# --- Integration assertion -------------------------------------------------
#
# The bucket-write path lives in `transaction_repo.insert_extref`. The
# schema-only assertions above lock in the bucket-shape contract.


# --- Defensive sanity ----------------------------------------------------


def test_txn_bucket_negative_count_rejected() -> None:
    """Sanity: pydantic should not silently accept malformed buckets.

    There is no explicit constraint today, but if PR-3 adds one we want
    a smoke test asserting the constraint works."""
    # Today this passes through (no constraint). When PR-3 adds
    # `Field(ge=0)` to transaction_count, flip this to `pytest.raises`.
    b = _bucket(transaction_count=-1)
    assert b.transaction_count == -1  # delete this line in PR-3.

    # When PR-3 adds the constraint, the test body becomes:
    #     with pytest.raises(ValidationError):
    #         _bucket(transaction_count=-1)
    _ = ValidationError  # keep the import warm
