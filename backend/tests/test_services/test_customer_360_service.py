"""PR-9 — Customer360Service tests.

Covers:
  - rich shape (flag on or rich=True) carries every expected top-level
    rich key, drops the raw embedding vector, drops `_id`.
  - lean shape (flag off / rich=False) preserves the legacy envelope and
    excludes rich-only keys.
  - `rich=None` reads `flags.RICH_CUSTOMER_360` at call time.
  - `CustomerNotFound` is raised when the customer doc is missing.
  - explicit `customer_type` hint is forwarded to the repo.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.errors import CustomerNotFound
from app.core.feature_flags import flags
from app.services.customer_360_service import Customer360Service


_NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def _rich_doc() -> dict:
    return {
        "_id": "mongo-internal-id",
        "customer_id": "cust_R1",
        "customer_type": "residential",
        "account_id": "acc_R1",
        "name": "Lee Wei Ming",
        "email": "lee@example.com",
        "ic_number": "850101-14-5555",
        "address": {"city": "Kuala Lumpur", "state": "WP"},
        "segment": "platinum",
        "tier": "RetailGroup",
        "unified_profile": {"name": "Lee Wei Ming"},
        "entities": ["acme_paytv", "acme_streaming"],
        "entity_profiles": {
            "acme_paytv": {"primary_package_name": "Acme Movies"},
        },
        "cross_entity_metrics": {"ltv": {"total_myr": 12345.67}},
        "brand_journey": [{"event": "signup"}],
        "interaction_history": {"support_tickets": []},
        "active_campaigns": [{"campaign_id": "c1", "status": "scheduled"}],
        "recommendations": {"churn_risk": {"band": "low"}},
        "equipment": [{"equipment_id": "eq1", "status": "active"}],
        "current_cycle": {"cycle_id": "cyc1"},
        "subscriptions": [],
        "active_promotions": [],
        "entitlements": [],
        "recent_transactions": [],
        "open_cases": [],
        "latest_features": None,
        "recent_support": [],
        "total_monthly_value_myr": 199.99,
        "lifetime_quarantine_count": 0,
        "embedding": [0.1] * 1024,
        "embedding_generated_at": _NOW,
        "embedding_model": "voyage-4",
    }


def _make_service(doc: dict | None) -> tuple[Customer360Service, AsyncMock]:
    repo = AsyncMock()
    repo.get_by_customer_id = AsyncMock(return_value=doc)
    svc = Customer360Service(repo)
    return svc, repo


# ---------------------------------------------------------------------
# 1. rich=True returns the rich shape, strips embedding + _id
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rich_profile_carries_expected_keys_and_strips_internals() -> None:
    svc, _ = _make_service(_rich_doc())
    result = await svc.get_profile("cust_R1", rich=True)

    # Rich-only keys are present.
    for key in (
        "unified_profile",
        "entities",
        "entity_profiles",
        "cross_entity_metrics",
        "brand_journey",
        "interaction_history",
        "active_campaigns",
        "recommendations",
        "equipment",
        "current_cycle",
        "embedding_generated_at",
    ):
        assert key in result, f"missing rich key: {key}"

    # Identity keys preserved.
    assert result["customer_id"] == "cust_R1"
    assert result["customer_type"] == "residential"

    # Internals dropped.
    assert "_id" not in result
    assert "embedding" not in result, "raw embedding vector must not leak to HTTP"


# ---------------------------------------------------------------------
# 2. rich=False returns the lean shape (no rich-only keys)
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lean_profile_excludes_rich_only_keys() -> None:
    svc, _ = _make_service(_rich_doc())
    result = await svc.get_profile("cust_R1", rich=False)

    # Lean envelope keys present.
    assert result["customer_id"] == "cust_R1"
    assert result["name"] == "Lee Wei Ming"
    assert result["address"] == {"city": "Kuala Lumpur", "state": "WP"}

    # Rich-only keys NOT in the lean envelope.
    for key in (
        "unified_profile",
        "entities",
        "entity_profiles",
        "cross_entity_metrics",
        "brand_journey",
        "interaction_history",
        "active_campaigns",
        "equipment",
        "current_cycle",
        "embedding_generated_at",
    ):
        assert key not in result, f"lean shape leaked rich key: {key}"

    # Definitely no embedding vector.
    assert "embedding" not in result
    assert "_id" not in result


# ---------------------------------------------------------------------
# 3. rich=None reads flags.RICH_CUSTOMER_360
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rich_none_defaults_from_feature_flag(monkeypatch) -> None:
    svc, _ = _make_service(_rich_doc())

    # Flag on → rich shape.
    monkeypatch.setattr(flags, "RICH_CUSTOMER_360", True)
    result = await svc.get_profile("cust_R1")
    assert "unified_profile" in result
    assert "embedding" not in result

    # Flag off → lean shape.
    monkeypatch.setattr(flags, "RICH_CUSTOMER_360", False)
    result = await svc.get_profile("cust_R1")
    assert "unified_profile" not in result
    assert result["name"] == "Lee Wei Ming"


# ---------------------------------------------------------------------
# 4. Missing customer raises CustomerNotFound
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_customer_raises_not_found() -> None:
    svc, _ = _make_service(None)
    with pytest.raises(CustomerNotFound):
        await svc.get_profile("cust_missing", rich=True)


# ---------------------------------------------------------------------
# 5. Explicit customer_type hint is forwarded to the repository
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_customer_type_hint_passed_to_repo() -> None:
    svc, repo = _make_service(_rich_doc())
    await svc.get_profile("cust_R1", customer_type="commercial", rich=False)
    repo.get_by_customer_id.assert_awaited_once_with(
        "cust_R1", customer_type="commercial"
    )
