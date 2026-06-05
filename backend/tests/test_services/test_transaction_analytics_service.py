"""Unit tests for `app.services.transaction_analytics_service.TransactionAnalyticsService`.

The service is a thin facade — these tests pin:

  - `customer_pattern` returns the repo's payload verbatim when the
    customer exists.
  - missing customers surface as `None` so the route layer can map to
    a 404 envelope.
  - `days` is clamped into [1, 365] before being forwarded to the repo
    (out-of-range values must not silently expand the window).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.transaction_analytics_service import (
    DAYS_MAX,
    DAYS_MIN,
    TransactionAnalyticsService,
)


def _make_service(
    *,
    customer: dict | None,
    pattern: dict | None = None,
) -> tuple[TransactionAnalyticsService, AsyncMock, AsyncMock]:
    customer_repo = AsyncMock()
    customer_repo.get_by_customer_id = AsyncMock(return_value=customer)
    txn_repo = AsyncMock()
    txn_repo.customer_pattern = AsyncMock(
        return_value=pattern or {
            "customer_id": "c1",
            "window_days": 30,
            "txn_count": 0,
            "amount_mean_myr": 0.0,
            "amount_stddev_myr": 0.0,
            "top_charge_codes": [],
            "first_txn_at": None,
            "last_txn_at": None,
        }
    )
    return TransactionAnalyticsService(txn_repo, customer_repo), customer_repo, txn_repo


@pytest.mark.asyncio
async def test_customer_pattern_returns_repo_payload() -> None:
    expected = {
        "customer_id": "c1",
        "window_days": 30,
        "txn_count": 5,
        "amount_mean_myr": 99.0,
        "amount_stddev_myr": 12.0,
        "top_charge_codes": [{"charge_code": "PPV", "count": 3}],
        "first_txn_at": None,
        "last_txn_at": None,
    }
    svc, _, txn_repo = _make_service(
        customer={"customer_id": "c1"},
        pattern=expected,
    )

    out = await svc.customer_pattern("c1", days=30)

    assert out == expected
    txn_repo.customer_pattern.assert_awaited_once_with("c1", days=30)


@pytest.mark.asyncio
async def test_customer_pattern_missing_customer_returns_none() -> None:
    svc, _, txn_repo = _make_service(customer=None)

    out = await svc.customer_pattern("c-missing", days=30)

    assert out is None
    txn_repo.customer_pattern.assert_not_awaited()


@pytest.mark.asyncio
async def test_customer_pattern_clamps_days_below_minimum() -> None:
    svc, _, txn_repo = _make_service(customer={"customer_id": "c1"})

    await svc.customer_pattern("c1", days=0)

    txn_repo.customer_pattern.assert_awaited_once_with("c1", days=DAYS_MIN)


@pytest.mark.asyncio
async def test_customer_pattern_clamps_days_above_maximum() -> None:
    svc, _, txn_repo = _make_service(customer={"customer_id": "c1"})

    await svc.customer_pattern("c1", days=10_000)

    txn_repo.customer_pattern.assert_awaited_once_with("c1", days=DAYS_MAX)


@pytest.mark.asyncio
async def test_customer_pattern_passes_through_in_range_days() -> None:
    svc, _, txn_repo = _make_service(customer={"customer_id": "c1"})

    await svc.customer_pattern("c1", days=14)

    txn_repo.customer_pattern.assert_awaited_once_with("c1", days=14)
