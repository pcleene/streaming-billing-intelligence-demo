"""Service-level unit tests for the Customer 360 vertical.

We stub the repository so the service contract (CRM-lag masking,
NotFound mapping) is verified without needing MongoDB.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.core.errors import CustomerNotFound
from app.services.customer_service import CustomerService


def _fake_view() -> dict:
    """Phase A (ADR-011): the 360 returns a single embedded document — no
    aggregation join, recent_transactions / open_cases / latest_features all
    live on the customer doc itself.
    """
    return {
        "customer_id": "cust_000001",
        "name": "Aiman Tan",
        "segment": "premium",
        "address": {"state": "Selangor"},
        "subscriptions": [{"package_id": "pkg_premium"}],
        "recent_transactions": [
            {"transaction_id": "txn_1"},
            {"transaction_id": "txn_2"},
        ],
        "open_cases": [
            {
                "case_id": "case_1",
                "severity": "high",
                "status": "open",
                "rule_types": ["discount_mismatch"],
                "created_at": datetime(2026, 5, 1),
                "updated_at": datetime(2026, 5, 1),
            }
        ],
        "latest_features": {
            "as_of": datetime(2026, 5, 4),
            "txn_count_24h": 12,
            "spend_7d_myr": 320.50,
        },
    }


def _make_service(view: dict | None) -> CustomerService:
    # PR-2: CustomerService.get_360 dispatches via repo.get_by_customer_id
    # (which honours customer_index when STORAGE_SPLIT is on).
    repo = AsyncMock()
    repo.get_by_customer_id = AsyncMock(return_value=view)
    return CustomerService(repo)


@pytest.mark.asyncio
async def test_get_360_returns_embedded_view_when_present() -> None:
    svc = _make_service(_fake_view())
    out = await svc.get_360("cust_000001")
    assert out["customer_id"] == "cust_000001"
    assert out["crm_lag"]["simulated"] is False
    # Embedded payloads come straight off the customer doc.
    assert len(out["recent_transactions"]) == 2
    assert out["open_cases"][0]["case_id"] == "case_1"
    assert out["latest_features"]["txn_count_24h"] == 12


@pytest.mark.asyncio
async def test_get_360_uses_find_one_not_aggregate() -> None:
    """Sanity: the 360 read path no longer hits aggregate.

    PR-2 collapsed the read further: it goes through the type-aware
    `get_by_customer_id` (which is itself a single-collection find_one).
    """
    repo = AsyncMock()
    repo.get_by_customer_id = AsyncMock(return_value=_fake_view())
    repo.aggregate = AsyncMock(return_value=[])
    svc = CustomerService(repo)
    await svc.get_360("cust_000001")
    repo.get_by_customer_id.assert_awaited_once()
    repo.aggregate.assert_not_called()


@pytest.mark.asyncio
async def test_get_360_raises_when_missing() -> None:
    svc = _make_service(None)
    with pytest.raises(CustomerNotFound):
        await svc.get_360("cust_unknown")


@pytest.mark.asyncio
async def test_get_360_simulated_lag_masks_live_fields() -> None:
    svc = _make_service(_fake_view())
    out = await svc.get_360("cust_000001", simulate_crm_lag=True)
    assert out["crm_lag"]["simulated"] is True
    assert out["crm_lag"]["lag_hours"] >= 0
    # Stale CRM view should not show fresh ops data.
    assert out["recent_transactions"] == []
    assert out["open_cases"] == []
    assert out["latest_features"] is None
    # Snapshot timestamp is parseable
    datetime.fromisoformat(out["crm_lag"]["snapshot_at"])
