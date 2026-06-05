"""Unit tests for `app.services.metrics_refresh_service.MetricsRefreshService`.

Pure-Python tests using `tests._fakes.FakeDB` for the customer
repository and an `AsyncMock` for `MetricsAggregatorService`. The
service is the orchestration layer in front of the aggregator: it
owns the cooldown contract, the force-bypass, and the bounded-
concurrency batch flow.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.errors import CustomerNotFound
from app.repositories.customer_repo import CustomerRepository
from app.services.metrics_refresh_service import (
    DEFAULT_COOLDOWN_SECONDS,
    MetricsRefreshService,
)
from tests._fakes import FakeDB


_BASE = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def _seed_customer(
    db: FakeDB,
    *,
    customer_id: str,
    last_computed_at: datetime | None = None,
    customer_type: str = "residential",
) -> None:
    coll = db["customers_residential"]
    cem: dict = {}
    if last_computed_at is not None:
        cem["last_computed_at"] = last_computed_at
    coll._docs.append({  # type: ignore[attr-defined]
        "customer_id": customer_id,
        "customer_type": customer_type,
        "cross_entity_metrics": cem,
    })


def _make_service(
    db: FakeDB, *, aggregator: AsyncMock | None = None
) -> tuple[MetricsRefreshService, AsyncMock]:
    if aggregator is None:
        aggregator = AsyncMock()
        aggregator.compute_for_customer = AsyncMock(
            return_value={
                "ltv": {"total_myr": 100.0, "currency": "MYR"},
                "monthly_spend_trend_12m": [],
                "viewing_hours_trend_12m": [],
                "ppv_count_trend_12m": [],
            }
        )
    repo = CustomerRepository(db)
    svc = MetricsRefreshService(aggregator, repo)
    return svc, aggregator


# ---------------------------------------------------------------------
# refresh_one
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_one_fresh_path_calls_aggregator() -> None:
    """Customer with no prior compute → aggregator runs, result persisted."""
    db = FakeDB()
    _seed_customer(db, customer_id="c-1")
    svc, aggregator = _make_service(db)

    out = await svc.refresh_one("c-1")

    assert out["customer_id"] == "c-1"
    assert out["computed"] is True
    assert out["skipped_reason"] is None
    assert out["cross_entity_metrics"]["ltv"]["total_myr"] == 100.0
    aggregator.compute_for_customer.assert_awaited_once_with("c-1")

    # Persisted on the customer doc with a UTC `last_computed_at` stamp.
    saved = await db["customers_residential"].find_one({"customer_id": "c-1"})
    cem = saved["cross_entity_metrics"]
    assert isinstance(cem["last_computed_at"], datetime)
    assert cem["last_computed_at"].tzinfo is not None


@pytest.mark.asyncio
async def test_refresh_one_cooldown_skip() -> None:
    """`last_computed_at` 1h ago → skipped with reason cooldown_active."""
    db = FakeDB()
    recent = _BASE - timedelta(hours=1)
    _seed_customer(db, customer_id="c-1", last_computed_at=recent)
    svc, aggregator = _make_service(db)

    out = await svc.refresh_one("c-1")

    assert out["computed"] is False
    assert out["skipped_reason"] == "cooldown_active"
    aggregator.compute_for_customer.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_one_force_bypasses_cooldown() -> None:
    """force=True must trigger a recompute even when cooldown is active."""
    db = FakeDB()
    recent = datetime.now(timezone.utc) - timedelta(minutes=30)
    _seed_customer(db, customer_id="c-1", last_computed_at=recent)
    svc, aggregator = _make_service(db)

    out = await svc.refresh_one("c-1", force=True)

    assert out["computed"] is True
    assert out["skipped_reason"] is None
    aggregator.compute_for_customer.assert_awaited_once_with("c-1")


@pytest.mark.asyncio
async def test_refresh_one_cooldown_expired_runs_again() -> None:
    """`last_computed_at` older than cooldown → aggregator runs."""
    db = FakeDB()
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=DEFAULT_COOLDOWN_SECONDS + 60
    )
    _seed_customer(db, customer_id="c-1", last_computed_at=stale)
    svc, aggregator = _make_service(db)

    out = await svc.refresh_one("c-1")

    assert out["computed"] is True
    aggregator.compute_for_customer.assert_awaited_once_with("c-1")


@pytest.mark.asyncio
async def test_refresh_one_missing_customer_raises() -> None:
    db = FakeDB()
    svc, aggregator = _make_service(db)

    with pytest.raises(CustomerNotFound):
        await svc.refresh_one("nope")
    aggregator.compute_for_customer.assert_not_awaited()


# ---------------------------------------------------------------------
# refresh_many
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_many_concurrency_cap_respected() -> None:
    """With 12 customers and max_concurrency=3, peak in-flight ≤ 3."""
    db = FakeDB()
    for i in range(12):
        _seed_customer(db, customer_id=f"c-{i}")

    in_flight = 0
    peak = 0

    async def slow_compute(customer_id: str) -> dict:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.01)
            return {"ltv": {"total_myr": 1.0, "currency": "MYR"}}
        finally:
            in_flight -= 1

    aggregator = AsyncMock()
    aggregator.compute_for_customer = AsyncMock(side_effect=slow_compute)
    svc, _ = _make_service(db, aggregator=aggregator)

    out = await svc.refresh_many(
        [f"c-{i}" for i in range(12)], max_concurrency=3
    )

    assert out["requested"] == 12
    assert out["computed"] == 12
    assert out["skipped"] == 0
    assert out["errors"] == []
    assert peak <= 3, f"concurrency cap violated: peak={peak}"


@pytest.mark.asyncio
async def test_refresh_many_errors_propagate_per_id() -> None:
    """A single bad id surfaces in the errors list; the rest still run."""
    db = FakeDB()
    _seed_customer(db, customer_id="ok-1")
    _seed_customer(db, customer_id="ok-2")
    # 'missing' is intentionally NOT seeded.

    svc, aggregator = _make_service(db)

    out = await svc.refresh_many(["ok-1", "missing", "ok-2"])

    assert out["requested"] == 3
    assert out["computed"] == 2
    assert out["skipped"] == 0
    assert len(out["errors"]) == 1
    assert out["errors"][0]["customer_id"] == "missing"
    assert out["errors"][0]["reason"] == "not_found"


@pytest.mark.asyncio
async def test_refresh_many_mixes_computed_and_cooldown_skips() -> None:
    """Some customers cooled down, others fresh → counters split correctly."""
    db = FakeDB()
    _seed_customer(db, customer_id="fresh")
    _seed_customer(
        db,
        customer_id="cooled",
        last_computed_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    svc, _ = _make_service(db)

    out = await svc.refresh_many(["fresh", "cooled"])

    assert out["requested"] == 2
    assert out["computed"] == 1
    assert out["skipped"] == 1
    assert out["errors"] == []


# ---------------------------------------------------------------------
# burst status passthrough
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_burst_status_passthrough() -> None:
    """Service forwards args + envelope unchanged to the aggregator."""
    db = FakeDB()
    payload = {
        "run_id": "R",
        "active": False,
        "samples": [],
        "summary": {"row_count": 0},
    }
    aggregator = AsyncMock()
    aggregator.get_burst_status = AsyncMock(return_value=payload)
    svc, _ = _make_service(db, aggregator=aggregator)

    out = await svc.get_burst_status(run_id="R", limit=42)

    assert out is payload
    aggregator.get_burst_status.assert_awaited_once_with("R", limit=42)
