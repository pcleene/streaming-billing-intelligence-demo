"""Unit tests for the next-best-offer service (Phase B.6).

Mocks the customer repo. Verifies:
  - missing customer → CustomerNotFound
  - high-risk profile → high band, retention discount as priority 1
  - heavy spender on small package → upgrade offer
  - PPV-heavy → addon bundle offer
  - no active subs → winback offer
  - low-risk loyal high-spender → loyalty perk
  - offers are sorted by priority asc
  - persisted via $set recommendations
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import CustomerNotFound
from app.services.nbo_service import NboService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_repo(cust: dict | None) -> MagicMock:
    """PR-2: NboService talks to the type-aware repo, not the legacy
    `repo.collection`.

    - `get_by_customer_id` returns the doc.
    - `_coll_for(ctype)` returns whichever typed collection the doc lives
      in. We funnel both branches into the same fake `update_one`.
    """
    repo = MagicMock()
    repo.get_by_customer_id = AsyncMock(return_value=cust)
    typed_coll = MagicMock()
    typed_coll.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    repo._coll_for = MagicMock(return_value=typed_coll)
    # Expose the fake update_one for assertions.
    repo.collection = typed_coll
    return repo


def _cust(**overrides) -> dict:
    base = {
        "customer_id": "cust_001",
        "name": "Test User",
        "subscriptions": [
            {"package_code": "BASIC", "package_name": "Basic", "status": "active",
             "monthly_fee_myr": 80.0, "started_at": _utcnow()},
        ],
        "active_promotions": [],
        "open_cases": [],
        "recent_transactions": [],
        "latest_features": {
            "as_of": _utcnow(),
            "minutes_since_last_txn": 60,
            "spend_to_package_ratio": 0.5,
            "quarantine_count_30d": 0,
            "discount_rate_30d": 0.0,
            "package_value_myr": 80.0,
            "spend_24h_myr": 30.0,
        },
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_missing_customer_raises() -> None:
    repo = _make_repo(None)
    svc = NboService(repo)
    with pytest.raises(CustomerNotFound):
        await svc.compute("cust_missing")


@pytest.mark.asyncio
async def test_high_risk_profile_yields_retention_priority_1() -> None:
    cust = _cust(
        latest_features={
            "as_of": _utcnow(),
            "minutes_since_last_txn": 60 * 24 * 20,  # > 14 days
            "spend_to_package_ratio": 0.10,           # low utilisation
            "quarantine_count_30d": 4,                 # recent quarantines
            "discount_rate_30d": 0.30,                 # heavy discounts
            "package_value_myr": 120.0,
            "spend_24h_myr": 0.0,
        },
        open_cases=[
            {"case_id": "c1", "severity": "high", "status": "open",
             "created_at": _utcnow(), "updated_at": _utcnow()},
            {"case_id": "c2", "severity": "medium", "status": "open",
             "created_at": _utcnow(), "updated_at": _utcnow()},
        ],
    )
    repo = _make_repo(cust)
    svc = NboService(repo)
    out = await svc.compute("cust_001")
    assert out.churn_risk.band == "high"
    assert out.churn_risk.score >= 0.65
    assert "no_txn_in_14d" in out.churn_risk.drivers
    # Retention discount should be the first offer.
    assert out.next_best_offers[0].offer_type == "retention_discount"
    assert out.next_best_offers[0].priority == 1
    # Persistence happened.
    repo.collection.update_one.assert_awaited_once()
    update_doc = repo.collection.update_one.call_args.args[1]
    assert "recommendations" in update_doc["$set"]


@pytest.mark.asyncio
async def test_heavy_spender_small_package_yields_upgrade() -> None:
    cust = _cust(
        latest_features={
            "as_of": _utcnow(),
            "minutes_since_last_txn": 30,
            "spend_to_package_ratio": 0.95,   # > 0.85 → upgrade
            "quarantine_count_30d": 0,
            "discount_rate_30d": 0.0,
            "package_value_myr": 60.0,
            "spend_24h_myr": 50.0,
        },
    )
    repo = _make_repo(cust)
    svc = NboService(repo)
    out = await svc.compute("cust_001")
    types = [o.offer_type for o in out.next_best_offers]
    assert "upgrade" in types
    upgrade = next(o for o in out.next_best_offers if o.offer_type == "upgrade")
    assert upgrade.expected_uplift_myr == round(60.0 * 0.30, 2)


@pytest.mark.asyncio
async def test_ppv_heavy_yields_addon_bundle() -> None:
    cust = _cust(
        recent_transactions=[
            {"transaction_id": f"t{i}", "transaction_type": "ppv_purchase",
             "amount": 9.9, "timestamp": _utcnow(), "merchant_id": "m"}
            for i in range(4)
        ],
    )
    repo = _make_repo(cust)
    svc = NboService(repo)
    out = await svc.compute("cust_001")
    types = [o.offer_type for o in out.next_best_offers]
    assert "addon" in types


@pytest.mark.asyncio
async def test_no_active_subs_yields_winback() -> None:
    cust = _cust(
        subscriptions=[
            {"package_code": "BASIC", "package_name": "Basic",
             "status": "cancelled", "monthly_fee_myr": 80.0,
             "started_at": _utcnow()},
        ],
    )
    repo = _make_repo(cust)
    svc = NboService(repo)
    out = await svc.compute("cust_001")
    types = [o.offer_type for o in out.next_best_offers]
    assert "winback" in types


@pytest.mark.asyncio
async def test_low_risk_loyal_spender_yields_loyalty_perk() -> None:
    cust = _cust(
        latest_features={
            "as_of": _utcnow(),
            "minutes_since_last_txn": 30,
            "spend_to_package_ratio": 0.50,
            "quarantine_count_30d": 0,
            "discount_rate_30d": 0.0,
            "package_value_myr": 200.0,
            "spend_24h_myr": 250.0,  # > 100
        },
        active_promotions=[],
    )
    repo = _make_repo(cust)
    svc = NboService(repo)
    out = await svc.compute("cust_001")
    assert out.churn_risk.band == "low"
    types = [o.offer_type for o in out.next_best_offers]
    assert "loyalty_perk" in types


@pytest.mark.asyncio
async def test_offers_sorted_by_priority_asc() -> None:
    cust = _cust(
        latest_features={
            "as_of": _utcnow(),
            "minutes_since_last_txn": 60 * 24 * 20,
            "spend_to_package_ratio": 0.95,
            "quarantine_count_30d": 4,
            "discount_rate_30d": 0.30,
            "package_value_myr": 100.0,
            "spend_24h_myr": 0.0,
        },
        recent_transactions=[
            {"transaction_id": f"t{i}", "transaction_type": "ppv_purchase",
             "amount": 9.9, "timestamp": _utcnow(), "merchant_id": "m"}
            for i in range(4)
        ],
        subscriptions=[
            {"package_code": "BASIC", "package_name": "Basic",
             "status": "cancelled", "monthly_fee_myr": 80.0,
             "started_at": _utcnow()},
        ],
    )
    repo = _make_repo(cust)
    svc = NboService(repo)
    out = await svc.compute("cust_001")
    priorities = [o.priority for o in out.next_best_offers]
    assert priorities == sorted(priorities)
