"""PR-9 — MetricsAggregatorService tests.

Verifies:
  - 12-month trend produces exactly 12 entries oldest→newest.
  - LTV total equals the sum of settled transaction `total_myr`s in the
    window.
  - Empty transaction history yields 12 zero-filled entries (no crash).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.services.metrics_aggregator_service import MetricsAggregatorService


_AS_OF = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def _make_service(rows: list[dict]) -> MetricsAggregatorService:
    repo = AsyncMock()
    repo.aggregate = AsyncMock(return_value=rows)
    return MetricsAggregatorService(repo)


@pytest.mark.asyncio
async def test_trend_has_exactly_12_entries_oldest_first() -> None:
    """Window ending May 2026: 2025-06 .. 2026-05 (oldest first)."""
    svc = _make_service([])
    metrics = await svc.compute_for_customer("cust_R1", as_of=_AS_OF)

    spend_trend = metrics["monthly_spend_trend_12m"]
    hours_trend = metrics["viewing_hours_trend_12m"]
    ppv_trend = metrics["ppv_count_trend_12m"]

    assert len(spend_trend) == 12
    assert len(hours_trend) == 12
    assert len(ppv_trend) == 12

    # Oldest → newest.
    months = [e["month"] for e in spend_trend]
    assert months[0] == "2025-06"
    assert months[-1] == "2026-05"
    # Strictly increasing month strings (lex order works for YYYY-MM).
    assert months == sorted(months)


@pytest.mark.asyncio
async def test_ltv_equals_sum_of_settled_transactions() -> None:
    """Three months of activity; the rest zero-filled. LTV is the sum."""
    rows = [
        {"_id": {"year": 2025, "month": 6}, "spend": 100.0, "viewing_minutes": 0, "ppv_count": 0},
        {"_id": {"year": 2026, "month": 1}, "spend": 250.50, "viewing_minutes": 0, "ppv_count": 0},
        {"_id": {"year": 2026, "month": 5}, "spend": 99.50, "viewing_minutes": 0, "ppv_count": 0},
    ]
    svc = _make_service(rows)
    metrics = await svc.compute_for_customer("cust_R1", as_of=_AS_OF)

    assert metrics["ltv"]["currency"] == "MYR"
    assert metrics["ltv"]["total_myr"] == pytest.approx(450.0, rel=1e-6)

    # The trend point amounts mirror the raw rows in the right slots.
    by_month = {e["month"]: e["amount_myr"] for e in metrics["monthly_spend_trend_12m"]}
    assert by_month["2025-06"] == pytest.approx(100.0)
    assert by_month["2026-01"] == pytest.approx(250.50)
    assert by_month["2026-05"] == pytest.approx(99.50)
    # Other months are zero-filled.
    assert by_month["2025-09"] == 0.0


@pytest.mark.asyncio
async def test_empty_history_yields_12_zero_filled_entries() -> None:
    svc = _make_service([])
    metrics = await svc.compute_for_customer("cust_R1", as_of=_AS_OF)

    assert metrics["ltv"]["total_myr"] == 0.0
    assert all(e["amount_myr"] == 0.0 for e in metrics["monthly_spend_trend_12m"])
    assert all(e["hours"] == 0.0 for e in metrics["viewing_hours_trend_12m"])
    assert all(e["count"] == 0 for e in metrics["ppv_count_trend_12m"])


@pytest.mark.asyncio
async def test_viewing_hours_and_ppv_count_propagate() -> None:
    """Sanity that the non-spend trends carry through too."""
    rows = [
        {
            "_id": {"year": 2026, "month": 5},
            "spend": 50.0,
            "viewing_minutes": 240,
            "ppv_count": 3,
        },
    ]
    svc = _make_service(rows)
    metrics = await svc.compute_for_customer("cust_R1", as_of=_AS_OF)

    last_hours = metrics["viewing_hours_trend_12m"][-1]
    last_ppv = metrics["ppv_count_trend_12m"][-1]
    assert last_hours["month"] == "2026-05"
    assert last_hours["hours"] == pytest.approx(4.0)
    assert last_ppv["month"] == "2026-05"
    assert last_ppv["count"] == 3
