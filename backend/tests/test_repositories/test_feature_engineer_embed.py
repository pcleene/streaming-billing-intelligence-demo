"""Unit tests for FeatureEngineer._sync_customer_embeds.

Validates that the change-stream worker mirrors:
  - the new transaction onto customers.recent_transactions (capped 50, newest-first)
  - the latest features doc onto customers.latest_features (snapshot)

The worker tolerates a missing customer (silent no-op) — we cover that path too.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.workers.feature_engineer import FeatureEngineer, _LATEST_FEATURES_FIELDS


def _txn() -> dict:
    return {
        "transaction_id": "txn_z",
        "customer_id": "cust_42",
        "timestamp": datetime(2026, 5, 4, 11, 0, 0),
        "transaction_type": "ppv_purchase",
        "amount": 19.90,
        "discount_amount": 0.0,
        "merchant_id": "acme-now",
        "quarantined": False,
    }


def _features_doc() -> dict:
    doc = {"customer_id": "cust_42", "updated_at": datetime(2026, 5, 4, 11, 0, 5)}
    for f in _LATEST_FEATURES_FIELDS:
        doc[f] = 1.5  # filled with sentinel value, type doesn't matter for this test
    return doc


@pytest.mark.asyncio
async def test_sync_pushes_recent_and_sets_latest_features() -> None:
    customers = AsyncMock()
    feat = AsyncMock()
    feat.find_one = AsyncMock(return_value=_features_doc())
    customers.update_one = AsyncMock()

    fe = FeatureEngineer()
    await fe._sync_customer_embeds(customers, feat, _txn())

    # Two writes: $push for recent_transactions, $set for latest_features.
    assert customers.update_one.await_count == 2

    push_call = customers.update_one.call_args_list[0]
    push_filter, push_update = push_call.args
    assert push_filter == {"customer_id": "cust_42"}
    push = push_update["$push"]["recent_transactions"]
    assert push["$position"] == 0
    assert push["$slice"] == 50
    assert push["$each"][0]["transaction_id"] == "txn_z"

    set_call = customers.update_one.call_args_list[1]
    set_filter, set_update = set_call.args
    assert set_filter == {"customer_id": "cust_42"}
    snapshot = set_update["$set"]["latest_features"]
    assert "as_of" in snapshot
    # Every mirror field is carried forward.
    for field in _LATEST_FEATURES_FIELDS:
        assert field in snapshot


@pytest.mark.asyncio
async def test_sync_skips_when_no_customer_id() -> None:
    customers = AsyncMock()
    feat = AsyncMock()
    fe = FeatureEngineer()
    txn = _txn()
    txn.pop("customer_id")
    await fe._sync_customer_embeds(customers, feat, txn)
    customers.update_one.assert_not_awaited()
    feat.find_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_no_features_doc_still_pushes_recent() -> None:
    """When a customer has no features row yet, recent_transactions still
    gets the new sample but latest_features stays untouched.
    """
    customers = AsyncMock()
    feat = AsyncMock()
    feat.find_one = AsyncMock(return_value=None)

    fe = FeatureEngineer()
    await fe._sync_customer_embeds(customers, feat, _txn())

    # Exactly one write — the $push for recent_transactions.
    assert customers.update_one.await_count == 1
    _, push_update = customers.update_one.call_args.args
    assert "$push" in push_update
