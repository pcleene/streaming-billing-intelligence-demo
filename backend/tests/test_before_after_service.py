"""Tests for the before/after service (Phase B.7).

Mocks a fake-DB facade where each collection name maps to an AsyncMock
exposing `find_one`. Verifies:
  - missing case → CaseNotFound
  - missing customer → CustomerNotFound
  - crm_snapshots hit produces a 'crm_snapshot' projection
  - crm_snapshots miss falls back to 'derived_lag' and ages out new items
  - would_quarantine reflects whether the case predates the snapshot
  - latency derived from resolved_at - created_at
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.constants import CRM_SNAPSHOTS, CUSTOMERS, QUARANTINE_CASES
from app.core.errors import CaseNotFound, CustomerNotFound
from app.services.before_after_service import BeforeAfterService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_db(*, case=None, customer=None, snapshot=None,
             snapshots_raise: bool = False):
    """Build a fake db where db[NAME].find_one returns the configured doc."""
    cases = MagicMock()
    cases.find_one = AsyncMock(return_value=case)
    customers = MagicMock()
    customers.find_one = AsyncMock(return_value=customer)
    snaps = MagicMock()
    if snapshots_raise:
        snaps.find_one = AsyncMock(side_effect=Exception("collection missing"))
    else:
        snaps.find_one = AsyncMock(return_value=snapshot)
    mapping = {
        QUARANTINE_CASES: cases,
        CUSTOMERS: customers,
        CRM_SNAPSHOTS: snaps,
    }
    db = MagicMock()
    db.__getitem__.side_effect = lambda name: mapping[name]
    return db, mapping


def _case(**overrides) -> dict:
    base = {
        "case_id": "case-1",
        "customer_id": "cust_001",
        "status": "open",
        "severity": "medium",
        "created_at": _utcnow() - timedelta(hours=2),
        "rules_triggered": [
            {"rule_id": "r1", "rule_type": "discount_mismatch",
             "rule_name": "Discount mismatch"},
        ],
        "ai_assist": None,
    }
    base.update(overrides)
    return base


def _customer(**overrides) -> dict:
    base = {
        "customer_id": "cust_001",
        "name": "Test User",
        "segment": "silver",
        "address": {"state": "Selangor"},
        "subscriptions": [
            {"package_code": "BASIC", "status": "active",
             "monthly_fee_myr": 80.0, "started_at": _utcnow()},
        ],
        "open_cases": [],
        "recent_transactions": [],
        "total_monthly_value_myr": 80.0,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_missing_case_raises() -> None:
    db, _ = _make_db(case=None)
    svc = BeforeAfterService(db)
    with pytest.raises(CaseNotFound):
        await svc.get("case-missing")


@pytest.mark.asyncio
async def test_missing_customer_raises() -> None:
    db, _ = _make_db(case=_case(), customer=None)
    svc = BeforeAfterService(db)
    with pytest.raises(CustomerNotFound):
        await svc.get("case-1")


@pytest.mark.asyncio
async def test_crm_snapshot_hit_uses_warehouse_projection() -> None:
    snap_at = _utcnow() - timedelta(hours=24)
    snapshot = {
        "customer_id": "cust_001",
        "snapshot_at": snap_at,
        "payload": {
            "customer_id": "cust_001",
            "name": "Test User",
            "segment": "silver",
            "address": {"state": "Selangor"},
            "subscriptions": [{"status": "active"}],
            "open_cases": [],
            "recent_transactions": [{"transaction_id": "t-old"}],
        },
    }
    db, _ = _make_db(case=_case(), customer=_customer(), snapshot=snapshot)
    svc = BeforeAfterService(db)
    out = await svc.get("case-1")
    assert out.before.source == "crm_snapshot"
    assert out.before.recent_transaction_count == 1
    assert out.after.source == "live"


@pytest.mark.asyncio
async def test_no_snapshot_falls_back_to_derived_lag() -> None:
    cust = _customer(
        recent_transactions=[
            {"transaction_id": "t-new", "timestamp": _utcnow() - timedelta(hours=1)},
            {"transaction_id": "t-old", "timestamp": _utcnow() - timedelta(days=2)},
        ],
        open_cases=[
            {"case_id": "case-recent", "severity": "medium", "status": "open",
             "created_at": _utcnow() - timedelta(hours=1)},
        ],
    )
    db, _ = _make_db(case=_case(), customer=cust, snapshot=None)
    svc = BeforeAfterService(db)
    out = await svc.get("case-1")
    assert out.before.source == "derived_lag"
    # Recent items (within crm_lag window) are aged out.
    assert out.before.recent_transaction_count == 1
    assert out.before.open_case_count == 0


@pytest.mark.asyncio
async def test_snapshot_collection_missing_falls_back_gracefully() -> None:
    db, _ = _make_db(case=_case(), customer=_customer(), snapshots_raise=True)
    svc = BeforeAfterService(db)
    out = await svc.get("case-1")
    assert out.before.source == "derived_lag"


@pytest.mark.asyncio
async def test_would_quarantine_true_when_case_predates_snapshot() -> None:
    case = _case(created_at=_utcnow() - timedelta(days=2))  # before lag cutoff
    db, _ = _make_db(case=case, customer=_customer())
    svc = BeforeAfterService(db)
    out = await svc.get("case-1")
    assert out.would_quarantine is True


@pytest.mark.asyncio
async def test_would_quarantine_false_when_case_is_recent() -> None:
    case = _case(created_at=_utcnow() - timedelta(minutes=5))
    db, _ = _make_db(case=case, customer=_customer())
    svc = BeforeAfterService(db)
    out = await svc.get("case-1")
    assert out.would_quarantine is False


@pytest.mark.asyncio
async def test_latency_when_resolved() -> None:
    created = _utcnow() - timedelta(minutes=10)
    resolved = _utcnow() - timedelta(minutes=4)
    case = _case(status="resolved", created_at=created, resolved_at=resolved)
    db, _ = _make_db(case=case, customer=_customer())
    svc = BeforeAfterService(db)
    out = await svc.get("case-1")
    assert out.auto_resolution_latency_seconds is not None
    assert 350 < out.auto_resolution_latency_seconds < 370


@pytest.mark.asyncio
async def test_after_includes_ai_assist_summary() -> None:
    case = _case(ai_assist={
        "summary": "Promo expired before discount applied.",
        "likelihood": "true_positive",
        "confidence": 0.8,
    })
    db, _ = _make_db(case=case, customer=_customer())
    svc = BeforeAfterService(db)
    out = await svc.get("case-1")
    assert out.after.has_ai_assist is True
    assert out.after.ai_assist_summary == "Promo expired before discount applied."
    assert out.after.rules_visible == ["discount_mismatch"]
