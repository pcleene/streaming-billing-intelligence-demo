"""PR-11 — `MetricsAggregatorService.get_burst_status` tests.

Pure-Python tests against `tests._fakes.FakeDB`. We never need a live
Mongo: the service reads the `system_metrics` collection through the
async-collection surface that FakeCollection already implements.

The five tests cover the full return-shape contract from the spec:

  - active vs inactive flag based on the most-recent row's `mode`
  - explicit `run_id` filter
  - peak / mean TPS summary numerics
  - target_tps_compliance (rows >= 90% of peak observed_tps)
  - empty / steady-only envelope
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.metrics_aggregator_service import MetricsAggregatorService
from tests._fakes import FakeDB


_BASE = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds_offset: int) -> datetime:
    """Return a UTC timestamp `seconds_offset` after the fixed base."""
    return _BASE + timedelta(seconds=seconds_offset)


def _row(
    *,
    seconds_offset: int,
    mode: str,
    observed_tps: float,
    burst_run_id: str | None = None,
    rule_eval_p99_ms: float = 0.0,
    p99_ms_ingest: float = 0.0,
) -> dict[str, Any]:
    """Build a system_metrics-shaped doc — matches what
    `app.workers.metrics_recorder._sample` writes."""
    return {
        "recorded_at": _ts(seconds_offset),
        "mode": mode,
        "burst_run_id": burst_run_id,
        "observed_tps": observed_tps,
        "p50_ms_ingest": 0.0,
        "p99_ms_ingest": p99_ms_ingest,
        "quarantine_per_sec": 0.0,
        "rule_eval_p99_ms": rule_eval_p99_ms,
        "txns_in_window": int(observed_tps * 60),
        "cases_in_window": 0,
    }


def _make_service(db: FakeDB) -> MetricsAggregatorService:
    repo = AsyncMock()
    repo.aggregate = AsyncMock(return_value=[])
    return MetricsAggregatorService(
        repo, system_metrics_collection=db["system_metrics"]
    )


# --- 1. Active flag tracks the most-recent row's mode ---------------


@pytest.mark.asyncio
async def test_get_burst_status_returns_active_when_recent_row_is_burst() -> None:
    """A run where the most-recent sample is mode='burst' is `active`,
    and `started_at` reflects the oldest burst sample."""
    db = FakeDB()
    coll = db["system_metrics"]
    # 5 rows newest-last; rows 1-4 are burst (run_id "R"), row 0 steady.
    await coll.insert_one(_row(seconds_offset=0, mode="steady", observed_tps=10.0))
    await coll.insert_one(
        _row(seconds_offset=60, mode="burst", observed_tps=80.0, burst_run_id="R")
    )
    await coll.insert_one(
        _row(seconds_offset=120, mode="burst", observed_tps=120.0, burst_run_id="R")
    )
    await coll.insert_one(
        _row(seconds_offset=180, mode="burst", observed_tps=160.0, burst_run_id="R")
    )
    await coll.insert_one(
        _row(seconds_offset=240, mode="burst", observed_tps=180.0, burst_run_id="R")
    )

    svc = _make_service(db)
    out = await svc.get_burst_status()

    assert out["active"] is True
    assert out["run_id"] == "R"
    assert out["started_at"] == _ts(60)  # oldest burst row's recorded_at
    assert out["ended_at"] is None
    # Rows are newest-first.
    assert out["rows"][0]["recorded_at"] == _ts(240)


# --- 2. Explicit run_id filter --------------------------------------


@pytest.mark.asyncio
async def test_get_burst_status_specific_run_id() -> None:
    """When a `run_id` is passed, every returned row carries that
    `burst_run_id` even if a more-recent run from a different id exists."""
    db = FakeDB()
    coll = db["system_metrics"]
    # Three rows for run A, three for run B (B is newer).
    for i in range(3):
        await coll.insert_one(
            _row(seconds_offset=i * 60, mode="burst",
                 observed_tps=100.0, burst_run_id="A")
        )
    for i in range(3):
        await coll.insert_one(
            _row(seconds_offset=300 + i * 60, mode="burst",
                 observed_tps=150.0, burst_run_id="B")
        )

    svc = _make_service(db)
    out = await svc.get_burst_status(run_id="B")

    assert out["run_id"] == "B"
    assert len(out["rows"]) == 3
    for row in out["rows"]:
        assert row["burst_run_id"] == "B"


# --- 3. Peak / mean TPS summary -------------------------------------


@pytest.mark.asyncio
async def test_get_burst_status_summary_peak_and_mean() -> None:
    """observed_tps `[50, 100, 150, 200, 100]` -> peak=200, mean=120."""
    db = FakeDB()
    coll = db["system_metrics"]
    values = [50.0, 100.0, 150.0, 200.0, 100.0]
    for i, v in enumerate(values):
        await coll.insert_one(
            _row(seconds_offset=i * 60, mode="burst",
                 observed_tps=v, burst_run_id="R")
        )

    svc = _make_service(db)
    out = await svc.get_burst_status(run_id="R")

    summary = out["summary"]
    assert summary["row_count"] == 5
    assert summary["peak_tps"] == pytest.approx(200.0, abs=0.01)
    assert summary["mean_tps"] == pytest.approx(120.0, abs=0.01)


# --- 4. Compliance fraction (>= 90% of peak) -----------------------


@pytest.mark.asyncio
async def test_get_burst_status_compliance_fraction() -> None:
    """Peak observed_tps = 200 → threshold = 180. Of ten rows, count
    those at or above 180 and divide by 10. Expected fraction = 0.4."""
    db = FakeDB()
    coll = db["system_metrics"]
    # Peak = 200; rows at/above 0.9*200 = 180 are: 180, 190, 200, 200 → 4.
    values = [50.0, 75.0, 100.0, 120.0, 150.0, 170.0, 180.0, 190.0, 200.0, 200.0]
    for i, v in enumerate(values):
        await coll.insert_one(
            _row(seconds_offset=i * 60, mode="burst",
                 observed_tps=v, burst_run_id="R")
        )

    svc = _make_service(db)
    out = await svc.get_burst_status(run_id="R")

    summary = out["summary"]
    assert summary["peak_tps"] == pytest.approx(200.0, abs=0.01)
    # 4 out of 10 rows are >= 180.
    assert summary["target_tps_compliance"] == pytest.approx(0.4, abs=0.0001)


# --- 5. Inactive when latest is steady ------------------------------


@pytest.mark.asyncio
async def test_get_burst_status_inactive_when_latest_row_is_steady() -> None:
    """All rows steady → no burst; envelope reports `active=False` and
    summary is the empty zero-shape."""
    db = FakeDB()
    coll = db["system_metrics"]
    for i in range(5):
        await coll.insert_one(
            _row(seconds_offset=i * 60, mode="steady", observed_tps=10.0)
        )

    svc = _make_service(db)
    out = await svc.get_burst_status()

    assert out["active"] is False
    assert out["rows"] == []
    assert out["summary"]["row_count"] == 0
    assert out["summary"]["peak_tps"] == 0.0
    assert out["summary"]["target_tps_compliance"] == 0.0


# --- Bonus: missing collection raises clearly -----------------------


@pytest.mark.asyncio
async def test_get_burst_status_requires_system_metrics_collection() -> None:
    """The PR-9 ctor (transaction_repo only) is still valid; calling
    burst status without wiring system_metrics must raise."""
    repo = AsyncMock()
    svc = MetricsAggregatorService(repo)  # no system_metrics arg
    with pytest.raises(RuntimeError):
        await svc.get_burst_status()
