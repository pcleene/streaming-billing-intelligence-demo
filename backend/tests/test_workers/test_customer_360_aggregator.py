"""Unit tests for `app.workers.customer_360_aggregator`.

The tests use the in-memory `FakeDB` from `tests/_fakes.py` so we never
talk to a real Mongo. `MetricsAggregatorService` is replaced with an
`AsyncMock` — the worker is responsible for calling
`compute_for_customer` per customer and merging the result back into
the doc, NOT for the math itself (that's C1's service-level deliverable).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.repositories.customer_repo import CustomerRepository
from app.workers.customer_360_aggregator import (
    AGGREGATOR_INTERVAL_SECONDS,
    COOLDOWN_SECONDS,
    _Aggregator,
    _compute_engagement_rates,
)
from tests._fakes import FakeDB


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _seed_customer(
    db: FakeDB,
    *,
    customer_id: str,
    customer_type: str = "residential",
    cross_entity_metrics: dict | None = None,
    interaction_history: dict | None = None,
) -> None:
    """Insert a customer doc directly into the legacy `customers` alias.

    The aggregator with `STORAGE_SPLIT=False` (default) scans that
    collection, which is exactly the path covered by these tests.
    """
    coll = db["customers_residential"]  # alias of `customers`
    coll._docs.append({  # type: ignore[attr-defined]
        "customer_id": customer_id,
        "customer_type": customer_type,
        "cross_entity_metrics": cross_entity_metrics or {},
        "interaction_history": interaction_history or {},
        "current_cycle": {},
    })


def _build_aggregator(db: FakeDB, *, metrics: AsyncMock) -> _Aggregator:
    repo = CustomerRepository(db)
    return _Aggregator(
        customer_repo=repo,
        metrics_aggregator=metrics,
        interval_seconds=60,
        cooldown_seconds=COOLDOWN_SECONDS,
    )


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_calls_compute_for_each_customer() -> None:
    db = FakeDB()
    _seed_customer(db, customer_id="c-1")
    _seed_customer(db, customer_id="c-2")
    _seed_customer(db, customer_id="c-3")

    metrics = AsyncMock()
    metrics.compute_for_customer = AsyncMock(
        return_value={"ltv_total_myr": 1234.0}
    )
    agg = _build_aggregator(db, metrics=metrics)

    out = await agg.run_once()

    assert out["scanned"] == 3
    assert out["updated"] == 3
    assert out["skipped"] == 0
    assert out["errors"] == 0
    assert metrics.compute_for_customer.await_count == 3
    called_ids = {
        c.args[0] if c.args else c.kwargs.get("customer_id")
        for c in metrics.compute_for_customer.call_args_list
    }
    assert called_ids == {"c-1", "c-2", "c-3"}


@pytest.mark.asyncio
async def test_metrics_result_persists_back_into_customer_doc() -> None:
    """The 12-month LTV trend (and anything else the service returns)
    flows back into `cross_entity_metrics` on the customer document.
    """
    db = FakeDB()
    _seed_customer(db, customer_id="c-1")

    ltv_trend = [
        {"month": "2025-06", "ltv_myr": 100.0},
        {"month": "2025-07", "ltv_myr": 110.0},
    ]
    metrics_payload = {
        "ltv_total_myr": 1234.0,
        "ltv_trend_12mo": ltv_trend,
        "top_entity": "internet",
    }
    metrics = AsyncMock()
    metrics.compute_for_customer = AsyncMock(return_value=metrics_payload)

    agg = _build_aggregator(db, metrics=metrics)
    await agg.run_once()

    saved = await db["customers_residential"].find_one({"customer_id": "c-1"})
    assert saved is not None
    cem = saved["cross_entity_metrics"]
    assert cem["ltv_total_myr"] == 1234.0
    assert cem["ltv_trend_12mo"] == ltv_trend
    assert cem["top_entity"] == "internet"
    # Worker stamps last_computed_at so the cooldown gate fires next tick.
    assert isinstance(cem["last_computed_at"], datetime)


@pytest.mark.asyncio
async def test_cooldown_skips_recently_aggregated_customer() -> None:
    db = FakeDB()
    recent = datetime.now(timezone.utc) - timedelta(hours=2)  # < 6h
    _seed_customer(
        db,
        customer_id="c-recent",
        cross_entity_metrics={"last_computed_at": recent},
    )
    _seed_customer(db, customer_id="c-stale")  # no last_computed_at → process

    metrics = AsyncMock()
    metrics.compute_for_customer = AsyncMock(return_value={"ltv_total_myr": 1.0})
    agg = _build_aggregator(db, metrics=metrics)

    out = await agg.run_once()

    assert out["scanned"] == 2
    assert out["skipped"] == 1
    assert out["updated"] == 1
    # Only the stale customer triggered the service.
    metrics.compute_for_customer.assert_awaited_once()
    called_ids = {
        c.args[0] if c.args else c.kwargs.get("customer_id")
        for c in metrics.compute_for_customer.call_args_list
    }
    assert called_ids == {"c-stale"}


@pytest.mark.asyncio
async def test_per_customer_failure_increments_errors_and_continues() -> None:
    db = FakeDB()
    _seed_customer(db, customer_id="c-1")
    _seed_customer(db, customer_id="c-bad")
    _seed_customer(db, customer_id="c-3")

    async def _compute(customer_id: str):
        if customer_id == "c-bad":
            raise RuntimeError("upstream blew up")
        return {"ltv_total_myr": 100.0}

    metrics = AsyncMock()
    metrics.compute_for_customer = AsyncMock(side_effect=_compute)
    agg = _build_aggregator(db, metrics=metrics)

    out = await agg.run_once()

    assert out["scanned"] == 3
    assert out["updated"] == 2
    assert out["errors"] == 1
    assert out["skipped"] == 0
    # All three were attempted — c-bad's failure didn't stop the batch.
    assert metrics.compute_for_customer.await_count == 3


@pytest.mark.asyncio
async def test_channel_engagement_rates_computed_from_seeded_arrays() -> None:
    """5 marketing events, 2 of type "opened" → marketing_open_rate=0.4.
    4 tickets, 3 resolved → support_response_rate=0.75.
    """
    db = FakeDB()
    interaction_history = {
        "support_tickets": [
            {"status": "resolved"},
            {"status": "resolved"},
            {"status": "resolved"},
            {"status": "open"},
        ],
        "marketing_events": [
            {"event_type": "opened"},
            {"event_type": "opened"},
            {"event_type": "clicked"},
            {"event_type": "delivered"},
            {"event_type": "delivered"},
        ],
    }
    _seed_customer(
        db,
        customer_id="c-1",
        interaction_history=interaction_history,
    )

    metrics = AsyncMock()
    metrics.compute_for_customer = AsyncMock(return_value={})
    agg = _build_aggregator(db, metrics=metrics)
    await agg.run_once()

    saved = await db["customers_residential"].find_one({"customer_id": "c-1"})
    rates = saved["interaction_history"]["channel_engagement_rates"]
    assert rates["support_response_rate"] == pytest.approx(0.75)
    assert rates["marketing_open_rate"] == pytest.approx(0.4)
    assert rates["marketing_click_rate"] == pytest.approx(0.2)


# ----------------------------------------------------------------------
# Direct helper coverage (cheap sanity)
# ----------------------------------------------------------------------


def test_compute_engagement_rates_empty_arrays_zero_out() -> None:
    rates = _compute_engagement_rates({})
    assert rates == {
        "support_response_rate": 0.0,
        "marketing_open_rate": 0.0,
        "marketing_click_rate": 0.0,
    }


def test_compute_engagement_rates_handles_none_input() -> None:
    rates = _compute_engagement_rates(None)
    assert rates["support_response_rate"] == 0.0
    assert rates["marketing_open_rate"] == 0.0
    assert rates["marketing_click_rate"] == 0.0


def test_aggregator_interval_seconds_module_constant() -> None:
    assert AGGREGATOR_INTERVAL_SECONDS == 86_400
    assert COOLDOWN_SECONDS == 6 * 3600
